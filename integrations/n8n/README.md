# n8n integration example

`docflow-email-intake-and-review-notifications.json` is an importable n8n workflow
demonstrating the two integration points most SMB customers actually ask for:
getting documents in without using the UI, and finding out when something needs a
human without polling the dashboard. It is a reference to adapt, not a drop-in
production config — see the caveats below.

## What it does

**Branch 1 — email intake.** An IMAP trigger watches an inbox. Emails with an
attachment are uploaded to `POST /api/v1/documents` using an API key. This is the
"forward invoices to docs@yourcompany.com" pattern.

**Branch 2 — review notifications.** A webhook endpoint receives Docflow's
`document.processed` / `document.needs_review` events, verifies the
`X-Docflow-Signature` HMAC, and posts to Slack when a document needs a human,
linking straight to it in the app.

## Setup

1. Import the JSON into n8n (Workflows → Import from File).
2. Create an IMAP credential for the intake inbox and select it on the **Email
   Trigger (IMAP)** node.
3. Create a Docflow API key (Settings → API Keys in the app, or
   `POST /api/v1/settings/api-keys` — see [docs/API.md](../../docs/API.md)).
   Add it to n8n as an **HTTP Header Auth** credential named `Docflow API Key`
   with header `Authorization` = `Bearer dfk_...`, and select it on the
   **Upload to Docflow** node.
4. Activate the workflow, then copy the **Docflow Webhook** node's Production
   URL.
5. Register that URL with Docflow:
   ```bash
   curl -X POST "$DOCFLOW_API_URL/api/v1/settings/webhooks" \
     -H "Authorization: Bearer $DOCFLOW_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
           "url": "<the n8n webhook Production URL>",
           "events": ["document.processed", "document.needs_review"]
         }'
   ```
   The response includes a signing secret **shown once**. Set it as the
   `DOCFLOW_WEBHOOK_SECRET` environment variable on the n8n instance.
6. Set `DOCFLOW_API_URL` and `DOCFLOW_APP_URL` environment variables on the n8n
   instance (API base URL and frontend base URL, respectively).
7. Create a Slack credential and pick a channel on the **Notify Reviewer**
   node, or swap it for email/Teams/Discord — the branching logic upstream
   doesn't care what the notification step is.

## Caveats

- **Node parameter shapes are version-sensitive.** This targets n8n's HTTP
  Request v4 / IF v2 / Webhook v2 node schemas. On a different n8n version,
  some fields may need re-selecting in the node UI after import — the overall
  wiring and flow logic will still be correct.
- **Signature verification re-serializes the parsed JSON body** to reconstruct
  the string Docflow signed. That matches Docflow's compact
  `json.dumps(..., separators=(",", ":"))` output in the common case, but
  isn't guaranteed byte-identical in general. For production use, enable "Raw
  Body" in the Webhook node's options and verify against the raw buffer
  instead — see the comment in the **Verify Signature** code node.
- This is one workflow with two independent triggers for convenience. Split it
  into two workflows if intake and notifications should scale, fail, or
  deploy independently.
