# Convert the working Railway PureFruit site to direct Razorpay checkout

The domain www.purefruit.in and Railway Hobby server already work. Do **not** change DNS, Railway domain or /data volume. Leave `DATABASE_PATH=/data/orders.db` and `sh start.sh` unchanged.

1. Open https://dashboard.razorpay.com/ and create or sign into a PureFruit merchant account.
2. Switch to **Test Mode**, go to **Account & Settings → API Keys** and generate a **Test Key ID** and **Test Key Secret**.
3. Under **Account & Settings → Webhooks**, create a **Test** webhook:
   - URL: `https://www.purefruit.in/api/razorpay/webhook`
   - Events: `payment.captured`
   - Secret: a new long random webhook secret **different from the API secret**.
4. In **Railway → PureFruit → Variables**, add these 3 secrets:
   - `RAZORPAY_KEY_ID` = Test Key ID (starts `rzp_test_`)
   - `RAZORPAY_KEY_SECRET` = Test API Key Secret
   - `RAZORPAY_WEBHOOK_SECRET` = exactly the webhook secret chosen in step 3
   Keep your other 4 variables unchanged, especially the persistent `DATABASE_PATH`.
5. Back up `/data/orders.db`. Upload the updated package's extracted files into the **existing** GitHub repository `purefruit-site`, replacing the old files. Railway deploys the commit automatically.
6. Open www.purefruit.in, submit a dummy order and **use Razorpay's test checkout**. Confirm it appears in `/staff` as `Paid / Confirmed` only if Razorpay marked it `captured`; pending/failed checkout must not appear on a delivery list. Test the webhook event and check Razorpay Dashboard transaction logs. Note: Test mode does not collect real money.
7. Before Live Mode, complete Razorpay onboarding/website approval and the relevant privacy, delivery and cancellation/refund policy pages. Configure Auto Capture.
8. Generate **Live** API keys in Razorpay Live Mode, make a **separate Live webhook** for `payment.captured`, and replace *all three* Razorpay Railway variables. Deploy and reconcile a small first real payment and settlement.

Staff page: `https://www.purefruit.in/staff`

**Do not paste either secret into GitHub, screenshots, emails or chat.** If the payment variables are missing, checkout is disabled rather than falling back to WhatsApp.

## Rollback
If there is a problem, deploy the previous GitHub commit in Railway without deleting or changing the `/data` volume. Do not ask customers to retry a potentially debited transaction; check Razorpay transactions first.
