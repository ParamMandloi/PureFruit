"""PureFruit: manual PhonePe UPI QR payments and staff order management on Railway."""

import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

import qrcode

import requests
from telegram_alerts import send_order_alert, discover_groups, bot_ready
from zoneinfo import ZoneInfo

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template,
    request, session, url_for, send_file,
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
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
RAZORPAY_API = "https://api.razorpay.com/v1"
PAYMENTS_READY = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET)
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "upi_qr").strip().lower()
# Recipient data decoded from the PhonePe QR supplied by the merchant. These are
# public payee identifiers, not login credentials or payment-gateway secrets.
UPI_PAYEE_ADDRESS = os.getenv("UPI_PAYEE_ADDRESS", "9619140077-gsb@ibl").strip()
UPI_PAYEE_NAME = os.getenv("UPI_PAYEE_NAME", "GREENRISE BUZZ PRIVATE LIMITED").strip()
UPI_PAYEE_MCC = os.getenv("UPI_PAYEE_MCC", "0000").strip()
LAUNCH_DATE = date(2026, 11, 6)
PLANS = {5: 599, 10: 1099, 20: 1999}  # Regular rupee prices
# Offer begins on 5 November in India time, and ends just as 7 November begins.
# All checkout prices use the server clock. Existing orders retain their stored price.
PROMO_START_IST = datetime(2026, 11, 5, 16, 0, tzinfo=IST)
PROMO_END_EXCLUSIVE_IST = datetime(2026, 11, 7, 0, 0, tzinfo=IST)
DHANTERAS_DATE = date(2026, 11, 6)
DHANTERAS_PRICES = {days: (regular * 75) // 100 for days, regular in PLANS.items()}


def promo_active(at=None):
    """Server-side 25% discount eligibility; exclusive end 7 Nov 00:00 IST."""
    point = at or now_ist()
    if point.tzinfo is None:
        raise ValueError("Promotion check must use a timezone-aware datetime")
    point = point.astimezone(IST)
    return PROMO_START_IST <= point < PROMO_END_EXCLUSIVE_IST


def price_for_plan(days, at=None):
    """Lock the order amount at creation. Prices are always whole INR rupees."""
    return DHANTERAS_PRICES[days] if promo_active(at) else PLANS[days]


def checkout_config():
    """Fresh server-side pricing and available weekdays; auto-reverts 7 Nov IST."""
    pricing_time = now_ist()
    today = pricing_time.date()
    first = next_available_day()
    options = []
    day = first
    while len(options) < 40 and day <= today + timedelta(days=120):
        if day.weekday() < 5:
            options.append({"value": day.isoformat(), "label": day.strftime('%a, %d %b %Y')})
        day += timedelta(days=1)
    return {
        "plans": {str(d): price_for_plan(d, pricing_time) for d in PLANS},
        "regular_plans": {str(d): price for d, price in PLANS.items()},
        "promo_active": promo_active(pricing_time),
        "promo_date": DHANTERAS_DATE.isoformat(),
        "promo_ends_at": PROMO_END_EXCLUSIVE_IST.isoformat(),
        "server_date": today.isoformat(),
        "min_start": first.isoformat(),
        "start_dates": options,
    }
SLOTS = {"morning": "Morning (8–9 AM)", "afternoon": "Afternoon (4–5 PM)"}
FRUITS = {0: "Mosambi", 1: "Pineapple", 2: "ABC", 3: "Orange", 4: "Watermelon"}
STATUSES = ("payment_pending", "confirmed", "completed", "cancelled", "whatsapp_opened")


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
            status TEXT NOT NULL DEFAULT 'payment_pending',
            payment_status TEXT NOT NULL DEFAULT 'unpaid',
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )""")
        # Add columns in place; retain existing orders from the WhatsApp-era website.
        cols = {col[1] for col in con.execute("PRAGMA table_info(orders)")}
        for name in ("razorpay_order_id", "razorpay_payment_id", "paid_at", "upi_reference", "upi_submitted_at", "upi_verified_at", "upi_verified_by"):
            if name not in cols:
                con.execute(f"ALTER TABLE orders ADD COLUMN {name} TEXT")
        if "upi_verified_amount" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN upi_verified_amount INTEGER")
        if "payment_method" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'razorpay'")
        # Track staff review outcomes separately from payment_status; customer
        # reports, 'not received' and 'check later' must never activate delivery.
        if "upi_review_status" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN upi_review_status TEXT NOT NULL DEFAULT 'awaiting'")
        if "upi_review_note" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN upi_review_note TEXT NOT NULL DEFAULT ''")
        if "upi_reviewed_at" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN upi_reviewed_at TEXT")
        # Keep an audit trail for test-order cleanup, without personal customer data.
        # Monotonic protection: once confirmed/paid, never eligible for cleanup,
        # even if staff later changes the displayed status to Cancelled.
        if "ever_confirmed" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN ever_confirmed INTEGER NOT NULL DEFAULT 0")
        con.execute("""UPDATE orders SET ever_confirmed=1
            WHERE ever_confirmed=0 AND (
                status IN ('confirmed','completed') OR payment_status='paid'
                OR paid_at IS NOT NULL OR upi_verified_at IS NOT NULL
                OR razorpay_payment_id IS NOT NULL OR upi_verified_amount IS NOT NULL
            )""")
        con.execute("""CREATE TABLE IF NOT EXISTS deleted_test_order_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT NOT NULL UNIQUE,
            deleted_at TEXT NOT NULL,
            previous_status TEXT NOT NULL,
            previous_payment_status TEXT NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS orders_start_date_idx ON orders(start_date)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_razorpay_order_idx ON orders(razorpay_order_id)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_razorpay_payment_idx ON orders(razorpay_payment_id)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_upi_reference_idx ON orders(upi_reference)")


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
    """First start date is the next Monday-Friday after the booking day (IST)."""
    candidate = max(now_ist().date() + timedelta(days=1), LAUNCH_DATE)
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


