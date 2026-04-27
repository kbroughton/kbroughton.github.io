#!/bin/bash
# One-time setup: store Gmail OAuth client credentials in the bot's Secret Manager project.
#
# Usage:
#   cp gmail-bot.conf.template gmail-bot.conf  # fill in your values
#   ./store-oauth-client-secret.sh --bot-type reader|sender --env stg|prd
#   ./store-oauth-client-secret.sh --bot-type reader --env stg --grant user:alice@example.com

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${GMAIL_BOT_CONF:-${SCRIPT_DIR}/gmail-bot.conf}"

if [[ ! -f "$CONF_FILE" ]]; then
    echo "ERROR: Config file not found: $CONF_FILE"
    echo "Copy gmail-bot.conf.template to gmail-bot.conf and fill in your values."
    exit 1
fi
# shellcheck source=gmail-bot.conf
source "$CONF_FILE"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() { echo -e "${GREEN}=== $1 ===${NC}"; }
print_status() { echo -e "${YELLOW}$1${NC}"; }
print_error()  { echo -e "${RED}ERROR: $1${NC}"; }
print_info()   { echo -e "${BLUE}$1${NC}"; }

# ── Validate required config ──────────────────────────────────────────────────
for var in PROJECT_PREFIX; do
    if [[ -z "${!var:-}" ]]; then
        print_error "Required config var not set: $var (check $CONF_FILE)"
        exit 1
    fi
done

# ── Derive project ID from config ─────────────────────────────────────────────
project_id() {
    local bot_type="$1" env="$2"
    if [[ -n "${PROJECT_ORG:-}" ]]; then
        echo "${PROJECT_PREFIX}-${bot_type}-${PROJECT_ORG}-${env}"
    else
        echo "${PROJECT_PREFIX}-${bot_type}-${env}"
    fi
}

SECRET_NAME="gmail-bot-oauth-client"
CREDS_DIR="${HOME}/.config/gmail-bot"
BOT_TYPE=""
ENV=""
GRANT_MEMBERS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --bot-type) BOT_TYPE="$2"; shift 2 ;;
        --env)      ENV="$2";      shift 2 ;;
        --grant)    GRANT_MEMBERS+=("$2"); shift 2 ;;
        *)
            print_error "Unknown argument: $1"
            echo "Usage: $0 --bot-type reader|sender --env stg|prd [--grant <member>]"
            exit 1
            ;;
    esac
done

if [[ -z "$BOT_TYPE" || -z "$ENV" ]]; then
    print_error "Usage: $0 --bot-type reader|sender --env stg|prd [--grant <member>]"
    exit 1
fi

case "$BOT_TYPE" in
    reader|sender) ;;
    *)
        print_error "Invalid --bot-type: must be reader or sender"
        exit 1
        ;;
esac

case "$ENV" in
    stg|prd) ;;
    *)
        print_error "Invalid --env: must be stg or prd"
        exit 1
        ;;
esac

PROJECT=$(project_id "$BOT_TYPE" "$ENV")

print_header "Store Gmail OAuth Client Credentials"
print_info "Bot type : $BOT_TYPE"
print_info "Env      : $ENV"
print_info "Project  : $PROJECT"
print_info "Secret   : $SECRET_NAME"
echo ""

# ── Load credentials: env vars → local file → prompt ─────────────────────────
LOCAL_CREDS_FILE="${CREDS_DIR}/${PROJECT_PREFIX}-${BOT_TYPE}-${ENV}-client.json"

CLIENT_ID="${GMAIL_BOT_CLIENT_ID:-}"
CLIENT_SECRET="${GMAIL_BOT_CLIENT_SECRET:-}"

if [[ -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]]; then
    print_status "Using credentials from environment variables."
elif [[ -f "$LOCAL_CREDS_FILE" ]]; then
    CLIENT_ID=$(python3 -c "import json; d=json.load(open('$LOCAL_CREDS_FILE')); print(d['client_id'])" 2>/dev/null || true)
    CLIENT_SECRET=$(python3 -c "import json; d=json.load(open('$LOCAL_CREDS_FILE')); print(d['client_secret'])" 2>/dev/null || true)
    if [[ -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]]; then
        print_status "Using credentials from $LOCAL_CREDS_FILE."
    else
        print_info "Could not parse $LOCAL_CREDS_FILE — prompting."
    fi
else
    print_info "No credentials file at $LOCAL_CREDS_FILE — prompting."
fi

if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    read -r -p "Client ID: " CLIENT_ID
    read -r -s -p "Client Secret: " CLIENT_SECRET
    echo ""
fi

if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    print_error "Client ID and Client Secret are required."
    exit 1
fi

PAYLOAD=$(CLIENT_ID="$CLIENT_ID" CLIENT_SECRET="$CLIENT_SECRET" \
    python3 -c 'import json,os; print(json.dumps({"client_id":os.environ["CLIENT_ID"],"client_secret":os.environ["CLIENT_SECRET"]}))')

# ── Store in Secret Manager ───────────────────────────────────────────────────
if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT" &>/dev/null; then
    print_status "Secret exists — adding new version..."
    printf '%s' "$PAYLOAD" | gcloud secrets versions add "$SECRET_NAME" \
        --data-file=- \
        --project="$PROJECT"
    print_status "Secret version updated ✓"
else
    print_status "Creating secret $SECRET_NAME..."
    printf '%s' "$PAYLOAD" | gcloud secrets create "$SECRET_NAME" \
        --data-file=- \
        --replication-policy=automatic \
        --project="$PROJECT"
    print_status "Secret created ✓"
fi

# ── Grant access ──────────────────────────────────────────────────────────────
print_header "Granting Access"

if [[ -n "${ENGINEERING_GROUP:-}" ]]; then
    gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
        --member="$ENGINEERING_GROUP" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT"
    print_status "secretAccessor granted to $ENGINEERING_GROUP ✓"
fi

for member in "${GRANT_MEMBERS[@]}"; do
    gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
        --member="$member" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT"
    print_status "secretAccessor granted to $member ✓"
done

echo ""
print_header "Done"
print_info "Run the OAuth flow with:"
print_info "  python3 setup_gmail_bot_auth.py --bot-type $BOT_TYPE --env $ENV"
