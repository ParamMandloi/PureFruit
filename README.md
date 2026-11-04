**Railway Hobby hosting instructions:** see [RAILWAY_SETUP.md](RAILWAY_SETUP.md). The service binds `0.0.0.0:$PORT` using `sh start.sh`.

# PureFruit — WhatsApp-first ordering website

A simple, phone-friendly PureFruit website based on the existing grey / white / black flyer (color only in fruit illustrations). Includes a **private staff order report**. No WhatsApp API, payment gateway or subscription software is required.

## What it does

- Shows fixed Monday–Friday menu: Mosambi, Pineapple, ABC, Orange and Watermelon.
- Lets customers choose 5 days (₹599), 10 days (₹1,099), or 20 days (₹1,999).
- Lets customers choose one fixed daily slot (8–9 AM or 4–5 PM), a weekday start date, and their L&T Serene County flat.
- Saves an **order request** into the SQLite database before redirecting the customer to a prefilled WhatsApp message for **+91 80740 62979**.
- Provides a password-protected **`/staff`** dashboard with order and payment status updates, statistics, daily delivery lists and CSV downloads.
- The daily list automatically counts delivery days on Monday–Friday only, starting from the customer's selected date. Staff must first change the request status to *Confirmed*.

## Important limitations

**Clicking to WhatsApp does not prove a message was sent.** The dashboard records customer requests / handoffs. A team member must check WhatsApp, confirm the order, collect payment separately and mark the order *Confirmed* and *Paid* in `/staff`. The website does not read WhatsApp messages or confirm payment automatically.

The first available delivery is November 6, 2026. No weekend deliveries. This starter app does not handle public holidays or skipped deliveries; adjust dates manually if needed. The start date is a requested start date, not a confirmed delivery commitment.

## Run locally

Requires Python 3.10+.

```bash
cd purefruit-website
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
```

Edit `.env`. Set **ADMIN_PASSWORD** to a long, unique password and **SECRET_KEY** to a random string. The provided `start.sh` reads `.env` automatically:

```bash
./start.sh
```

Visit **http://127.0.0.1:5000** for the ordering page and **http://127.0.0.1:5000/staff** for the staff report. Windows users can set environment variables and run `python app.py`.

**For a quick layout preview without installing anything:** open `PureFruit-preview.html` in your browser. Preview mode goes to WhatsApp but **cannot save order requests** to the report.

## Deploy to purefruit.in

This is a Python/Flask web application and needs a Python-compatible host **with persistent storage** (for its SQLite database), or a server you control. A static-only host will display the page but cannot run the order report.

Recommended production setup:

1. Host it on a server with HTTPS and a **persistent** directory, e.g. `/var/lib/purefruit`.
2. Set environment variables `ADMIN_PASSWORD`, `SECRET_KEY`, `HTTPS_ONLY=1`, and `DATABASE_PATH=/var/lib/purefruit/orders.db`. Do **not** commit `.env` or `orders.db`.
3. Run it with `waitress-serve --host=127.0.0.1 --port=5000 app:app` behind a reverse proxy such as nginx/Caddy, which terminates HTTPS.
4. Point your domain `purefruit.in` to your hosting provider using its DNS setup instructions.
5. Protect and regularly back up the database. It contains customer names, numbers, tower and flat numbers; limit staff access to the password and follow applicable privacy/data protection obligations.

For hosting that uses ephemeral file storage, configure a persistent disk / managed database first. Otherwise orders will disappear after a redeploy. Use TLS/HTTPS when live.

## Brand and commercial settings

- WhatsApp number: `WHATSAPP_NUMBER` in `app.py` and the support link in `templates/index.html`.
- Plans: `PLANS` in `app.py` and plan cards in `templates/index.html` / `static/app.js`.
- Menu: fruit names and images in `templates/index.html`, daily fruit lookup in `app.py`.
- Location, fixed slots, and other text: `templates/index.html`.

**Security note:** public form uses a per-session CSRF token and bot honeypot; the staff interface uses password login and CSRF checks. Before internet-scale deployment, add rate limiting, monitoring, and stronger staff authentication if needed.
