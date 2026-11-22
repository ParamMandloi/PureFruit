"""Daily checkout smoke tests. Run: python -m unittest discover -s tests"""
import importlib
import os
import re
import tempfile
import unittest
from datetime import timedelta

TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(TEMP_DIR.name, "orders.sqlite")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ADMIN_PASSWORD"] = "test-staff-password"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)

app_module = importlib.import_module("app")
app = app_module.app


class DailyOrdersTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        with app_module.get_db() as con:
            con.execute("DELETE FROM daily_orders")
            con.execute(
                "UPDATE daily_menu SET juice='Pineapple',unit_price=99,is_open=0,service_date=? WHERE id=1",
                ((app_module.now_ist().date() + timedelta(days=1 if app_module.now_ist().date().weekday() < 4 else 7-app_module.now_ist().date().weekday())).isoformat(),))
        # If the calculated next date happens to fall on a weekend, fix it.
        with app_module.get_db() as con:
            current = con.execute("SELECT service_date FROM daily_menu WHERE id=1").fetchone()[0]
            from datetime import date
            d = date.fromisoformat(current)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            con.execute("UPDATE daily_menu SET service_date=? WHERE id=1", (d.isoformat(),))

    def form_fields(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as s:
            return {"csrf_token": s["csrf_token"], "nonce": s["daily_nonce"]}

    def test_closed_form_cannot_submit(self):
        data = self.form_fields()
        data.update(portions="2", delivery_slot="morning", building_flat="B / 1204")
        response = self.client.post("/", data=data)
        self.assertEqual(response.status_code, 400)

    def test_place_order_without_phone_or_name_and_no_duplicate(self):
        with app_module.get_db() as con:
            con.execute("UPDATE daily_menu SET is_open=1 WHERE id=1")
        data = self.form_fields()
        data.update(portions="3", delivery_slot="afternoon", building_flat="Tower B / 1204")
        response = self.client.post("/", data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/daily/thanks/", response.headers["Location"])
        with app_module.get_db() as con:
            orders = con.execute("SELECT * FROM daily_orders").fetchall()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["portions"], 3)
        self.assertEqual(orders[0]["total_price"], 297)
        self.assertEqual(orders[0]["payment_status"], "unpaid")
        self.assertEqual(orders[0]["building_flat"], "Tower B / 1204")
        # A retry with the same nonce must not create a second record.
        retry = self.client.post("/daily", data=data, follow_redirects=False)
        self.assertEqual(retry.status_code, 303)
        with app_module.get_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_orders").fetchone()[0], 1)
        thanks = self.client.get(response.headers["Location"])
        self.assertIn(b"Order request saved", thanks.data)
        self.assertIn(b"not yet verified", thanks.data)

    def test_main_routes_use_daily_checkout(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Portions", response.data)
        self.assertIn(b"Building / Flat", response.data)
        legacy = self.client.get("/subscriptions")
        self.assertEqual(legacy.status_code, 200)

    def test_staff_home_is_daily_dashboard(self):
        with self.client.session_transaction() as sess:
            sess["staff_logged_in"] = True
        dashboard = self.client.get("/staff")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Daily Orders", dashboard.data)
        subscriptions = self.client.get("/staff/subscriptions")
        self.assertEqual(subscriptions.status_code, 200)

    def test_staff_menu_requires_login_and_csrf(self):
        response = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.headers["Location"])
        with self.client.session_transaction() as session:
            session["staff_logged_in"] = True
            session["csrf_token"] = "valid-test-csrf"
        response = self.client.post("/staff/daily/menu", data={"juice": "ABC"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
