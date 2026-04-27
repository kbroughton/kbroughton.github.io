# Gmail Bot Scripts

Companion scripts for the blog post
[Gmail Bot on GCP: OAuth2 with Least-Privilege Secret Manager](../../content/posts/gmail-bot-gcp-oauth/index.md).

## Files

| File | Purpose |
|---|---|
| `gmail-bot.conf.template` | Config template — copy to `gmail-bot.conf` and fill in |
| `create-gmail-bot-service-accounts.sh` | Create GCP projects, SAs, secrets, IAM |
| `store-oauth-client-secret.sh` | Store OAuth client credentials in Secret Manager |
| `setup_gmail_bot_auth.py` | Run OAuth2 flow and store refresh token in Secret Manager |
| `test_gmail_bot.py` | End-to-end credential verification |
| `test_send_email.py` | Send a real test email via the sender bot |

## Quick start

```bash
cp gmail-bot.conf.template gmail-bot.conf
# edit gmail-bot.conf

pip install google-auth google-auth-httplib2 google-api-python-client google-auth-oauthlib

./create-gmail-bot-service-accounts.sh --env stg
# follow the Console steps printed by the script

./store-oauth-client-secret.sh --bot-type reader --env stg
python3 setup_gmail_bot_auth.py --bot-type reader --env stg
python3 test_gmail_bot.py --bot-type reader --env stg
```

See the blog post for the full walkthrough.
