#!/usr/bin/env python3
"""
Verify Gmail bot setup: reads refresh token from Secret Manager,
exchanges it for an access token, and makes a live API call.

Configuration is read from gmail-bot.conf (or GMAIL_BOT_CONF env var).

Usage:
    pip install google-auth google-auth-httplib2 google-api-python-client
    python3 test_gmail_bot.py --bot-type reader|sender --env stg|prd
    python3 test_gmail_bot.py --bot-type reader --env stg --alias info@example.com
"""

import argparse
import json
import os
import subprocess
import sys

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print(
        "Missing dependency. Run: pip install google-auth google-auth-httplib2 google-api-python-client"
    )
    sys.exit(1)

SECRETS = {
    "reader": "gmail-bot-reader-token",
    "sender": "gmail-bot-sender-token",
}

OAUTH_CLIENT_SECRET_NAME = "gmail-bot-oauth-client"

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


def project_id(conf: dict, bot_type: str, env: str) -> str:
    prefix = conf.get("PROJECT_PREFIX", "email-bot")
    org = conf.get("PROJECT_ORG", "")
    if org:
        return f"{prefix}-{bot_type}-{org}-{env}"
    return f"{prefix}-{bot_type}-{env}"


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = OK if ok else FAIL
    print(f"  {status}  {label}" + (f": {detail}" if detail else ""))
    return ok


def fetch_secret(secret_name: str, proj: str) -> "dict | None":
    result = subprocess.run(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            f"--secret={secret_name}", f"--project={proj}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Gmail bot credentials end-to-end"
    )
    parser.add_argument("--bot-type", choices=["reader", "sender"], required=True)
    parser.add_argument("--env", choices=["stg", "prd"], required=True)
    parser.add_argument(
        "--alias",
        help="Verify this send-as alias is configured and verified on the account (reader bot only)",
    )
    args = parser.parse_args()

    conf = load_conf()
    proj = project_id(conf, args.bot_type, args.env)
    token_secret = SECRETS[args.bot_type]

    print(f"\n  Gmail bot test — {args.bot_type} / {args.env}")
    print(f"  Project : {proj}\n")

    all_ok = True

    # 1. OAuth client credentials in Secret Manager
    oauth_data = fetch_secret(OAUTH_CLIENT_SECRET_NAME, proj)
    ok = (
        oauth_data is not None
        and "client_id" in oauth_data
        and "client_secret" in oauth_data
    )
    all_ok &= check(
        "OAuth client secret exists in Secret Manager",
        ok,
        oauth_data.get("client_id", "")[:30] + "..." if ok else "not found",
    )

    # 2. Refresh token in Secret Manager
    token_data = fetch_secret(token_secret, proj)
    ok = (
        token_data is not None
        and "refresh_token" in token_data
        and token_data.get("refresh_token") != ""
    )
    all_ok &= check(
        "Refresh token exists in Secret Manager",
        ok,
        "pending placeholder only"
        if (token_data and "pending" in token_data)
        else ("" if ok else "not found"),
    )

    if not all_ok:
        print(f"\n  {FAIL}  Secrets not ready — run setup steps first.\n")
        sys.exit(1)

    # 3. Build credentials and make a live API call
    try:
        creds = Credentials(
            token=None,
            refresh_token=token_data["refresh_token"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            token_uri=token_data["token_uri"],
        )
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "")
        all_ok &= check("Gmail API call succeeded", bool(email), email)

        # Bot-type specific check
        if args.bot_type == "reader":
            msgs = service.users().messages().list(userId="me", maxResults=1).execute()
            count = msgs.get("resultSizeEstimate", 0)
            all_ok &= check(
                "Can list messages (gmail.readonly)",
                True,
                f"~{count} messages in inbox",
            )

        elif args.bot_type == "sender":
            import urllib.request

            req = urllib.request.Request(
                f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={creds.token}"
            )
            with urllib.request.urlopen(req) as resp:
                info = json.loads(resp.read())
            scope = info.get("scope", "")
            has_send = "gmail.send" in scope
            all_ok &= check(
                "gmail.send scope active", has_send, scope if not has_send else ""
            )

        # Alias check (reader bot only — requires gmail.readonly)
        if args.alias:
            if args.bot_type != "reader":
                print(f"\n  Note: --alias check requires gmail.readonly (reader bot). Skipped for sender.")
            else:
                aliases = service.users().settings().sendAs().list(userId="me").execute()
                match = next(
                    (
                        a
                        for a in aliases.get("sendAs", [])
                        if a["sendAsEmail"] == args.alias
                    ),
                    None,
                )
                verified = match is not None and match.get("verificationStatus") in (
                    "accepted",
                    None,
                )
                detail = (
                    "not found on account"
                    if not match
                    else ("unverified" if not verified else "")
                )
                all_ok &= check(
                    f"Alias {args.alias} verified", bool(match and verified), detail
                )

    except HttpError as e:
        all_ok = False
        check("Gmail API call", False, str(e))

    print()
    if all_ok:
        print(f"  {OK}  All checks passed.\n")
    else:
        print(f"  {FAIL}  Some checks failed — see above.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
