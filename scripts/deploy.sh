#!/usr/bin/env bash
# One-command deploy: schema -> seed -> rollup ops -> demo refresh -> Preset DB
# -> datasets -> charts -> dashboard -> alerts -> verify. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv311/bin/python

echo "== 1/9 schema =="        ; $PY scripts/db.py run sql/00_schema.sql
echo "== 2/9 rollup + recon ==" ; $PY scripts/db.py run sql/02_rollup_refresh.sql
echo "== 3/9 seed =="          ; $PY scripts/db.py run sql/01_seed.sql
echo "== 3b/9 phase 3-5 seed ==" ; $PY scripts/db.py run sql/04_phase35_seed.sql
echo "== 4/9 demo refresh =="  ; $PY scripts/db.py run sql/03_refresh_ops.sql
echo "== 5/9 register Neon in Preset ==" ; ( cd scripts && ../$PY 01_register_db.py )
echo "== 6/9 datasets =="      ; ( cd scripts && ../$PY build_cockpit.py datasets )
echo "== 7/9 charts =="        ; ( cd scripts && ../$PY build_cockpit.py charts )
echo "== 8/9 dashboard + alerts ==" ; ( cd scripts && ../$PY build_dashboard.py && ../$PY build_alerts.py )
echo "== 9/9 verify =="        ; ( cd scripts && ../$PY build_cockpit.py verify )
echo "Done. Run 'scripts/refresh.sh' before a demo to re-crisp the edge cases."
