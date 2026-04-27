---
title: "Gmail Bot on GCP"
date: 2026-04-27
draft: false
tags: ["gcp", "gmail", "oauth2", "security", "python", "bash"]
description: "OAuth2 with least-privilege Secret Manager — per-scope project isolation, no SA keys."
cover:
  image: cover.svg
  relative: true
  alt: "Diagram showing email flowing through a lock into a Secret Manager vault"
---

## The AI email gold rush — and why it matters for security

Every team wants an AI assistant that reads and sends email. Customer success wants
to auto-reply to support tickets. Engineering wants to consume OTP codes during
automated test runs. HR wants to send onboarding emails from a branded alias.
The result is a sprawl of ad-hoc scripts, credentials in `.env` files, and
shared passwords in Slack threads.

This is a serious security exposure. A Gmail credential isn't just an email credential
— it's a **password reset credential**. An attacker who compromises a bot account's
refresh token can:

- Trigger password reset emails for every service the bot account is registered on and
  read them in real time
- Enumerate all email in the inbox to map internal systems, vendors, and personnel
- Send email as a trusted internal address — a low-effort, high-credibility phishing
  vector that bypasses many filters because it originates from a legitimate domain
- Pivot to other Google services accessible with the same OAuth token if scopes are
  over-broad

The threat isn't hypothetical. CI/CD pipelines and test infrastructure are frequently
targeted because they have permissive network rules, long-lived credentials, and less
monitoring scrutiny than production systems.

