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
        "Plan: " + _safe(order.get("plan_days"), 3) + " days | ₹" + _safe(order.get("price"), 12),
        "Location: " + _safe(order.get("tower"), 50) + " / Flat " + _safe(order.get("flat"), 20),
        "Delivery: " + slot + " | Start " + _safe(start, 15),
        "Staff dashboard: " + STAFF_URL,
    ])


def send_order_alert(event, order, start_date_override=None):
    """Returns True only on Telegram acknowledgement. Failure does not affect orders."""
    token, chat_id = _config()
    if not token or not chat_id:
        return False
    message = _describe(event, order, start_date_override)
    if not message:
        return False
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
        # Never log URLs or exception objects: requests errors may contain the bot token.
        LOG.warning("Telegram notification failed: %s", type(err).__name__)
        return False


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
