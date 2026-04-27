---
title: "The App Password Attack Surface"
date: 2026-04-27
draft: false
tags: ["gmail", "smtp", "security", "google-workspace", "attack-surface"]
description: "SMTP app passwords bypass 2FA, carry no scope, never expire, and leave a thin audit trail. Here's the full attack surface and how to close it."
cover:
  image: cover.svg
  relative: true
  alt: "Diagram showing SMTP app password granting full mailbox access versus scoped OAuth2"
---

*Part of the [Gmail Bot on GCP](/posts/gmail-bot-gcp-oauth/) series.*

The [OAuth2 setup guide](/posts/gmail-bot-gcp-oauth/) explains how to do Gmail
automation right. This post explains why the easy alternative — SMTP with an app
password — is a security problem you probably have in your org right now and don't
know about.

---

## What app passwords are

When Google required 2-Step Verification for most accounts, it created a problem: legacy
apps that use SMTP or IMAP to connect to Gmail don't support OAuth2. Thunderbird in 2012,
that internal send-only relay your ops team set up in 2016, the CI pipeline someone wired
to Gmail with `smtplib` — none of them know how to do a browser-based consent flow.

App passwords are the solution: a 16-character token you generate under
**Google Account → Security → 2-Step Verification → App passwords**. You paste it into
the legacy app in place of your real password. The app connects via SMTP or IMAP and Gmail
treats it as authenticated.

Google deprecated the broader "less secure app access" setting in 2022. App passwords
survived. They still work.

---

## The attack surface

### No scope — full mailbox access

This is the central problem. OAuth2 tokens are scoped: a `gmail.readonly` token cannot
send email. A `gmail.send` token cannot read your inbox. The scope is enforced by Google's
authorization server — not by your application code, not by a firewall rule, not by hope.

An app password has no equivalent concept. A single app password, used over SMTP, gives
the holder full access to the mailbox: read every message, send as the account owner,
delete messages, modify labels. There is no app-password equivalent of `gmail.readonly`.

| | App password | OAuth2 token |
|---|---|---|
| **Read inbox** | Yes | Only with `gmail.readonly` or `gmail.modify` |
| **Send email** | Yes | Only with `gmail.send` or `gmail.modify` |
| **Delete messages** | Yes (IMAP) | Only with `gmail.modify` |
| **Scope restriction** | None | Enforced by authorization server |
| **Expiry** | Never | Access token: 1 hour |
| **Revocation** | Manual only | Revoke app or rotate refresh token |
| **2FA bypass** | Yes, by design | No — OAuth flow requires 2FA completion |

### 2FA bypass by design

App passwords were specifically designed to bypass 2-Step Verification. That's their job.
An attacker who obtains an app password does not need your TOTP code, your push
notification, or your hardware key. They connect directly via SMTP or IMAP and they're in.

This makes app password theft categorically different from password theft on a
2FA-protected account. The 2FA protection simply doesn't apply.

### Credentials that never expire

OAuth2 refresh tokens can be revoked. They can be rotated. They can be scoped to a single
registered client and invalidated when that client is decommissioned. You can write a
rotation policy and enforce it.

App passwords don't expire. A credential created in 2021 for a since-deleted tool is still
valid in 2026 unless someone manually revoked it. In practice, these credentials accumulate
quietly because nobody tracks which app password goes with which application, and nobody
wants to revoke them and break something.

### Thin audit trail

Gmail API activity via OAuth2 appears in Google Workspace audit logs with the OAuth
`client_id`, the requesting principal, and the scopes in use. You can filter for a
specific bot's activity. You can correlate with Secret Manager access events. You can
build an alert that fires when an unexpected `client_id` calls the API.

SMTP and IMAP authentication using app passwords appears as a login event with
`login_type: exchange`, `application_name: (whatever the connecting app reports, if
anything)`, and the source IP. The attribution is thinner, the events are noisier, and
the connecting app can report any `User-Agent` string it wants.

### The AI assistant trap

This is the specific threat vector that makes app passwords urgent right now.