def razorpay_api(method, path, **kwargs):
    """Only server-side code has the Razorpay API secret."""
    try:
        response = requests.request(
            method, RAZORPAY_API + path, auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=(5, 12), **kwargs
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        app.logger.exception("Razorpay API request failed at %s", path)
        raise RuntimeError("Payment provider unavailable. Please try again shortly.")


def payment_matches(order, payment):
    """Never trust a browser amount/order ID. Check provider entity against SQLite."""
    return (
        payment.get("id") and
        payment.get("order_id") == order["razorpay_order_id"] and
        payment.get("amount") == order["price"] * 100 and
        payment.get("currency") == "INR" and
        payment.get("status") == "captured"
    )


def mark_paid(order, payment_id):
    """Idempotent; no duplicate confirmations on webhook retry."""
    if not payment_id:
        return False
    timestamp = now_ist().isoformat(timespec="seconds")
    with get_db() as con:
        cur = con.execute(
            """UPDATE orders SET payment_status='paid', ever_confirmed=1,
                   status=CASE WHEN status='payment_pending' THEN 'confirmed' ELSE status END,
                   razorpay_payment_id=COALESCE(razorpay_payment_id, ?),
                   paid_at=COALESCE(paid_at, ?), updated_at=?
               WHERE order_ref=? AND (razorpay_payment_id IS NULL OR razorpay_payment_id=?)""",
            (payment_id, timestamp, timestamp, order["order_ref"], payment_id)
        )
    changed = cur.rowcount == 1
    if changed and order.get("payment_status") != "paid":
        send_order_alert("paid", dict(order), start_date_override=order.get("start_date"))
    return changed


def find_order(ref=None, razorpay_order_id=None):
    with get_db() as con:
        if ref:
            row = con.execute("SELECT * FROM orders WHERE order_ref=?", (ref,)).fetchone()
        else:
            row = con.execute("SELECT * FROM orders WHERE razorpay_order_id=?", (razorpay_order_id,)).fetchone()
    return dict(row) if row else None


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
        razorpay_ready=PAYMENTS_READY, razorpay_key_id=RAZORPAY_KEY_ID, payment_mode=PAYMENT_MODE,
        checkout=checkout_config(),
    )


