# PureFruit — Telegram staff notifications

This update is based on the last PureFruit **Dhanteras offer + staff buttons** release. It adds only the backend integrations and a new staff-only setup page. It does not replace existing `static` stylesheets, JavaScript, or ordering HTML. No existing database schema change is needed.

## Install in browser (GitHub)
1. Back up your Railway SQLite database at `/data/orders.db` before updating.
2. In the root of your existing `ParamMandloi/PureFruit` GitHub repository, replace **`app.py`** with this ZIP's `app.py`.
3. Upload **`telegram_alerts.py`** at the repo root (beside `app.py`).
4. Add **`templates/telegram_setup.html`** under the existing `templates` folder.
5. Commit to the branch connected to Railway. Verify the deployment becomes Active. Do **not** upload the enclosing extracted folder.

## Telegram setup
1. In Telegram, open verified **@BotFather**, issue `/newbot`, choose a display name and username ending in `bot`; copy your secret token. **Never paste the token in GitHub or ChatGPT.**
2. Create a **private** group `PureFruit Operations`; add only authorized staff and your bot. Ensure your bot can post messages.
3. Railway > PureFruit service > Variables: add `TELEGRAM_BOT_TOKEN` with the bot token, and deploy the staged variable change.
4. Send `/start` (or `/start@YourBotUsername`) to your bot **in the private group**.
5. While logged into `https://www.purefruit.in/staff`, visit `https://www.purefruit.in/staff/telegram/setup`. It lists group names and IDs seen by your bot. Refresh if needed. Your bot token is not exposed to the browser.
6. Copy the numeric (usually negative) group ID and add it in Railway as `TELEGRAM_CHAT_ID` (include leading minus sign). Deploy staged variable change.
7. Visit the staff-only `/staff/telegram/setup` page again and click **Send Telegram test**. See the test message in the private group.

## Which alerts arrive
- New UPI (and Razorpay) order: order reference, amount, plan, flat, delivery slot and start date. **Payment pending**, not a sale confirmation.
- Customer taps "I've completed payment" or submits a UPI reference: **payment reported, NOT VERIFIED**. Repeated taps do not repeat alerts.
- Staff marks a real PhonePe bank credit verified: **payment confirmed**.
- Staff marks payment not received / check later: group notification.
- No phone numbers or customer names in alerts; use the protected dashboard for full details.

Telegram alerts are best-effort, not a guaranteed notification queue. Telegram downtime or missing bot credentials does **not** interrupt customer orders or staff actions. The website's staff dashboard remains authoritative. Do not share the bot token. Adding the bot to a private group is sufficient to send alerts if it has permission; do not disable group privacy mode unnecessarily.

Official Telegram Bot API: https://core.telegram.org/bots/api