The current wave of AI productivity tools — agents that read your email, summarize
threads, draft replies, book meetings — often ask for a way to access your Gmail. The
OAuth2 flow is the right answer, but it requires the app to register a Google Cloud
project, configure an OAuth consent screen, and request specific scopes. That's friction.

The shortcut is to ask users to create an app password. Some legitimate tools do this.
Many less-careful tools do this. And some tools with no good intentions do this, because
"create an app password and paste it here" is an effective credential harvesting technique
that most users have no framework for evaluating.

When a user creates an app password for an AI assistant:
- The tool gets full mailbox access, regardless of what it actually needs
- The credential never expires, so access persists long after the user stops using the tool
- If the tool's backend is compromised, every user's mailbox is compromised with it
- There is no centralized visibility into which tools hold these credentials across your org

Every AI email tool that asks for an app password instead of completing an OAuth2 flow is
asking for more access than it needs, indefinitely, with no scope and no audit trail.

### SMTP relay abuse

Once an attacker has a working app password with SMTP access, they have a credentialed
sending identity at your domain. Unlike a compromised OAuth `gmail.send` token (which is
tied to a registered `client_id` and appears in API audit logs), SMTP auth produces
message headers that are indistinguishable from legitimate user-sent email at the transport
layer.

An org's email security tools often maintain a different trust model for mail sent via
SMTP auth versus mail processed through the Gmail API. SMTP-authenticated mail from your
own domain bypasses many inbound filters because it originates inside the org.

This makes a compromised app password a low-friction phishing launchpad: the attacker
sends as a real user, from a real domain, via the real mail server, and most detection
tooling treats it as internal mail unless it's specifically watching for SMTP auth anomalies.

### Google Workspace admin policy gaps

Google Workspace admins can restrict OAuth app access via the "configured apps" policy
in Admin Console → Security → API Controls → App Access Control. Apps not in the
allowlist can be blocked from accessing Workspace data.

This policy does not cover app passwords.

App password creation is controlled separately, through 2SV settings. The only way to
prevent users from creating app passwords is to enforce a 2SV method that doesn't support
them — specifically, enforcing FIDO2 security keys or passkeys as the required 2SV method.
Many orgs have not done this because it requires hardware procurement and user change
management.

The result is a common org posture where OAuth app access is monitored and controlled,
but app password creation is unrestricted and invisible.

---

## Finding app password usage in your org

### Google Workspace audit logs

In Admin Console → Reports → Audit → Login, filter for SMTP and IMAP authentication:

```
event_name = "login_success"
login_type = "exchange"
```

Or via the Workspace Reports API:

```bash
# List recent SMTP/IMAP login events
gcloud logging read \
  'logName="projects/YOUR_PROJECT/logs/cloudaudit.googleapis.com%2Factivity"
   protoPayload.serviceName="login.googleapis.com"
   protoPayload.metadata.loginType="exchange"' \
  --freshness=7d \
  --format='table(timestamp, protoPayload.authenticationInfo.principalEmail, httpRequest.remoteIp)'
```

Any account appearing here is authenticating via a legacy protocol. Cross-reference
against your list of known-legitimate legacy integrations. Everything else is a question
that needs an answer.

### Finding app passwords + SMTP credentials in code

The following searches surface hardcoded Gmail credentials across common patterns.
Run these against your GitHub org, GitLab group, or local codebase.

**GitHub code search (via `gh` CLI):**

```bash
# Python smtplib + Gmail
gh search code --owner YOUR_ORG \
  "smtp.gmail.com smtplib" \
  --language python

# Python smtplib with app-password-length string (16 chars, no spaces)
gh search code --owner YOUR_ORG \
  "smtp.gmail.com" "password" \
  --language python

# Node.js nodemailer + Gmail
gh search code --owner YOUR_ORG \
  "nodemailer gmail" \
  --language javascript

gh search code --owner YOUR_ORG \
  "nodemailer gmail" \
  --language typescript

# Ruby Net::SMTP + Gmail
gh search code --owner YOUR_ORG \
  "smtp.gmail.com Net::SMTP" \
  --language ruby

# Go net/smtp + Gmail
gh search code --owner YOUR_ORG \
  "smtp.gmail.com net/smtp" \
  --language go

# Java JavaMail / jakarta.mail
gh search code --owner YOUR_ORG \
  "smtp.gmail.com Session.getInstance" \
  --language java

# Generic: any file containing gmail + password (broad, noisy)
gh search code --owner YOUR_ORG \
  "gmail.com" "smtp_password OR mail_password OR email_password"
```

