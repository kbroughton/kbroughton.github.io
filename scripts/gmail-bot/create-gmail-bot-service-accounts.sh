#!/bin/bash
# Create Gmail bot service accounts with least-privilege Secret Manager access.
#
# Creates email-bot-reader and/or email-bot-sender SAs, each scoped to only
# their own secret — not project-wide secretAccessor.
#
# Usage:
#   cp gmail-bot.conf.template gmail-bot.conf  # fill in your values
#   ./create-gmail-bot-service-accounts.sh --env stg|prd [--reader] [--sender]

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
for var in PROJECT_PREFIX BILLING_ACCOUNT OBSERVABILITY_PROJECT ENGINEERING_GROUP BOT_ACCOUNT_EMAIL; do
    if [[ -z "${!var:-}" ]]; then
        print_error "Required config var not set: $var (check $CONF_FILE)"
        exit 1
    fi
done

# ── Derive project IDs from config ────────────────────────────────────────────
project_id() {
    local bot_type="$1" env="$2"
    if [[ -n "${PROJECT_ORG:-}" ]]; then
        echo "${PROJECT_PREFIX}-${bot_type}-${PROJECT_ORG}-${env}"
    else
        echo "${PROJECT_PREFIX}-${bot_type}-${env}"
    fi
}

READER_PROJECT_STG=$(project_id reader "${ENV_STG:-stg}")
READER_PROJECT_PRD=$(project_id reader "${ENV_PRD:-prd}")
SENDER_PROJECT_STG=$(project_id sender "${ENV_STG:-stg}")
SENDER_PROJECT_PRD=$(project_id sender "${ENV_PRD:-prd}")

# ── Bot definitions ───────────────────────────────────────────────────────────
READER_SA_NAME="${PROJECT_PREFIX}-reader"
READER_SECRET="gmail-bot-reader-token"
READER_SCOPES="https://www.googleapis.com/auth/gmail.readonly"
READER_DESC="Gmail read-only bot for OTP and email verification"

SENDER_SA_NAME="${PROJECT_PREFIX}-sender"
SENDER_SECRET="gmail-bot-sender-token"
SENDER_SCOPES="https://www.googleapis.com/auth/gmail.send"
SENDER_DESC="Gmail sender bot for outbound emails"

# ── Argument parsing ──────────────────────────────────────────────────────────
ENV=""
CREATE_READER=false
CREATE_SENDER=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --env)    ENV="$2"; shift 2 ;;
        --reader) CREATE_READER=true; shift ;;
        --sender) CREATE_SENDER=true; shift ;;
        *)
            print_error "Unknown argument: $1"
            echo "Usage: $0 --env stg|prd [--reader] [--sender]"
            exit 1
            ;;
    esac
done

if [[ -z "$ENV" ]]; then
    print_error "Usage: $0 --env stg|prd [--reader] [--sender]"
    exit 1
fi

case $ENV in
    stg|prd) ;;
    *)
        print_error "Invalid --env: must be stg or prd"
        exit 1
        ;;
esac

case $ENV in
    stg)
        READER_PROJECT="$READER_PROJECT_STG"
        SENDER_PROJECT="$SENDER_PROJECT_STG"
        FOLDER_ID="${STG_FOLDER_ID:-}"
        ;;
    prd)
        READER_PROJECT="$READER_PROJECT_PRD"
        SENDER_PROJECT="$SENDER_PROJECT_PRD"
        FOLDER_ID="${PRD_FOLDER_ID:-}"
        ;;
esac

if [ "$CREATE_READER" = false ] && [ "$CREATE_SENDER" = false ]; then
    CREATE_READER=true
    CREATE_SENDER=true
fi

# ── Functions ─────────────────────────────────────────────────────────────────

check_requirements() {
    if ! command -v gcloud &>/dev/null; then
        print_error "gcloud CLI is required but not installed"
        exit 1
    fi
}

create_project() {
    local project_id="$1"
    local folder_id="$2"

    if gcloud projects describe "$project_id" &>/dev/null; then
        print_status "Project $project_id already exists ✓"
        return
    fi

    local create_args=(projects create "$project_id" "--name=$project_id")
    if [[ -n "$folder_id" ]]; then
        create_args+=("--folder=$folder_id")
        print_status "Creating project $project_id under folder $folder_id..."
    else
        print_status "Creating project $project_id (no folder parent)..."
    fi

    gcloud "${create_args[@]}"
    print_status "Project created ✓"

    gcloud billing projects link "$project_id" --billing-account="$BILLING_ACCOUNT"
    print_status "Billing account linked ✓"

    gcloud services enable secretmanager.googleapis.com gmail.googleapis.com --project="$project_id"
    print_status "Secret Manager + Gmail APIs enabled ✓"
    echo ""
}

