# PureFruit — No Missed Orders + Safe Test-Entry Deletion

This single combined patch fixes **unreported unpaid checkouts disappearing from the UPI queue** and adds a protected test-order deletion option. It replaces exactly two files in the **repository root paths**, keeping all other code, Telegram drafts, payment flows, fruit photos and CSS unchanged.

## Upload using your GitHub browser
1. Back up Railway's `/data/orders.db` **before** deploying. Deletions are permanent.
2. Replace root `app.py` with the `app.py` in this ZIP.
3. Replace root `templates/admin.html` with `templates/admin.html` in this ZIP.
4. Commit both changes to `main`. Verify Railway is running the new commit.
5. Refresh `https://www.purefruit.in/staff` in an incognito window.

## New queues
- **Awaiting Customer Payment:** any unpaid UPI order even when no customer clicked `I've completed payment`.
- **Payment Reported — Verify Now:** customer says they paid.
- **Payment Follow-ups:** flagged by staff (`Payment Not Received`, `Check Later`) or other unverified states.
- Queues are computed from ALL outstanding UPI orders, independently of All Orders filters/pagination.
- No unverified order enters deliveries. Never mark Payment Received without verifying the bank credit and UTR.

## Delete only unverified cancelled test orders
1. Find the dummy order under **All orders / payment attempts**.
2. Select status **Cancelled**, then click **Save**.
3. If there has been **no** reported or verified payment, bank reference, or Razorpay payment attempt, a **Delete Test Order** button will appear. Confirm the browser prompt.
4. Cancelled orders with reported payments still cannot be deleted; keep these for reconciliation.
5. A minimal audit entry (order reference, deleted time, old status) is retained in the DB without personal details.
6. **Paid, confirmed or completed orders cannot be deleted, even if they are later cancelled.** A new permanent `ever_confirmed` flag preserves this across status changes.

IMPORTANT: Even when an order is still unpaid in the website, someone could have sent a bank transfer late. Check PhonePe Business before using Delete. Never delete genuine customer orders merely to tidy up the queue; cancel or follow up instead.

## Verification
- Create a *test* UPI order and stop on its payment screen without clicking completed. It should appear immediately under Awaiting Customer Payment.
- From staff, click Check Later; it should move to Follow-ups. Click Recheck Payment; it returns to Awaiting.
- Mark a dummy unpaid order Cancelled and reload. Delete Test Order should appear; deleting removes it from reports.
- Check an existing Paid or Confirmed order: it must display Protected and never show a delete button.
- Confirm the old customer payment confirmation flow and Telegram notifications remain functional.

No code in `static/app.js`, `templates/index.html`, `telegram_alerts.py`, `static/style.css`, or Railway environment variables is changed.
