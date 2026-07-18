#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  . .env
  set +a
fi

INTERVAL_MINUTES="${WAT_INTERVAL_MINUTES:-10}"
DASHBOARD_HOST="${WAT_DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${WAT_DASHBOARD_PORT:-8000}"
CONFIG_PATH="${WAT_CONFIG:-config/items.yaml}"
DATABASE_URL_VALUE="${DATABASE_URL:-sqlite:///data/auction_tracker.sqlite3}"
RUN_IMMEDIATELY="${WAT_RUN_IMMEDIATELY:-1}"
DASHBOARD_DEV_MODE="${WAT_DASHBOARD_DEV_MODE:-0}"
ADDON_SAVED_VARIABLES="${WOW_AUCTION_TRACKER_SAVED_VARIABLES:-}"

usage() {
  cat <<'EOF'
Usage: scripts/start-local.sh [options]

Start scheduled auction ingest and the local dashboard together.

Options:
  --interval-minutes N       Ingest interval in minutes. Default: 10.
  --host HOST                Dashboard host. Default: 127.0.0.1.
  --port PORT                Dashboard port. Default: 8000.
  --config PATH              Item config path. Default: config/items.yaml.
  --database-url URL         SQLAlchemy database URL. Default: DATABASE_URL or local SQLite.
  --no-run-immediately       Wait one interval before the first ingest run.
  --dev-mode                 Enable dashboard display-only dev states.
  --addon-saved-variables PATH
                             SavedVariables path for dashboard addon imports.
  -h, --help                 Show this help.

Environment overrides:
  WAT_INTERVAL_MINUTES, WAT_DASHBOARD_HOST, WAT_DASHBOARD_PORT, WAT_CONFIG,
  DATABASE_URL, WAT_RUN_IMMEDIATELY, WAT_DASHBOARD_DEV_MODE,
  WOW_AUCTION_TRACKER_SAVED_VARIABLES
EOF
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$option requires a value." >&2
    usage >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval-minutes)
      require_value "$1" "${2:-}"
      INTERVAL_MINUTES="$2"
      shift 2
      ;;
    --host)
      require_value "$1" "${2:-}"
      DASHBOARD_HOST="$2"
      shift 2
      ;;
    --port)
      require_value "$1" "${2:-}"
      DASHBOARD_PORT="$2"
      shift 2
      ;;
    --config)
      require_value "$1" "${2:-}"
      CONFIG_PATH="$2"
      shift 2
      ;;
    --database-url)
      require_value "$1" "${2:-}"
      DATABASE_URL_VALUE="$2"
      shift 2
      ;;
    --no-run-immediately)
      RUN_IMMEDIATELY=0
      shift
      ;;
    --dev-mode)
      DASHBOARD_DEV_MODE=1
      shift
      ;;
    --addon-saved-variables)
      require_value "$1" "${2:-}"
      ADDON_SAVED_VARIABLES="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but was not found on PATH." >&2
  exit 1
fi

schedule_cmd=(
  uv run wow-auctions
  --config "$CONFIG_PATH"
  --database-url "$DATABASE_URL_VALUE"
  schedule
  --interval-minutes "$INTERVAL_MINUTES"
)

if [[ "$RUN_IMMEDIATELY" == "0" || "$RUN_IMMEDIATELY" == "false" ]]; then
  schedule_cmd+=(--no-run-immediately)
fi

dashboard_cmd=(
  uv run wow-auctions
  --config "$CONFIG_PATH"
  --database-url "$DATABASE_URL_VALUE"
  dashboard
  --host "$DASHBOARD_HOST"
  --port "$DASHBOARD_PORT"
)

if [[ "$DASHBOARD_DEV_MODE" == "1" || "$DASHBOARD_DEV_MODE" == "true" ]]; then
  dashboard_cmd+=(--dev-mode)
fi

if [[ -n "$ADDON_SAVED_VARIABLES" ]]; then
  dashboard_cmd+=(--addon-saved-variables "$ADDON_SAVED_VARIABLES")
fi

schedule_pid=""
cleanup() {
  local status=$?
  if [[ -n "$schedule_pid" ]] && kill -0 "$schedule_pid" >/dev/null 2>&1; then
    echo "Stopping ingest scheduler..."
    kill "$schedule_pid" >/dev/null 2>&1 || true
    wait "$schedule_pid" 2>/dev/null || true
  fi
  exit "$status"
}

trap cleanup EXIT INT TERM

echo "Starting ingest scheduler every ${INTERVAL_MINUTES} minute(s)..."
"${schedule_cmd[@]}" &
schedule_pid=$!

echo "Starting dashboard at http://${DASHBOARD_HOST}:${DASHBOARD_PORT} ..."
"${dashboard_cmd[@]}"
