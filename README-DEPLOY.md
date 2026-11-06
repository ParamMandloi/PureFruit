# PureFruit — Refresh / Return Home + Telegram WhatsApp drafts

This is a **front-end + Telegram-only** change. No payment, price, staff, database or Razorpay backend files are replaced.

## Files to update in the existing GitHub repository

* `static/app.js` — removes automatic checkout restoration after browser reload; retains an optional Resume previous payment control and adds Back to Home / Start a new order.
* `templates/index.html` — preserves the current fast mobile UPI layout and updates the JavaScript asset version to avoid stale browser caches.
* `telegram_alerts.py` — keeps current staff alerts, then sends an **additional separate copy-ready WhatsApp draft** in the *private Telegram group* for three events: new order (unpaid), payment reported (not yet verified), and payment verified (confirmed). It does not send WhatsApp messages automatically.

## Browser-only deployment

1. Back up `/data/orders.db` before any deployment, as usual.
2. Extract this ZIP to Downloads.
3. In github.com/ParamMandloi/PureFruit on `main`, replace the exact three files at the paths above. Do **not** upload a new enclosing folder.
4. Commit directly to main and confirm Railway runs the latest commit.
5. Refresh the site and test a new unpaid order using clearly marked test details. The checkout should show the UPI options.
6. Refresh the page: the homepage/order form should be available again, with a small **Resume previous payment** option. Selecting Resume returns to the existing order without creating a new one; Dismiss forgets only the browser's saved reference.
7. Place another test order and tap **I've completed payment** without sending actual money. Confirm Telegram receives two messages: staff status + copy-ready WhatsApp draft. The order must remain Pending Verification in `/staff`.
8. In `/staff`, use the test order's Check Later / Payment Not Received as needed. To test the final confirmed draft, **only** mark Payment Received after matching a genuine credit and UTR in PhonePe Business. Do not mark an unpaid test order paid.

## Safety and scope

* Do not share Telegram bot credentials or API secrets in GitHub.
* Group messages contain the customer name and mobile number in the private operations group to allow manual WhatsApp follow-up; restrict membership to approved staff and avoid forwarding internal alerts externally.
* Staff must verify a real credited amount and UTR before confirming any order. Tapping I've completed payment does **not** confirm funds.
* An existing order is not cancelled or deleted when a customer returns to Home or dismisses the Resume link.
* If the user has since edited these two front-end files, merge the changes instead of replacing later work.
