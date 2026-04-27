---
title: "Gmail Bot: Advanced Hardening"
date: 2026-04-27
draft: false
tags: ["gcp", "gmail", "security", "google-workspace", "abnormal-security"]
description: "Forwarding rules, a security-agent classification layer, credential usage monitoring, and Abnormal Security integration."
cover:
  image: cover.svg
  relative: true
  alt: "Layered shield diagram showing defense-in-depth email security"
---

*Part of the [Gmail Bot on GCP](/posts/gmail-bot-gcp-oauth/) series.*

The setup guide gives you proper credential isolation. This page covers what to do
next: reducing what the bot account can see, protecting sensitive human accounts,
adding a security-agent filtering layer, and building monitoring that fires when
credentials are used outside their expected context.

---

## 1. Forwarding rules: limit what the bot can read

The weakest point in a `gmail.readonly` bot is that if the bot account accumulates
email over time, a compromised refresh token gives an attacker access to an
ever-growing inbox. The fix is to ensure the bot account's inbox contains **only the
emails the automation actually needs** — nothing more.

### Option A: Forward from a human account to the bot account

If the bot's job is to read OTP or verification emails that arrive at a human address,
set up a Gmail forwarding filter on the **human account** rather than using the human
account's credentials directly.

1. In the human account: **Settings → See all settings → Forwarding and POP/IMAP**
2. Add the bot account as a forwarding address and confirm via the verification email
3. Create a filter: **Settings → Filters and Blocked Addresses → Create a new filter**
   - Match on: `subject:(verification OR OTP OR "one-time") OR from:(noreply@*)` — tune as needed
   - Action: **Forward to** `bot@yourorg.com`, optionally **Delete it** so it doesn't accumulate in the human inbox

The bot account now receives only filtered copies. Its inbox stays narrow, and the human
account's credentials are never touched by automation.

### Option B: Deliver directly to a dedicated bot account

If the service being automated can send verification emails to any address, register the
bot account email directly (`otp-bot@yourorg.com`) rather than forwarding from a human.
No forwarding rule needed — the bot account's inbox is purpose-built and contains nothing
else.

This is the cleanest model. The bot account is created specifically for automation,
never used interactively, and its inbox is scoped by construction.

### Why this matters

A stolen `gmail.readonly` refresh token scoped to a narrow inbox is dramatically less
useful to an attacker than one scoped to a general employee inbox. Password reset emails
for unrelated services, internal communications, and vendor invoices are all off the table.

---

## 2. Protecting C-Suite and sensitive accounts

Executive and finance inboxes deserve extra care. These accounts receive information
that would be highly valuable to an attacker — M&A discussions, board communications,
wire transfer requests — and they are also high-value phishing targets precisely because
compromising them yields so much.

**The forwarding rule pattern is non-negotiable for these accounts.** Automation should
never hold credentials that give direct access to a C-Suite inbox. If an executive's
OTP emails need to be read by automation, the forwarding filter approach (Option A above)
is the only acceptable model: only the specific matching messages leave the inbox, and
the original account's credentials stay out of any pipeline.

Apply the same principle to:
- Finance and AP/AR accounts (wire fraud exposure)
- Legal and compliance accounts (privilege exposure)
- Board and investor relations accounts
- Shared inboxes with unusually broad visibility (`all@`, `leadership@`, distribution lists)

For these accounts, go further than a forwarding rule: also ensure that no human has
authorized any OAuth app on the account without a formal review, and that your email
security tool is monitoring them at elevated sensitivity.

---

## 3. The security-agent bot pattern

Even with careful forwarding filters, there is a class of problem that filtering alone
cannot solve: **you do not always know in advance what is sensitive**.

A general-purpose automation bot that reads forwarded email might encounter a message
that, while matching the forwarding filter, contains sensitive content — an OTP email
that happens to be CC'd on a board thread, or a verification email that reveals an
undisclosed acquisition target.

One architectural response is to introduce a **security-agent bot** as a gating layer
between the source inbox and the general-purpose automation.

### Design principle: one job, very locked down

The security agent has exactly one job: **assess sensitivity**. It does not act on
email, does not forward messages, does not trigger downstream workflows. It reads, it
classifies, it labels. That's it.

This narrow scope is deliberate. A security agent that also does other things becomes
harder to audit and creates ambiguity about why it accessed a message. Keeping it
single-purpose means any deviation from that behavior — a new API call, a new
outbound connection — is unambiguously anomalous.

The other bots downstream can be more permissive and purpose-driven because they only
ever see what the security agent has already cleared. They operate on a pre-filtered
view of reality. Their job is easier and their blast radius is smaller.

### How it works

