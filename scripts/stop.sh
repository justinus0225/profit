#!/usr/bin/env bash
# P.R.O.F.I.T. 시스템 중지 스크립트

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "Stopping P.R.O.F.I.T. system..."
docker compose down

echo "P.R.O.F.I.T. stopped."
echo "Note: Data volumes are preserved. Use 'docker compose down -v' to remove all data."
