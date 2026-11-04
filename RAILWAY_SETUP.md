# Deploy PureFruit on Railway Hobby

The production site and staff order report are served by one small Python/Flask service with SQLite on a Railway persistent volume. Customers complete their order by sending the prefilled WhatsApp message. Website requests do NOT prove a WhatsApp message was sent.

## Deploy steps

1. Create a PRIVATE GitHub repository named `purefruit-site` and upload the contents of this directory to the repository root (do not upload only the ZIP). Never upload `.env`, any `orders.db` file, or passwords.
2. In [Railway](https://railway.com), select Hobby, create a project, choose **Deploy from GitHub repo**, authorize your private repository and select `purefruit-site`.
3. Open the new web service > **Settings > Deploy** and set its **Custom Start Command** to `sh start.sh`. The Python dependencies are detected from `requirements.txt` automatically.
4. Open **Variables**. Add the following variables (choose your own strong random values):

   | Name | Value |
   | --- | --- |
   | `ADMIN_PASSWORD` | A long unique staff password (16+ characters) |
   | `SECRET_KEY` | A securely generated random 64-character value |
   | `HTTPS_ONLY` | `1` |
   | `DATABASE_PATH` | `/data/orders.db` |

   Do not set `PORT`: Railway provides it. Do not commit secrets to GitHub.
5. Attach a **Volume** to the WEB SERVICE (not a new database): in the project canvas right-click > New Volume > select your web service > set **Mount Path** to `/data`. This is essential; the app stores customer orders at `/data/orders.db`. Without a persistent volume, orders disappear on redeploy. Keep just 1 app replica when using SQLite.
6. Deploy/redeploy the web service. Check logs for startup errors. Under service > **Settings > Networking**, click **Generate Domain**. Test the generated `*.up.railway.app` URL and its `/staff` login.
7. Create a test order and tap the WhatsApp link. Confirm the prefilled WhatsApp recipient is `+91 80740 62979`. The report at `/staff` will show the request. Manually check WhatsApp received the order; manually mark as confirmed and paid.
8. Under **Settings > Networking > + Custom Domain**, add `www.purefruit.in` (and optionally `purefruit.in`; Hobby supports two custom domains per service). At your domain registrar/DNS provider, copy **both** the CNAME and TXT verification records Railway displays. Do not guess DNS values. Use the host recommended by Railway for apex/root domains, where DNS support varies. Railway provisions HTTPS after domain verification.
9. After launch, monitor usage and set Railway spend alerts/limits. Configure a periodic SQLite volume backup; a persistent volume is not itself an offsite backup.

## Caveats

- If users open WhatsApp but never send the message, their row still appears in the staff dashboard as a **request**; payment is **not** automatic.
- The delivery calculation excludes weekends but not public holidays or manually paused service.
- Hosting a real customer database means the site needs a privacy notice and access restricted to responsible staff. For launch, use the password-only dashboard cautiously. Add sign-in throttling before wider adoption.
- The `PureFruit-preview.html` file is a standalone UI demonstration only; deploy the full app in this folder, not the preview HTML alone.

Official Railway references:
- https://docs.railway.com/pricing/plans
- https://docs.railway.com/volumes
- https://docs.railway.com/networking/domains/working-with-domains
- https://docs.railway.com/networking/troubleshooting/application-failed-to-respond
