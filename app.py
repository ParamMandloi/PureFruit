"""PureFruit: a minimal WhatsApp-first ordering site with a private SQLite staff report."""

import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "LOCAL_DEVELOPMENT_ONLY_CHANGE_ME")
app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY", "0") == "1",
)

IST = ZoneInfo("Asia/Kolkata")
DATABASE = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "orders.db"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
WHATSAPP_NUMBER = "918074062979"
LAUNCH_DATE = date(2026, 11, 6)
PLANS = {5: 599, 10: 1099, 20: 1999}
SLOTS = {"morning": "Morning (8–9 AM)", "afternoon": "Afternoon (4–5 PM)"}
FRUITS = {0: "Mosambi", 1: "Pineapple", 2: "ABC", 3: "Orange", 4: "Watermelon"}
STATUSES = ("whatsapp_opened", "confirmed", "completed", "cancelled")
PAYMENT_STATUSES = ("unpaid", "paid")


def now_ist():
    return datetime.now(IST)


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def setup_db():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE)), exist_ok=True)
    with get_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            tower TEXT NOT NULL,
            flat TEXT NOT NULL,
            plan_days INTEGER NOT NULL,
            price INTEGER NOT NULL,
            delivery_slot TEXT NOT NULL,
            start_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'whatsapp_opened',
            payment_status TEXT NOT NULL DEFAULT 'unpaid',
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS orders_start_date_idx ON orders(start_date)")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


def require_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not token or not hmac.compare_digest(token, session.get("csrf_token", "")):
        abort(403, description="Invalid security token. Refresh the page and try again.")


