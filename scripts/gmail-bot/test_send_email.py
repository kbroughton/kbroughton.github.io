#!/usr/bin/env python3
"""
Test the sender bot by sending a real email.

Checks: credentials readable from Secret Manager → email sent (confirmed by message ID).
Note: gmail.send scope is send-only; Sent folder verification is not possible.
Alias verification requires gmail.readonly — use test_gmail_bot.py --alias instead.

Configuration is read from gmail-bot.conf (or GMAIL_BOT_CONF env var).

Usage:
    python3 test_send_email.py --env stg --to you@example.com
    python3 test_send_email.py --env stg --to you@example.com --from-alias info@example.com
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from email.message import EmailMessage

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print(
        "Missing dependency. Run: pip install google-auth google-auth-httplib2 google-api-python-client"
    )
    sys.exit(1)

OK   = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def load_conf() -> dict:
    conf_path = os.environ.get(
        "GMAIL_BOT_CONF",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail-bot.conf"),
    )
    conf = {}
    if not os.path.exists(conf_path):
        return conf
    with open(conf_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            conf[key.strip()] = value.strip().strip('"').strip("'")
    return conf


def project_id(conf: dict, env: str) -> str:
    prefix = conf.get("PROJECT_PREFIX", "email-bot")
    org = conf.get("PROJECT_ORG", "")
    if org:
        return f"{prefix}-sender-{org}-{env}"
    return f"{prefix}-sender-{env}"


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = OK if ok else FAIL
    print(f"  {status}  {label}" + (f": {detail}" if detail else ""))
    return ok


def get_service(conf: dict, env: str):
    proj = project_id(conf, env)
    result = subprocess.run(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            "--secret=gmail-bot-sender-token",
            f"--project={proj}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  {FAIL}  Could not read secret from {proj}")
        sys.exit(1)
    token = json.loads(result.stdout)
    required = {"refresh_token", "client_id", "client_secret", "token_uri"}
    missing = required - token.keys()
    if missing or token.get("refresh_token", "").startswith("{"):
        print(
            f"  {FAIL}  Secret is a placeholder or missing keys: {missing or 'run setup_gmail_bot_auth.py first'}"
        )
        sys.exit(1)
    creds = Credentials(
        token=None,
        refresh_token=token["refresh_token"],
        client_id=token["client_id"],
        client_secret=token["client_secret"],
        token_uri=token["token_uri"],
    )
    return build("gmail", "v1", credentials=creds), proj


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test email via the sender bot")
    parser.add_argument("--env", choices=["stg", "prd"], required=True)
    parser.add_argument("--to", required=True, help="Recipient address")
    parser.add_argument(
        "--from-alias",
        default=None,
        help="Send as this alias (must be verified on the bot account)",
    )
    args = parser.parse_args()

    conf = load_conf()
    bot_email = conf.get("BOT_ACCOUNT_EMAIL", "")
    sender_address = args.from_alias or bot_email or "bot@example.com"
    subject = (
        f"[email-bot test] sender/{args.env} — {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(f"\n  Send email test — sender / {args.env}")
    print(f"  From    : {sender_address}")
    print(f"  To      : {args.to}")
    print(f"  Subject : {subject}\n")

    service, proj = get_service(conf, args.env)
    all_ok = True

    # gmail.send is send-only — no alias verification here.
    # Gmail rejects the send if the alias isn't configured on the bot account.

    try:
        msg = EmailMessage()
        msg["From"] = sender_address
        msg["To"] = args.to
        msg["Subject"] = subject
        msg.set_content(
            f"This is an automated test from the email-bot-sender ({args.env}).\n\n"
            f"Bot account : {bot_email or 'see BOT_ACCOUNT_EMAIL in gmail-bot.conf'}\n"
            f"Sent as     : {sender_address}\n"
            f"Project     : {proj}\n"
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        message_id = sent.get("id", "")
        all_ok &= check("Email sent", bool(message_id), f"message id: {message_id}")
    except HttpError as e:
        all_ok = False
        check("Email sent", False, str(e))
        sys.exit(1)

    # A message ID in the send response confirms delivery.
    # gmail.send is send-only — Sent folder and alias verification are not possible here.

    print()
    if all_ok:
        print(f"  {OK}  All checks passed. Check {args.to} for the test email.\n")
    else:
        print(f"  {FAIL}  Some checks failed — see above.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