```
Human inbox
    │
    ├─ forwarding filter (broad) ──► security-agent inbox
    │                                         │
    │                              [gmail.readonly only]
    │                              [classifies: safe / grey / block]
    │                              [applies label, nothing else]
    │                                         │
    │                     ┌──────────────────┼──────────────────┐
    │                     ▼                  ▼                  ▼
    │               SAFE label          GREY label         BLOCK label
    │               (released to        (queued for        (held, alert
    │               purpose bots)       morning review)    sent immediately)
    │
    └─ original message stays in human inbox, unmodified
```

**SAFE** — matches expected template, known sender, content consistent with the
forwarding filter's intent. Released immediately to purpose-specific bots.

**GREY** — plausible but uncertain. Longer than expected, unexpected sender domain,
content that brushes against sensitive keywords, or a pattern the classifier hasn't
seen before. Queued for human review.

**BLOCK** — clear sensitivity signal: board-related terms, legal hold language, known
executive names in body text, attachments on an account that should never receive them.
Held and an alert sent immediately; does not proceed to any automation.

### The morning review queue

Grey-area emails accumulate in a review queue. Rather than interrupting a human for
each one, the security agent batches them and sends a digest:

- **Trigger: every 10–20 grey-area emails**, whichever comes first after a minimum
  quiet period (avoid waking someone at 3 AM for email 10)
- **Trigger: every 12 hours** regardless of volume — even a queue of 1 gets reviewed
  on the morning/evening cadence

The digest gives the reviewer the subject, sender, a one-sentence summary, and the
reason the agent flagged it as grey. The reviewer approves (releases to automation),
escalates (moves to BLOCK), or reclassifies (updates the classifier so the same pattern
is handled automatically next time).

This cadence keeps human attention load low — typically under 5 minutes per review
session — while ensuring grey-area emails don't sit unreviewed for more than half a day.

### What the security agent classifies on

- **Structural signals**: length deviation from expected template, presence of
  attachments or links in what should be a plain OTP email, HTML complexity above a
  threshold for a transactional message
- **Sender signals**: domain age, typosquatting distance from known senders, SPF/DKIM
  alignment failures, first-time sender to this inbox
- **Content signals**: blocklist of sensitive topics (`acquisition`, `merger`,
  `legal hold`, `board`, `wire`, `NDA`), executive name mentions, CC/BCC lines that
  suggest a broader thread
- **Timing signals**: OTP email that was not preceded by an automation-triggered login
  within the expected window (unsolicited OTPs indicate someone else is trying to log in
  as the bot account)
- **Volume signals**: more than N messages from the same sender in a short window
  (potential flooding or loop)

The agent does not need to be sophisticated to add meaningful value. A keyword blocklist
and a template-length check catches the majority of anomalies. The grey bucket exists
precisely so that edge cases go to a human rather than being silently passed or silently
dropped.

### Relationship to purpose-specific bots

The security agent is infrastructure, not a product feature. Purpose-specific bots —
the OTP reader, the notification sender, the test-automation credential consumer — sit
downstream and operate only on SAFE-labeled messages. They do not need to implement
their own sensitivity logic; that concern is handled once, upstream, consistently.

This layering also means you can deploy new purpose bots without re-auditing sensitivity
handling. The security agent's classification applies to all of them.

---

## 4. Credential usage monitoring: alerting on unexpected context

Credential hygiene at rest (Secret Manager, least-privilege IAM) is necessary but not
sufficient. You also need to know when a credential is used **from an unexpected context**
— a different IP, a different machine identity, a different time of day.

The following describes the monitoring model. Implementation depends on your stack
(GCP-native alerting, a SIEM, or a dedicated secrets security tool), but the
detection logic is the same in all cases.

### The expected usage envelope

For each bot credential, define its expected usage envelope at setup time:

| Signal | Expected value | Example |
|---|---|---|
| Accessing principal | A specific service account or Workload Identity | `ci-runner@project.iam.gserviceaccount.com` |
| Source network | Your CI/CD infrastructure CIDR or Cloud Run region | `10.x.x.x/16`, `us-central1` |
| GCP resource labels | Tags on the running workload | `env=prd`, `team=platform` |
| Time of day | Business hours, or the CI/CD pipeline window | 06:00–22:00 UTC |
| Access frequency | Calls per hour consistent with pipeline cadence | 1–10/hour |

Store this envelope as metadata alongside the secret — in a label, a companion secret
version, or your CMDB.

### What to alert on

**Secret Manager access anomalies** (via Cloud Audit Logs):
- Access by any principal other than the expected service account
- Access from a project other than the bot's own GCP project
- Access outside the expected time window by more than a threshold (e.g. 3 AM access
  when your pipeline only runs during business hours)
- Burst access — 50 secret reads in a minute when normal cadence is 2/hour

**OAuth token use anomalies** (via Google Workspace Audit or your identity provider):
- Gmail API calls authenticated with the bot's `client_id` from an IP not in your
  infrastructure range
- An interactive browser-based OAuth session for a bot account (bots should never have
  browser sessions — this indicates a human logging in with the bot's credentials, or
  an attacker performing a token refresh via browser)