**Patterns to look for in code review:**

```python
# Python — classic smtplib app password pattern
import smtplib
server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
server.login('user@gmail.com', 'xxxx xxxx xxxx xxxx')  # 16-char app password
```

```javascript
// Node.js — nodemailer with app password
const transporter = nodemailer.createTransport({
  service: 'gmail',
  auth: { user: 'user@gmail.com', pass: 'xxxx xxxx xxxx xxxx' }
});
```

```ruby
# Ruby — Net::SMTP
smtp = Net::SMTP.new('smtp.gmail.com', 587)
smtp.enable_starttls
smtp.start('gmail.com', 'user@gmail.com', 'xxxx xxxx xxxx xxxx', :plain)
```

```go
// Go — net/smtp
auth := smtp.PlainAuth("", "user@gmail.com", "xxxx xxxx xxxx xxxx", "smtp.gmail.com")
```

```java
// Java — JavaMail
props.put("mail.smtp.password", "xxxx xxxx xxxx xxxx");
```

**What to look for:**
- 16-character strings matching `[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}` — the exact format
  Google generates for app passwords
- `smtp.gmail.com` or `imap.gmail.com` next to a password variable
- `.env` files with `GMAIL_PASSWORD`, `SMTP_PASSWORD`, `EMAIL_PASSWORD`, or `MAIL_PASS`

**Check secret scanning results:**

```bash
# GitHub's built-in secret scanning (if enabled on the org)
gh api orgs/YOUR_ORG/secret-scanning/alerts \
  --paginate \
  --jq '.[] | select(.secret_type | contains("google")) | {repo: .repository.name, file: .locations[0].path, state: .state}'
```

---

## How to close it

### For your own bots and integrations

Use OAuth2 with scoped tokens stored in Secret Manager, as described in the
[Gmail Bot setup guide](/posts/gmail-bot-gcp-oauth/). No SMTP. No app passwords.
The setup is more involved the first time; the security properties are not comparable.

### For your Google Workspace org

**Disable app passwords org-wide** by enforcing FIDO2 as the only 2SV method:

Admin Console → Security → 2-Step Verification:
- Set "Authentication" to "Any" or enforce security keys
- **Only FIDO2 enforcement prevents app password creation.** Enforcing Authenticator apps
  or SMS does not.

**If FIDO2 enforcement isn't feasible immediately:**
- Audit existing app passwords: Admin Console → Users → select user → Security →
  Signing in to Google → App passwords (you can see count but not the passwords themselves)
- Monitor for SMTP/IMAP auth events via audit logs as described above
- Set up an alert for SMTP auth from unexpected IP ranges

**Enable OAuth app access control:**

Admin Console → Security → API Controls → App Access Control:
- Set to "Don't allow users to access any third-party apps" or restrict to trusted apps
- This doesn't cover app passwords, but it reduces the OAuth surface in parallel

**Add SMTP/IMAP auth events to your SIEM:**

The SMTP auth events described above should be ingested alongside your other identity
events. An account that successfully authenticates via SMTP after months of silence, or
from a new country, or outside business hours, is an incident until proven otherwise.

---

## Summary

App passwords are a 2FA bypass by design, with no scope enforcement, no expiry, and a
thin audit trail. In an era where AI tools routinely ask for Gmail credentials as part of
their setup flow, the average org's app password surface is growing faster than it's
being managed.

The fix for new integrations is straightforward: use the OAuth2 setup described in this
series. The harder work is finding what already exists — which is why the code search
patterns above matter.

---

*Back to [Gmail Bot on GCP: setup guide](/posts/gmail-bot-gcp-oauth/)*  
*See also: [Advanced Hardening](/posts/gmail-bot-hardening/)*
