#!/usr/bin/env python3
"""Clone spec — **Integration Command Center · SLA Ops**.

A second dashboard that is a copy of the Integration Command Center (value_spec)
with a RICHER SLA tab: in addition to the order-grain on-time/breach headline it
adds pairwise *response-SLA* tracking (204->990 confirmation, 204->214 first
update, 204->210 invoice cycle time) ported from the reference q10_sla_pairs
model onto the single shipment world.

Reuse-first & non-destructive to dash 14:
  * DATASETS  = value_spec datasets + vw_sla_pairs.  ensure_datasets only CREATES
    the missing one; existing datasets are left untouched.
  * CHARTS    = ONLY the net-new "Pair SLA · " charts.  ensure_charts therefore
    never rewrites a dash-14 chart; the reused tiles resolve by name and are only
    link-appended to this new dashboard (they keep dash 14 too).
  * LAYOUT    = value_spec layout with the SLA tab augmented by the pair section.
Build with:  python build_value_sla.py
"""
import value_spec as V

# inherit everything; override identity + extend SLA
DASHBOARD_TITLE = "Integration Command Center · Logistics"
SLUG = "integration-command-center-logistics"

KH, CH, TH, BH = V.KH, V.CH, V.TH, V.BH
KPI = V.KPI
T_SLA = V.T_SLA

# --- datasets: all of value_spec's + the pairwise-SLA view --------------------
DATASETS = {**V.DATASETS,
            "vw_sla_pairs": dict(sql="SELECT * FROM vw_sla_pairs", dttm="trigger_at")}

# --- the ONLY net-new charts (dash-14 charts are reused, never rebuilt) -------
P = "204→990 confirmation"
U = "204→214 first update"
I = "204→210 invoice"


def _ontime(pair):
    return ("ontime",
            f"100.0*COUNT(*) FILTER (WHERE pair='{pair}' AND sla_state='met')"
            f"/NULLIF(COUNT(*) FILTER (WHERE pair='{pair}' AND sla_state IN ('met','missed')),0)")


CHARTS = [
    # KPI strip — overall + per-pair on-time %
    dict(slice="Pair SLA · Overall compliance %", tab=T_SLA, dataset="vw_sla_pairs",
         **KPI, kind="bignum", number_format=".1f", subheader="responses within threshold",
         metric=("compliance",
                 "100.0*COUNT(*) FILTER (WHERE sla_state='met')"
                 "/NULLIF(COUNT(*) FILTER (WHERE sla_state IN ('met','missed')),0)")),
    dict(slice="Pair SLA · Confirmation on-time %", tab=T_SLA, dataset="vw_sla_pairs",
         **KPI, kind="bignum", number_format=".1f", subheader="204→990 ≤ 4h",
         metric=_ontime(P)),
    dict(slice="Pair SLA · Update on-time %", tab=T_SLA, dataset="vw_sla_pairs",
         **KPI, kind="bignum", number_format=".1f", subheader="204→214 ≤ 24h",
         metric=_ontime(U)),
    dict(slice="Pair SLA · Invoice on-time %", tab=T_SLA, dataset="vw_sla_pairs",
         **KPI, kind="bignum", number_format=".1f", subheader="204→210 ≤ 72h",
         metric=_ontime(I)),
    # Centerpiece — compliance state stacked per pair type
    dict(slice="Pair SLA · Compliance by pair type", tab=T_SLA, dataset="vw_sla_pairs",
         kind="bar", dim="pair", series="sla_state", row_limit=20,
         metric=("orders", "COUNT(*)")),
    # Actual latency experienced per pair (avg minutes)
    dict(slice="Pair SLA · Avg latency by pair", tab=T_SLA, dataset="vw_sla_pairs",
         kind="bar", dim="pair", row_limit=20,
         metric=("avg_min", "ROUND(AVG(elapsed_min))")),
    # Where the breaches sit — missed responses by partner
    dict(slice="Pair SLA · Breaches by partner", tab=T_SLA, dataset="vw_sla_pairs",
         kind="bar", dim="partner", series="sla_state", row_limit=20,
         metric=("missed", "COUNT(*) FILTER (WHERE sla_state='missed')")),
    # Operational worklist — the breached responses, newest trigger first
    dict(slice="Pair SLA · Breach worklist", tab=T_SLA, dataset="vw_sla_pairs",
         kind="raw", row_limit=200,
         cols=["shipment_id", "partner", "lob", "pair", "elapsed_min",
               "threshold_min", "sla_state", "value_usd", "trigger_at"],
         filters=[("sla_state", "==", "missed")],
         order=[("trigger_at", False)]),
]

# The redesigned Transaction view (interchange-grain master-detail) introduces
# net-new charts that no other dashboard owns. The legacy build_value.py that
# once ensured value_spec.CHARTS is gone, so this build must CREATE them. Pull
# them straight from value_spec by tab so the two stay in sync, and append to the
# ensured set (ensure_charts iterates this CHARTS list).
CHARTS = CHARTS + [c for c in V.NEW_CHARTS if c["tab"] == V.T_TXN]

# --- layout: clone value_spec, then append the pair section to the SLA tab ----
_PAIR_ROWS = [
    ("Pair SLA · Overall compliance %", 3, KH, "Pair compliance %"),
    ("Pair SLA · Confirmation on-time %", 3, KH, "204→990 on-time %"),
    ("Pair SLA · Update on-time %", 3, KH, "204→214 on-time %"),
    ("Pair SLA · Invoice on-time %", 3, KH, "204→210 on-time %"),
    ("Pair SLA · Compliance by pair type", 7, CH, "Compliance by pair type"),
    ("Pair SLA · Avg latency by pair", 5, CH, "Avg latency (min) by pair"),
    ("Pair SLA · Breaches by partner", 12, CH, "Pair breaches by partner"),
    ("Pair SLA · Breach worklist", 12, BH, "Pair-SLA breach worklist"),
]

# The Logistics clone owns the SLA story end-to-end (order grain + pairwise),
# using the reconciled Q17 "Partner 360 scorecard". The legacy cockpit-world
# Partner-SLA tiles have since been removed from value_spec entirely (their
# vw_partner_sla dataset read the dropped txn_current). This stays as a
# defensive no-op so any stray re-introduction never lands on dash 15.
STALE_SLICES = {"Partner SLA scorecard", "% Met by partner"}

LAYOUT = []
for tab_title, rows in V.LAYOUT:
    rows = [e for e in rows if e[0] not in STALE_SLICES]
    if tab_title == T_SLA:
        rows = list(rows) + _PAIR_ROWS
    LAYOUT.append((tab_title, rows))

# inherited verbatim by the builder
NATIVE_FILTERS = V.NATIVE_FILTERS
SHIP_DRILLDOWN_FILTER = getattr(V, "SHIP_DRILLDOWN_FILTER", None)
DRILLDOWN_FILTERS = getattr(V, "DRILLDOWN_FILTERS", None)
CROSS_FILTER_SOURCE = getattr(V, "CROSS_FILTER_SOURCE", None)
