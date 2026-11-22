"""Daily one-screen ordering, isolated from existing subscription/payment tables."""
import io
import re
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

import qrcode
from flask import abort, flash, redirect, render_template, request, session, url_for, send_file

from telegram_alerts import send_daily_order_alert


JUICES = {
    "ABC": "fruit/abc.webp",
    "Pineapple": "fruit/pineapple.webp",
    "Orange": "fruit/orange.webp",
    "Mosambi": "fruit/mosambi.webp",
    "Watermelon": "fruit/watermelon.webp",
}
SLOTS = {"morning": "8–9:30 AM", "afternoon": "4–5:30 PM"}
STATUSES = ("new", "preparing", "delivered", "cancelled")


def register_daily_orders(app, get_db, now_ist, csrf_token, require_csrf,
                          staff_only, csv_response, upi_payee_address, upi_payee_name):
    """Register separate daily-order routes; retain legacy subscription behavior."""

    def next_weekday(day):
        while day.weekday() > 4:
            day += timedelta(days=1)
        return day

    with get_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS daily_menu (
            id INTEGER PRIMARY KEY CHECK(id=1),
            service_date TEXT NOT NULL,
            juice TEXT NOT NULL,
            unit_price INTEGER NOT NULL,
            is_open INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT NOT NULL UNIQUE,
            nonce TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            service_date TEXT NOT NULL,
            juice TEXT NOT NULL,
            portions INTEGER NOT NULL CHECK(portions BETWEEN 1 AND 30),
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            delivery_slot TEXT NOT NULL,
            building_flat TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            payment_status TEXT NOT NULL DEFAULT 'unpaid',
            payment_method TEXT NOT NULL DEFAULT '',
            paid_at TEXT,
            updated_at TEXT NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS daily_orders_date_idx ON daily_orders(service_date)")
        con.execute("""INSERT OR IGNORE INTO daily_menu
            (id,service_date,juice,unit_price,is_open,updated_at)
            VALUES (1,?,?,?,?,?)""", (
                next_weekday(now_ist().date()).isoformat(), "Pineapple", 99, 0,
                now_ist().isoformat(timespec="seconds")))

    def menu():
        with get_db() as con:
            return dict(con.execute("SELECT * FROM daily_menu WHERE id=1").fetchone())

    def order_by_ref(ref):
        with get_db() as con:
            row = con.execute("SELECT * FROM daily_orders WHERE order_ref=?", (ref,)).fetchone()
        return dict(row) if row else None

    def allowed_slots(current_menu):
        if not current_menu["is_open"]:
            return {}
        try:
            target = date.fromisoformat(current_menu["service_date"])
        except ValueError:
            return {}
        now = now_ist()
        if target < now.date() or target.weekday() > 4:
            return {}
        if target > now.date():
            return SLOTS
        # Avoid taking orders for a slot after prep begins.
        return {
            key: label for key, label in SLOTS.items()
            if now.time() < (time(7, 0) if key == "morning" else time(15, 0))
        }

    def render_form(current_menu, error=None, status=200):
        slots = allowed_slots(current_menu)
        nonce = secrets.token_hex(16)
        session["daily_nonce"] = nonce
        session["daily_menu_version"] = current_menu["updated_at"]
        response = app.make_response((render_template(
            "daily.html", csrf=csrf_token(), nonce=nonce, menu=current_menu,
            image=JUICES.get(current_menu["juice"], ""), slots=slots, error=error
        ), status))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/daily", methods=["GET", "POST"])
    @app.route("/", methods=["GET", "POST"])
    def daily_order_page():
        current_menu = menu()
        if request.method == "GET":
            return render_form(current_menu)
        require_csrf()
        if request.form.get("website"):
            abort(400)
        nonce = request.form.get("nonce", "")
        if session.get("daily_menu_version") != current_menu["updated_at"]:
            return render_form(current_menu, "Today’s menu changed. Please check the juice and resubmit.", 409)
        if not re.fullmatch(r"[0-9a-f]{32}", nonce) or nonce != session.get("daily_nonce"):
            abort(403)
        # Idempotency: refreshing / double-tapping cannot create a second order.
        with get_db() as con:
            previous = con.execute("SELECT order_ref FROM daily_orders WHERE nonce=?", (nonce,)).fetchone()
        if previous:
            return redirect(url_for("daily_confirmation", order_ref=previous["order_ref"]), code=303)
        slots = allowed_slots(current_menu)
        raw_qty = request.form.get("portions", "")
        location = " ".join(request.form.get("building_flat", "").split())
        slot = request.form.get("delivery_slot", "")
        if (not re.fullmatch(r"[1-9][0-9]?", raw_qty) or
                not 1 <= int(raw_qty) <= 30 or
                not 3 <= len(location) <= 80 or slot not in slots):
            return render_form(current_menu, "Check portions, delivery slot and building / flat.", 400)
        qty = int(raw_qty)
        stamp = now_ist().isoformat(timespec="seconds")
        order = dict(
            order_ref="PFD-" + now_ist().strftime("%y%m%d") + "-" + secrets.token_hex(6).upper(),
            nonce=nonce, created_at=stamp,
            service_date=current_menu["service_date"], juice=current_menu["juice"],
            portions=qty, unit_price=current_menu["unit_price"],
            total_price=qty * current_menu["unit_price"],
            delivery_slot=slot, building_flat=location, updated_at=stamp)
        try:
            with get_db() as con:
                con.execute("""INSERT INTO daily_orders
                    (order_ref,nonce,created_at,service_date,juice,portions,unit_price,
                     total_price,delivery_slot,building_flat,updated_at)
                    VALUES (:order_ref,:nonce,:created_at,:service_date,:juice,:portions,:unit_price,
                            :total_price,:delivery_slot,:building_flat,:updated_at)""", order)
        except sqlite3.IntegrityError:
            with get_db() as con:
                previous = con.execute("SELECT order_ref FROM daily_orders WHERE nonce=?", (nonce,)).fetchone()
            if not previous:
                raise
            return redirect(url_for("daily_confirmation", order_ref=previous["order_ref"]), code=303)
        send_daily_order_alert(order)
        return redirect(url_for("daily_confirmation", order_ref=order["order_ref"]), code=303)

    @app.get("/daily/thanks/<order_ref>")
    def daily_confirmation(order_ref):
        order = order_by_ref(order_ref)
        if not order:
            abort(404)
        params = {
            "pa": upi_payee_address, "pn": upi_payee_name,
            "am": f'{order["total_price"]:.2f}', "cu": "INR",
            "tr": order_ref, "tn": "PureFruit " + order_ref
        }
        response = app.make_response(render_template(
            "daily_thanks.html", order=order, slots=SLOTS,
            upi_link="upi://pay?" + urlencode(params)
        ))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/daily/qr/<order_ref>.png")
    def daily_payment_qr(order_ref):
        order = order_by_ref(order_ref)
        if not order or order["status"] == "cancelled":
            abort(404)
        params = {
            "pa": upi_payee_address, "pn": upi_payee_name,
            "am": f'{order["total_price"]:.2f}', "cu": "INR",
            "tr": order_ref, "tn": "PureFruit " + order_ref
        }
        uri = "upi://pay?" + urlencode(params)
        qr = qrcode.QRCode(box_size=7, border=3)
        qr.add_data(uri)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        buf.seek(0)
        response = send_file(buf, mimetype="image/png")
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/staff/daily")
    @app.get("/staff")
    @staff_only
    def daily_admin():
        current_menu = menu()
        selected = request.args.get("date", current_menu["service_date"])
        try:
            selected = date.fromisoformat(selected).isoformat()
        except ValueError:
            selected = current_menu["service_date"]
        with get_db() as con:
            rows = [dict(r) for r in con.execute(
                """SELECT * FROM daily_orders WHERE service_date=?
                   ORDER BY CASE delivery_slot WHEN 'morning' THEN 0 ELSE 1 END,
                            building_flat COLLATE NOCASE, id""", (selected,))]
        valid = [r for r in rows if r["status"] != "cancelled"]
        stats = {
            "orders": len(valid),
            "portions": sum(r["portions"] for r in valid),
            "value": sum(r["total_price"] for r in valid),
            "unpaid": sum(r["payment_status"] != "paid" for r in valid),
            "morning": sum(r["portions"] for r in valid if r["delivery_slot"] == "morning"),
            "afternoon": sum(r["portions"] for r in valid if r["delivery_slot"] == "afternoon"),
        }
        return render_template("daily_admin.html", csrf=csrf_token(),
                               menu=current_menu, juices=JUICES, slots=SLOTS,
                               statuses=STATUSES, rows=rows, stats=stats,
                               selected=selected, today=now_ist().date().isoformat(),
                               public_url=url_for("daily_order_page", _external=True))

    @app.post("/staff/daily/menu")
    @staff_only
    def daily_update_menu():
        require_csrf()
        juice = request.form.get("juice", "")
        try:
            unit_price = int(request.form.get("unit_price", "0"))
            service_date = date.fromisoformat(request.form.get("service_date", ""))
        except (TypeError, ValueError):
            abort(400)
        if (juice not in JUICES or not 50 <= unit_price <= 500 or
                not now_ist().date() <= service_date <= now_ist().date()+timedelta(days=30) or
                service_date.weekday() > 4):
            abort(400)
        with get_db() as con:
            con.execute("""UPDATE daily_menu SET service_date=?, juice=?, unit_price=?,
                           is_open=?, updated_at=? WHERE id=1""", (
                               service_date.isoformat(), juice, unit_price,
                               1 if request.form.get("is_open") == "yes" else 0,
                               now_ist().isoformat(timespec="seconds")))
        flash("Daily menu updated. Existing orders retain their original juice and price.", "success")
        return redirect(url_for("daily_admin"))

    @app.post("/staff/daily/order/<order_ref>/status")
    @staff_only
    def daily_update_status(order_ref):
        require_csrf()
        status = request.form.get("status", "")
        if status not in STATUSES:
            abort(400)
        with get_db() as con:
            cur = con.execute("UPDATE daily_orders SET status=?,updated_at=? WHERE order_ref=?",
                              (status, now_ist().isoformat(timespec="seconds"), order_ref))
        if not cur.rowcount:
            abort(404)
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.post("/staff/daily/order/<order_ref>/paid")
    @staff_only
    def daily_mark_paid(order_ref):
        require_csrf()
        method = request.form.get("payment_method", "")
        if method not in ("cash", "upi") or request.form.get("verified") != "yes":
            abort(400, description="Verify receipt of cash or bank credit before marking paid.")
        with get_db() as con:
            cur = con.execute("""UPDATE daily_orders SET payment_status='paid',payment_method=?,
                paid_at=?,updated_at=? WHERE order_ref=? AND payment_status='unpaid'""",
                (method, now_ist().isoformat(timespec="seconds"),
                 now_ist().isoformat(timespec="seconds"), order_ref))
        if not cur.rowcount and not order_by_ref(order_ref):
            abort(404)
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.get("/staff/daily.csv")
    @staff_only
    def daily_export():
        selected = request.args.get("date", menu()["service_date"])
        try:
            selected = date.fromisoformat(selected).isoformat()
        except ValueError:
            abort(400)
        with get_db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM daily_orders WHERE service_date=? ORDER BY delivery_slot,building_flat",
                (selected,))]
        fields = [(n, label) for n, label in (
            ("order_ref", "Reference"), ("created_at", "Order time IST"),
            ("service_date", "Delivery date"), ("juice", "Juice"),
            ("portions", "Portions"), ("unit_price", "Unit price INR"),
            ("total_price", "Total INR"), ("delivery_slot", "Slot"),
            ("building_flat", "Building / Flat"), ("status", "Fulfillment"),
            ("payment_status", "Payment"), ("payment_method", "Payment method"))]
        return csv_response(rows, "purefruit-daily-" + selected + ".csv", fields)
