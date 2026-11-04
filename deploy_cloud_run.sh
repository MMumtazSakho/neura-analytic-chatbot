#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROJECT_ID="id-rd-ai-demo"
SERVICE_NAME="neura-analytics-chatbot"
REGION="asia-southeast2"
GCLOUD_BIN="${GCLOUD_BIN:-/Users/mohammadmumtazsakho/Downloads/google-cloud-sdk/bin/gcloud}"

if [ ! -f "$GCLOUD_BIN" ]; then
    GCLOUD_BIN="$(which gcloud || true)"
fi

if [ -z "$GCLOUD_BIN" ]; then
    echo "Error: gcloud CLI tidak ditemukan." >&2
    exit 1
fi

ENV_FILE="../.env"
if [ ! -f "$ENV_FILE" ]; then
    ENV_FILE=".env"
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: File .env tidak ditemukan di workspace." >&2
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi
# JSON is valid YAML; keep secrets out of gcloud's argument list.
DEPLOY_ENV_FILE="$(mktemp)"
chmod 600 "$DEPLOY_ENV_FILE"
trap 'rm -f "$DEPLOY_ENV_FILE"' EXIT
"$PYTHON_BIN" -m scripts.export_deploy_env > "$DEPLOY_ENV_FILE"

echo "==> Deploying $SERVICE_NAME to Google Cloud Run (project: $PROJECT_ID, region: $REGION)..."

"$GCLOUD_BIN" run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --platform managed \
  --cpu 1 \
  --memory 1Gi \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 15 \
  --timeout 300 \
  --cpu-throttling \
  --session-affinity \
  --env-vars-file "$DEPLOY_ENV_FILE"