@app.get("/api/checkout-config")
def public_checkout_config():
    response = jsonify(checkout_config())
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.post("/api/orders")
def create_order():
    require_csrf()
    if PAYMENT_MODE != "razorpay":
        return jsonify(error="Razorpay checkout is not enabled. Please use UPI QR checkout."), 503
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

    if not PAYMENTS_READY:
        return jsonify(error="Online payment is not yet enabled. Please contact PureFruit."), 503
    ref = "PF-" + now_ist().strftime("%y%m%d") + "-" + secrets.token_hex(6).upper()
    timestamp = now_ist().isoformat(timespec="seconds")
    order = dict(order_ref=ref, created_at=timestamp, name=name, phone=phone,
                 tower=tower, flat=flat, plan_days=days, price=price_for_plan(days),
                 delivery_slot=slot, start_date=start.isoformat(),
                 status="payment_pending", payment_status="unpaid", updated_at=timestamp)
    try:
        rp = razorpay_api("POST", "/orders", json={
            "amount": order["price"] * 100, "currency": "INR", "receipt": ref,
            "notes": {"subscription_days": str(days)}
        })
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 502
    rzp_order_id = rp.get("id")
    if (not isinstance(rzp_order_id, str) or not rzp_order_id.startswith("order_") or
            rp.get("amount") != order["price"]*100 or rp.get("currency") != "INR"):
        app.logger.error("Unexpected Razorpay order API response")
        return jsonify(error="Could not start payment. Please try again."), 502
    order["razorpay_order_id"] = rzp_order_id
    with get_db() as con:
        con.execute("""INSERT INTO orders (order_ref,created_at,name,phone,tower,flat,
            plan_days,price,delivery_slot,start_date,status,payment_status,updated_at,razorpay_order_id)
            VALUES (:order_ref,:created_at,:name,:phone,:tower,:flat,:plan_days,:price,
            :delivery_slot,:start_date,:status,:payment_status,:updated_at,:razorpay_order_id)""", order)
    send_order_alert("new", order)
    return jsonify(reference=ref, razorpay_order_id=rzp_order_id,
                   key_id=RAZORPAY_KEY_ID, amount=order["price"]*100,
                   currency="INR", name=name, phone=phone)




def validate_customer_order(data):
    """Validate orders server-side; price is selected from trusted plan table."""
    name = clean_text(data.get("name"), 70)
    phone = re.sub(r"\D", "", str(data.get("phone", "")))
    tower = clean_text(data.get("tower"), 50)
    flat = clean_text(data.get("flat"), 30)
    slot = data.get("delivery_slot")
    try:
        days = int(data.get("plan_days", 0))
        start = date.fromisoformat(str(data.get("start_date", "")))
    except (ValueError, TypeError):
        return None
    if len(phone) == 12 and phone.startswith("91"):
        phone = phone[2:]
    if not (2 <= len(name) <= 70 and len(phone) == 10 and phone[0] in "6789" and
            tower and flat and days in PLANS and slot in SLOTS and
            next_available_day() <= start <= now_ist().date() + timedelta(days=120) and
            start.weekday() < 5):
        return None
    return {"name": name, "phone": phone, "tower": tower, "flat": flat,
            "plan_days": days, "price": price_for_plan(days), "delivery_slot": slot,
            "start_date": start.isoformat()}


@app.post("/api/upi/orders")
def create_upi_order():
    require_csrf()
    if PAYMENT_MODE != "upi_qr":
        return jsonify(error="UPI QR checkout is not enabled."), 503
    if not request.is_json:
        return jsonify(error="Invalid order data."), 400
    data = request.get_json(silent=True) or {}
    if data.get("website"):
        return jsonify(error="Invalid request."), 400
    validated = validate_customer_order(data)
    if not validated:
        return jsonify(error="Check name, mobile, tower, flat, plan, slot and weekday start date."), 400
    timestamp = now_ist().isoformat(timespec="seconds")
    order_ref = "PF-" + now_ist().strftime("%y%m%d") + "-" + secrets.token_hex(6).upper()
    validated.update(order_ref=order_ref, created_at=timestamp, updated_at=timestamp)
    with get_db() as con:
        con.execute("""INSERT INTO orders (order_ref,created_at,name,phone,tower,flat,plan_days,
            price,delivery_slot,start_date,status,payment_status,updated_at,payment_method)
            VALUES (:order_ref,:created_at,:name,:phone,:tower,:flat,:plan_days,:price,
            :delivery_slot,:start_date,'payment_pending','unpaid',:updated_at,'upi_qr')""", validated)
    send_order_alert("new", validated)
    return jsonify(reference=order_ref, amount=validated["price"],
                   payee="GREENRISE BUZZ PRIVATE LIMITED", status="payment_pending"), 201


