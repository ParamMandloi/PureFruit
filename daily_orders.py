"""Daily PureFruit orders, capacity, returning customers and staff management.

This feature owns separate tables. Historical subscription records are not touched.
"""
import hashlib
import io
import re
import secrets
import sqlite3
from datetime import date, time, timedelta
from urllib.parse import urlencode

import qrcode
from flask import abort, flash, redirect, render_template, request, session, url_for, send_file

from telegram_alerts import send_daily_order_alert, send_daily_payment_report

JUICES = {
    "ABC": "fruit/abc.webp",
    "Pineapple": "fruit/pineapple.webp",
    "Orange": "fruit/orange.webp",
    "Mosambi": "fruit/mosambi.webp",
    "Watermelon": "fruit/watermelon.webp",
}
SLOTS = {"morning": "8–9 AM", "afternoon": "4–5 PM"}
STATUSES = ("new", "preparing", "delivered", "cancelled")
MAX_DAILY_ORDERS = 1000
COOKIE_NAME = "purefruit_daily_device"
COOKIE_AGE = 180 * 24 * 3600


def build_order_qr_uri(payee_address, payee_name, order):
    """Generate a UPI QR with the exact amount but no unverified receipt claim."""
    params = {
        "pa": payee_address, "pn": payee_name,
        "am": f'{order["total_price"]:.2f}',
        "cu": "INR", "tn": "PureFruit " + order["order_ref"],
    }
    return "upi://pay?" + urlencode(params)

def build_phonepe_payment_uri(payee_address, payee_name, order):
    """Simple UPI pay-to-VPA intent with amount prefilled for PhonePe.

    Unlike the prior merchant-style link, omit merchant transaction reference,
    custom mode and MCC fields for this P2P-style HUF payee. Banks or PhonePe
    may still decline a transaction; manual pay-to-mobile remains the fallback.
    """
    params = {
        "pa": payee_address,
        "pn": payee_name,
        "am": f'{order["total_price"]:.2f}',
        "cu": "INR",
        "tn": "PureFruit juice",
    }
    return "phonepe://pay?" + urlencode(params)


def slots_for_date(target, now):
    """Pure IST date/time rule; exactly 09:00 closes morning and 17:00 closes evening."""
    if target.weekday() >= 5 or target < now.date():
        return ()
    if target > now.date():
        return ("morning", "afternoon")
    if now.time() < time(9, 0):
        return ("morning", "afternoon")
    if now.time() < time(17, 0):
        return ("afternoon",)
    return ()




