#!/usr/bin/env python3
"""Spec for the **Integration Command Center** — the demo-quality, single-world
unification of dashboards 12/13 + Q12/Q15/Q17.

Design (per user's vision, 2026-06-24):
  * ONE data world. Everything reads the cockpit contract (db3 / public.txn_*),
    so partner names, doc types, LOB, and the native FILTERS are consistent
    across every tab (no more P1/P2/P3 vs DHL/Maersk split). The mp_demo
    (P1/P2/P3) tabs are dropped, not ported.
  * Integration lens only — message flow, choreography, SLA, exceptions,
    anomalies, partner integration health. No physical/economics fields.
  * Flow: Home -> Shipment view -> Transaction view -> Issue details ->
    Anomalies -> EDI view -> API view -> Partner Insights. SLA folded into
    Partner Insights.

How it's assembled (build_value.py):
  * Most charts are REUSED from cockpit_spec (already render on db3); we just
    re-tab them into the new flow. ENSURE = those + the new EDI/API charts.
  * Q15 (Predictive Anomaly) and Q17 (Partner 360) charts already exist; they
    are referenced by slice name in LAYOUT (EXTERNAL) and not re-created.
  * build_value.py creates a NEW dashboard and APPENDS it to each chart's
    dashboards list, so dashboards 12/13 stay intact as a fallback.
"""
import copy
import cockpit_spec as C

DASHBOARD_TITLE = "Integration Command Center"
SLUG = "integration-command-center"

# -- tab titles (vision flow) ------------------------------------------------
T_HOME = "Home"
T_SHIP = "Shipment view"
T_TXN  = "Transaction view"
T_ISSUE = "Issue details"
T_ANOM = "Anomalies"
T_EDI  = "EDI view"
T_API  = "API view"
T_PART = "Partner Insights"

# ---------------------------------------------------------------------------
# DATASETS — reuse all cockpit datasets, add two protocol-scoped rollups so the
# EDI/API tabs can show by-partner / by-type bars without a per-chart WHERE.
# ---------------------------------------------------------------------------
NEW_DS = {
    "vw_rollup_edi": dict(sql="SELECT * FROM txn_rollup_hourly WHERE protocol='edi'", dttm="bucket"),
    "vw_rollup_api": dict(sql="SELECT * FROM txn_rollup_hourly WHERE protocol='api'", dttm="bucket"),
}
DATASETS = {**C.DATASETS, **NEW_DS}


# ---------------------------------------------------------------------------
# CHARTS to ENSURE (create/update on db3). Reused cockpit charts are re-tabbed
# copies; EDI/API charts are net-new on the protocol-scoped datasets.
# ---------------------------------------------------------------------------
def _pick(slice_name, tab):
    for c in C.CHARTS:
        if c["slice"] == slice_name:
            d = copy.deepcopy(c)
            d["tab"] = tab
            return d
    raise KeyError(f"slice not found in cockpit_spec: {slice_name}")


REUSED = [
    # Home
    ("Overview: Transactions", T_HOME), ("Overview: Auto-processed %", T_HOME),
    ("Overview: Open exceptions", T_HOME), ("Overview: In-flight", T_HOME),
    ("Overview: EDI vs API", T_HOME), ("Overview: Volume by family", T_HOME),
    ("LOB: Message by status", T_HOME), ("Volume by message type", T_HOME),
    ("Throughput over time", T_HOME),
    # Shipment view (LOB = logistics lines of business)
    ("LOB: Total messages received", T_SHIP), ("LOB: Success", T_SHIP),
    ("LOB: Failure", T_SHIP), ("LOB: Message by partner", T_SHIP),
    ("LOB: Message by type", T_SHIP), ("Volume by LOB", T_SHIP),
    ("LOB: Processing trend", T_SHIP),
    # Transaction view (Minimal "Details")
    ("LOB: Details", T_TXN), ("LOB: Incoming data", T_TXN),
    ("LOB: Outgoing data", T_TXN), ("LOB: Ack data", T_TXN),
    # Issue details (exceptions while processing)
    ("Failed (period)", T_ISSUE), ("Rejected (period)", T_ISSUE),
    ("Duplicates suppressed", T_ISSUE), ("Exceptions by reason", T_ISSUE),
    ("Partner vs platform", T_ISSUE), ("Exception queue", T_ISSUE),
    ("Failure signatures", T_ISSUE),
    # EDI view (reused: ack + file are inherently EDI)
    ("FA tracking", T_EDI), ("File explorer", T_EDI),
    # Partner Insights (SLA folded in)
    ("Partner SLA scorecard", T_PART), ("% Met by partner", T_PART),
]

