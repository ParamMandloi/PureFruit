# PureFruit: 25% promotion + staff payment review buttons

## Offer window (India time)

- Starts: **Thursday, 5 November 2026, 4:00 PM IST**. Deploy after this time and the offer activates as soon as the deployment is running.
- Ends: **Saturday, 7 November 2026, 12:00 AM IST** (i.e., the end of Friday, 6 November at 11:59:59 PM).
- Prices during offer: **5 days ₹449; 10 days ₹824; 20 days ₹1,499**.
- Prices starting 7 November: **₹599, ₹1,099, ₹1,999** automatically, no deployment needed.
- Existing orders retain the price saved when the order was created. UPI QR reads the saved amount, not today's new price.
- Only Monday–Friday start dates; earliest start is **next weekday after the booking date**. The server checks this as well as the dropdown.

## Staff controls

- **Payment Received — Confirm**: Requires the actual bank UTR, matching paid amount, and explicit check of bank credit; then activates subscription.
- **Payment Not Received**: Records review note/status, places order in follow-up without adding any deliveries.
- **Check Later**: Places order in follow-up without adding any deliveries.
- **Recheck Payment**: Returns follow-up order to awaiting-verification queue. Staff may also verify a late-arriving payment directly from follow-up.
- Existing paid records and Razorpay transactions stay unchanged.

## Browser-only upload — IMPORTANT

The ZIP contains only these FOUR REPLACEMENT FILES, in their exact repository paths:

1. `app.py` — repository root
2. `templates/index.html` — templates folder
3. `templates/admin.html` — templates folder
4. `static/app.js` — static folder

**Do not replace `static/style.css`.** The promo and staff button styles are included inside the two updated HTML templates to preserve the fixed bottle/hero styling.

1. Back up your Railway database (`/data/orders.db`) first if you have real orders. Keep the Railway `/data` volume attached.
2. Extract the ZIP on your computer, then go to your GitHub PureFruit repo and select the `main` branch.
3. For each file above, open the corresponding existing path in GitHub, click the pencil icon, replace **all** content with the matching extracted file, and commit to `main`. Alternatively, use github.dev to replace the same four paths; no nested folder.
4. Railway should automatically deploy the newest commit. Confirm its deployment is Active before taking orders.
5. In a private browser window, open https://www.purefruit.in. Confirm 25% OFF banner and amounts ₹449 / ₹824 / ₹1,499, and that the earliest start is the next weekday.
6. Place a dummy order, press I've completed payment (do **not** make an actual UPI payment for this test), then check https://www.purefruit.in/staff. Press **Check Later**, **Recheck Payment**, and **Payment Not Received** in turn. Confirm none of these mark the order Paid or add it to deliveries. Do not press Payment Received unless you verify a real bank credit and UTR.
7. At 12:00 AM on 7 November IST, the website will revert to its regular prices. For orders created before then, old amounts remain locked.

Do not upload API credentials, `.env`, or your SQLite order database to GitHub. This ZIP contains no secrets or real customer data.

## Notes

- Payment is still by UPI QR, subject to manual verification in PhonePe Business.
- The package is **not deployed yet**. A GitHub commit and successful Railway deployment are required to activate it.
- Unit checks in this workspace covered offer boundaries, pricing, weekday selection and template/JavaScript syntax; a live Railway end-to-end payment test is still needed.
