# Mail Header Extractor V4 — Free / No Card

V4 is optimized for Vercel Hobby: React static frontend + FastAPI Python Function. It has no Docker server, database, Redis, or paid infrastructure requirement.

## Deploy
1. Push this repository to GitHub.
2. Import it into Vercel.
3. Keep the project on Hobby.
4. Do not start Pro/trial or add a payment method.
5. Deploy.

Optional environment variable: `APP_ACCESS_PASSWORD`.

V4 intentionally limits each extraction request to 200 messages because serverless functions have execution limits. Use multiple batches for larger mailboxes.

The app fetches `BODY.PEEK[HEADER]` only. It does not download message bodies. DKIM/SPF/DMARC are read from Authentication-Results when available; they are not independently verified.
