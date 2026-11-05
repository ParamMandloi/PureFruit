# PureFruit — PhonePe static UPI QR checkout (temporary)

This package updates the **existing** PureFruit website. Do not create a new Railway service, GitHub repository or database. Your existing look (top-left PureFruit logo, bottle hero, five fruit photos) is retained.

## What changes

- Customers enter their subscription and delivery details on the website.
- An order ID is saved **unpaid** in the existing SQLite database.
- The website displays the supplied official PhonePe QR of **GREENRISE BUZZ PRIVATE LIMITED** and the plan amount.
- Customers enter their payment-app reference (UPI UTR / transaction reference). That makes the order **Pending verification**, **not Paid**.
- Staff log into `/staff`, check actual incoming credit in **PhonePe Business**, match **amount + reference + recipient**, then type the confirmed reference and amount and check the bank-credit verification box. Only then the order is marked **Paid / Confirmed**.
- The delivery list includes **verified paid orders only**. Existing Razorpay records are preserved.

**This is not automatic payment settlement or instant UPI verification.** A static QR cannot supply authenticated transaction webhook callbacks. A screenshot or customer-provided reference is not proof of funds.

## Before uploading

1. **Backup your existing `/data/orders.db` Railway volume** (or use Railway's verified backup/download facility if available); export `/staff/orders.csv` as an extra human-readable backup.
2. Confirm that the PhonePe QR belongs to the correct legal entity permitted to collect the PureFruit sales proceeds. The customer sees "GREENRISE BUZZ PRIVATE LIMITED" as recipient.
3. Retain your Railway persistent volume mounted at `/data`, `DATABASE_PATH=/data/orders.db`, `SECRET_KEY`, and `ADMIN_PASSWORD`.
4. Optionally add `PAYMENT_MODE=upi_qr` under Railway Variables (the new application also defaults to UPI QR).

## Files to replace in the ROOT of your existing GitHub repo

```text
app.py                    (replace)
templates/index.html      (replace)
templates/admin.html      (replace)
static/app.js             (replace)
static/style.css          (replace)
static/phonepe-qr.jpg     (new: YOUR exact uploaded QR)
```

**Do not upload another parent folder** such as `PureFruit_UPI_QR_Update/`. Do not overwrite `static/fruit/`, your other HTML templates, or `start.sh`.

### Browser-only upload approach

- Open the existing GitHub repository `ParamMandloi/PureFruit` on `main`.
- Press `.` (period) to open the GitHub browser editor, or navigate to `https://github.dev/ParamMandloi/PureFruit`.
- In the editor's Explorer, place the six files in the paths shown above, replacing existing contents. Add the new JPEG to `static/`.
- In **Source Control**, stage files, commit a message such as `Add manual PhonePe UPI QR checkout`, and **Commit & Push** to `main`.
- Confirm your Railway service deploys the new commit and is running.

If dragging into the browser editor does not replace correctly, use the GitHub file pencil editor for each text file and GitHub's **Add file → Upload files** for the JPEG.

## Smoke test

1. Open `https://www.purefruit.in` in a private browser window, pick the 5-day ₹599 plan and enter **test** delivery details.
2. Tap **Place order & show UPI QR**. Verify the image and recipient `GREENRISE BUZZ PRIVATE LIMITED` and amount.
3. The order should exist in `/staff` as **Unpaid**. Do not mark it Paid.
4. Submit a syntactically valid dummy test reference (e.g. `TEST00000000001`); the order should show **Pending verification** but should **not** enter the delivery report.
5. Delete/cancel that dummy test order via your staff status control; do **not** confirm it without a real bank credit.
6. When receiving a real transfer, match the actual credited amount, payee and UTR in PhonePe Business and only then click **Confirm verified payment** in the staff queue.
7. Check the order now shows **Paid / Confirmed**, is included exactly once on the correct delivery date, and appears in CSV.

## A note on dates

If staff verifies a payment after the customer's chosen start date has passed, the application moves the start to the next available weekday so missed days are not silently counted as delivered. Staff should communicate the adjusted date to the customer.

## Existing Razorpay

The old server-side signed Razorpay verification and webhook handlers remain in `app.py` to preserve historical payment handling. The live customer page no longer presents Razorpay checkout. Existing Razorpay keys do not need to be exposed or changed for this manual-QR workflow.

## Known limits

- This uses a static QR: customers must manually pay the displayed amount; QR does not include a dynamically assigned payment amount/order ID.
- Staff must still verify actual credits and must not trust screenshots, UTRs alone or a "payment successful" message shown on a customer's phone.
- No automated WhatsApp capture. Set the standard WhatsApp Business greeting to link customers to `https://www.purefruit.in`, where their orders will be saved.
- The front-end remembers a pending QR across a refresh in the same browser tab. Customers who lose the tab should contact staff with their order number rather than blindly retrying and paying twice.
- Do not use this as a substitute for accounting, invoicing, refund handling or merchant compliance.

### Rollback

Use GitHub history to restore the last working website commit and redeploy. Keep the same Railway volume and do not delete orders. Merely toggling an environment variable is not sufficient to restore the old customer-facing Razorpay page.

## Verification performed while preparing the package

- Python source syntax and JavaScript syntax checks
- Jinja template parse check
- SQLite schema-migration checks, including re-running migration
- Simulated Python core business-flow checks for new order → pending verification → staff confirmation; duplicate-confirmation and duplicate-UTR protection

A real Railway deployment and live PhonePe transaction have **not** been tested here. Test the actual website and bank-reconciliation process before opening it to customers.
