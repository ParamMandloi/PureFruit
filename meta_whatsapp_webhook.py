"""PureFruit official Meta WhatsApp Cloud API webhook (inbound-only, stage 1).

Safe to add alongside existing website routes and subscription tables.
Does NOT send outbound messages or change order/payment status.
"""

import hashlib
import hmac
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

meta_whatsapp_bp = Blueprint("meta_whatsapp", __name__)
LOG = logging.getLogger(__name__)


def _database_path():
    # Mirrors PureFruit app.py's existing database selection.
    return os.environ.get("DATABASE_PATH") or str(Path(__file__).resolve().parent / "orders.db")


def _database():
    con = sqlite3.connect(_database_path(), timeout=5)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("""CREATE TABLE IF NOT EXISTS meta_whatsapp_inbound (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT NOT NULL UNIQUE,
        sender_wa_id TEXT NOT NULL,
        phone_number_id TEXT,
        message_type TEXT NOT NULL,
        message_text TEXT,
        media_id TEXT,
        received_at TEXT NOT NULL,
        processed INTEGER NOT NULL DEFAULT 0
    )""")
    return con


def _message_content(msg):
    typ = msg.get("type", "unknown")
    if typ == "text":
        return (msg.get("text") or {}).get("body", ""), ""
    if typ == "button":
        btn = msg.get("button") or {}
        return btn.get("payload") or btn.get("text") or "", ""
    if typ == "interactive":
        reply = msg.get("interactive") or {}
        item = reply.get("button_reply") or reply.get("list_reply") or {}
        return item.get("id") or item.get("title") or "", ""
    media = msg.get(typ) or {}
    return media.get("caption") or "", media.get("id") or ""


@meta_whatsapp_bp.route("/webhooks/meta-whatsapp", methods=["GET", "POST"])
def meta_whatsapp_webhook():
    if request.method == "GET":
        configured = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
        supplied = request.args.get("hub.verify_token", "")
        mode = request.args.get("hub.mode", "")
        challenge = request.args.get("hub.challenge", "")
        if configured and supplied and mode == "subscribe" and challenge and hmac.compare_digest(configured, supplied):
            return Response(challenge, status=200, mimetype="text/plain")
        return Response("Forbidden", status=403, mimetype="text/plain")

    app_secret = os.environ.get("META_APP_SECRET", "")
    raw_body = request.get_data(cache=True)
    supplied_signature = request.headers.get("X-Hub-Signature-256", "")
    if not app_secret or not supplied_signature.startswith("sha256="):
        return Response("Forbidden", status=403)
    expected_signature = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, supplied_signature):
        return Response("Forbidden", status=403)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return jsonify({"ok": True, "saved": 0}), 200

    inserted = 0
    received = datetime.now(timezone.utc).isoformat()
    try:
        with _database() as con:
            for entry in payload.get("entry") or []:
                for change in entry.get("changes") or []:
                    if change.get("field") != "messages":
                        continue
                    value = change.get("value") or {}
                    number_id = (value.get("metadata") or {}).get("phone_number_id", "")
                    # Status-only webhooks must never create customer orders.
                    for msg in value.get("messages") or []:
                        message_id = msg.get("id", "")
                        sender = msg.get("from", "")
                        if not message_id or not sender:
                            continue
                        typ = msg.get("type", "unknown")
                        content, media_id = _message_content(msg)
                        cursor = con.execute("""INSERT OR IGNORE INTO meta_whatsapp_inbound
                            (message_id, sender_wa_id, phone_number_id, message_type,
                             message_text, media_id, received_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (message_id, sender, number_id, typ, content, media_id, received))
                        inserted += cursor.rowcount
    except sqlite3.Error:
        LOG.exception("Meta WhatsApp inbound persistence failed")
        return jsonify({"ok": False}), 503  # Non-2xx asks Meta to retry.

    # Never log private messages, phone numbers, tokens or media IDs.
    LOG.info("Meta WhatsApp signed webhook processed; new inbound messages=%d", inserted)
    return jsonify({"ok": True, "saved": inserted}), 200