- A token refresh that succeeds but was not preceded by a Secret Manager access in the
  same time window (suggests the refresh token was exfiltrated and is being used outside
  your infrastructure)

**Workload identity / machine identity signals**:
- If your CI/CD attaches a Workload Identity or VM instance identity to all API calls,
  an OAuth token use that lacks a corresponding workload identity token in the same time
  window is anomalous
- GCP resource labels on Cloud Run jobs or GKE pods can be checked against the labels
  that should accompany legitimate uses; calls with no labels or unexpected labels warrant
  investigation

### The detection gap: OAuth tokens are opaque to the issuing server

One important limitation: once an OAuth refresh token is used to obtain an access token,
subsequent Gmail API calls carry only the access token. Google's audit logs record the
`client_id` but not the originating IP of the initial token exchange in a way that is
easily correlated to the API call's source. This is why both layers of monitoring matter:

1. **Secret Manager access logs** tell you when the refresh token was read out of storage
   — this is the earliest detectable signal of exfiltration
2. **Gmail API audit logs** (Google Workspace Admin → Reports → Audit → Gmail) tell you
   what was done with the resulting access token

The gap between these two signals — a Secret Manager access that is not followed by a
corresponding Gmail API call from your expected infrastructure — may indicate the token
was exfiltrated and used elsewhere.

---

## 5. Registering bot accounts with your email security tool

Email security platforms like Abnormal Security, Proofpoint, or Defender for Office 365
build behavioral baselines for every mailbox. A bot account that suddenly sends 500
emails a day looks like a compromised account — unless the platform knows it's expected
behavior.

### What to register

For each bot account, provide your email security team:

| Field | Example |
|---|---|
| Account email | `otp-bot@yourorg.com` |
| Send-as aliases | `info@yourorg.com`, `support@yourorg.com` |
| Expected behavior | Outbound only / inbound OTPs only / both |
| Expected volume | Up to N emails/day |
| Owning team | Platform Engineering |
| GCP project | `email-bot-sender-prd` |
| Expected auth pattern | Service credential only — no interactive logins |
| Last credential rotation | 2026-04-27 |

### What to ask the platform to watch for

- **Interactive logins** — bot accounts should never have browser sessions
- **Authentication from unexpected IPs** — service credential usage should originate
  from your CI/CD or Cloud Run infrastructure only
- **OAuth scope changes** — any change to authorized scopes is a change-management event
- **Forwarding rule additions** — a common exfiltration technique after account compromise
- **Sudden reply traffic on an outbound-only account** — may indicate the send-as alias
  was used in a phishing campaign and recipients are replying
- **Message content anomalies** — Abnormal and similar platforms can flag bot-sent
  messages that deviate from established templates (e.g. a `gmail.send` bot that suddenly
  sends messages with attachments it never sent before)

### Abnormal Security specifics

Navigate to **Account Takeover → Monitored Accounts** and add each bot account. Mark it
as a service account or shared mailbox. In the configuration notes, record the expected
authentication pattern (service credential, no browser) so the anomaly detector has an
accurate baseline rather than trying to learn it from scratch.

If Abnormal ingests your GCP audit logs via a SIEM integration, tag the Secret Manager
secrets and GCP projects with the bot account email so log correlation is
straightforward when investigating an alert.

---

## 6. Audit log query (GCP)

```bash
# List all secret accesses in the last 24h for a token secret
gcloud logging read \
  'resource.type="secretmanager.googleapis.com/Secret"
   protoPayload.resourceName:"gmail-bot-reader-token"
   protoPayload.methodName="google.cloud.secretmanager.v1.SecretManagerService.AccessSecretVersion"' \
  --project=email-bot-reader-prd \
  --freshness=24h \
  --format='table(timestamp, protoPayload.authenticationInfo.principalEmail)'
```

Create a log-based metric and alert if the accessing principal is anything other than
your expected service account or CI/CD identity.

---

## Summary checklist

- [ ] Forwarding rules in place for all human accounts whose email bots need to read
- [ ] C-Suite, finance, legal, and board accounts explicitly reviewed — no bot holds
      direct inbox credentials for these accounts
- [ ] Security-agent bot considered for any pipeline where forwarding filter breadth
      cannot be fully controlled
- [ ] Expected usage envelope documented for each bot credential (principal, network, time)
- [ ] Secret Manager access alert configured for out-of-envelope access
- [ ] Gmail API audit logs reviewed for bot `client_id` usage anomalies
- [ ] Bot accounts registered in email security platform with expected behavior profile
- [ ] Send-as aliases verified and tested with `test_gmail_bot.py --alias`
- [ ] Credential rotation schedule recorded in team registry

---

*Back to [Gmail Bot on GCP: setup guide](/posts/gmail-bot-gcp-oauth/)*  
*See also: [Domain-Wide Delegation vs Per-User OAuth](/posts/gmail-bot-dwd/)*
