# PureFruit · My Orders + Archive Fix

## What changes

- **My Orders** at `https://www.purefruit.in/my-orders` shows orders associated with this browser. A private, HTTP-only 180-day cookie is set when a new order is created. Orders are retrieved from the server database, not merely from browser local storage.
- Unpaid UPI orders have a **Pay Now** button, which loads the *same order reference, original ₹ amount and original UPI QR*. Payment-reported orders show **Awaiting verification**, to avoid double payment. Paid orders show their schedule and remaining weekday deliveries.
- If the same browser resubmits the same unpaid checkout (same customer, plan, tower/flat, slot, date), PureFruit **reuses** the existing order instead of creating a duplicate.
- Earlier orders created **before** this update may be recovered **individually** using their full order reference plus their registered mobile number. This does not unlock every order associated with that number. Recovery attempts are rate-limited. Full cross-device account history requires a properly verified phone OTP login in a future iteration.
- **Staff > Archived Orders** hides historical cancelled orders and paid subscriptions whose delivery dates are over from the active list. Archive does **not** delete or change payment records, and archived orders can be restored.
- Existing safe Delete Test Order rules remain: once an order was paid/confirmed or a payment was reported, it cannot be permanently deleted. You may cancel and archive it instead, after checking bank credits and any obligations.
- PhonePe QR, Razorpay verification code, Telegram alerts and styling for fruit/bottle are unchanged.

## Files to replace in the SAME existing paths

1. `app.py` → root `app.py`
2. `templates/index.html` → `templates/index.html`
3. `templates/admin.html` → `templates/admin.html`
4. `templates/my_orders.html` → **new file** under existing `templates/`
5. `static/app.js` → `static/app.js`

Do not upload the surrounding ZIP folder, and **do not replace `style.css`, `telegram_alerts.py`, or Railway environment variables**.

### Install (browser-only)

1. **Back up `/data/orders.db` on Railway first**; do not delete/recreate Railway's persistent volume or service. This update adds database tables/columns on first startup, preserving all existing records.
2. Extract ZIP on Mac. Use GitHub browser editor to replace the four existing files at their exact paths. To create the new `templates/my_orders.html`, open `templates/` → **Add file → Create new file**, paste the new template contents, and commit. If dragging files, drag their **contents** into the correct folders, not a nested parent folder.
3. Commit to `main` of `ParamMandloi/PureFruit` and verify Railway creates a fresh deployment showing Active. Check **Deploy Logs** for errors.
4. In a fresh browser session, place a **test unpaid order**, open My Orders, confirm Pay Now loads the original price/QR. Repeat identical checkout on same browser: no second order should be generated; check Telegram is not duplicated.
5. In My Orders, tap Pay Now; return without paying, and verify it remains unpaid. Tap "I've completed payment" only on this marked test order; **do not mark it Payment Received** without a genuine PhonePe credit.
6. Test staff Archive with a **cancelled test order**, check Archived Orders and Restore. Check active unpaid orders stay in their payment attention queues.
7. Verify paid/confirmed orders do not have a **Delete Test Order** option.

## Important limitations

- Order history is tied to the customer's **browser/device**. Another device or cleared cookies won't automatically show the whole history. A customer can recover a *specific older order* using their order reference + registered phone. No one can search other people's orders by phone number alone.
- Amount and payment status remain authoritative on Railway. A click on "I've completed payment" is only a customer claim, not proof of payment; staff should check PhonePe Business before confirmation.
- Do not archive an order with an unresolved payment or ongoing deliveries. If a customer claims payment, check the bank credit before cancelling it.
- This version adds no OTP supplier, recurring payment subscription, or gateway. No new Railway settings are needed.

## Tests performed for this package

- Python syntax and JavaScript syntax checks
- Offline SQLite migration, customer-token ownership, duplicate order prevention, individual order recovery, archive/restore, paid-order deletion protections
- Jinja2 template compilation and customer history rendering

This is **not** a live Railway deployment or end-to-end browser/payment test. Verify on the live test account before using it for customers.