This post shows an architecture that does it right: **separate GCP projects per scope
and environment**, **OAuth2 credentials in Secret Manager**, and **least-privilege IAM
at the secret level**. All scripts are in the companion repo at
[scripts/gmail-bot/](https://github.com/kbroughton/kbroughton.github.io/tree/main/scripts/gmail-bot).

Related pages:
- [Domain-Wide Delegation vs Per-User OAuth](/posts/gmail-bot-dwd/) — choose the right
  access model before you build
- [Advanced Hardening](/posts/gmail-bot-hardening/) — forwarding rules, reduced inbox
  exposure, and email security tool integration

---

## Why this architecture?

| Concern | Approach |
|---|---|
| Least-privilege scopes | `gmail.readonly` and `gmail.send` in **separate GCP projects** |
| No long-lived SA keys | OAuth2 refresh token stored in Secret Manager |
| Per-environment isolation | 4 projects: reader-stg, reader-prd, sender-stg, sender-prd |
| Credentials not shared via Slack | Secret Manager is the single source of truth |
| Team access | Engineering group granted secret-level (not project-level) IAM |

### Why separate GCP projects force separate scopes

Google's OAuth consent screen is a **per-GCP-project resource**. Each project has exactly
one consent screen, and that consent screen has exactly one set of data access scopes.
Every OAuth client created in that project inherits those scopes — you cannot create one
client with `gmail.readonly` and another with `gmail.send` within the same project.

The chain is: **one GCP project → one OAuth app → one data access profile**.

This means the only way to enforce that the reader credential can never send email (and
the sender credential can never read email) is to put them in separate projects. It's not
a preference — it's what the platform enforces. Separate projects give you true scope
isolation at the authorization layer, not just at the application layer.

---

## Bot types

| Bot | Gmail scope | Use case |
|---|---|---|
| `reader` | `gmail.readonly` | Read OTP / verification emails during automated logins |
| `sender` | `gmail.send` | Send outbound emails from a verified alias |

Each bot gets its own GCP project per environment:

| | stg | prd |
|---|---|---|
| reader | `email-bot-reader-stg` | `email-bot-reader-prd` |
| sender | `email-bot-sender-stg` | `email-bot-sender-prd` |

(If you set `PROJECT_ORG=acme` in the config, projects become `email-bot-reader-acme-stg`, etc.)

---

## Prerequisites

```bash
pip install google-auth google-auth-httplib2 google-api-python-client google-auth-oauthlib
gcloud auth login
gcloud auth application-default login
```

---

## Configuration

Copy the template and fill in your values — all scripts source this file:

```bash
cp gmail-bot.conf.template gmail-bot.conf
```

```bash
# gmail-bot.conf
PROJECT_PREFIX="email-bot"
PROJECT_ORG=""           # optional — if set: ${PREFIX}-{type}-${ORG}-${env}
BILLING_ACCOUNT=""       # e.g. 012345-ABCDEF-789012
STG_FOLDER_ID=""         # optional GCP folder
PRD_FOLDER_ID=""
OBSERVABILITY_PROJECT="" # project for logging/tracing/metrics
ENGINEERING_GROUP=""     # e.g. group:eng@example.com
BOT_ACCOUNT_EMAIL=""     # Gmail account the bot authenticates as
```

Do not commit `gmail-bot.conf` — add it to `.gitignore`.

---

## Step 1: Create GCP projects and service accounts

```bash
./create-gmail-bot-service-accounts.sh --env stg          # both bots
./create-gmail-bot-service-accounts.sh --env stg --reader # reader only
./create-gmail-bot-service-accounts.sh --env stg --sender # sender only
```

This script:
- Creates GCP projects (optionally under a folder)
- Links billing
- Enables Secret Manager and Gmail APIs
- Creates service accounts
- Creates a placeholder secret for the refresh token
- Grants the SA `secretAccessor` on its own secret only (not project-wide)
- Grants your engineering group `secretAccessor` on the same secret
- Grants observability roles (logging, tracing, metrics) on your monitoring project

After the script runs, it prints the exact manual steps needed in the GCP Console.

---

## Step 2: GCP Console — per project

For each project (`email-bot-reader-stg`, `email-bot-sender-stg`, etc.):

### OAuth consent screen

Navigate to **APIs & Services → OAuth consent screen**:
- User type: **Internal** (keeps the consent screen within your organization)
- App name: `email-bot-{reader|sender}-{stg|prd}`

> **Internal vs External — choose Internal if:**
> - The bot account (`BOT_ACCOUNT_EMAIL`) is a member of your Google Workspace org
> - The human who runs the one-time OAuth consent flow is also in your org
> - You are not granting access to external (non-org) users
>
> Internal apps skip the Google verification process and are not subject to the
> unverified-app warning screen. They are restricted to users within your Workspace
> domain, so there is no risk of an external party authorizing your app.
>
> Use **External** only if the bot account is a personal Gmail account or if you need
> non-org users to authorize the app — neither of which applies to a bot that accesses
> employee or bot mailboxes within your organization.
>
> Send-as aliases pointing to other domains (e.g. `info@example.com` on a
> `bot@yourorg.com` account) do **not** change this decision. The audience type controls
> who can *authorize the OAuth app*, not what addresses the authorized account can send
> from.

### Data access scopes

OAuth consent screen → **Data access → Add or remove scopes**:
- Reader: `https://www.googleapis.com/auth/gmail.readonly`
- Sender: `https://www.googleapis.com/auth/gmail.send`

### OAuth client

**APIs & Services → Credentials → Create Credentials → OAuth client ID**:
- Type: **Desktop app** ← important
- Name: `email-bot-{reader|sender}-{stg|prd}`
- Download the JSON and save to `~/.config/email-bot/email-bot-{reader|sender}-{env}-client.json`

> **Why Desktop app, not Web app?** Desktop app OAuth clients accept any `localhost` port
> without registering redirect URIs. Web app clients require an exact URI match including
> trailing slash and port — painful for a local OAuth flow that picks a random port.

---

## Step 3: Store OAuth client credentials

```bash
./store-oauth-client-secret.sh --bot-type reader --env stg
./store-oauth-client-secret.sh --bot-type sender --env stg
```

Credential lookup order: `GMAIL_BOT_CLIENT_ID`/`GMAIL_BOT_CLIENT_SECRET` env vars →
local JSON file → interactive prompt. Writes to Secret Manager as `gmail-bot-oauth-client`
in the bot's project.

---

## Step 4: Run the OAuth flow

```bash
python3 setup_gmail_bot_auth.py --bot-type reader --env stg
python3 setup_gmail_bot_auth.py --bot-type sender --env stg
```

Opens a browser. **Sign in as the bot account** (not your admin account). After consent,
the refresh token is stored in Secret Manager as `gmail-bot-reader-token` or
`gmail-bot-sender-token`.

If you see "No refresh token returned", revoke the app's existing access under
**Google Account → Security → Third-party apps**, then re-run.

---

## Step 5: Verify

```bash
python3 test_gmail_bot.py --bot-type reader --env stg
python3 test_gmail_bot.py --bot-type sender --env stg

# Check that a send-as alias is verified (requires reader bot + gmail.readonly)
python3 test_gmail_bot.py --bot-type reader --env stg --alias info@example.com

# Send a real test email
python3 test_send_email.py --env stg --to you@example.com
python3 test_send_email.py --env stg --to you@example.com --from-alias info@example.com
```

---

## Using the reader bot in Python

```python
import json, subprocess
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_gmail_service(bot_type: str, env: str):
    # Project IDs match your gmail-bot.conf PREFIX and ORG settings
    project = f"email-bot-{bot_type}-{env}"  # adjust if PROJECT_ORG is set
    secret = f"gmail-bot-{bot_type}-token"
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         f"--secret={secret}", f"--project={project}"],
        capture_output=True, text=True, check=True,
    )
    token = json.loads(result.stdout)
    creds = Credentials(
        token=None,
        refresh_token=token["refresh_token"],
        client_id=token["client_id"],
        client_secret=token["client_secret"],
        token_uri=token["token_uri"],
    )
    return build("gmail", "v1", credentials=creds)

# Read recent unread messages
service = get_gmail_service("reader", "stg")
messages = service.users().messages().list(
    userId="me", q="is:unread", maxResults=10,
).execute().get("messages", [])

# Get full message and extract OTP
import re
msg = service.users().messages().get(
    userId="me", id=messages[0]["id"], format="full",
).execute()
headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
body = msg["snippet"]
otp = re.search(r"\b\d{6}\b", headers.get("Subject", "") + " " + body)
if otp:
    print("OTP:", otp.group())
```

## Using the sender bot in Python

```python
import base64
from email.message import EmailMessage

service = get_gmail_service("sender", "stg")

msg = EmailMessage()
msg["From"] = "info@example.com"   # verified send-as alias on the bot account
msg["To"] = "user@example.com"
msg["Subject"] = "Thanks for signing up"
msg.set_content("Welcome!")

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
service.users().messages().send(userId="me", body={"raw": raw}).execute()
```

---

## Tips for enterprises

Individual teams setting up ad-hoc email bots creates a sprawl of untracked OAuth
apps, inconsistent credential hygiene, and no visibility into what has access to what.
InfoSec and DevOps can get ahead of this.

### Pre-provision bot apps for major departments

Rather than letting each team run their own OAuth consent screen setup (which requires
Workspace admin privileges and produces inconsistent results), a central platform team
can provision a standard set of bots and hand teams the credentials they need:

```
email-bot-reader-{dept}-prd   — e.g. email-bot-reader-engineering-prd
email-bot-sender-{dept}-prd   —     email-bot-sender-hr-prd
```

The setup script accepts a `PROJECT_ORG` value to namespace by department. The platform
team owns the GCP projects and IAM; product teams get a Secret Manager path and run the
OAuth flow once. Rotation is centralized.

### Maintain a registry

Keep a simple registry — a shared doc, a Firestore collection, or a CMDB entry — mapping
each bot account email to its GCP project, owning team, OAuth app, and last-rotated date.
This makes periodic credential audits tractable and gives your security team a known-good
baseline to compare against.

### Enforce rotation

Secret Manager versions make rotation easy. Enforce a rotation policy:
- Refresh tokens: rotate annually or on team member departure
- OAuth client secrets: rotate when the originating engineer leaves or if a project is
  compromised

### Choosing between this approach and Domain-Wide Delegation

This post's approach (per-user OAuth, per-scope project) is right for most cases.
If your use case involves accessing many mailboxes programmatically — e.g. an IT tool
that needs to archive email org-wide — Domain-Wide Delegation may be appropriate.

**[Read the full comparison: Domain-Wide Delegation vs Per-User OAuth →](/posts/gmail-bot-dwd/)**

---

## Rotating credentials

To rotate the OAuth client secret: re-run steps 3 and 4. Secret Manager keeps previous
versions, so rollback is one `gcloud secrets versions enable` away.

To revoke bot access entirely: **Google Account → Security → Third-party apps → remove
the app**.

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `redirect_uri_mismatch` | Wrong OAuth client type (Web app) | Create a **Desktop app** client |
| `invalid_client` | Wrong project's client credentials | Check which project's OAuth client is stored in Secret Manager |
| Script hangs at Secret Manager | API not enabled or billing not linked | Script now enables APIs and links billing in `create_sa()`, not just `create_project()` |
| No refresh token returned | App already authorized without `prompt=consent` | Revoke in Google Account → Security, then re-run |
| 403 on alias check with sender bot | `sendAs.list` requires `gmail.readonly` | Use `test_gmail_bot.py --bot-type reader --alias ...` |

---

## Further reading

- [Domain-Wide Delegation vs Per-User OAuth](/posts/gmail-bot-dwd/)
- [Advanced Hardening: Forwarding Rules and Email Security Tools](/posts/gmail-bot-hardening/)
- [The App Password Attack Surface](/posts/gmail-smtp-app-passwords/) — why SMTP + app passwords is not the alternative
