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
    # Cockpit-world shipment choreography (ported from mp_demo into public via
    # sql/11; partners remapped onto the cockpit set). Powers the Shipment view.
    "vw_shipment_integration": dict(sql="SELECT * FROM vw_shipment_integration", dttm="shipment_date"),
    "vw_shipment_messages": dict(sql="SELECT * FROM vw_shipment_messages", dttm="transaction_timestamp"),
    "vw_shipment_journey": dict(sql="SELECT * FROM vw_shipment_journey", dttm="status_timestamp"),
}
DATASETS = {**C.DATASETS, **NEW_DS}

# Teal KPI styling lifted from the Integration Value Cockpit (Control Tower):
# teal #007A87 big numbers, sized so titles/subheaders stay legible in tiles.
KPI = dict(header_font_size=0.4, subheader_font_size=0.15,
           color_picker={"r": 0, "g": 122, "b": 135, "a": 1})


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
    # Home — clean, prefix-free cockpit charts reused as supporting visuals
    # (the Control-Tower KPI row + trends are net-new below to avoid hijacking
    # the mp_demo charts that dashboards 12/13 still use as a fallback).
    ("EDI vs API split", T_HOME), ("Volume by family", T_HOME),
    ("Volume by message type", T_HOME),
    # Shipment view: built net-new on the cockpit-world shipment views (below).
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
    # ===== Home — Control-Tower KPIs (cockpit world, teal, no prefix) =====
    # Doc-type families: Orders=850 PO, Invoices=810, Notices/ASN=856.
    dict(slice="Messages processed", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", metric=("vol", "SUM(txn_count)"), subheader="total messages"),
    dict(slice="Orders (850)", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="purchase orders",
         metric=("orders", "SUM(txn_count) FILTER (WHERE doc_type='850')")),
    dict(slice="Invoices (810)", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="invoices",
         metric=("inv", "SUM(txn_count) FILTER (WHERE doc_type='810')")),
    dict(slice="Notices / ASN", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="ship notices (856)",
         metric=("asn", "SUM(txn_count) FILTER (WHERE doc_type='856')")),
    dict(slice="Open issues", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="failed + rejected",
         metric=("issues", "SUM(failed_count)+SUM(rejected_count)")),
    dict(slice="Message volume trend", tab=T_HOME, dataset="vw_rollup",
         kind="ts", x="bucket", chart="line", metric=("txns", "SUM(txn_count)")),
    dict(slice="Open issues trend", tab=T_HOME, dataset="vw_rollup",
         kind="ts", x="bucket", chart="line",
         metric=("issues", "SUM(failed_count)+SUM(rejected_count)")),
    dict(slice="Issues by reason", tab=T_HOME, dataset="q3_exceptions_by_reason",
         kind="pie", groupby="reason_category", metric=("occ", "SUM(occurrences)")),
    dict(slice="Partners with open issues", tab=T_HOME, dataset="vw_rollup",
         kind="bar", dim="partner", row_limit=15,
         metric=("issues", "SUM(failed_count)+SUM(rejected_count)")),

    # ===== Shipment view — Shipment Integration 360 (cockpit world) =====
    # Faithful port of dash-12's Shipment 360, re-sourced onto public.vw_shipment_*.
    dict(slice="Shipments in scope", tab=T_SHIP, dataset="vw_shipment_integration", **KPI,
         kind="bignum", metric=("ships", "COUNT(*)"), subheader="shipments in integration scope"),
    dict(slice="Choreography complete", tab=T_SHIP, dataset="vw_shipment_integration", **KPI,
         kind="bignum", number_format=".1f", subheader="all expected messages present",
         metric=("pct", "100.0*AVG(choreography_complete::int)")),
    dict(slice="ACK coverage", tab=T_SHIP, dataset="vw_shipment_integration", **KPI,
         kind="bignum", number_format=".1f", subheader="ACKs received vs required",
         metric=("ack", "100.0*SUM(ack_received_cnt)/NULLIF(SUM(ack_required_cnt),0)")),
    dict(slice="Response-SLA met", tab=T_SHIP, dataset="vw_shipment_integration", **KPI,
         kind="bignum", number_format=".1f", subheader="204->990 within partner target",
         metric=("sla", "100.0*SUM(response_sla_met::int)/NULLIF(SUM(CASE WHEN cnt_204>0 AND cnt_990>0 THEN 1 ELSE 0 END),0)")),
    dict(slice="Shipments w/ flow anomalies", tab=T_SHIP, dataset="vw_shipment_integration", **KPI,
         kind="bignum", subheader="integration anomalies detected",
         metric=("anom", "SUM(CASE WHEN anomaly_count>0 THEN 1 ELSE 0 END)")),
    dict(slice="Choreography mix", tab=T_SHIP, dataset="vw_shipment_integration",
         kind="pie", groupby="choreography_status", metric=("ships", "COUNT(*)")),
    dict(slice="Response latency by partner", tab=T_SHIP, dataset="vw_shipment_integration",
         kind="bar", dim="partner", metric=("avg_min", "AVG(response_minutes)")),
    dict(slice="Shipment message mix", tab=T_SHIP, dataset="vw_shipment_messages",
         kind="bar", dim="doc_type", metric=("msgs", "COUNT(*)"), row_limit=30),
    dict(slice="Shipment integration worklist", tab=T_SHIP, dataset="vw_shipment_integration",
         kind="raw", row_limit=200,
         cols=["shipment_id", "partner", "transport_mode", "total_messages",
               "choreography_status", "response_minutes", "expected_204_990_minutes",
               "ack_pending", "error_cnt", "anomaly_count", "critical_anomaly_count",
               "business_impact_amount", "shipment_status"],
         order=[("anomaly_count", False), ("business_impact_amount", False)]),
    dict(slice="Shipment message set", tab=T_SHIP, dataset="vw_shipment_messages",
         kind="raw", row_limit=300,
         cols=["shipment_id", "doc_type", "transaction_timestamp", "processing_status",
               "ack_required", "ack_received", "error_code", "control_number"],
         order=[("transaction_timestamp", True)]),
    dict(slice="Shipment status journey", tab=T_SHIP, dataset="vw_shipment_journey",
         kind="raw", row_limit=300,
         cols=["shipment_id", "status_sequence", "status_code", "status_timestamp",
               "city", "partner"],
         order=[("status_sequence", True)]),

    # ===== EDI view (protocol-scoped rollup) =====
    dict(slice="EDI · Transactions", tab=T_EDI, dataset="vw_rollup_edi", **KPI,
         kind="bignum", metric=("edi", "SUM(txn_count)"), subheader="EDI messages"),
    dict(slice="EDI · Auto-processed %", tab=T_EDI, dataset="vw_rollup_edi", **KPI,
         kind="bignum", number_format=".1f", subheader="straight-through",
         metric=("auto", "100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))")),
    dict(slice="EDI · Exceptions", tab=T_EDI, dataset="vw_rollup_edi", **KPI,
         kind="bignum", metric=("exc", "SUM(failed_count)+SUM(rejected_count)"),
         subheader="failed + rejected"),
    dict(slice="EDI · Volume by partner", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bar", dim="partner", metric=("txns", "SUM(txn_count)"), row_limit=20),
    dict(slice="EDI · Volume by message type", tab=T_EDI, dataset="vw_rollup_edi",
         kind="bar", dim="doc_type", metric=("txns", "SUM(txn_count)"), row_limit=30),
    dict(slice="EDI · Throughput", tab=T_EDI, dataset="vw_rollup_edi",
         kind="timebar", metric=("txns", "SUM(txn_count)"), series="direction"),
    # ===== API view (protocol-scoped rollup) =====
    dict(slice="API · Transactions", tab=T_API, dataset="vw_rollup_api", **KPI,
         kind="bignum", metric=("api", "SUM(txn_count)"), subheader="API calls"),
    dict(slice="API · Auto-processed %", tab=T_API, dataset="vw_rollup_api", **KPI,
         kind="bignum", number_format=".1f", subheader="straight-through",
         metric=("auto", "100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))")),
    dict(slice="API · Errors", tab=T_API, dataset="vw_rollup_api", **KPI,
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
        ("Messages processed", 3, KH), ("Orders (850)", 3, KH),
        ("Invoices (810)", 2, KH), ("Notices / ASN", 2, KH), ("Open issues", 2, KH),
        ("Message volume trend", 6, CH), ("Open issues trend", 6, CH),
        ("EDI vs API split", 4, CH), ("Volume by family", 4, CH), ("Issues by reason", 4, CH),
        ("Volume by message type", 6, CH), ("Partners with open issues", 6, CH),
    ]),
    (T_SHIP, [   # Shipment Integration 360 — cockpit world (public.vw_shipment_*)
        ("Shipments in scope", 3, KH), ("Choreography complete", 3, KH),
        ("ACK coverage", 2, KH), ("Response-SLA met", 2, KH),
        ("Shipments w/ flow anomalies", 2, KH),
        ("Choreography mix", 4, CH), ("Response latency by partner", 4, CH),
        ("Shipment message mix", 4, CH),
        ("Shipment integration worklist", 12, BH),
        ("Shipment message set", 6, TH), ("Shipment status journey", 6, TH),
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
