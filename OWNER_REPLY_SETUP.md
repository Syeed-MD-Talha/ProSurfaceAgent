# Owner replies for customer questions

For a service question the bot cannot confirm, web chat saves a pending request
and immediately tells the customer that the team will confirm. A background
worker emails the owner a private link. The owner writes the exact customer
answer on that page. It appears automatically in the original browser chat,
without another paid model call. Replies go only to that conversation.

The customer can return using the same browser for 30 days. A server restart
requires signing in again, but preserves that browser's chat and pending answers.
Clearing cookies or moving to a different browser opens a separate conversation.
Photos remain governed by the existing upload privacy settings; refreshed history
shows attachment names rather than storing photo bytes in this new database.

## Deployment settings

Settings are loaded from the environment / existing `.env` on server startup.
This implementation does not read, print, or edit the `.env` file through admin
tools. The following names document the settings for deployment management:

- Existing email settings: `OWNER_NOTIFY_EMAIL`, `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT` (default `587`, STARTTLS).
- `OWNER_REPLY_BASE_URL`: the final HTTPS website origin, for example
  `https://chat.example.com`. Set this when deploying to Hostinger. The web
  application must actually be reachable at that address and forward these routes.
  No public address is guessed from incoming request headers. During local use,
  an empty value generates `http://127.0.0.1:<server-port>` links that open on
  the same computer only.
- `OWNER_REPLY_TOKEN_TTL_HOURS`: positive integer; default `168` (seven days).
- `WEB_STATE_DIR`: optional persistent writable directory. Default:
  `QA_agent/.prosurface-agent/web-state`. Keep it private and outside the public
  web document root. Preserve it across releases and restarts. It contains chat
  history, the owner request queue and the new web session archive.

Restart the QA server after changing deployment settings. `/api/health` reports
only whether email settings and a public reply origin are configured; it never
returns credentials. Missing email configuration produces a truthful request
to contact the team directly rather than a promise to email the owner.

## Operation and protection

- Reply links contain a random capability in the URL fragment; the page removes
  it from the address bar and uses it in an authorization header. The database
  stores its hash. A link grants access only to its associated question, expires,
  and can submit one answer. GET requests and email scanners never submit answers.
- The link page requires no shared customer password. Keep the link private.
  Answer text is treated as plain text and passes the existing output checks.
- SMTP failures retry up to three times, including after a server restart.
  The customer sees a contact-the-team notice if delivery repeatedly fails.
  Pending copies of the same customer question share one request/email.
- The owner must use **the reply link** in the email. Replying to the email itself
  is not processed. No inbox reading or email-provider webhook is required.
- Customer history and pending answers persist. They are shown after login only
  to the browser possessing that chat's private cookie. One-time owner confirmations
  stay within that chat and do not update the general knowledge base or prices.
- Use a single QA server process with persistent disk. Multiple worker processes
  require a shared session/rate-limit store before horizontal scaling.

## Manual acceptance scenario

1. In customer browser A, ask an unconfirmed service question. Confirm a pending
   message appears immediately. In a separate browser B, verify A's question is absent.
2. Open the owner email's private link, write a customer-facing answer, and submit.
3. Browser A should receive the team update within about four seconds. B must not.
4. Reopen the link: the answer is shown and a second submission is rejected.
5. Restart QA, sign in again in browser A, and confirm its history and answer remain.

Automated tests use temporary databases, fake mail and stubbed model responses.
They do not send owner email or use live customer data.