def create_upi_uri(order, mode="01"):
    """Construct an amount-prefilled UPI request, *not* a payment receipt.

    Never copy signed/proprietary QR data, accept a VPA from the browser, or
    assume a UPI-app return means money arrived. The original merchant QR is
    still available as a fallback if an app rejects amount-prefilled links.
    """
    days = int(order["plan_days"])
    if (order.get("payment_method") != "upi_qr" or days not in PLANS or
            int(order["price"]) not in (PLANS[days], DHANTERAS_PRICES[days])):
        raise ValueError("Invalid UPI order")
    if not re.fullmatch(r"PF-[A-Za-z0-9-]{8,40}", order["order_ref"]):
        raise ValueError("Invalid order reference")
    params = [
        ("pa", UPI_PAYEE_ADDRESS),
        ("pn", UPI_PAYEE_NAME),
        ("am", f'{int(order["price"]):.2f}'),
        ("cu", "INR"),
        ("tr", order["order_ref"]),
        ("tn", f'PureFruit {order["order_ref"]}'),
    ]
    if UPI_PAYEE_MCC:
        params.append(("mc", UPI_PAYEE_MCC))
    # Standard 01 = QR, 04 = mobile UPI intent; provider support varies.
    params.append(("mode", mode))
    return "upi://pay?" + urlencode(params)


@app.get("/api/upi/orders/<order_ref>/payment")
def get_upi_payment(order_ref):
    order = find_order(ref=order_ref)
    if not order or order.get("payment_method") != "upi_qr":
        return jsonify(error="Order not found."), 404
    if order["status"] == "cancelled":
        return jsonify(error="Order cancelled."), 410
    if order["payment_status"] == "paid":
        return jsonify(reference=order_ref, status="paid")
    return jsonify(reference=order_ref, amount=order["price"],
                   payee=UPI_PAYEE_NAME, status=order["payment_status"],
                   intent=create_upi_uri(order, mode="04"),
                   qr=url_for("upi_order_qr", order_ref=order_ref))


@app.get("/api/upi/orders/<order_ref>/qr.png")
def upi_order_qr(order_ref):
    order = find_order(ref=order_ref)
    if not order or order.get("payment_method") != "upi_qr":
        abort(404)
    if order["status"] == "cancelled":
        abort(410)
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(create_upi_uri(order, mode="01"))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    response = send_file(output, mimetype="image/png", download_name=f'{order_ref}-upi.png')
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.post("/api/upi/orders/<order_ref>/claim")
def claim_upi_payment(order_ref):
    """A customer CLAIMED to have paid; never mark the order paid here."""
    require_csrf()
    if PAYMENT_MODE != "upi_qr":
        return jsonify(error="UPI checkout is not enabled."), 503
    timestamp = now_ist().isoformat(timespec="seconds")
    with get_db() as con:
        row = con.execute("SELECT payment_method,payment_status,status FROM orders WHERE order_ref=?", (order_ref,)).fetchone()
        if not row or row["payment_method"] != "upi_qr":
            return jsonify(error="UPI order not found."), 404
        if row["status"] == "cancelled":
            return jsonify(error="This order was cancelled."), 409
        if row["payment_status"] == "paid":
            return jsonify(reference=order_ref, status="paid")
        # Idempotent: repeat taps never create a second order or extra payment.
        con.execute("""UPDATE orders SET payment_status='pending_verification',
                       upi_submitted_at=COALESCE(upi_submitted_at, ?), updated_at=?
                       WHERE order_ref=? AND payment_status!='paid'""",
                    (timestamp, timestamp, order_ref))
    if row["payment_status"] != "pending_verification":
        latest = find_order(ref=order_ref)
        if latest:
            send_order_alert("payment_reported", latest)
    return jsonify(reference=order_ref, status="pending_verification",
                   message="Payment reported. Staff will confirm after checking the actual PhonePe credit.")


