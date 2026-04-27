---
title: "Gmail Bot: DWD vs Per-User OAuth"
date: 2026-04-27
draft: false
tags: ["gcp", "gmail", "oauth2", "security", "google-workspace"]
description: "Domain-Wide Delegation gives one SA access to every inbox. Per-user OAuth limits blast radius to one account. A decision guide with security trade-offs."
cover:
  image: cover.svg
  relative: true
  alt: "Diagram comparing Domain-Wide Delegation blast radius versus per-user OAuth"
---

*Part of the [Gmail Bot on GCP](/posts/gmail-bot-gcp-oauth/) series.*

Before you write a line of code, decide which access model fits your use case. The choice
affects blast radius, auditability, and how much Workspace admin involvement you need.

---

## The two models

### Domain-Wide Delegation (DWD)

A Google Cloud service account is granted permission by a Workspace super-admin to
impersonate **any user in the organization**. The SA presents its own credentials and
then acts as a specified user — no OAuth consent flow required for each user.

```python
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file(
    "sa-key.json",
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
)
# Impersonate any mailbox in the org
delegated = creds.with_subject("any.user@yourorg.com")
service = build("gmail", "v1", credentials=delegated)
```

### Per-user OAuth (this series)

Each bot account goes through a standard OAuth2 consent flow and grants access to
**its own mailbox only**. The resulting refresh token is stored in Secret Manager and
used at runtime. No super-admin involvement after initial setup.

---

## Side-by-side comparison

| | Domain-Wide Delegation | Per-User OAuth |
|---|---|---|
| **Mailboxes accessible** | Any mailbox in the org | Only the bot account's own mailbox |
| **Blast radius if compromised** | Entire organization | Single bot account |
| **Admin requirement** | Workspace super-admin to enable DWD | Workspace admin to create bot account; engineer runs OAuth flow |
| **OAuth consent flow** | Not required | Required once per bot/env |
| **Auditability** | Single SA; harder to attribute per-mailbox access | Each bot account has its own audit trail |
| **Scope enforcement** | Scopes set at DWD grant; can be broad | Scopes set per OAuth consent screen; enforced at GCP project level |
| **Credential type** | SA key file or Workload Identity | OAuth refresh token in Secret Manager |
| **Revocation** | Revoke DWD grant (affects all impersonation) | Revoke individual OAuth app or refresh token |
| **Setup complexity** | Low (one SA, one admin action) | Higher (4 GCP projects, OAuth flow per bot) |
| **Ongoing overhead** | Low | Moderate (credential rotation, project management) |

---

## When to use Domain-Wide Delegation

DWD is appropriate when:

- You are building **IT or admin tooling** that legitimately needs to access many
  mailboxes — e.g. an email archiving system, an eDiscovery tool, or a compliance
  scanner
- The tool is operated by a small, trusted team with strong access controls
- You have the Workspace super-admin involvement and governance processes to grant and
  audit DWD safely
- You accept that a compromised SA key exposes every mailbox in the org

If you choose DWD, prefer **Workload Identity Federation** over SA key files to avoid
long-lived credentials on disk. Scope the DWD grant to the minimum necessary and audit
it in the Workspace Admin console regularly.

---

## When to use per-user OAuth (recommended for most cases)

Per-user OAuth is appropriate when:

- The bot accesses **a single dedicated bot account** — an OTP reader, a notification
  sender, a test automation account
- You want the blast radius of a credential compromise limited to one mailbox
- You want clear per-account audit logs
- Multiple teams each own their own bot with independent credentials

This is the right model for the vast majority of product and engineering automation.
The overhead of 4 GCP projects and a one-time OAuth flow is small compared to the
reduction in blast radius.

---

## The security asymmetry

The fundamental difference is **what an attacker gets** if they steal the credential:

| Credential stolen | Attacker access |
|---|---|
| DWD service account key | Every employee mailbox in the organization |
| OAuth refresh token (per-user) | One bot account's inbox |

A compromised DWD SA key is a catastrophic breach. A compromised single-bot refresh token
is a contained incident — serious, but limited in scope and easier to remediate.

---

## Conclusion

Use **per-user OAuth** (this series' approach) unless you have a genuine need to access
multiple employee mailboxes. The extra GCP project overhead is a one-time cost; the
blast-radius reduction is permanent.

If you do use DWD, treat the service account key with the same controls as a production
database root password: no local copies, no CI/CD pipelines, Workload Identity Federation
where possible, and regular audits of the DWD grant in Workspace Admin.

---

*Back to [Gmail Bot on GCP: setup guide](/posts/gmail-bot-gcp-oauth/)*  
*See also: [Advanced Hardening](/posts/gmail-bot-hardening/)*
