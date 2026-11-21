PUREFRUIT — META WHATSAPP DIRECT WEBHOOK — STAGE 1
================================================
Goal: connect the Meta test number to your own Railway-hosted Flask app.
The integration is inbound-only. It does NOT send replies, create customer orders,
mark payments as paid, or change delivery status. Those functions are the next stage.

1. BACKUP
   Back up the Railway volume database /data/orders.db before updating code.

2. GITHUB (main branch)
   - Add meta_whatsapp_webhook.py to the same folder as app.py.
   - In your existing app.py, immediately after the `app = Flask(__name__)` line,
     register the new blueprint:

       from meta_whatsapp_webhook import meta_whatsapp_bp
       app.register_blueprint(meta_whatsapp_bp)

   - Keep all existing app.py routes, database logic, and existing Interakt webhook.
     The Meta webhook has a separate path, so it won't conflict.
   - Commit the change and wait for Railway to deploy.

3. RAILWAY VARIABLES (PureFruit service > Variables)
   - META_WEBHOOK_VERIFY_TOKEN = a long unique random phrase of your choosing
     (use at least 32 random characters; DO NOT reuse API/access tokens).
   - META_APP_SECRET = App Settings > Basic > App Secret (get from Meta)
     (DO NOT paste the secret into GitHub or ChatGPT).
   - DATABASE_PATH = /data/orders.db (keep your existing value)
   - Verify the deploy succeeds. HTTPS_ONLY and other existing variables stay unchanged.

4. META DASHBOARD > STEP 2: PRODUCTION SETUP > CONFIGURE WEBHOOKS
   - Callback URL: https://www.purefruit.in/webhooks/meta-whatsapp
   - Verify token: EXACT value of META_WEBHOOK_VERIFY_TOKEN in Railway
   - Leave "Attach a client certificate" OFF for this initial setup.
   - Click "Verify and save".
   - Find webhook fields and subscribe to `messages`.

5. VERIFY
   - The success check is Meta's "Verify and save" completing without error.
   - Your app is currently UNPUBLISHED: as Meta's warning says, its dashboard
     test-webhook feature is the reliable initial webhook test. Don't assume
     real customer message events will arrive until Meta enables them.
   - The Meta webhook Test button (messages field, if available) can deliver
     a signed sample event. See Railway Deployments > View Logs for:
       Meta WhatsApp signed webhook processed; new inbound messages=...
   - Test data goes to separate table: meta_whatsapp_inbound in orders.db.

SECURITY & RELIABILITY
   - The GET verification token and POST signature use DIFFERENT secrets.
   - Every POST is checked with X-Hub-Signature-256 HMAC-SHA256, using the
     Meta APP SECRET. Unverified messages are rejected.
   - Messages are stored once by Meta message ID; repeated deliveries ignored.
   - Delivery-status events do NOT become incoming orders.
   - No customer personal data or secrets are written to server logs.
   - 403 means invalid token or signature; 404 means code not deployed/path wrong;
     500/503 means backend/database trouble; 200 means verification OK.

AFTER STAGE 1
   Build official Cloud API outbound replies + existing PureFruit staff order/fulfillment
   dashboard using captured messages; retire Interakt only once live number migration
   or coexistence eligibility and production readiness are confirmed.
