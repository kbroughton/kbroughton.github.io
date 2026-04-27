#!/usr/bin/env python3
"""
One-time setup: authorizes Gmail API access for a bot account and stores
the refresh token in Secret Manager.

Configuration is read from gmail-bot.conf (or GMAIL_BOT_CONF env var).

Usage:
    pip install google-auth-oauthlib
    python3 setup_gmail_bot_auth.py --bot-type reader|sender --env stg|prd

    Or with env vars:
    GMAIL_BOT_CLIENT_ID=... GMAIL_BOT_CLIENT_SECRET=... \\
        python3 setup_gmail_bot_auth.py --bot-type reader --env stg

Prerequisite in GCP Console:
    OAuth client type must be "Desktop app" (no redirect URI registration needed)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Missing dependency. Run: pip install google-auth-oauthlib")
    sys.exit(1)

BOT_CONFIGS = {
    "reader": {
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "secret_name": "gmail-bot-reader-token",
        "description": "Gmail read-only bot (OTP/verification)",
    },
    "sender": {
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        "secret_name": "gmail-bot-sender-token",
        "description": "Gmail sender bot (confirmation emails)",
    },
}

OAUTH_CLIENT_SECRET_NAME = "gmail-bot-oauth-client"

REDIRECT_PORT = 0  # 0 = random available port; safe with Desktop app OAuth client type


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


def creds_dir(conf: dict) -> str:
    prefix = conf.get("PROJECT_PREFIX", "email-bot")
    return os.path.expanduser(f"~/.config/{prefix}")


def _parse_client_json(data: str, source: str) -> "tuple[str, str] | None":
    try:
        creds = json.loads(data)
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        if client_id and client_secret:
            print(f"Using OAuth client credentials from {source}.")
            return client_id, client_secret
        print(f"Warning: {source} is missing client_id or client_secret.")
    except json.JSONDecodeError:
        print(f"Warning: {source} is not valid JSON.")
    return None


def load_client_credentials(
    conf: dict, bot_type: str, env: str, proj: str
) -> "tuple[str, str]":
    """Load OAuth client credentials in priority order:
    1. Env vars          — explicit override, useful in CI
    2. Secret Manager    — fetched from the bot's own project
    3. Local config file — ~/.config/<prefix>/<prefix>-{bot_type}-{env}-client.json
    4. Interactive prompt
    """
    # 1. Env vars
    client_id = os.environ.get("GMAIL_BOT_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_BOT_CLIENT_SECRET")
    if client_id and client_secret:
        print("Using OAuth client credentials from environment variables.")
        return client_id, client_secret

    # 2. Secret Manager (bot's own project)
    result = subprocess.run(
        [
            "gcloud", "secrets", "versions", "access", "latest",
            f"--secret={OAUTH_CLIENT_SECRET_NAME}",
            f"--project={proj}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        parsed = _parse_client_json(
            result.stdout, f"Secret Manager ({proj}/{OAUTH_CLIENT_SECRET_NAME})"
        )
        if parsed:
            return parsed

    # 3. Local config file
    prefix = conf.get("PROJECT_PREFIX", "email-bot")
    local_file = os.path.join(
        creds_dir(conf), f"{prefix}-{bot_type}-{env}-client.json"
    )
    if os.path.exists(local_file):
        with open(local_file) as f:
            parsed = _parse_client_json(f.read(), local_file)
        if parsed:
            return parsed

    # 4. Prompt
    print("\nNo credentials found. Store them first with:")
    print(f"  ./store-oauth-client-secret.sh --bot-type {bot_type} --env {env}\n")
    import getpass

    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()
    return client_id, client_secret


def run_gcloud(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", *args], capture_output=True, text=True)


def store_in_secret_manager(
    token_data: dict, secret_name: str, proj: str
) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(token_data, f, indent=2)
        tmp_path = f.name

    try:
        check = run_gcloud("secrets", "describe", secret_name, f"--project={proj}")

        if check.returncode == 0:
            result = run_gcloud(
                "secrets", "versions", "add", secret_name,
                f"--data-file={tmp_path}", f"--project={proj}",
            )
            action = "Updated"
        else:
            result = run_gcloud(
                "secrets", "create", secret_name,
                f"--data-file={tmp_path}", f"--project={proj}",
                "--replication-policy=automatic",
            )
            action = "Created"

        if result.returncode != 0:
            print(f"gcloud error: {result.stderr}")
            sys.exit(1)

        print(f"{action} secret: projects/{proj}/secrets/{secret_name}")
    finally:
        os.unlink(tmp_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authorize Gmail API bot and store refresh token"
    )
    parser.add_argument(
        "--bot-type",
        choices=["reader", "sender"],
        required=True,
        help="reader: gmail.readonly for OTP | sender: gmail.send for confirmations",
    )
    parser.add_argument("--env", choices=["stg", "prd"], required=True)
    args = parser.parse_args()

    conf = load_conf()
    config = BOT_CONFIGS[args.bot_type]
    proj = project_id(conf, args.bot_type, args.env)

    print(f"\nBot type : {args.bot_type} ({config['description']})")
    print(f"Scopes   : {', '.join(config['scopes'])}")
    print(f"Project  : {proj}")
    print(f"Secret   : {config['secret_name']}\n")

    client_id, client_secret = load_client_credentials(conf, args.bot_type, args.env, proj)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print("Opening browser for Gmail authorization...")
    print("Sign in as the BOT account (not your admin account).\n")

    flow = InstalledAppFlow.from_client_config(client_config, config["scopes"])
    creds = flow.run_local_server(port=REDIRECT_PORT, prompt="consent")

    if not creds.refresh_token:
        print(
            "No refresh token returned. Revoke existing access in Google Account settings and retry."
        )
        sys.exit(1)

    token_data = {
        "bot_type": args.bot_type,
        "refresh_token": creds.refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": config["scopes"],
    }

    print("Storing refresh token in Secret Manager...")
    store_in_secret_manager(token_data, config["secret_name"], proj)
    print(f"\nDone. {args.bot_type} service account can now read this secret at runtime.")


if __name__ == "__main__":
    main()