def staff_only(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("staff_logged_in"):
            return redirect(url_for("staff_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def next_available_day():
    candidate = max(now_ist().date(), LAUNCH_DATE)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def dates_in_plan(start, count):
    current = start
    made = 0
    while made < count:
        if current.weekday() < 5:
            yield current
            made += 1
        current += timedelta(days=1)


def clean_text(value, max_len):
    return " ".join(str(value or "").split())[:max_len]


def wa_link(order):
    start = date.fromisoformat(order["start_date"]).strftime("%d %b %Y")
    message = (
        "Hello PureFruit! I would like to request a juice subscription.\n\n"
        f"Reference: {order['order_ref']}\n"
        f"Plan: {order['plan_days']} delivery days – ₹{order['price']:,}\n"
        f"Delivery: {SLOTS[order['delivery_slot']]}\n"
        f"Starting: {start}\n"
        f"Name: {order['name']}\n"
        f"Tower: {order['tower']}\n"
        f"Flat: {order['flat']}\n"
        f"Phone: {order['phone']}\n\n"
        "Please confirm availability and payment instructions."
    )
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}"


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/staff") else "no-cache"
    return response


@app.get("/")
def index():
    return render_template(
        "index.html", csrf=csrf_token(), min_start=next_available_day().isoformat(),
        phone=WHATSAPP_NUMBER,
    )


@app.post("/api/orders")
def create_order():
    require_csrf()
    if not request.is_json:
        return jsonify(error="Please submit the form again."), 400
    data = request.get_json(silent=True) or {}
    if data.get("website"):
        return jsonify(error="Unable to process request."), 400
    name = clean_text(data.get("name"), 70)
    phone = re.sub(r"\D", "", str(data.get("phone", "")))
    tower = clean_text(data.get("tower"), 50)
    flat = clean_text(data.get("flat"), 30)
    slot = data.get("delivery_slot")
    try:
        days = int(data.get("plan_days", 0))
        start = date.fromisoformat(str(data.get("start_date", "")))
    except (ValueError, TypeError):
        return jsonify(error="Select a valid plan and start date."), 400
    if not (2 <= len(name) <= 70 and 10 <= len(phone) <= 12 and phone[-10] in "6789" and
            tower and flat and days in PLANS and slot in SLOTS and
            next_available_day() <= start <= now_ist().date() + timedelta(days=120) and start.weekday() < 5):
        return jsonify(error="Check your name, Indian mobile number, flat, plan, slot and weekday start date."), 400
    # Normalize optional +91 prefix to a 10-digit Indian phone number.
    if len(phone) == 12 and phone.startswith("91"):
        phone = phone[2:]
    if len(phone) != 10 or phone[0] not in "6789":
        return jsonify(error="Enter a valid 10-digit Indian mobile number."), 400

    ref = "PF-" + now_ist().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()
    timestamp = now_ist().isoformat(timespec="seconds")
    order = dict(order_ref=ref, created_at=timestamp, name=name, phone=phone,
                 tower=tower, flat=flat, plan_days=days, price=PLANS[days],
                 delivery_slot=slot, start_date=start.isoformat(),
                 status="whatsapp_opened", payment_status="unpaid", updated_at=timestamp)
    with get_db() as con:
        con.execute("""INSERT INTO orders (order_ref,created_at,name,phone,tower,flat,
            plan_days,price,delivery_slot,start_date,status,payment_status,updated_at)
            VALUES (:order_ref,:created_at,:name,:phone,:tower,:flat,:plan_days,:price,
            :delivery_slot,:start_date,:status,:payment_status,:updated_at)""", order)
    return jsonify(reference=ref, whatsapp_url=wa_link(order))


@app.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    if request.method == "POST":
        require_csrf()
        if not ADMIN_PASSWORD:
            flash("ADMIN_PASSWORD must be configured on the server.", "error")
        elif hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session.clear()
            session["staff_logged_in"] = True
            csrf_token()
            return redirect(url_for("staff_dashboard"))
        else:
            flash("Incorrect password.", "error")
    return render_template("login.html", csrf=csrf_token())


@app.post("/staff/logout")
@staff_only
def staff_logout():
    require_csrf()
    session.clear()
    return redirect(url_for("index"))


def all_orders():
    with get_db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM orders ORDER BY id DESC")]


def safe_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return now_ist().date()


@app.get("/staff")
@staff_only
def staff_dashboard():
    orders = all_orders()
    q = clean_text(request.args.get("q"), 60).lower()
    status_filter = request.args.get("status", "all")
    if q:
        orders = [r for r in orders if any(q in str(r[key]).lower() for key in ("order_ref", "name", "phone", "tower", "flat"))]
    if status_filter in STATUSES:
        orders = [r for r in orders if r["status"] == status_filter]
    stats_orders = all_orders()
    stats = {
        "requests": len(stats_orders),
        "confirmed": sum(r["status"] == "confirmed" for r in stats_orders),
        "paid": sum(r["payment_status"] == "paid" for r in stats_orders),
        "booked_value": sum(r["price"] for r in stats_orders if r["status"] in ("confirmed", "completed")),
    }
    selected_date = safe_date(request.args.get("delivery_date"))
    delivery_orders = []
    if selected_date.weekday() < 5:
        for r in stats_orders:
            if r["status"] in ("confirmed", "completed") and selected_date in dates_in_plan(date.fromisoformat(r["start_date"]), r["plan_days"]):
                delivery_orders.append(r)
    delivery_orders.sort(key=lambda r: (r["delivery_slot"], r["tower"].lower(), r["flat"].lower()))
    return render_template("admin.html", csrf=csrf_token(), orders=orders[:300],
                           stats=stats, statuses=STATUSES, selected_date=selected_date,
                           fruit=FRUITS.get(selected_date.weekday()), deliveries=delivery_orders,
                           q=q, status_filter=status_filter, slots=SLOTS)


@app.post("/staff/order/<order_ref>")
@staff_only
def update_order(order_ref):
    require_csrf()
    status = request.form.get("status", "")
    payment = request.form.get("payment_status", "")
    notes = clean_text(request.form.get("notes"), 500)
    if status not in STATUSES or payment not in PAYMENT_STATUSES:
        abort(400)
    with get_db() as con:
        cursor = con.execute("""UPDATE orders SET status=?, payment_status=?, notes=?, updated_at=?
                                WHERE order_ref=?""",
                             (status, payment, notes, now_ist().isoformat(timespec="seconds"), order_ref))
        if not cursor.rowcount:
            abort(404)
    flash(f"Updated {order_ref}.", "success")
    return redirect(url_for("staff_dashboard"))


def csv_response(rows, name, columns):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for r in rows:
        values = []
        for key, _ in columns:
            value = str(r.get(key, ""))
            # Prevent spreadsheet-formula injection on open in Excel/Sheets.
            if value.startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/staff/orders.csv")
@staff_only
def export_orders():
    cols = [("order_ref", "Reference"), ("created_at", "Requested at (IST)"),
            ("name", "Name"), ("phone", "Phone"), ("tower", "Tower"),
            ("flat", "Flat"), ("plan_days", "Delivery days"), ("price", "Price INR"),
            ("delivery_slot", "Slot"), ("start_date", "Start date"),
            ("status", "Order status"), ("payment_status", "Payment"), ("notes", "Notes")]
    return csv_response(all_orders(), "purefruit-orders.csv", cols)


@app.get("/staff/deliveries.csv")
@staff_only
def export_deliveries():
    target = safe_date(request.args.get("delivery_date"))
    rows = [dict(r) for r in all_orders() if r["status"] in ("confirmed", "completed") and
            target.weekday() < 5 and target in dates_in_plan(date.fromisoformat(r["start_date"]), r["plan_days"])]
    rows.sort(key=lambda r: (r["delivery_slot"], r["tower"], r["flat"]))
    for r in rows:
        r["fruit"] = FRUITS.get(target.weekday(), "")
    cols = [("order_ref", "Reference"), ("name", "Name"), ("phone", "Phone"),
            ("tower", "Tower"), ("flat", "Flat"), ("delivery_slot", "Slot"),
            ("fruit", "Juice"), ("payment_status", "Payment")]
    return csv_response(rows, f"purefruit-deliveries-{target.isoformat()}.csv", cols)


setup_db()

if __name__ == "__main__":
    if not ADMIN_PASSWORD:
        print("NOTE: set ADMIN_PASSWORD and SECRET_KEY for a secure staff report.")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
