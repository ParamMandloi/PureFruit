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
from daily_orders import (MAX_DAILY_ORDERS, slots_for_date, build_order_qr_uri,
                          build_phonepe_payment_uri, REQUESTED_TEST_CLEANUP_REFS,
                          retire_screenshot_test_orders)

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
        response = self.client.get("/order")
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
                     total_price,delivery_slot,building_flat,updated_at,name,phone)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"TEST-{i}", f"nonce-{i}", "2026-11-22T09:00:00", self.service_date,
                     "Pineapple", 1, 99, 99, "morning", "Tower A / 104", "2026-11-22T09:00:00",
                     "Sample Daily Customer", "9876543210"))

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
        self.assertEqual(orders[0]["order_type"], "actual")
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
        again = self.client.get("/order")
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
        self.assertNotIn(b"Ordering as", self.client.get("/order").data)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1)

    def test_1000_orders_per_delivery_date_cap_and_delete(self):
        self.open_menu()
        fields = self.form_fields()
        self.seed_orders(MAX_DAILY_ORDERS)
        blocked = self.client.post("/", data=fields)
        self.assertEqual(blocked.status_code, 409)
        closed = self.client.get("/order")
        self.assertIn(b"Ordering is currently closed", closed.data)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1000)
        csrf = self.staff_login()
        # Real orders cannot be deleted until staff explicitly mark them Test.
        blocked_delete = self.client.post("/staff/daily/order/TEST-0/delete",
            data={"csrf_token": csrf, "confirm_ref": "TEST-0", "date": self.service_date})
        self.assertEqual(blocked_delete.status_code, 302)
        with app_module.get_db() as con:
            self.assertIsNone(con.execute(
                "SELECT deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()[0])
        classified = self.client.post("/staff/daily/order/TEST-0/type",
            data={"csrf_token": csrf, "order_type": "test", "date": self.service_date})
        self.assertEqual(classified.status_code, 302)
        removed = self.client.post("/staff/daily/order/TEST-0/delete",
            data={"csrf_token": csrf, "confirm_ref": "TEST-0", "date": self.service_date})
        self.assertEqual(removed.status_code, 302)
        with app_module.get_db() as con:
            row = con.execute("SELECT deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()
        self.assertIsNotNone(row["deleted_at"])
        self.assertIn(b"Number of portions", self.client.get("/order").data)

    def test_cannot_delete_paid_test_or_actual_preparing_order(self):
        self.seed_orders(2)
        with app_module.get_db() as con:
            con.execute("""UPDATE daily_orders SET order_type='test',
                payment_status='paid', paid_at='2026-11-23T06:00:00' WHERE order_ref='TEST-0'""")
            con.execute("UPDATE daily_orders SET status='preparing' WHERE order_ref='TEST-1'")
        csrf = self.staff_login()
        for ref in ("TEST-0", "TEST-1"):
            r = self.client.post(f"/staff/daily/order/{ref}/delete",
                data={"csrf_token": csrf, "confirm_ref": ref, "date": self.service_date})
            self.assertEqual(r.status_code, 302)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders WHERE deleted_at IS NOT NULL").fetchone()[0], 0)
        # A classified Test may be removed even if preparation was started.
        classified = self.client.post("/staff/daily/order/TEST-1/type",
            data={"csrf_token": csrf, "order_type": "test", "date": self.service_date})
        self.assertEqual(classified.status_code, 302)
        removed = self.client.post("/staff/daily/order/TEST-1/delete",
            data={"csrf_token": csrf, "confirm_ref": "TEST-1", "date": self.service_date})
        self.assertEqual(removed.status_code, 302)
        with app_module.get_db() as con:
            row = con.execute("SELECT order_type,deleted_at FROM daily_orders WHERE order_ref='TEST-1'").fetchone()
            self.assertEqual(row["order_type"], "test")
            self.assertIsNotNone(row["deleted_at"])

    def test_staff_can_mark_order_actual_or_test_and_delete_only_test(self):
        self.open_menu()
        self.seed_orders(1)
        csrf = self.staff_login()
        url = "/staff/daily/order/TEST-0"
        initial = self.client.get("/staff?date=" + self.service_date)
        self.assertIn(b'class="type-badge actual">ACTUAL</span>', initial.data)
        self.assertNotIn(b'<button class="danger" type="submit">Delete test</button>', initial.data)
        # Invalid classification cannot mutate the record.
        self.assertEqual(self.client.post(url + "/type",
            data={"csrf_token": csrf, "order_type": "other"}).status_code, 400)
        marked_test = self.client.post(url + "/type",
            data={"csrf_token": csrf, "order_type": "test", "date": self.service_date})
        self.assertEqual(marked_test.status_code, 302)
        page = self.client.get("/staff?date=" + self.service_date)
        self.assertIn(b'class="type-badge test">TEST</span>', page.data)
        self.assertIn(b'<button class="danger" type="submit">Delete test</button>', page.data)
        back_to_actual = self.client.post(url + "/type",
            data={"csrf_token": csrf, "order_type": "actual", "date": self.service_date})
        self.assertEqual(back_to_actual.status_code, 302)
        with app_module.get_db() as con:
            row = con.execute("SELECT order_type,deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()
            self.assertEqual(row["order_type"], "actual")
            self.assertIsNone(row["deleted_at"])
        forbidden_delete = self.client.post(url + "/delete",
            data={"csrf_token": csrf, "confirm_ref": "TEST-0", "date": self.service_date})
        self.assertEqual(forbidden_delete.status_code, 302)
        with app_module.get_db() as con:
            self.assertIsNone(con.execute("SELECT deleted_at FROM daily_orders WHERE order_ref='TEST-0'").fetchone()[0])

    def test_retires_only_four_screenshot_test_refs_and_protects_paid(self):
        self.assertEqual(len(REQUESTED_TEST_CLEANUP_REFS), 4)
        # Create rows resembling the production screenshot, with controlled values.
        # One screenshot test ref has since been marked paid: it must survive.
        refs = list(REQUESTED_TEST_CLEANUP_REFS)
        preserved_paid = "PFD-261123-C78F63B732C5"
        with app_module.get_db() as con:
            for idx, ref in enumerate(refs + [preserved_paid, "UNRELATED-UNPAID-1"]):
                state = "paid" if idx in (2, 4) else ("pending_verification" if idx == 1 else "unpaid")
                paid_at = "2026-11-23T07:00:00" if idx in (2, 4) else None
                con.execute("""INSERT INTO daily_orders
                    (order_ref,nonce,created_at,service_date,juice,portions,unit_price,
                     total_price,delivery_slot,building_flat,updated_at,name,phone,
                     payment_status,paid_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ref, f"cleanup-nonce-{idx}", "2026-11-23T07:30:00",
                     self.service_date, "Mosambi", 1, 99, 99, "morning",
                     "Tower A / 101", "2026-11-23T07:30:00", "Screenshot Test", "9876543210",
                     state, paid_at))
            changed = retire_screenshot_test_orders(con, "2026-11-23T07:45:00")
        self.assertEqual(changed, 3)
        with app_module.get_db() as con:
            for idx, ref in enumerate(refs):
                row = con.execute(
                    "SELECT deleted_at,order_type,payment_status FROM daily_orders WHERE order_ref=?",
                    (ref,),
                ).fetchone()
                if idx == 2:
                    self.assertIsNone(row["deleted_at"])
                    self.assertEqual(row["payment_status"], "paid")
                    self.assertEqual(row["order_type"], "actual")
                else:
                    self.assertIsNotNone(row["deleted_at"])
                    self.assertEqual(row["order_type"], "test")
            for ref in (preserved_paid, "UNRELATED-UNPAID-1"):
                self.assertIsNone(con.execute(
                    "SELECT deleted_at FROM daily_orders WHERE order_ref=?", (ref,)
                ).fetchone()[0])
            self.assertEqual(retire_screenshot_test_orders(con, "2026-11-23T07:46:00"), 0)
        self.staff_login()
        page = self.client.get("/staff?date=" + self.service_date)
        for idx, ref in enumerate(refs):
            if idx != 2:
                self.assertNotIn(ref.encode(), page.data)
        self.assertIn(preserved_paid.encode(), page.data)

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
        form = self.client.get("/order")
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
        page = self.client.get("/order")
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
        other_browser.get("/order")
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
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        self.assertIn(b"Visit Website", landing.data)
        self.assertIn(b"Order Juice", landing.data)
        self.assertIn(b'href="/website"', landing.data)
        self.assertIn(b'href="/order"', landing.data)
        self.assertEqual(self.client.post("/").status_code, 405)
        self.assertIn(b"Number of portions", self.client.get("/order").data)
        self.assertIn(b"Number of portions", self.client.get("/daily").data)
        website = self.client.get("/website")
        self.assertEqual(website.status_code, 200)
        self.assertIn(b"Fresh juice.", website.data)
        self.assertIn(b"Order Daily Juice", website.data)
        self.assertEqual(self.client.get("/subscriptions").status_code, 200)
        self.staff_login()
        self.assertIn(b"PureFruit", self.client.get("/staff").data)
        self.assertEqual(self.client.get("/staff/subscriptions").status_code, 200)

    def test_staff_dashboard_hides_legacy_and_highlights_slots_portions(self):
        self.open_menu()
        self.seed_orders(1)
        with app_module.get_db() as con:
            # Older daily orders predate the customer-name/mobile fields.
            con.execute("""INSERT INTO daily_orders
                (order_ref,nonce,created_at,service_date,juice,portions,unit_price,
                 total_price,delivery_slot,building_flat,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("LEGACY-OLD-1", "legacy-nonce-1", "2026-11-22T09:00:00",
                 self.service_date, "Pineapple", 2, 99, 198,
                 "afternoon", "Legacy tower / 100", "2026-11-22T09:00:00"))
        self.staff_login()
        page = self.client.get("/staff?date=" + self.service_date)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Sample Daily Customer", page.data)
        self.assertIn(b"TEST-0", page.data)
        self.assertNotIn(b"LEGACY-OLD-1", page.data)
        self.assertNotIn(b'Legacy order', page.data)
        self.assertNotIn(b'href="/staff/subscriptions"', page.data)
        self.assertIn(b'href="/staff"', page.data)
        self.assertIn(b'class="big-slot">8', page.data)
        self.assertIn(b'class="big-portions">1</span>', page.data)
        self.assertIn(b".order-table .big-slot", page.data)
        self.assertIn(b".order-table .big-portions", page.data)
        self.assertIn("8–9 AM".encode("utf-8"), page.data)
        self.assertIn(b"Save type", page.data)
        self.assertIn(b"class=\"type-badge actual\">ACTUAL", page.data)
        csv = self.client.get("/staff/daily.csv?date=" + self.service_date)
        self.assertEqual(csv.status_code, 200)
        self.assertIn(b"TEST-0", csv.data)
        self.assertNotIn(b"LEGACY-OLD-1", csv.data)
        # Old records are preserved in the database, not deleted.
        with app_module.get_db() as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM daily_orders WHERE order_ref='LEGACY-OLD-1'"
            ).fetchone()[0], 1)

    def test_compact_staff_dashboard_has_status_colors_filters_and_pagination(self):
        self.seed_orders(27)
        with app_module.get_db() as con:
            con.execute("UPDATE daily_orders SET status='delivered' WHERE order_ref='TEST-0'")
            con.execute("""UPDATE daily_orders SET payment_status='paid',payment_method='upi',
                paid_at='2026-11-24T08:00:00' WHERE order_ref='TEST-1'""")
            con.execute("UPDATE daily_orders SET payment_status='pending_verification' WHERE order_ref='TEST-2'")
            con.execute("UPDATE daily_orders SET status='preparing' WHERE order_ref='TEST-3'")
        self.staff_login()
        response = self.client.get("/staff?date=" + self.service_date)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Showing 20 of 27 matching orders", response.data)
        self.assertIn(b"Page 1 of 2", response.data)
        self.assertIn(b"state-new", response.data)
        self.assertIn(b"chip-new", response.data)
        self.assertIn(b"chip-paid", response.data)
        self.assertIn(b"chip-delivered", response.data)
        self.assertIn(b"chip-reported", response.data)
        self.assertIn(b'<details class="box menu-box"', response.data)
        self.assertNotIn(b"Customer-wise summary", response.data)
        page2 = self.client.get("/staff?date="+self.service_date+"&page=2")
        self.assertIn(b"Showing 7 of 27 matching orders", page2.data)
        self.assertIn(b"Page 2 of 2", page2.data)
        delivered = self.client.get("/staff?date="+self.service_date+"&state=delivered")
        self.assertIn(b"Showing 1 of 1 matching orders", delivered.data)
        self.assertIn(b"TEST-0", delivered.data)
        self.assertNotIn(b"TEST-1", delivered.data)
        paid = self.client.get("/staff?date="+self.service_date+"&state=paid")
        self.assertIn(b"TEST-1", paid.data)
        reported = self.client.get("/staff?date="+self.service_date+"&state=reported")
        self.assertIn(b"TEST-2", reported.data)
        preparing = self.client.get("/staff?date="+self.service_date+"&state=preparing")
        self.assertIn(b"TEST-3", preparing.data)
        afternoon = self.client.get("/staff?date="+self.service_date+"&slot=afternoon")
        self.assertIn(b"Showing 0 of 0 matching orders", afternoon.data)

    def test_customer_history_moved_to_private_page(self):
        self.open_menu()
        self.client.post("/order", data=self.form_fields())
        self.staff_login()
        dashboard = self.client.get("/staff?date="+self.service_date)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/staff/customers"', dashboard.data)
        self.assertNotIn(b"Customer-wise summary", dashboard.data)
        page = self.client.get("/staff/customers")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Customer History", page.data)
        self.assertIn(b"Ananya Sharma", page.data)
        self.assertIn(b"9876543210", page.data)
        search = self.client.get("/staff/customers?q=9876543210")
        self.assertIn(b"Ananya Sharma", search.data)
        missing = self.client.get("/staff/customers?q=nobody")
        self.assertNotIn(b"Ananya Sharma", missing.data)
        other_browser = app.test_client()
        self.assertEqual(other_browser.get("/staff/customers").status_code, 302)

    def test_staff_modifications_require_csrf(self):
        no_login = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(no_login.status_code, 302)
        self.staff_login()
        bad = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(bad.status_code, 403)
        bad_delete = self.client.post("/staff/daily/order/TEST-0/delete", data={"confirm_ref": "TEST-0"})
        self.assertEqual(bad_delete.status_code, 403)
        bad_type = self.client.post("/staff/daily/order/TEST-0/type", data={"order_type": "test"})
        self.assertEqual(bad_type.status_code, 403)


if __name__ == "__main__":
    unittest.main()