create_sa() {
    local sa_name="$1"
    local secret_name="$2"
    local scopes="$3"
    local description="$4"
    local project_id="$5"
    local sa_email="${sa_name}@${project_id}.iam.gserviceaccount.com"

    print_header "SA: $sa_name ($ENV)"
    print_info "Project: $project_id"
    print_info "Email  : $sa_email"
    print_info "Secret : $secret_name"
    print_info "Scopes : $scopes"
    echo ""

    gcloud billing projects link "$project_id" --billing-account="$BILLING_ACCOUNT"
    print_status "Billing account linked ✓"

    gcloud services enable secretmanager.googleapis.com gmail.googleapis.com --project="$project_id"
    print_status "Secret Manager + Gmail APIs enabled ✓"

    if gcloud iam service-accounts describe "$sa_email" --project="$project_id" &>/dev/null; then
        print_status "SA already exists ✓"
    else
        gcloud iam service-accounts create "$sa_name" \
            --display-name="$description" \
            --description="$description ($ENV)" \
            --project="$project_id"
        print_status "SA created ✓"
    fi

    if ! gcloud secrets describe "$secret_name" --project="$project_id" &>/dev/null; then
        print_status "Creating placeholder secret $secret_name..."
        echo '{"pending":"run setup_gmail_bot_auth.py to populate"}' | \
            gcloud secrets create "$secret_name" \
                --data-file=- \
                --replication-policy=automatic \
                --project="$project_id"
        print_status "Placeholder secret created ✓"
    fi

    gcloud secrets add-iam-policy-binding "$secret_name" \
        --member="serviceAccount:$sa_email" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$project_id"
    if [[ -n "${ENGINEERING_GROUP:-}" ]]; then
        gcloud secrets add-iam-policy-binding "$secret_name" \
            --member="$ENGINEERING_GROUP" \
            --role="roles/secretmanager.secretAccessor" \
            --project="$project_id"
    fi
    print_status "secretAccessor granted ✓"

    print_status "Granting observability roles on $OBSERVABILITY_PROJECT..."
    local failed_roles=""
    for role in \
        roles/cloudtrace.agent \
        roles/logging.logWriter \
        roles/monitoring.metricWriter \
        roles/errorreporting.writer
    do
        if ! gcloud projects add-iam-policy-binding "$OBSERVABILITY_PROJECT" \
            --member="serviceAccount:$sa_email" \
            --role="$role"; then
            failed_roles="${failed_roles} ${role}"
            print_error "Failed to grant $role on $OBSERVABILITY_PROJECT"
        fi
    done
    if [[ -n "$failed_roles" ]]; then
        print_error "Observability binding failures for $sa_email:$failed_roles"
        exit 1
    fi
    print_status "Observability permissions granted ✓"
    echo ""
}

display_summary() {
    print_header "Setup Complete ($ENV)"
    echo ""
    print_info "══════════════════════════════════════════════════════"
    print_info "  MANUAL STEPS REQUIRED IN GCP CONSOLE"
    print_info "══════════════════════════════════════════════════════"
    echo ""

    local step=1
    for entry in $([ "$CREATE_READER" = true ] && echo "reader:$READER_PROJECT:$READER_SCOPES") \
                 $([ "$CREATE_SENDER" = true ] && echo "sender:$SENDER_PROJECT:$SENDER_SCOPES"); do
        local bot="${entry%%:*}"
        local rest="${entry#*:}"
        local project="${rest%%:*}"
        local scope="${rest##*:}"
        echo "  ── $bot ($project) ──"
        echo ""
        echo "  $step. OAuth Consent Screen"
        echo "     Console → APIs & Services → OAuth consent screen"
        echo "     Project : $project"
        echo "     Type    : Internal"
        echo "     App name: ${PROJECT_PREFIX}-$bot-$ENV"
        echo ""
        (( step++ ))
        echo "  $step. Data Access (Scopes)"
        echo "     OAuth consent screen → Data access → Add or remove scopes"
        echo "     Add: $scope"
        echo ""
        (( step++ ))
        echo "  $step. Create OAuth Client"
        echo "     Credentials → Create Credentials → OAuth client ID"
        echo "     Type : Desktop app"
        echo "     Name : ${PROJECT_PREFIX}-$bot-$ENV"
        echo "     → Save to: ~/.config/gmail-bot/${PROJECT_PREFIX}-$bot-$ENV-client.json"
        echo ""
        (( step++ ))
        echo "  $step. Store credentials"
        echo "     ./store-oauth-client-secret.sh --bot-type $bot --env $ENV"
        echo ""
        (( step++ ))
    done

    print_info "══════════════════════════════════════════════════════"
    print_info "  THEN: run the OAuth flow to store refresh tokens"
    print_info "══════════════════════════════════════════════════════"
    echo ""
    [ "$CREATE_READER" = true ] && echo "  python3 setup_gmail_bot_auth.py --bot-type reader --env $ENV"
    [ "$CREATE_SENDER" = true ] && echo "  python3 setup_gmail_bot_auth.py --bot-type sender --env $ENV"
    echo ""
}

main() {
    check_requirements

    print_header "Gmail Bot Service Account Setup"
    print_info "Config      : $CONF_FILE"
    print_info "Environment : $ENV"
    [[ -n "$FOLDER_ID" ]] && print_info "Folder      : $FOLDER_ID" || print_info "Folder      : (none)"
    [ "$CREATE_READER" = true ] && print_info "Reader project: $READER_PROJECT"
    [ "$CREATE_SENDER" = true ] && print_info "Sender project: $SENDER_PROJECT"
    echo ""

    if [ "$CREATE_READER" = true ]; then
        print_header "Reader Project"
        create_project "$READER_PROJECT" "$FOLDER_ID"
        create_sa "$READER_SA_NAME" "$READER_SECRET" "$READER_SCOPES" "$READER_DESC" "$READER_PROJECT"
    fi

    if [ "$CREATE_SENDER" = true ]; then
        print_header "Sender Project"
        create_project "$SENDER_PROJECT" "$FOLDER_ID"
        create_sa "$SENDER_SA_NAME" "$SENDER_SECRET" "$SENDER_SCOPES" "$SENDER_DESC" "$SENDER_PROJECT"
    fi

    display_summary
}

main "$@"