@app.post("/api/upi/orders/<order_ref>/submit")
def submit_upi_reference(order_ref):
    require_csrf()
    if PAYMENT_MODE != "upi_qr":
        return jsonify(error="UPI QR checkout is not enabled."), 503
    data = request.get_json(silent=True) or {}
    # Validates a reference FORMAT, not whether money was actually received.
    utr = str(data.get("upi_reference", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{10,40}", utr):
        return jsonify(error="Enter the 10–40 character UPI transaction reference from your payment app."), 400
    with get_db() as con:
        row = con.execute("SELECT payment_method,payment_status,upi_reference FROM orders WHERE order_ref=?", (order_ref,)).fetchone()
        if not row or row["payment_method"] != "upi_qr":
            return jsonify(error="UPI order not found."), 404
        if row["payment_status"] == "paid":
            return jsonify(error="This order is already paid."), 409
        if row["upi_reference"] and row["upi_reference"] != utr:
            return jsonify(error="A transaction reference was already submitted. Contact PureFruit to correct it."), 409
        try:
            con.execute("""UPDATE orders SET upi_reference=?, upi_submitted_at=COALESCE(upi_submitted_at, ?),
                           payment_status='pending_verification',updated_at=?
                           WHERE order_ref=? AND payment_status!='paid'""",
                        (utr, now_ist().isoformat(timespec="seconds"),
                         now_ist().isoformat(timespec="seconds"), order_ref))
        except sqlite3.IntegrityError:
            return jsonify(error="This transaction reference is already linked to another order. Contact PureFruit."), 409
    if row["payment_status"] != "pending_verification":
        latest = find_order(ref=order_ref)
        if latest:
            send_order_alert("payment_reported", latest)
    return jsonify(reference=order_ref, status="pending_verification",
                   message="Payment details submitted. Your subscription starts only after PureFruit verifies bank credit.")


@app.post("/staff/upi/<order_ref>/confirm")
@staff_only
def staff_confirm_upi(order_ref):
    require_csrf()
    current = find_order(ref=order_ref)
    if not current or current.get("payment_method") != "upi_qr":
        abort(404)
    if current["payment_status"] == "paid":
        flash("This order is already marked paid; no duplicate confirmation was made.", "error")
        return redirect(url_for("staff_dashboard"))
    if current["status"] == "cancelled":
        flash("Cancelled orders cannot be confirmed. Review the order first.", "error")
        return redirect(url_for("staff_dashboard"))
    confirmed_utr = str(request.form.get("confirmed_utr", "")).strip().upper()
    verified_amount = str(request.form.get("verified_amount", "")).strip()
    # Customer UTR is optional in the express flow. Staff must record the
    # transaction reference from an ACTUAL PhonePe Business/bank credit.
    # Unique DB index prevents reuse of the same credited transaction.
    if (not re.fullmatch(r"[A-Z0-9-]{10,40}", confirmed_utr) or
            (current["upi_reference"] and not hmac.compare_digest(confirmed_utr, current["upi_reference"])) or
            verified_amount != str(current["price"]) or request.form.get("bank_credit_checked") != "yes" or
            current["payment_status"] not in ("unpaid", "pending_verification")):
        flash("Not confirmed. Match a real PhonePe bank credit, amount and unique UTR first.", "error")
        return redirect(url_for("staff_dashboard"))
    timestamp = now_ist().isoformat(timespec="seconds")
    old_start = date.fromisoformat(current["start_date"])
    start_date = max(old_start, next_available_day()).isoformat()
    try:
        with get_db() as con:
            cur = con.execute("""UPDATE orders SET payment_status='paid', status='confirmed', ever_confirmed=1,
                         paid_at=?, upi_verified_at=?, upi_verified_by='staff',
                         upi_verified_amount=?, start_date=?, updated_at=?, upi_reference=?,
                         upi_review_status='verified', upi_reviewed_at=?, upi_review_note='Bank credit verified'
                         WHERE order_ref=? AND payment_method='upi_qr'
                         AND payment_status IN ('unpaid','pending_verification') AND status!='cancelled'
                         AND (upi_reference IS NULL OR upi_reference=?)""",
                      (timestamp, timestamp, current["price"], start_date, timestamp,
                       confirmed_utr, timestamp, order_ref, confirmed_utr))
            if cur.rowcount != 1:
                flash("The order changed; refresh and check before confirming.", "error")
                return redirect(url_for("staff_dashboard"))
    except sqlite3.IntegrityError:
        flash("That PhonePe UTR was already used for another order. Do not confirm twice.", "error")
        return redirect(url_for("staff_dashboard"))
    send_order_alert("paid", {**current, "start_date": start_date}, start_date_override=start_date)
    flash(f"UPI bank credit verified for {order_ref}. Subscription confirmed. Start date: {start_date}.", "success")
    return redirect(url_for("staff_dashboard"))

@app.post("/staff/upi/<order_ref>/review")
@staff_only
def staff_review_upi(order_ref):
    """Staff triage: no credit / check later / return to verification queue.

    Do not modify payment_status or order status. None of these actions are
    proof of payment, and they never cause delivery activation.
    """
    require_csrf()
    action = request.form.get("review_action", "")
    actions = {"not_received", "check_later", "recheck"}
    if action not in actions:
        abort(400, description="Unknown payment review action")
    note = clean_text(request.form.get("review_note", ""), 250)
    stamp = now_ist().isoformat(timespec="seconds")
    with get_db() as con:
        updated = con.execute("""UPDATE orders
            SET upi_review_status=?, upi_review_note=?, upi_reviewed_at=?, updated_at=?
            WHERE order_ref=? AND payment_method='upi_qr'
              AND payment_status IN ('unpaid','pending_verification') AND status!='cancelled'""",
            ("awaiting" if action == "recheck" else action,
             note, stamp, stamp, order_ref))
    if updated.rowcount != 1:
        flash("This order can no longer be reviewed as an unpaid UPI order.", "error")
    else:
        message = {"not_received": "Payment not received — moved to follow-up.",
                   "check_later": "Payment marked Check Later — still unverified.",
                   "recheck": "Order returned to awaiting verification."}[action]
        if action in ("not_received", "check_later"):
            latest = find_order(ref=order_ref)
            if latest:
                send_order_alert(action, latest)
        flash(f"{order_ref}: {message}", "success")
    return redirect(url_for("staff_dashboard"))


@app.post("/api/orders/<order_ref>/verify")
def verify_checkout(order_ref):
    require_csrf()
    values = request.get_json(silent=True) or {}
    rp_order_id = str(values.get("razorpay_order_id", ""))
    payment_id = str(values.get("razorpay_payment_id", ""))
    signature = str(values.get("razorpay_signature", ""))
    order = find_order(ref=order_ref)
    if not (order and order.get("razorpay_order_id") == rp_order_id and
            payment_id.startswith("pay_") and len(signature) == 64):
        return jsonify(error="Invalid payment reference."), 400
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(),
                        f"{order['razorpay_order_id']}|{payment_id}".encode(), "sha256").hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify(error="Payment verification failed."), 403
    try:
        payment = razorpay_api("GET", "/payments/" + payment_id)
    except RuntimeError:
        # The webhook may still confirm later; never treat a timeout as success.
        return jsonify(reference=order_ref, status="pending"), 202
    if payment_matches(order, payment):
        if not mark_paid(order, payment_id):
            return jsonify(error="Payment requires staff review."), 409
        return jsonify(reference=order_ref, status="paid")
    # 'authorized' is not captured, so the subscription is NOT confirmed yet.
    return jsonify(reference=order_ref, status="pending"), 202


@app.get("/api/orders/<order_ref>/status")
def order_payment_status(order_ref):
    order = find_order(ref=order_ref)
    if not order or not order.get("razorpay_order_id"):
        abort(404)
    return jsonify(reference=order_ref, status=order["payment_status"])


@app.post("/api/razorpay/webhook")
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:
        return "Not configured", 503
    raw = request.get_data(cache=False)
    supplied = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw, "sha256").hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return "Invalid signature", 403
    try:
        import json
        event = json.loads(raw)
    except (ValueError, TypeError):
        return "Invalid JSON", 400
    if not isinstance(event, dict):
        return "Invalid event", 400
    if event.get("event") == "payment.captured":
        payment = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {})
        if not isinstance(payment, dict):
            return "Invalid payment", 400
        order = find_order(razorpay_order_id=payment.get("order_id")) if payment.get("order_id") else None
        if order and payment_matches(order, payment):
            mark_paid(order, payment.get("id"))
    return "OK", 200


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


