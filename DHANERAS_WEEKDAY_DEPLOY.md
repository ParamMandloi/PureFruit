# PureFruit — Dhanteras offer + weekday-only start dates

Deployment package for the existing **PureFruit UPI Express / Railway** website.

## Date and prices

India time zone (`Asia/Kolkata`) is always used by the Flask server.

- Nov 5: ₹599 / ₹1,099 / ₹1,999.
- **Friday, Nov 6, 2026, 00:00–23:59:59 IST: ₹449 / ₹824 / ₹1,499** (25% off, rounded down to whole rupees).
- Nov 7 and later: automatically back to ₹599 / ₹1,099 / ₹1,999.

The discount is based on the date the **order is created** (server time), not the customer's phone clock or selected delivery start date. Once an order has been placed, its price is fixed and its payment QR keeps that fixed amount even after midnight. Payment claims remain pending until PhonePe Business is checked. Do not mark unverified bank credits as paid.

## Weekend exclusion

The start-date field is now a **weekday-only dropdown** (the next 40 valid Monday–Friday dates). Saturday and Sunday never appear. The server also validates weekday start dates independently. If payment verification takes time, the existing staff confirmation logic moves the start date to the next available weekday.

## Deploy from GitHub browser

1. Back up your existing Railway volume database `/data/orders.db` before changing code; keep the prior GitHub commit available for rollback.
2. Unzip the package on your Mac.
3. In your existing GitHub repository `ParamMandloi/PureFruit` on `main`, replace **only** these files at the root paths (do not upload the enclosing folder):
   - `app.py`
   - `templates/index.html`
   - `static/app.js`
   - `static/style.css`
4. Commit to `main`; Railway should redeploy the latest commit.
5. Test order page on mobile: check the weekday-only dropdown and the correct discounted price **on Nov 6 IST**. On Nov 5 the regular prices should display; there is no need to alter Railway system time or upload another version on Nov 6.
6. Make a test order and confirm the amount on the generated UPI QR equals the saved order amount. Do not mark test orders paid without verifying real funds received.

**No changes** are made to the QR payee, existing secret keys, Railway volume, database schema, checkout phone number, admin dashboard, fruit pictures, logo, or bottle layout. Do not change these settings to enable the promotion.

### Source and caching

The server is the authority for plan price; order handlers ignore prices supplied by the browser. Checkout config updates on page load, when users return to the tab and every minute, with a final refresh before submission. New orders created after midnight automatically use the appropriate day's price.

### Rollback

If an issue occurs, revert the last GitHub commit so Railway deploys the prior code. The persistent `/data` volume must remain attached; **do not delete it**.
