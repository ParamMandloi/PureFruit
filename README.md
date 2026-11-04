# PureFruit — Website Checkout + Private Staff Report

Mobile-first subscription ordering for **www.purefruit.in**, using **Razorpay Standard Checkout**, **Flask**, **SQLite on Railway's mounted `/data` volume**, and a password-protected **/staff** page.

**The customer pays on the website. WhatsApp is only a help link.** Subscription is confirmed only after the server receives a verified, *captured* Razorpay payment. Staff cannot mark an unpaid order as paid by changing a dropdown. The backend keeps legacy records created by the previous WhatsApp version.

## Customer journey

1. Select 5 days (₹599), 10 days (₹1,099) or 20 days (₹1,999), one fixed weekday delivery slot (8–9 AM or 4–5 PM), a start date and name/phone/tower/flat.
2. Server creates Razorpay order for the **server-owned** plan price (in paise) and saves a `payment_pending` row in SQLite. No amount from the browser is trusted.
3. Customer pays through official Razorpay Checkout.
4. Browser sends Razorpay's signed checkout result to the server. The server validates the HMAC signature, fetches the payment from Razorpay and checks its **captured** status, expected amount, currency and Razorpay order ID.
5. Razorpay's signed `payment.captured` webhook can mark it paid even if the customer closes the browser. Webhooks are idempotent.
6. The staff report shows verified paid orders, transaction IDs and delivery lists, and exports CSV.

### Limitations to handle before taking live money

- **Auto-capture must be enabled in the Razorpay dashboard**. An `authorized` payment is **not** a completed subscription.
- These are **prepaid packages**, not automatic recurring deductions. Renewal and refund workflow are not automated.
- Confirm delivery eligibility, capacity, holidays, cancellation/refund rules, customer support and mandatory business/policy pages for Razorpay approval before accepting live orders.
- An uncaptured/pending payment is not a confirmed order. If a payment was debited but not captured, check the Razorpay dashboard rather than asking the customer to pay twice.
- Keep a reliable **offsite database backup**, HTTPS and a single Railway replica when using SQLite. Restrict staff password access; implement more robust login protection/rate limiting for wider launch.
- The database schema is migrated in place; existing rows are not deleted. Back up `/data/orders.db` before upgrading.
- This package is a ready-to-deploy implementation, **not** a completed live end-to-end payment test. Configure Razorpay and test simulated payments first.

## Railway configuration

Set these 7 variables **in Railway**, not in GitHub:

```env
ADMIN_PASSWORD=<existing strong staff password>
SECRET_KEY=<existing private session secret>
HTTPS_ONLY=1
DATABASE_PATH=/data/orders.db
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=<test API key secret>
RAZORPAY_WEBHOOK_SECRET=<new separate webhook secret>
```

The **three Razorpay values** must be provided before activating this version; until then the Pay button is disabled. Do not share secrets in ChatGPT or include them in screenshots. Keep existing ADMIN_PASSWORD, SECRET_KEY and database volume settings unchanged.

**Razorpay → Account & Settings → Webhooks** (in **Test** mode):
- **Webhook URL:** `https://www.purefruit.in/api/razorpay/webhook`
- **Event:** `payment.captured`
- **Secret:** create a long random value and put the exact same value in Railway's `RAZORPAY_WEBHOOK_SECRET`.

If you configure webhook from the Railway URL instead, use `https://purefruit-production.up.railway.app/api/razorpay/webhook` consistently. Do not put credentials in the webhook URL.

## Updating the existing GitHub repository

1. Back up the Railway SQLite volume first. Keep the current live deployment working until you have Test credentials and webhook configured.
2. Unzip the package and upload its **contents** into the **root of your existing `purefruit-site` GitHub repo**, replacing matching old files. Do not create a second repository or upload the ZIP as-is.
3. Ensure `app.py`, `requirements.txt`, `start.sh`, `static/` and `templates/` exist at the repository root.
4. Railway deploys new commits from GitHub. Keep Custom Start Command `sh start.sh`; keep `/data` volume and existing `www.purefruit.in` custom domain.
5. Run test transactions on the website with Razorpay **Test** keys. Check successful, failed, cancelled, delayed/webhook and duplicate events. Compare `/staff` and Razorpay Dashboard. Only paid orders must appear in delivery CSV.
6. After Razorpay account activation, generate **Live** keys, configure a **Live** webhook with a new secret, replace all three Railway payment variables with Live values and redeploy. Run a small live payment and reconcile settlement before advertising checkout.

**Important:** Test and Live credentials/webhooks are separate. `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` are two different secrets.

## Technical routes

- `/`: order form
- `/api/orders` POST: create Razorpay order + local pending order
- `/api/orders/<reference>/verify` POST: confirm via signed Razorpay result + server fetch
- `/api/orders/<reference>/status` GET: read paid/pending status (no customer details)
- `/api/razorpay/webhook` POST: signature-verified `payment.captured` handling
- `/staff`: private report, order statuses, paid values and CSV exports

Plans and fruit schedule are in `app.py`, `templates/index.html` and `static/app.js`. Monday–Friday menu: Mosambi, Pineapple, ABC, Orange, Watermelon. 300 ml bottles. No custom schedule.

**Security:** API key secret is server-only; Key ID is publicly visible by design. Verify all bank/payment changes in Razorpay Dashboard. Never store customer card details.

Official docs:
- https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/
- https://razorpay.com/docs/webhooks/
- https://razorpay.com/docs/server-integration/python/test-app/