NEW_CHARTS = [
    # ===== EDI view (protocol-scoped rollup) =====
    dict(slice="EDI · Transactions", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bignum", metric=("edi", "SUM(txn_count)"), subheader="EDI messages"),
    dict(slice="EDI · Auto-processed %", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bignum", number_format=".1f", subheader="straight-through",
         metric=("auto", "100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))")),
    dict(slice="EDI · Exceptions", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bignum", metric=("exc", "SUM(failed_count)+SUM(rejected_count)"),
         subheader="failed + rejected"),
    dict(slice="EDI · Volume by partner", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bar", dim="partner", metric=("txns", "SUM(txn_count)"), row_limit=20),
    dict(slice="EDI · Volume by message type", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bar", dim="doc_type", metric=("txns", "SUM(txn_count)"), row_limit=30),
    dict(slice="EDI · Throughput", tab=T_EDI, dataset="vw_rollup_edi",
         kind="timebar", metric=("txns", "SUM(txn_count)"), series="direction"),
    # ===== API view (protocol-scoped rollup) =====
    dict(slice="API · Transactions", tab=T_API, dataset="vw_rollup_api",
         kind="bignum", metric=("api", "SUM(txn_count)"), subheader="API calls"),
    dict(slice="API · Auto-processed %", tab=T_API, dataset="vw_rollup_api",
         kind="bignum", number_format=".1f", subheader="straight-through",
         metric=("auto", "100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))")),
    dict(slice="API · Errors", tab=T_API, dataset="vw_rollup_api",
         kind="bignum", metric=("exc", "SUM(failed_count)+SUM(rejected_count)"),
         subheader="failed + rejected"),
    dict(slice="API · Volume by partner", tab=T_API, dataset="vw_rollup_api",
         kind="bar", dim="partner", metric=("txns", "SUM(txn_count)"), row_limit=20),
    dict(slice="API · Volume by message type", tab=T_API, dataset="vw_rollup_api",
         kind="bar", dim="doc_type", metric=("txns", "SUM(txn_count)"), row_limit=30),
    dict(slice="API · Throughput", tab=T_API, dataset="vw_rollup_api",
         kind="timebar", metric=("txns", "SUM(txn_count)"), series="direction"),
    dict(slice="API · Status mix", tab=T_API, dataset="vw_rollup_api",
         kind="donut", groupby="status", metric=("txns", "SUM(txn_count)")),
]

CHARTS = [_pick(s, t) for s, t in REUSED] + NEW_CHARTS


# ---------------------------------------------------------------------------
# LAYOUT — single source of truth for tab order, rows, and tile sizing.
# Each entry: (tab_title, [(slice, w, h), ...]). Charts pack into rows<=12 wide.
# Includes EXTERNAL (Q15/Q17) slices that already exist as Preset charts.
# ---------------------------------------------------------------------------
KH, CH, TH, BH = 30, 50, 60, 56   # KPI / chart / tall-table / table heights

LAYOUT = [
    (T_HOME, [
        ("Overview: Transactions", 3, KH), ("Overview: Auto-processed %", 3, KH),
        ("Overview: Open exceptions", 3, KH), ("Overview: In-flight", 3, KH),
        ("Overview: EDI vs API", 4, CH), ("Overview: Volume by family", 4, CH),
        ("LOB: Message by status", 4, CH),
        ("Volume by message type", 6, CH), ("Throughput over time", 6, CH),
    ]),
    (T_SHIP, [
        ("LOB: Total messages received", 4, KH), ("LOB: Success", 4, KH), ("LOB: Failure", 4, KH),
        ("LOB: Message by partner", 4, CH), ("LOB: Message by type", 4, CH), ("Volume by LOB", 4, CH),
        ("LOB: Processing trend", 12, CH),
    ]),
    (T_TXN, [
        ("LOB: Details", 12, TH),
        ("LOB: Incoming data", 6, CH), ("LOB: Outgoing data", 6, CH),
        ("LOB: Ack data", 12, BH),
    ]),
    (T_ISSUE, [
        ("Failed (period)", 4, KH), ("Rejected (period)", 4, KH), ("Duplicates suppressed", 4, KH),
        ("Exceptions by reason", 6, CH), ("Partner vs platform", 6, CH),
        ("Exception queue", 12, BH),
        ("Failure signatures", 12, CH),
    ]),
    (T_ANOM, [   # EXTERNAL — Q15
        ("Silent feeds", 3, KH), ("Severe-drop feeds", 3, KH),
        ("Feeds on watch", 3, KH), ("Partners affected", 3, KH),
        ("Feed status mix", 4, CH), ("Abnormal feeds by partner", 4, CH),
        ("Worst volume drop % by partner", 4, CH),
        ("Daily volume by partner", 12, CH),
        ("Abnormal feed worklist", 12, TH),
        ("Partner anomaly scorecard", 12, TH),
    ]),
    (T_EDI, [
        ("EDI · Transactions", 4, KH), ("EDI · Auto-processed %", 4, KH), ("EDI · Exceptions", 4, KH),
        ("EDI · Volume by partner", 6, CH), ("EDI · Volume by message type", 6, CH),
        ("EDI · Throughput", 12, CH),
        ("FA tracking", 6, BH), ("File explorer", 6, BH),
    ]),
    (T_API, [
        ("API · Transactions", 4, KH), ("API · Auto-processed %", 4, KH), ("API · Errors", 4, KH),
        ("API · Volume by partner", 6, CH), ("API · Volume by message type", 6, CH),
        ("API · Throughput", 8, CH), ("API · Status mix", 4, CH),
    ]),
    (T_PART, [   # EXTERNAL — Q17 + reused SLA
        ("Partners tracked", 3, KH), ("Partners flagged", 3, KH),
        ("Total $ at risk", 3, KH), ("Avg exception rate %", 3, KH),
        ("Volume by partner", 4, CH), ("Exception rate by partner %", 4, CH),
        ("Dollars at risk by partner", 4, CH),
        ("Partner 360 scorecard", 12, TH),
        ("Partner SLA scorecard", 7, BH), ("% Met by partner", 5, CH),
    ]),
]

# Native dashboard filters (column-scoped, single world -> consistent values).
NATIVE_FILTERS = [
    ("Environment", "environment"),
    ("LOB", "lob"),
    ("Partner", "partner"),
    ("Protocol", "protocol"),
    ("Channel", "channel"),
    ("Doc type", "doc_type"),
]
