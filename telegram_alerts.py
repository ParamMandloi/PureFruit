"""Best-effort Telegram staff alerts. Never confirms a payment or changes orders.

Configuration via Railway service variables only:
    TELEGRAM_BOT_TOKEN  : secret bot token from @BotFather
    TELEGRAM_CHAT_ID    : private group chat ID, generally negative

Any send failure is logged safely and does not break checkout or staff actions.
"""

import logging
import os
import re

import requests

LOG = logging.getLogger("purefruit.telegram")
API = "https://api.telegram.org"
STAFF_URL = "https://www.purefruit.in/staff"


def _config():
    return (os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            os.getenv("TELEGRAM_CHAT_ID", "").strip())


def bot_ready():
    token, group = _config()
    return bool(token and group)


def _safe(value, limit=90):
    return re.sub(r"[\x00-\x1f]+", " ", str(value or "")).strip()[:limit]


def _describe(event, order, start_date_override=None):
    labels = {
        "new": "NEW ORDER — Awaiting payment",
        "payment_reported": "PAYMENT REPORTED — Not verified",
        "paid": "PAYMENT VERIFIED — Subscription confirmed",
        "not_received": "PAYMENT NOT RECEIVED — Follow up",
        "check_later": "PAYMENT CHECK LATER — Follow up",
    }
    if event == "test":
        return "PureFruit Telegram test successful. Staff alerts are connected."
    if event not in labels:
        return None
    start = start_date_override or order.get("start_date")
    slot = order.get("delivery_slot", "")
    slot = {"morning": "8–9 AM", "afternoon": "4–5 PM"}.get(slot, _safe(slot))
    return "\n".join([
        "PureFruit | " + labels[event],
        "Order: " + _safe(order.get("order_ref")),
        "Customer: " + _safe(order.get("name"), 70) + " | WhatsApp: " + _safe(order.get("phone"), 20),
        "Plan: " + _safe(order.get("plan_days"), 3) + " days | ₹" + _safe(order.get("price"), 12),
        "Location: " + _safe(order.get("tower"), 50) + " / Flat " + _safe(order.get("flat"), 20),
        "Delivery: " + slot + " | Start " + _safe(start, 15),
        "Staff dashboard: " + STAFF_URL,
    ])



def _customer_whatsapp_draft(event, order, start_date_override=None):
    """Text to copy from the *private staff group* to the customer's WhatsApp.

    This does NOT send WhatsApp messages or alter payment verification. It is
    emitted only after the matching backend event has happened.
    """
    if event not in ("new", "payment_reported", "paid"):
        return None
    name = _safe(order.get("name"), 70).split(" ")[0] or "there"
    ref = _safe(order.get("order_ref"), 50)
    days = _safe(order.get("plan_days"), 3)
    price = _safe(order.get("price"), 10)
    tower = _safe(order.get("tower"), 50)
    flat = _safe(order.get("flat"), 30)
    start = _safe(start_date_override or order.get("start_date"), 15)
    slot = {"morning": "8–9 AM", "afternoon": "4–5 PM"}.get(
        order.get("delivery_slot"), _safe(order.get("delivery_slot"), 35))
    if event == "new":
        return (f"Hello {name},\n\n"
                f"Thank you for choosing PureFruit. We've received your order "
                f"request ({ref}) for {days} days, ₹{price}.\n\n"
                "Your order is awaiting payment. Please complete it using the "
                "UPI payment option shown on your order page. If you need help "
                "or have already paid, please reply here.\n\n"
                "PureFruit – Naturally Fresh")
    if event == "payment_reported":
        return (f"Hello {name},\n\n"
                f"Thank you for informing us about your payment for PureFruit "
                f"order {ref} (₹{price}).\n\n"
                "Our team is checking the payment in our account. Your subscription "
                "will be confirmed after verification, and we'll share the final "
                "delivery start date and time slot.\n\n"
                "PureFruit – Naturally Fresh")
    return (f"Hello {name},\n\n"
            "Your PureFruit payment has been verified and your subscription "
            "is confirmed.\n\n"
            f"Order: {ref}\n"
            f"Plan: {days} delivery days\n"
            f"Amount received: ₹{price}\n"
            f"Start date: {start}\n"
            f"Delivery slot: {slot}\n"
            f"Address: {tower}, Flat {flat}, L&T Serene County\n"
            "Delivery days: Monday to Friday\n"
            "Bottle size: 300 ml\n\n"
            "Thank you for choosing PureFruit.\n"
            "PureFruit – Naturally Fresh")


def _send_text(token, chat_id, message):
    try:
        response = requests.post(
            f"{API}/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"},
            timeout=(2, 4),
        )
        if response.status_code != 200 or not response.json().get("ok", False):
            LOG.warning("Telegram notification failed: HTTP %s", response.status_code)
            return False
        return True
    except (requests.RequestException, ValueError) as err:
        # Do not log exception text: it could contain a bot credential.
        LOG.warning("Telegram notification failed: %s", type(err).__name__)
        return False

def send_order_alert(event, order, start_date_override=None):
    """Send staff alert + copyable customer draft to private Telegram group.

    For new/payment_reported/paid, the second Telegram message is standalone
    WhatsApp-ready text for staff to copy. Payment state is never changed here.
    """
    token, chat_id = _config()
    if not token or not chat_id:
        return False
    alert = _describe(event, order, start_date_override)
    if not alert:
        return False
    delivered = _send_text(token, chat_id, alert)
    if not delivered:
        return False
    customer_draft = _customer_whatsapp_draft(event, order, start_date_override)
    if customer_draft:
        # Separate Telegram message so staff can copy all text in one tap.
        # Do NOT include order phone numbers in the customer-facing message.
        delivered = _send_text(token, chat_id, customer_draft) and delivered
    return delivered


def discover_groups():
    """Fetch recent group IDs for staff-only setup screen; never returns bot token."""
    token, _ = _config()
    if not token:
        return [], "Add TELEGRAM_BOT_TOKEN to Railway first."
    try:
        resp = requests.get(f"{API}/bot{token}/getUpdates", timeout=(2, 5),
                            params={"limit": 100, "timeout": 0})
        data = resp.json()
        if resp.status_code != 200 or not data.get("ok"):
            return [], "Unable to read bot updates. Check bot token and make sure no Telegram webhook is configured."
        chats = {}
        for update in data.get("result", []):
            message = update.get("message") or update.get("my_chat_member") or {}
            chat = message.get("chat") or {}
            if chat.get("type") in ("group", "supergroup"):
                chats[str(chat["id"])] = _safe(chat.get("title") or "Private group", 100)
        return sorted(chats.items()), None
    except (requests.RequestException, ValueError, KeyError) as err:
        LOG.warning("Telegram group discovery failed: %s", type(err).__name__)
        return [], "Could not fetch recent group messages. Try sending /start in the group and refreshing."
