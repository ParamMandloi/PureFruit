"""Smoke tests for daily ordering on the single live site. Run via GitHub Actions."""
import importlib
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(TEMP_DIR.name, "orders.sqlite")
os.environ["SECRET_KEY"] = "test-secret-not-for-production"
os.environ["ADMIN_PASSWORD"] = "staff-test-password"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)

app_module = importlib.import_module("app")
from daily_orders import MAX_DAILY_ORDERS, slots_for_date, build_order_qr_uri, build_phonepe_payment_uri

app = app_module.app


def next_weekday(day):
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class DailyOrdersTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.delivery_date = next_weekday(app_module.now_ist().date() + timedelta(days=1))
        self.service_date = self.delivery_date.isoformat()
        with app_module.get_db() as con:
            con.execute("DELETE FROM daily_orders")
            con.execute("DELETE FROM daily_customer_devices")
            con.execute("DELETE FROM daily_customers")
            con.execute("DELETE FROM daily_menus")
            con.execute("""INSERT INTO daily_menus (service_date,juice,unit_price,is_open,updated_at)
                         VALUES (?,?,?,?,?)""",
                        (self.service_date, "Pineapple", 99, 0, "test-menu-v1"))

    def open_menu(self):
        with app_module.get_db() as con:
            con.execute("UPDATE daily_menus SET is_open=1 WHERE service_date=?", (self.service_date,))

    def form_fields(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            return {
                "csrf_token": sess["csrf_token"],
                "nonce": sess["daily_nonce"],
                "delivery_option": self.service_date + "|morning",
                "portions": "3",
                "building_flat": "Tower B / 1204",
                "customer_name": "Ananya Sharma",
                "customer_mobile": "9876543210",
            }

    def staff_login(self):
        with self.client.session_transaction() as sess:
            sess["staff_logged_in"] = True
            sess["csrf_token"] = "staff-test-csrf"
        return "staff-test-csrf"

    def seed_orders(self, quantity):
        with app_module.get_db() as con:
            for i in range(quantity):
                con.execute("""INSERT INTO daily_orders
                    (order_ref,nonce,created_at,service_date,juice,portions,unit_price,
                     total_price,delivery_slot,building_flat,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"TEST-{i}", f"nonce-{i}", "2026-11-22T09:00:00", self.service_date,
                     "Pineapple", 1, 99, 99, "morning", "Tower A / 104", "2026-11-22T09:00:00"))

    def test_closed_form_cannot_submit(self):
        fields = self.form_fields()
        response = self.client.post("/", data=fields)
        self.assertEqual(response.status_code, 409)

    def test_order_saved_and_remembers_customer_without_whatsapp_chat(self):
        self.open_menu()
        fields = self.form_fields()
        response = self.client.post("/", data=fields, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/daily/thanks/", response.headers["Location"])
        self.assertIn("purefruit_daily_device=", response.headers.get("Set-Cookie", ""))
        with app_module.get_db() as con:
            orders = con.execute("SELECT * FROM daily_orders").fetchall()
            customers = con.execute("SELECT * FROM daily_customers").fetchall()
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(customers), 1)
        self.assertEqual(orders[0]["total_price"], 297)
        self.assertEqual(orders[0]["name"], "Ananya Sharma")
        self.assertEqual(orders[0]["phone"], "9876543210")
        self.assertEqual(orders[0]["payment_status"], "unpaid")
        self.assertEqual(orders[0]["building_flat"], "Tower B / 1204")
        thanks = self.client.get(response.headers["Location"])
        self.assertIn(b"Order confirmed", thanks.data)
        self.assertIn(b"whether you pay now or later", thanks.data)
        # A repeated POST with the same nonce never creates a duplicate.
        repeat = self.client.post("/", data=fields)
        self.assertEqual(repeat.status_code, 303)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1)
        # Next visit on same browser: details prefilled and hidden until Change is tapped.
        again = self.client.get("/")
        self.assertIn(b"Ordering as", again.data)
        self.assertIn(b"Ananya Sharma", again.data)
        self.assertIn(b'value="9876543210"', again.data)

    def test_customer_can_forget_cookie_without_erasing_orders(self):
        self.open_menu()
        fields = self.form_fields()
        self.client.post("/", data=fields)
        with self.client.session_transaction() as sess:
            token = sess["csrf_token"]
        r = self.client.post("/daily/forget", data={"csrf_token": token})
        self.assertEqual(r.status_code, 303)
        self.assertNotIn(b"Ordering as", self.client.get("/").data)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1)

    def test_1000_orders_per_delivery_date_cap_and_delete(self):
        self.open_menu()
        fields = self.form_fields()
        self.seed_orders(MAX_DAILY_ORDERS)
        blocked = self.client.post("/", data=fields)
        self.assertEqual(blocked.status_code, 409)
        closed = self.client.get("/")
        self.assertIn(b"Ordering is currently closed", closed.data)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1000)
        csrf = self.staff_login()
        removed = self.client.post("/staff/daily/order/TEST-0/delete",
            data={"csrf_token": csrf, "confirm_ref": "TEST-0", "date": self.service_date})
        self.assertEqual(removed.status_code, 302)
        with app_module.get_db() as con:
            row = con.execute("SELECT deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()
        self.assertIsNotNone(row["deleted_at"])
        self.assertIn(b"Number of portions", self.client.get("/").data)

    def test_cannot_delete_paid_or_preparing_orders(self):
        self.seed_orders(2)
        with app_module.get_db() as con:
            con.execute("UPDATE daily_orders SET payment_status='paid' WHERE order_ref='TEST-0'")
            con.execute("UPDATE daily_orders SET status='preparing' WHERE order_ref='TEST-1'")
        csrf = self.staff_login()
        for ref in ("TEST-0", "TEST-1"):
            r = self.client.post(f"/staff/daily/order/{ref}/delete",
                data={"csrf_token": csrf, "confirm_ref": ref, "date": self.service_date})
            self.assertEqual(r.status_code, 302)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders WHERE deleted_at IS NOT NULL").fetchone()[0], 0)

    def test_archive_old_orders_preserves_paid_history_and_restore(self):
        self.seed_orders(2)
        prior = (app_module.now_ist().date() - timedelta(days=1)).isoformat()
        with app_module.get_db() as con:
            con.execute("UPDATE daily_orders SET service_date=?,payment_status='paid',payment_method='upi',paid_at=? WHERE order_ref='TEST-0'",
                        (prior, "2026-11-20T12:00:00"))
        csrf = self.staff_login()
        no_confirm = self.client.post("/staff/daily/archive-old", data={"csrf_token": csrf})
        self.assertEqual(no_confirm.status_code, 400)
        archived = self.client.post("/staff/daily/archive-old",
            data={"csrf_token": csrf, "confirm_archive": "yes"})
        self.assertEqual(archived.status_code, 302)
        with app_module.get_db() as con:
            prior_row = con.execute("SELECT archived_at,payment_status,paid_at,deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()
            current_row = con.execute("SELECT archived_at FROM daily_orders WHERE order_ref='TEST-1'").fetchone()
        self.assertIsNotNone(prior_row["archived_at"])
        self.assertEqual(prior_row["payment_status"], "paid")
        self.assertIsNotNone(prior_row["paid_at"])
        self.assertIsNone(prior_row["deleted_at"])
        self.assertIsNone(current_row["archived_at"])
        self.assertIn(b"TEST-0", self.client.get("/staff?view=archived").data)
        self.assertNotIn(b"TEST-0", self.client.get("/staff?date="+prior).data)
        restored = self.client.post("/staff/daily/order/TEST-0/restore",
            data={"csrf_token": csrf, "date": prior})
        self.assertEqual(restored.status_code, 302)
        with app_module.get_db() as con:
            self.assertIsNone(con.execute("SELECT archived_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()[0])

    def test_no_payment_app_buttons_and_manual_fallback(self):
        self.open_menu()
        order = self.client.post("/", data=self.form_fields(), follow_redirects=False)
        self.assertEqual(order.status_code, 303)
        self.assertEqual(app_module.UPI_PAYEE_ADDRESS, "9619140077-huf@ybl")
        self.assertEqual(app_module.UPI_PAYEE_NAME, "PARAMPRAKASH RAJENDRA MANDLOI KARTA")
        page = self.client.get(order.headers["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"9619140077-huf@ybl", page.data)
        self.assertIn(b"Copy UPI ID", page.data)
        self.assertIn(b"Pay to UPI ID", page.data)
        self.assertIn(b"Open QR image to save", page.data)
        self.assertIn(b"Order confirmed", page.data)
        self.assertIn(b"I've made payment", page.data)
        # The customer requested no PhonePe or Google Pay buttons.
        self.assertNotIn(b'id="upi-phonepe"', page.data)
        self.assertNotIn(b'id="upi-gpay"', page.data)
        self.assertIn(b"Recommended: Pay to mobile number", page.data)
        self.assertIn(b"9619140077", page.data)
        self.assertIn(b"Copy number", page.data)
        self.assertIn(b"QR amount: ", page.data)
        self.assertNotIn(b"phonepe://pay?", page.data)
        self.assertNotIn(b"tez://upi/pay?", page.data)
        self.assertNotIn(b'href="upi://pay?', page.data)
        self.assertNotIn(b"wa.me/", page.data)
        ref = order.headers["Location"].rsplit("/", 1)[-1]
        qr = self.client.get("/daily/qr/"+ref+".png")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.content_type, "image/png")
        self.assertGreater(len(qr.data), 100)
        uri = build_order_qr_uri(
            app_module.UPI_PAYEE_ADDRESS, app_module.UPI_PAYEE_NAME,
            {"total_price": 297, "order_ref": ref},
        )
        self.assertIn("am=297.00", uri)
        self.assertIn("pa=9619140077-huf%40ybl", uri)
        self.assertIn("cu=INR", uri)

    def test_amount_matches_portions_in_db_page_and_qr(self):
        self.open_menu()
        for qty in (1, 2, 5, 10):
            with self.subTest(qty=qty):
                fields = self.form_fields()
                fields["portions"] = str(qty)
                response = self.client.post("/", data=fields, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                ref = response.headers["Location"].rsplit("/", 1)[-1]
                with app_module.get_db() as con:
                    row = con.execute(
                        "SELECT portions,unit_price,total_price FROM daily_orders WHERE order_ref=?",
                        (ref,)
                    ).fetchone()
                self.assertEqual(row["portions"], qty)
                self.assertEqual(row["unit_price"], 99)
                self.assertEqual(row["total_price"], qty * 99)
                page = self.client.get(response.headers["Location"])
                self.assertIn(f"Total ({qty} × ₹99)".encode("utf-8"), page.data)
                self.assertIn(f'₹{qty*99}'.encode("utf-8"), page.data)
                self.assertIn(f'id="upi-amount">{qty*99}</span>'.encode("utf-8"), page.data)
                qr = build_order_qr_uri(
                    app_module.UPI_PAYEE_ADDRESS, app_module.UPI_PAYEE_NAME,
                    {"total_price": row["total_price"], "order_ref": ref},
                )
                self.assertIn(f"am={qty*99:.2f}", qr)
                phonepe = build_phonepe_payment_uri(
                    app_module.UPI_PAYEE_ADDRESS, app_module.UPI_PAYEE_NAME,
                    {"total_price": row["total_price"], "order_ref": ref},
                )
                self.assertIn(f"am={qty*99:.2f}", phonepe)
                self.assertIn("pa=9619140077-huf%40ybl", phonepe)
                self.assertNotIn("tr=", phonepe)
                self.assertNotIn("mode=", phonepe)
                self.assertNotIn(b"phonepe://pay?", page.data)

    def test_limit_is_1000_orders_and_10_portions(self):
        self.assertEqual(MAX_DAILY_ORDERS, 1000)
        self.open_menu()
        form = self.client.get("/")
        self.assertIn(b'max="10"', form.data)
        self.assertIn(b"Maximum 10 portions per order", form.data)
        self.assertIn(b"Math.min(10,", form.data)
        for invalid in ("0", "11", "30", "100"):
            fields = self.form_fields()
            fields["portions"] = invalid
            response = self.client.post("/", data=fields)
            self.assertEqual(response.status_code, 400)
        fields = self.form_fields()
        fields["portions"] = "10"
        response = self.client.post("/", data=fields, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        ref = response.headers["Location"].rsplit("/", 1)[-1]
        with app_module.get_db() as con:
            row = con.execute("SELECT portions,total_price FROM daily_orders WHERE order_ref=?", (ref,)).fetchone()
            self.assertEqual(row["portions"], 10)
            self.assertEqual(row["total_price"], 990)

    def test_amount_respects_price_saved_at_order_time(self):
        self.open_menu()
        with app_module.get_db() as con:
            con.execute(
                "UPDATE daily_menus SET unit_price=115,updated_at='price-v2' WHERE service_date=?",
                (self.service_date,),
            )
        fields = self.form_fields()
        fields["portions"] = "4"
        response = self.client.post("/", data=fields, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        ref = response.headers["Location"].rsplit("/", 1)[-1]
        with app_module.get_db() as con:
            row = con.execute(
                "SELECT portions,unit_price,total_price FROM daily_orders WHERE order_ref=?",
                (ref,),
            ).fetchone()
        self.assertEqual(row["total_price"], 460)
        self.assertEqual(row["unit_price"], 115)
        page = self.client.get(response.headers["Location"])
        self.assertIn("Total (4 × ₹115)".encode("utf-8"), page.data)
        self.assertIn(b'id="upi-amount">460</span>', page.data)
        uri = build_order_qr_uri(
            app_module.UPI_PAYEE_ADDRESS, app_module.UPI_PAYEE_NAME,
            {"total_price": row["total_price"], "order_ref": ref},
        )
        self.assertIn("am=460.00", uri)
        # Changing today's menu after booking must not alter amount due.
        with app_module.get_db() as con:
            con.execute(
                "UPDATE daily_menus SET unit_price=99,updated_at='price-v3' WHERE service_date=?",
                (self.service_date,),
            )
        self.assertIn(b'id="upi-amount">460</span>',
                      self.client.get(response.headers["Location"]).data)

    def test_order_page_is_compact_with_persistent_checkout_and_large_slots(self):
        self.open_menu()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"height:76px", page.data)
        self.assertIn(b"font-size:18px", page.data)
        self.assertIn(b"class=\"slot-time\"", page.data)
        self.assertIn(b"position:fixed", page.data)
        self.assertIn(b'class="checkout-dock"', page.data)
        self.assertIn(b'id="submit" type="submit">Place Order', page.data)
        self.assertIn(b"Number of portions", page.data)

    def test_payment_made_reports_only_and_does_not_hold_delivery(self):
        self.open_menu()
        placed = self.client.post("/", data=self.form_fields(), follow_redirects=False)
        self.assertEqual(placed.status_code, 303)
        ref = placed.headers["Location"].rsplit("/", 1)[-1]
        with self.client.session_transaction() as sess:
            csrf = sess["csrf_token"]
        path = "/daily/thanks/" + ref + "/payment-made"
        reported = self.client.post(path, data={"csrf_token": csrf}, follow_redirects=False)
        self.assertEqual(reported.status_code, 303)
        with app_module.get_db() as con:
            row = con.execute(
                "SELECT payment_status,payment_reported_at,paid_at,status FROM daily_orders WHERE order_ref=?",
                (ref,)).fetchone()
        self.assertEqual(row["payment_status"], "pending_verification")
        self.assertIsNotNone(row["payment_reported_at"])
        self.assertIsNone(row["paid_at"])
        self.assertEqual(row["status"], "new")
        thanks = self.client.get(reported.headers["Location"])
        self.assertIn(b"Payment reported", thanks.data)
        self.assertIn(b"delivery is already confirmed", thanks.data.lower())
        self.assertNotIn(b"wa.me/", thanks.data)
        # Repeated tapping is safe.
        self.assertEqual(self.client.post(path, data={"csrf_token": csrf}).status_code, 303)
        with app_module.get_db() as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM daily_orders WHERE payment_status='pending_verification'"
            ).fetchone()[0], 1)
        # Staff alone can verify actual payment; customer click cannot.
        staff_csrf = self.staff_login()
        confirmed = self.client.post("/staff/daily/order/"+ref+"/paid",
            data={"csrf_token": staff_csrf, "verified": "yes",
                  "payment_method": "upi", "date": self.service_date})
        self.assertEqual(confirmed.status_code, 302)
        with app_module.get_db() as con:
            paid = con.execute("SELECT payment_status,paid_at FROM daily_orders WHERE order_ref=?", (ref,)).fetchone()
        self.assertEqual(paid["payment_status"], "paid")
        self.assertIsNotNone(paid["paid_at"])

    def test_payment_report_requires_same_browser_and_csrf(self):
        self.open_menu()
        placed = self.client.post("/", data=self.form_fields(), follow_redirects=False)
        ref = placed.headers["Location"].rsplit("/", 1)[-1]
        path = "/daily/thanks/" + ref + "/payment-made"
        without_csrf = self.client.post(path)
        self.assertEqual(without_csrf.status_code, 403)
        other_browser = app.test_client()
        other_browser.get("/")
        with other_browser.session_transaction() as sess:
            other_csrf = sess["csrf_token"]
        rejected = other_browser.post(path, data={"csrf_token": other_csrf})
        self.assertEqual(rejected.status_code, 403)

    def test_mobile_and_name_are_required(self):
        self.open_menu()
        fields = self.form_fields()
        fields["customer_name"] = ""
        fields["customer_mobile"] = "123"
        response = self.client.post("/", data=fields)
        self.assertEqual(response.status_code, 400)

    def test_slot_cutoffs_are_exact_in_india_time(self):
        tz = ZoneInfo("Asia/Kolkata")
        day = date(2026, 11, 23)
        def dt(h, m):
            return datetime.combine(day, time(h, m), tzinfo=tz)
        self.assertEqual(slots_for_date(day, dt(8, 59)), ("morning", "afternoon"))
        self.assertEqual(slots_for_date(day, dt(9, 0)), ("afternoon",))
        self.assertEqual(slots_for_date(day, dt(16, 59)), ("afternoon",))
        self.assertEqual(slots_for_date(day, dt(17, 0)), ())
        self.assertEqual(slots_for_date(day + timedelta(days=1), dt(17, 0)),
                         ("morning", "afternoon"))
        self.assertEqual(slots_for_date(date(2026, 11, 22), dt(17, 0)), ())

    def test_main_and_legacy_routes(self):
        self.open_menu()
        self.assertIn(b"Number of portions", self.client.get("/").data)
        self.assertEqual(self.client.get("/subscriptions").status_code, 200)
        self.staff_login()
        self.assertIn(b"Daily Orders", self.client.get("/staff").data)
        self.assertEqual(self.client.get("/staff/subscriptions").status_code, 200)

    def test_staff_modifications_require_csrf(self):
        no_login = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(no_login.status_code, 302)
        self.staff_login()
        bad = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(bad.status_code, 403)
        bad_delete = self.client.post("/staff/daily/order/TEST-0/delete", data={"confirm_ref": "TEST-0"})
        self.assertEqual(bad_delete.status_code, 403)


if __name__ == "__main__":
    unittest.main()