@app.get("/staff/telegram/setup")
@staff_only
def telegram_setup():
    """Find private group chat IDs without revealing the bot token to a browser."""
    groups, error = discover_groups()
    return render_template("telegram_setup.html", csrf=csrf_token(),
                           groups=groups, error=error, enabled=bot_ready())


@app.post("/staff/telegram/test")
@staff_only
def telegram_test():
    require_csrf()
    if send_order_alert("test", {"order_ref": "SETUP"}):
        flash("Telegram test message sent. Check your private group.", "success")
    else:
        flash("Telegram test was not delivered. Check bot token, chat ID, and group permissions in Railway.", "error")
    return redirect(url_for("telegram_setup"))


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
    # Operational queues are always based on ALL outstanding orders, not the
    # filtered/paginated All Orders table. Every unverified UPI order must be
    # visible in exactly one queue so staff cannot miss a valid checkout.
    stats_orders = all_orders()
    for order in orders:
        order["can_delete_test"] = can_delete_test_order(order)
    outstanding = [r for r in stats_orders
                   if r.get("payment_method") == "upi_qr"
                   and r.get("payment_status") not in ("paid", "refunded")
                   and r.get("status") != "cancelled"]
    upi_unpaid = [r for r in outstanding
                  if r.get("payment_status") == "unpaid"
                  and r.get("upi_review_status", "awaiting") in ("awaiting", "")]
    upi_reported = [r for r in outstanding
                    if r.get("payment_status") == "pending_verification"
                    and r.get("upi_review_status", "awaiting") in ("awaiting", "")]
    # Catch any other unverified state rather than silently hiding it.
    upi_follow_up = [r for r in outstanding
                     if r not in upi_unpaid and r not in upi_reported]
    stats = {
        "requests": len(stats_orders),
        "confirmed": sum(r["status"] in ("confirmed", "completed") for r in stats_orders),
        "paid": sum(r["payment_status"] == "paid" for r in stats_orders),
        "booked_value": sum(r["price"] for r in stats_orders if r["payment_status"] == "paid"),
        "upi_unverified": len(outstanding),
        "upi_unpaid": len(upi_unpaid),
        "upi_to_verify": len(upi_reported),
        "upi_follow_up": len(upi_follow_up),
    }
    assert len(outstanding) == sum((len(upi_unpaid), len(upi_reported), len(upi_follow_up)))
    selected_date = safe_date(request.args.get("delivery_date"))
    delivery_orders = []
    if selected_date.weekday() < 5:
        for r in stats_orders:
            if r["payment_status"] == "paid" and r["status"] in ("confirmed", "completed") and selected_date in dates_in_plan(date.fromisoformat(r["start_date"]), r["plan_days"]):
                delivery_orders.append(r)
    delivery_orders.sort(key=lambda r: (r["delivery_slot"], r["tower"].lower(), r["flat"].lower()))
    return render_template("admin.html", csrf=csrf_token(), orders=orders[:300],
                           stats=stats, statuses=STATUSES, selected_date=selected_date,
                           fruit=FRUITS.get(selected_date.weekday()), deliveries=delivery_orders,
                            q=q, status_filter=status_filter, slots=SLOTS,
                            upi_unpaid=upi_unpaid, upi_reported=upi_reported,
                            upi_follow_up=upi_follow_up)


