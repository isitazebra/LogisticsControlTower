#!/usr/bin/env bash
# One-command deploy: schema -> signals seed -> bulk txn world -> derived views
# -> demo refresh -> Preset DB -> datasets/charts/dashboard/alerts -> verify.
# Idempotent. The dashboard is "Integration Command Center · Logistics" (dash15),
# built by build_value_sla.py over public.txn_events ("Postgres is the contract").
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv311/bin/python

echo "== 1/12 schema =="              ; $PY scripts/db.py run sql/00_schema.sql
echo "== 2/12 static signals seed ==" ; $PY scripts/db.py run sql/01_seed.sql
echo "== 3/12 bulk txn world =="      ; ( cd scripts && ../$PY gen_shipment_world.py )
echo "== 4/12 payload bodies (drill) ==" ; $PY scripts/db.py run sql/06_payloads.sql
echo "== 5/12 exception reason spread ==" ; $PY scripts/db.py run sql/13_exception_reason_backfill.sql
echo "== 6/12 rollup refresh fn =="   ; $PY scripts/db.py run sql/02_rollup_refresh.sql
echo "== 7/12 derived views =="       ; for f in 09_predictive_anomaly 10_partner_profile 14_consignment_views 16_sla_pairs; do \
                                          $PY scripts/db.py run "sql/${f}.sql"; done
echo "== 8/12 demo refresh (re-crisp) ==" ; $PY scripts/db.py run sql/03_refresh_ops.sql
echo "== 9/12 register Neon in Preset ==" ; ( cd scripts && ../$PY 01_register_db.py )
echo "== 10/12 datasets+charts+dashboard+verify ==" ; ( cd scripts && ../$PY build_value_sla.py all )
echo "== 11/12 alerts =="             ; ( cd scripts && ../$PY build_alerts.py )
echo "== 12/12 re-verify =="          ; ( cd scripts && ../$PY build_value_sla.py verify )
echo "Done. Run 'scripts/refresh.sh' before a demo to re-crisp the edge cases."