def register_daily_orders(app, get_db, now_ist, csrf_token, require_csrf,
                          staff_only, csv_response, upi_payee_address, upi_payee_name):
    """Register daily routes without changing the existing subscriptions."""

    def next_weekday(day):
        while day.weekday() > 4:
            day += timedelta(days=1)
        return day

    def service_days():
        now = now_ist()
        today = now.date()
        future = next_weekday(today + timedelta(days=1))
        return [today, future] if today.weekday() < 5 else [future]

    with get_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS daily_menu (
            id INTEGER PRIMARY KEY CHECK(id=1),
            service_date TEXT NOT NULL, juice TEXT NOT NULL,
            unit_price INTEGER NOT NULL, is_open INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT NOT NULL UNIQUE, nonce TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, service_date TEXT NOT NULL,
            juice TEXT NOT NULL, portions INTEGER NOT NULL CHECK(portions BETWEEN 1 AND 10),
            unit_price INTEGER NOT NULL, total_price INTEGER NOT NULL,
            delivery_slot TEXT NOT NULL, building_flat TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            payment_status TEXT NOT NULL DEFAULT 'unpaid',
            payment_method TEXT NOT NULL DEFAULT '', paid_at TEXT,
            updated_at TEXT NOT NULL
        )""")
        columns = {c[1] for c in con.execute("PRAGMA table_info(daily_orders)")}
        for col, specification in (
            ("name", "TEXT NOT NULL DEFAULT ''"),
            ("phone", "TEXT NOT NULL DEFAULT ''"),
            ("customer_id", "INTEGER"),
            ("deleted_at", "TEXT"),
            ("deleted_reason", "TEXT"),
            ("payment_reported_at", "TEXT"),
            ("archived_at", "TEXT"),
            ("archived_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in columns:
                con.execute(f"ALTER TABLE daily_orders ADD COLUMN {col} {specification}")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_menus (
            service_date TEXT PRIMARY KEY, juice TEXT NOT NULL,
            unit_price INTEGER NOT NULL, is_open INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""")
        # Non-destructive one-time migration of the original one-day menu.
        con.execute("""INSERT OR IGNORE INTO daily_menus
            (service_date, juice, unit_price, is_open, updated_at)
            SELECT service_date, juice, unit_price, is_open, updated_at
            FROM daily_menu WHERE id=1""")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_customer_devices (
            device_hash TEXT PRIMARY KEY, customer_id INTEGER NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES daily_customers(id)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS daily_orders_date_idx ON daily_orders(service_date)")
        con.execute("CREATE INDEX IF NOT EXISTS daily_orders_customer_idx ON daily_orders(customer_id)")
        con.execute("CREATE INDEX IF NOT EXISTS daily_orders_phone_idx ON daily_orders(phone)")
        con.execute("CREATE INDEX IF NOT EXISTS daily_orders_archive_idx ON daily_orders(archived_at, service_date)")

    def menus_for_dates(days=None):
        dates = days or service_days()
        placeholders = ",".join("?" for _ in dates)
        with get_db() as con:
            rows = con.execute(
                "SELECT * FROM daily_menus WHERE service_date IN (" + placeholders + ")",
                [d.isoformat() for d in dates]).fetchall()
        return {r["service_date"]: dict(r) for r in rows}

    def order_by_ref(ref):
        with get_db() as con:
            row = con.execute("SELECT * FROM daily_orders WHERE order_ref=?", (ref,)).fetchone()
        return dict(row) if row else None

    def browser_customer():
        raw = request.cookies.get(COOKIE_NAME, "")
        if not re.fullmatch(r"[0-9a-f]{64}", raw):
            return None
        hashed = hashlib.sha256(raw.encode("ascii")).hexdigest()
        with get_db() as con:
            row = con.execute(
                """SELECT c.id, c.name, c.phone
                   FROM daily_customer_devices d JOIN daily_customers c ON c.id=d.customer_id
                   WHERE d.device_hash=?""", (hashed,)).fetchone()
        return dict(row) if row else None

    def choices():
        """Return selectable slots based on IST cutoffs, scheduled menus and cap."""
        now = now_ist()
        days = service_days()
        menus = menus_for_dates(days)
        with get_db() as con:
            counts = {
                r["service_date"]: r["n"] for r in con.execute(
                    """SELECT service_date, COUNT(*) n FROM daily_orders
                       WHERE deleted_at IS NULL AND status!='cancelled'
                       GROUP BY service_date"""
                ).fetchall()
            }
        options = []
        for day in days:
            key = day.isoformat()
            scheduled = menus.get(key)
            if not scheduled or not scheduled["is_open"] or counts.get(key, 0) >= MAX_DAILY_ORDERS:
                continue
            enabled = slots_for_date(day, now)
            heading = "Today" if day == now.date() else day.strftime("%a, %d %b")
            for slot in enabled:
                options.append({
                    "value": key + "|" + slot, "service_date": key,
                    "slot": slot, "slot_label": SLOTS[slot],
                    "heading": heading, "juice": scheduled["juice"],
                    "unit_price": scheduled["unit_price"],
                    "image": JUICES.get(scheduled["juice"], ""),
                    "version": scheduled["updated_at"],
                })
        return options

    def display_menu(options):
        if options:
            return dict(options[0])
        with get_db() as con:
            row = con.execute(
                "SELECT * FROM daily_menus WHERE service_date>=? ORDER BY service_date LIMIT 1",
                (now_ist().date().isoformat(),)).fetchone()
        return dict(row) if row else {
            "juice": "Fresh", "unit_price": 99, "service_date": "", "image": ""
        }

    def render_form(error=None, status=200):
        options = choices()
        selected = display_menu(options)
        customer = browser_customer()
        nonce = secrets.token_hex(16)
        session["daily_nonce"] = nonce
        session["daily_versions"] = {option["service_date"]: option["version"] for option in options}
        response = app.make_response((render_template(
            "daily.html", csrf=csrf_token(), nonce=nonce,
            options=options, menu=selected, customer=customer,
            image=JUICES.get(selected["juice"], ""), error=error,
            limit=MAX_DAILY_ORDERS
        ), status))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.route("/daily", methods=["GET", "POST"])
    @app.route("/", methods=["GET", "POST"])
    def daily_order_page():
        if request.method == "GET":
            return render_form()
        require_csrf()
        if request.form.get("website"):
            abort(400)
        nonce = request.form.get("nonce", "")
        if not re.fullmatch(r"[0-9a-f]{32}", nonce) or nonce != session.get("daily_nonce"):
            abort(403)
        # Idempotent even if two POSTs race against each other.
        with get_db() as con:
            old = con.execute("SELECT order_ref FROM daily_orders WHERE nonce=?", (nonce,)).fetchone()
        if old:
            return redirect(url_for("daily_confirmation", order_ref=old["order_ref"]), code=303)

        selected = request.form.get("delivery_option", "")
        opt = next((item for item in choices() if item["value"] == selected), None)
        if opt is None or session.get("daily_versions", {}).get(opt["service_date"]) != opt["version"]:
            return render_form("That slot or menu has changed. Please select an available option.", 409)
        raw_qty = request.form.get("portions", "")
        location = " ".join(request.form.get("building_flat", "").split())[:81]
        name = " ".join(request.form.get("customer_name", "").split())[:71]
        phone = re.sub(r"\D", "", request.form.get("customer_mobile", ""))
        if len(phone) == 12 and phone.startswith("91"):
            phone = phone[2:]
        if (not re.fullmatch(r"[1-9][0-9]?", raw_qty) or
                not 1 <= int(raw_qty) <= 10 or not 3 <= len(location) <= 80 or
                not 2 <= len(name) <= 70 or not re.fullmatch(r"[6-9][0-9]{9}", phone)):
            return render_form("Enter valid portions, name, 10-digit mobile, and building / flat.", 400)

        qty = int(raw_qty)
        stamp = now_ist().isoformat(timespec="seconds")
        ref = "PFD-" + now_ist().strftime("%y%m%d") + "-" + secrets.token_hex(6).upper()
        raw_device = request.cookies.get(COOKIE_NAME, "")
        if not re.fullmatch(r"[0-9a-f]{64}", raw_device):
            raw_device = secrets.token_hex(32)
        fingerprint = hashlib.sha256(raw_device.encode("ascii")).hexdigest()

        try:
            with get_db() as con:
                con.execute("BEGIN IMMEDIATE")
                old = con.execute("SELECT order_ref FROM daily_orders WHERE nonce=?", (nonce,)).fetchone()
                if old:
                    return redirect(url_for("daily_confirmation", order_ref=old["order_ref"]), code=303)
                live = con.execute(
                    "SELECT * FROM daily_menus WHERE service_date=?", (opt["service_date"],)
                ).fetchone()
                if (not live or not live["is_open"] or live["updated_at"] != opt["version"] or
                        not any(item["value"] == selected for item in choices_for_time(live))):
                    return render_form("The menu or time slot has changed. Please refresh.", 409)
                occupied = con.execute(
                    """SELECT COUNT(*) FROM daily_orders
                       WHERE service_date=? AND deleted_at IS NULL AND status!='cancelled'""",
                    (opt["service_date"],)).fetchone()[0]
                if occupied >= MAX_DAILY_ORDERS:
                    return render_form("This delivery date is fully booked. Choose the next available day.", 409)

                con.execute(
                    """INSERT INTO daily_customers(phone,name,created_at,last_seen_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(phone) DO UPDATE SET name=excluded.name,last_seen_at=excluded.last_seen_at""",
                    (phone, name, stamp, stamp))
                customer_id = con.execute(
                    "SELECT id FROM daily_customers WHERE phone=?", (phone,)
                ).fetchone()[0]
                con.execute("""INSERT INTO daily_customer_devices(device_hash,customer_id,last_seen_at)
                    VALUES (?,?,?)
                    ON CONFLICT(device_hash) DO UPDATE SET
                    customer_id=excluded.customer_id,last_seen_at=excluded.last_seen_at""",
                    (fingerprint, customer_id, stamp))
                order = {
                    "order_ref": ref, "nonce": nonce, "created_at": stamp,
                    "service_date": opt["service_date"], "juice": live["juice"],
                    "portions": qty, "unit_price": live["unit_price"],
                    "total_price": qty * live["unit_price"], "delivery_slot": opt["slot"],
                    "building_flat": location, "name": name, "phone": phone,
                    "customer_id": customer_id, "updated_at": stamp,
                }
                con.execute("""INSERT INTO daily_orders (
                    order_ref, nonce, created_at, service_date, juice, portions, unit_price,
                    total_price, delivery_slot, building_flat, name, phone, customer_id, updated_at
                ) VALUES (
                    :order_ref, :nonce, :created_at, :service_date, :juice, :portions, :unit_price,
                    :total_price, :delivery_slot, :building_flat, :name, :phone, :customer_id, :updated_at
                )""", order)
        except sqlite3.IntegrityError:
            with get_db() as con:
                old = con.execute("SELECT order_ref FROM daily_orders WHERE nonce=?", (nonce,)).fetchone()
            if not old:
                raise
            ref = old["order_ref"]
        else:
            send_daily_order_alert(order)
        response = redirect(url_for("daily_confirmation", order_ref=ref), code=303)
        response.set_cookie(
            COOKIE_NAME, raw_device, max_age=COOKIE_AGE, secure=app.config["SESSION_COOKIE_SECURE"],
            httponly=True, samesite="Lax", path="/"
        )
        return response

    def choices_for_time(scheduled):
        """Validate the instant of submission; use while holding a write transaction."""
        day = date.fromisoformat(scheduled["service_date"])
        now = now_ist()
        if day.weekday() > 4 or day not in service_days():
            return []
        valid = slots_for_date(day, now)
        return [{"value": day.isoformat() + "|" + slot} for slot in valid]

    @app.post("/daily/forget")
    def forget_daily_customer():
        require_csrf()
        raw = request.cookies.get(COOKIE_NAME, "")
        if re.fullmatch(r"[0-9a-f]{64}", raw):
            hashed = hashlib.sha256(raw.encode("ascii")).hexdigest()
            with get_db() as con:
                con.execute("DELETE FROM daily_customer_devices WHERE device_hash=?", (hashed,))
        response = redirect(url_for("daily_order_page"), code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/daily/thanks/<order_ref>")
    def daily_confirmation(order_ref):
        order = order_by_ref(order_ref)
        if not order or order.get("deleted_at"):
            abort(404)
        # PhonePe gets an amount-prefilled, minimal P2P-style UPI link.
        # The successful pay-to-mobile route stays available if the bank
        # rejects payment to this HUF VPA through a web-initiated intent.
        response = app.make_response(render_template(
            "daily_thanks.html", order=order, slots=SLOTS,
            csrf=csrf_token(), payment_address=upi_payee_address,
            pay_mobile_number="9619140077",
        ))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/daily/thanks/<order_ref>/payment-made")
    def daily_report_payment(order_ref):
        """Record customer-reported payment, never bank-verified payment.

        Order delivery is already accepted regardless of payment state.
        """
        require_csrf()
        order = order_by_ref(order_ref)
        if not order or order.get("deleted_at") or order["status"] == "cancelled":
            abort(404)
        customer = browser_customer()
        if not customer or customer["id"] != order.get("customer_id"):
            abort(403, description="Open the payment page on the same device used to place the order.")
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            changed = con.execute(
                """UPDATE daily_orders SET payment_status='pending_verification',
                   payment_reported_at=?,updated_at=?
                   WHERE order_ref=? AND payment_status='unpaid'
                   AND deleted_at IS NULL AND status!='cancelled'""",
                (stamp, stamp, order_ref),
            ).rowcount
        if changed:
            send_daily_payment_report(order)
            flash("Payment reported. Thank you! We'll verify it. Delivery is already confirmed.", "success")
        return redirect(url_for("daily_confirmation", order_ref=order_ref), code=303)

    @app.get("/daily/qr/<order_ref>.png")
    def daily_payment_qr(order_ref):
        order = order_by_ref(order_ref)
        if not order or order.get("deleted_at") or order["status"] == "cancelled":
            abort(404)
        # Amount-prefilled QR. Receiver remains the selected HUF UPI VPA.
        # Authorization is ultimately the payer app/bank's decision.
        qr = qrcode.QRCode(box_size=7, border=3)
        qr.add_data(build_order_qr_uri(upi_payee_address, upi_payee_name, order))
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
        today = now_ist().date()
        default_day = today if today.weekday() < 5 and now_ist().time() < time(17, 0) else next_weekday(today + timedelta(days=1))
        selected = request.args.get("date", default_day.isoformat())
        try:
            selected = date.fromisoformat(selected).isoformat()
        except ValueError:
            selected = default_day.isoformat()
        view = request.args.get("view", "active")
        if view not in ("active", "archived"):
            view = "active"
        with get_db() as con:
            if view == "archived":
                rows = [dict(r) for r in con.execute(
                    """SELECT * FROM daily_orders WHERE archived_at IS NOT NULL
                       AND deleted_at IS NULL
                       AND TRIM(name)!='' AND TRIM(phone)!=''
                       ORDER BY service_date DESC, id DESC LIMIT 300""")]
            else:
                rows = [dict(r) for r in con.execute(
                    """SELECT * FROM daily_orders WHERE service_date=? AND deleted_at IS NULL
                       AND archived_at IS NULL AND TRIM(name)!='' AND TRIM(phone)!=''
                       ORDER BY CASE delivery_slot WHEN 'morning' THEN 0 ELSE 1 END,
                       building_flat COLLATE NOCASE, id""", (selected,))]
            archived_count = con.execute(
                """SELECT COUNT(*) FROM daily_orders WHERE archived_at IS NOT NULL
                   AND deleted_at IS NULL AND TRIM(name)!='' AND TRIM(phone)!=''"""
            ).fetchone()[0]
            customer_rows = [dict(r) for r in con.execute("""
                SELECT c.name, c.phone,
                    COUNT(o.id) AS orders, COALESCE(SUM(o.portions),0) AS portions,
                    COALESCE(SUM(o.total_price),0) AS total_value,
                    MAX(o.service_date) AS last_date
                FROM daily_customers c LEFT JOIN daily_orders o
                    ON o.customer_id=c.id AND o.deleted_at IS NULL
                      AND o.status!='cancelled'
                GROUP BY c.id ORDER BY last_date DESC, orders DESC LIMIT 100
            """)]
            upcoming = [dict(r) for r in con.execute(
                "SELECT * FROM daily_menus WHERE service_date>=? ORDER BY service_date LIMIT 8",
                (today.isoformat(),))]
        valid = [r for r in rows if r["status"] != "cancelled"]
        stats = {
            "orders": len(valid),
            "remaining": max(0, MAX_DAILY_ORDERS-len(valid)),
            "portions": sum(r["portions"] for r in valid),
            "value": sum(r["total_price"] for r in valid),
            "unpaid": sum(r["payment_status"] != "paid" for r in valid),
            "reported": sum(r["payment_status"] == "pending_verification" for r in valid),
            "morning": sum(r["portions"] for r in valid if r["delivery_slot"] == "morning"),
            "afternoon": sum(r["portions"] for r in valid if r["delivery_slot"] == "afternoon"),
        }
        editor_day = request.args.get("edit_date", selected)
        try:
            editor_day = date.fromisoformat(editor_day).isoformat()
        except ValueError:
            editor_day = selected
        menus = menus_for_dates([date.fromisoformat(editor_day)])
        menu = menus.get(editor_day) or {
            "service_date": editor_day, "juice": "Pineapple", "unit_price": 99, "is_open": 0
        }
        return render_template(
            "daily_admin.html", csrf=csrf_token(), menu=menu, juices=JUICES,
            slots=SLOTS, statuses=STATUSES, rows=rows, customers=customer_rows,
            stats=stats, selected=selected, today=today.isoformat(), upcoming=upcoming,
            view=view, archived_count=archived_count,
            limit=MAX_DAILY_ORDERS, public_url=url_for("daily_order_page", _external=True)
        )

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
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            con.execute("""INSERT INTO daily_menus(service_date,juice,unit_price,is_open,updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(service_date) DO UPDATE SET
                juice=excluded.juice,unit_price=excluded.unit_price,
                is_open=excluded.is_open,updated_at=excluded.updated_at""", (
                    service_date.isoformat(), juice, unit_price,
                    1 if request.form.get("is_open") == "yes" else 0, stamp))
        flash("Menu saved. Existing orders retain the original fruit and price.", "success")
        return redirect(url_for("daily_admin", date=service_date.isoformat(),
                                edit_date=service_date.isoformat()))

    @app.post("/staff/daily/order/<order_ref>/status")
    @staff_only
    def daily_update_status(order_ref):
        require_csrf()
        new_status = request.form.get("status", "")
        if new_status not in STATUSES:
            abort(400)
        with get_db() as con:
            cur = con.execute(
                """UPDATE daily_orders SET status=?,updated_at=? WHERE order_ref=?
                   AND deleted_at IS NULL""",
                (new_status, now_ist().isoformat(timespec="seconds"), order_ref))
        if not cur.rowcount:
            abort(404)
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.post("/staff/daily/order/<order_ref>/paid")
    @staff_only
    def daily_mark_paid(order_ref):
        require_csrf()
        method = request.form.get("payment_method", "")
        if method not in ("cash", "upi") or request.form.get("verified") != "yes":
            abort(400, description="Verify bank credit or cash receipt before marking paid.")
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            cur = con.execute(
                """UPDATE daily_orders SET payment_status='paid',payment_method=?,
                   paid_at=?,updated_at=? WHERE order_ref=? AND payment_status IN ('unpaid','pending_verification')
                   AND deleted_at IS NULL AND status!='cancelled'""",
                (method, stamp, stamp, order_ref))
        if not cur.rowcount and not order_by_ref(order_ref):
            abort(404)
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.post("/staff/daily/order/<order_ref>/delete")
    @staff_only
    def daily_delete_order(order_ref):
        """Soft delete only an unpaid, unfulfilled order; preserve financial audit."""
        require_csrf()
        if request.form.get("confirm_ref") != order_ref:
            abort(400, description="Order reference confirmation is required.")
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM daily_orders WHERE order_ref=?", (order_ref,)
            ).fetchone()
            if not existing:
                abort(404)
            if (existing["payment_status"] != "unpaid" or existing["paid_at"] or
                    existing["deleted_at"] or existing["status"] != "new"):
                flash("Only new, unpaid, not-yet-deleted orders can be deleted. Paid and prepared orders are protected.", "error")
                return redirect(url_for("daily_admin", date=request.form.get("date", "")))
            con.execute(
                """UPDATE daily_orders SET deleted_at=?,deleted_reason='Staff removed request',
                   status='cancelled',updated_at=? WHERE order_ref=?""",
                (stamp, stamp, order_ref))
        flash("Order deleted from active list. An audit record is retained.", "success")
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.post("/staff/daily/archive-old")
    @staff_only
    def daily_archive_old():
        """Clear older orders from the active list, retaining paid/transaction records."""
        require_csrf()
        if request.form.get("confirm_archive") != "yes":
            abort(400, description="Explicit confirmation is required.")
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            result = con.execute(
                """UPDATE daily_orders
                   SET archived_at=?, archived_reason='Past delivery date',
                       updated_at=?
                   WHERE service_date<? AND archived_at IS NULL AND deleted_at IS NULL""",
                (stamp, stamp, now_ist().date().isoformat()),
            )
        flash(f"{result.rowcount} past order(s) cleared from active lists. All records were retained.", "success")
        return redirect(url_for("daily_admin"))

    @app.post("/staff/daily/order/<order_ref>/restore")
    @staff_only
    def daily_restore_order(order_ref):
        require_csrf()
        stamp = now_ist().isoformat(timespec="seconds")
        with get_db() as con:
            result = con.execute(
                """UPDATE daily_orders SET archived_at=NULL,archived_reason='',
                   updated_at=? WHERE order_ref=? AND archived_at IS NOT NULL
                   AND deleted_at IS NULL""",
                (stamp, order_ref),
            )
        if not result.rowcount:
            abort(404)
        flash("Order restored to its delivery date list.", "success")
        return redirect(url_for("daily_admin", date=request.form.get("date", "")))

    @app.get("/staff/daily.csv")
    @staff_only
    def daily_export():
        selected = request.args.get("date", service_days()[0].isoformat())
        try:
            selected = date.fromisoformat(selected).isoformat()
        except ValueError:
            abort(400)
        with get_db() as con:
            rows = [dict(r) for r in con.execute(
                """SELECT * FROM daily_orders WHERE service_date=? AND deleted_at IS NULL
                   AND archived_at IS NULL AND TRIM(name)!='' AND TRIM(phone)!=''
                   ORDER BY delivery_slot,building_flat""", (selected,))]
        fields = [(k, label) for k, label in (
            ("order_ref", "Reference"), ("created_at", "Order time IST"),
            ("service_date", "Delivery date"), ("juice", "Juice"),
            ("name", "Customer"), ("phone", "Mobile"),
            ("portions", "Portions"), ("unit_price", "Unit price INR"),
            ("total_price", "Total INR"), ("delivery_slot", "Slot"),
            ("building_flat", "Building / Flat"), ("status", "Fulfillment"),
            ("payment_status", "Payment"), ("payment_method", "Payment method"))]
        return csv_response(rows, "purefruit-daily-" + selected + ".csv", fields)