def can_delete_test_order(order):
    """Only cancelled, never-paid and never-reported records may be deleted.

    Confirmed or completed orders, verified payments, UPI payment reports,
    and Razorpay payment attempts are retained, even after cancellation.
    """
    return (
        order.get("status") == "cancelled"
        and order.get("payment_status") == "unpaid"
        and not order.get("ever_confirmed", 0)
        and not any(order.get(field) for field in (
            "paid_at", "razorpay_order_id", "razorpay_payment_id",
            "upi_reference", "upi_submitted_at", "upi_verified_at",
            "upi_verified_by", "upi_verified_amount"
        ))
        and order.get("upi_review_status") != "verified"
    )


@app.post("/staff/order/<order_ref>/delete-test")
@staff_only
def delete_test_order(order_ref):
    """Delete only demonstrably unpaid, cancelled test entries; retain audit.

    Transactional guard prevents a payment update and deletion from racing.
    GET requests, CSRF-free requests and deletion of paid orders are rejected.
    """
    require_csrf()
    if request.form.get("confirm_order_ref") != order_ref or request.form.get("confirm_delete") != "yes":
        abort(400, description="Explicit order-reference confirmation required")
    timestamp = now_ist().isoformat(timespec="seconds")
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM orders WHERE order_ref=?", (order_ref,)).fetchone()
        if row is None:
            abort(404)
        order = dict(row)
        if not can_delete_test_order(order):
            flash("Deletion blocked. Only cancelled, unpaid orders with no payment attempt or report may be deleted. Paid and confirmed orders are permanently protected.", "error")
            return redirect(url_for("staff_dashboard"))
        con.execute("""INSERT INTO deleted_test_order_audit
                    (order_ref, deleted_at, previous_status, previous_payment_status)
                    VALUES (?, ?, ?, ?)""",
                    (order_ref, timestamp, order["status"], order["payment_status"]))
        deleted = con.execute("DELETE FROM orders WHERE order_ref=?", (order_ref,))
        if deleted.rowcount != 1:
            raise RuntimeError("Expected exactly one cancelled test order to be deleted")
    flash(f"Cancelled unpaid test order {order_ref} was deleted. Audit reference retained.", "success")
    return redirect(url_for("staff_dashboard"))


