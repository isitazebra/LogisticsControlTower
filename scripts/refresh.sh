#!/usr/bin/env bash
# Re-crisp the time-sensitive Q1/Q10 edge cases. Run before a demo (or loop it).
set -euo pipefail
cd "$(dirname "$0")/.."
.venv311/bin/python scripts/db.py run sql/03_refresh_ops.sql
echo "ops refreshed at $(date)"