@app.post("/staff/order/<order_ref>")
@staff_only
def update_order(order_ref):
    require_csrf()
    status = request.form.get("status", "")
    notes = clean_text(request.form.get("notes"), 500)
    if status not in STATUSES:
        abort(400)
    current = find_order(ref=order_ref)
    if not current:
        abort(404)
    if current["payment_status"] != "paid" and status in ("confirmed", "completed"):
        flash("Unpaid orders cannot be confirmed. Verify UPI bank credit or await Razorpay capture.", "error")
        return redirect(url_for("staff_dashboard"))
    with get_db() as con:
        cursor = con.execute("""UPDATE orders SET status=?, notes=?, updated_at=?,
                                ever_confirmed=CASE WHEN ? IN ('confirmed','completed')
                                                    THEN 1 ELSE ever_confirmed END
                                WHERE order_ref=?""",
                             (status, notes, now_ist().isoformat(timespec="seconds"), status, order_ref))
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
            ("status", "Order status"), ("payment_status", "Payment"),
            ("razorpay_order_id", "Razorpay order ID"),
            ("razorpay_payment_id", "Razorpay payment ID"),
            ("payment_method", "Payment method"), ("upi_reference", "Customer UPI reference"),
            ("upi_submitted_at", "UPI submitted at"), ("upi_verified_at", "UPI verified at"),
            ("upi_verified_amount", "UPI verified amount INR"),
            ("paid_at", "Paid at IST"), ("upi_review_status", "Staff review"),
            ("upi_review_note", "Staff review note"), ("upi_reviewed_at", "Staff reviewed at IST"),
            ("notes", "Notes")]
    return csv_response(all_orders(), "purefruit-orders.csv", cols)


@app.get("/staff/deliveries.csv")
@staff_only
def export_deliveries():
    target = safe_date(request.args.get("delivery_date"))
    rows = [dict(r) for r in all_orders() if r["payment_status"] == "paid" and r["status"] in ("confirmed", "completed") and
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
