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
T_ISSUE = "Exceptions"
T_SLA  = "SLA"              # On-time delivery vs. SLA breaches (single data world)
T_CHAN = "Channel Health"   # Arrival & channel/endpoint health (Q1 cockpit, reused)
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
    # SINGLE DATA WORLD (sql/14): the Shipment + Transaction tabs read the SAME
    # transactions as every other tab (public.txn_events, cockpit contract).
    # vw_shipment_detail is the single message source feeding BOTH tabs;
    # vw_shipment is the per-shipment (interchange) ROLLUP of those SAME rows. One
    # source + aligned columns + shared partners/doc-types -> all totals reconcile.
    "vw_shipment": dict(sql="SELECT * FROM vw_shipment", dttm="last_msg_ts"),
    "vw_shipment_detail": dict(sql="SELECT * FROM vw_shipment_detail", dttm="event_time"),
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
    ("EDI vs API split", T_HOME), ("Volume by message type", T_HOME),
    # Shipment view + Transaction view: built net-new on the SHARED txn source
    # (vw_txn_detail / vw_txn_shipment, sql/12) below — the old cockpit "LOB"
    # (q_lob_*) tiles are dropped so both tabs read one source with aligned cols.
    # Issue details (exceptions while processing)
    ("Failed (period)", T_ISSUE), ("Rejected (period)", T_ISSUE),
    ("Duplicates suppressed", T_ISSUE), ("Exceptions by reason", T_ISSUE),
    ("Partner vs platform", T_ISSUE), ("Exception queue", T_ISSUE),
    ("Failure signatures", T_ISSUE),
    # Channel Health — Q1 arrival / channel / endpoint monitors from the original
    # Integration Visibility Cockpit. Reused in place (link-appended), so the
    # original cockpit dashboard keeps them too. All on the single world: monitor
    # tables (ops_monitor_heartbeat / ops_endpoint_health / ops_expected_feeds / ops_pipeline_health)
    # + txn_current, same partner names + channels as the rest of the dashboard.
    ("Monitors reporting", T_CHAN), ("Stale / silent monitors", T_CHAN),
    ("Channel health", T_CHAN), ("Dead / degraded connections", T_CHAN),
    ("Missing expected feeds", T_CHAN), ("Hung pipelines", T_CHAN),
    ("Landed, not picked up", T_CHAN), ("Stuck / aging transactions", T_CHAN),
    ("Cert / key expiry", T_CHAN),
    # EDI view (reused: ack + file are inherently EDI)
    ("FA tracking", T_EDI), ("File explorer", T_EDI),
    # NOTE: the legacy cockpit-world "Partner SLA scorecard" / "% Met by partner"
    # tiles (dataset vw_partner_sla over the dropped txn_current) are gone — dash
    # 15 uses the reconciled Q17 "Partner 360 scorecard" (vw_partner_360) instead.
]

NEW_CHARTS = [
    # ===== Home — Control-Tower KPIs (cockpit world, teal, no prefix) =====
    # Order lifecycle (the only four message types in the world):
    #   Order = 204, Order confirmation = 990, Order updates = 214, Invoice = 210.
    # Each KPI uses a unique "Home · " internal name + sliceNameOverride so the
    # build never hijacks identically-named charts on dashboards 10/12/13.
    # Row 1 KPI: Total messages. The status-aligned headline (Processed / Stuck /
    # Failed) lives further down on vw_shipment_detail; the old success/auto-%/
    # exceptions binary tiles were retired (charts deleted) when Home adopted the
    # processing_status lifecycle.
    dict(slice="Total messages", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", metric=("vol", "SUM(txn_count)"), subheader="messages processed"),
    # Row 2 KPIs: the order lifecycle — order, confirmation, updates, invoice.
    dict(slice="Home · Order", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="orders (204)",
         metric=("orders", "SUM(txn_count) FILTER (WHERE doc_type='204')")),
    dict(slice="Home · Order confirmation", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="order confirmations (990)",
         metric=("conf", "SUM(txn_count) FILTER (WHERE doc_type='990')")),
    dict(slice="Home · Order updates", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="order updates (214)",
         metric=("upd", "SUM(txn_count) FILTER (WHERE doc_type='214')")),
    dict(slice="Home · Invoice", tab=T_HOME, dataset="vw_rollup", **KPI,
         kind="bignum", subheader="invoices (210)",
         metric=("inv", "SUM(txn_count) FILTER (WHERE doc_type='210')")),
    dict(slice="Message volume trend", tab=T_HOME, dataset="vw_rollup",
         kind="ts", x="bucket", chart="line", metric=("txns", "SUM(txn_count)")),
    dict(slice="Exceptions Trend", tab=T_HOME, dataset="vw_rollup",
         kind="ts", x="bucket", chart="line",
         metric=("exc", "SUM(failed_count)+SUM(rejected_count)")),
    # "Exceptions by reason" already exists (reused on the Exceptions tab) — give
    # the Home pie a unique internal name + sliceNameOverride to avoid a collapse.
    dict(slice="Home · Exceptions by reason", tab=T_HOME, dataset="vw_exceptions_by_reason",
         kind="pie", groupby="reason_category", metric=("occ", "SUM(occurrences)")),
    dict(slice="Exceptions by partner", tab=T_HOME, dataset="vw_rollup",
         kind="bar", dim="partner", row_limit=15,
         metric=("exc", "SUM(failed_count)+SUM(rejected_count)")),
    # Status-aligned headline (mirrors the Transaction view lifecycle): the async
    # end-state, the stuck watch list, and failures, plus the full status mix.
    # Message grain on vw_shipment_detail so they reconcile with that tab.
    dict(slice="Home · Processed", tab=T_HOME, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="acknowledged (done)",
         metric=("done", "COUNT(*) FILTER (WHERE processing_status='Processed')")),
    dict(slice="Home · Stuck", tab=T_HOME, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="in-flight past SLA",
         metric=("stuck", "COUNT(*) FILTER (WHERE is_stuck)")),
    dict(slice="Home · Failed", tab=T_HOME, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="terminal failures",
         metric=("fail", "COUNT(*) FILTER (WHERE processing_status='Failed')")),
    dict(slice="Home · Status mix", tab=T_HOME, dataset="vw_shipment_detail",
         kind="bar", dim="processing_status", metric=("msgs", "COUNT(*)"), row_limit=10),

    # ===== Shipment view — single-world rollup of public.txn_events =====
    # Sourced on public.vw_shipment (sql/14) = a per-ORDER ROLLUP of the very
    # same txn_events rows the Transaction view shows (public.vw_shipment_detail).
    # shipment_id == the order (ORD-NNNNNN); each order is 1×990 confirmation,
    # N×214 updates, and (when closed) 1×210 invoice. "complete" = invoice issued
    # AND zero failed/rejected. Same transactions + partners as every other tab.
    dict(slice="Shipments in scope", tab=T_SHIP, dataset="vw_shipment", **KPI,
         kind="bignum", metric=("ships", "COUNT(*)"), subheader="orders in integration scope"),
    dict(slice="Flow complete %", tab=T_SHIP, dataset="vw_shipment", **KPI,
         kind="bignum", number_format=".1f", subheader="invoiced, no exceptions",
         metric=("pct", "100.0*AVG(complete::int)")),
    dict(slice="Invoice coverage", tab=T_SHIP, dataset="vw_shipment", **KPI,
         kind="bignum", number_format=".1f", subheader="invoice (210) issued",
         metric=("inv", "100.0*SUM(has_invoice::int)/NULLIF(COUNT(*),0)")),
    dict(slice="Shipments w/ exceptions", tab=T_SHIP, dataset="vw_shipment", **KPI,
         kind="bignum", subheader="failed / rejected messages",
         metric=("exc", "SUM(CASE WHEN exception_cnt>0 THEN 1 ELSE 0 END)")),
    dict(slice="Completeness mix", tab=T_SHIP, dataset="vw_shipment",
         kind="pie", groupby="completeness_status", metric=("ships", "COUNT(*)")),
    dict(slice="Shipment message mix", tab=T_SHIP, dataset="vw_shipment_detail",
         kind="bar", dim="doc_type", metric=("msgs", "COUNT(*)"), row_limit=30),
    # Worklist = the order consolidation. Exceptions / open orders float to top.
    dict(slice="Shipment worklist", tab=T_SHIP, dataset="vw_shipment",
         kind="raw", row_limit=200,
         cols=["shipment_id", "partner", "protocol", "total_messages",
               "update_count", "completeness_status", "has_invoice",
               "exception_cnt", "duplicate_cnt", "value_usd", "last_msg_ts"],
         order=[("exception_cnt", False), ("last_msg_ts", False)]),
    # Drill-down message set: EMPTY until a specific shipment is chosen, via a
    # REQUIRED native filter on shipment_id (SHIP_DRILLDOWN_FILTER below, scoped to
    # ONLY this chart). Columns + order kept IDENTICAL to "Txn · Details" so the
    # drill-down set and the Transaction view show the same shape (one is the
    # per-shipment slice of the other).
    dict(slice="Shipment message set", tab=T_SHIP, dataset="vw_shipment_detail",
         kind="raw", row_limit=300,
         cols=["shipment_id", "business_ref", "partner", "doc_type", "direction",
               "event_time", "status", "reason_category", "error_code", "control_number"],
         order=[("event_time", False)]),

    # ===== Transaction view — PER-MESSAGE grid (reference "Details" shape) =====
    # The reference Details table is ONE ROW PER MESSAGE (a transaction set):
    # ediservice_messageid is the row key, shipment_number just groups messages.
    # Our equivalent is vw_shipment_detail (one row per business_ref message;
    # interchange_id/shipment_id is the group). This tab IS that flat message grid
    # + a payload drill; the interchange ROLLUP lives on the Shipment view, so the
    # two tabs are detail vs rollup (not two copies of the same grain). Unique
    # "Txn · " internal names + sliceNameOverride keep dashboards 10/12/13 untouched.
    #
    # KPI strip — status-focused: total, the happy end-state, the in-flight watch
    # list (Stuck), and Failed. All message grain on vw_shipment_detail so they
    # reconcile with the grid + status-mix below.
    dict(slice="Txn · In scope", tab=T_TXN, dataset="vw_shipment_detail", **KPI,
         kind="bignum", metric=("msgs", "COUNT(*)"), subheader="messages in scope"),
    dict(slice="Txn · Inbound msgs", tab=T_TXN, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="processed (acknowledged)",
         metric=("done", "COUNT(*) FILTER (WHERE processing_status='Processed')")),
    dict(slice="Txn · Outbound msgs", tab=T_TXN, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="in-flight past SLA",
         metric=("stuck", "COUNT(*) FILTER (WHERE is_stuck)")),
    dict(slice="Txn · Exceptions", tab=T_TXN, dataset="vw_shipment_detail", **KPI,
         kind="bignum", subheader="terminal failures",
         metric=("fail", "COUNT(*) FILTER (WHERE processing_status='Failed')")),
    # Status mix — every status at a glance (colored via label_colors).
    dict(slice="Txn · Status mix", tab=T_TXN, dataset="vw_shipment_detail",
         kind="bar", dim="processing_status", metric=("msgs", "COUNT(*)"), row_limit=10),
    # Master grid — ONE ROW PER MESSAGE (translate-and-forward). Reference
    # message-tracking columns: when, the grouping shipment + message ref, type,
    # direction, partner, status, control #, and BOTH the incoming and outgoing
    # filename. Newest first. business_ref is shown so a row can be picked for the
    # payload drill below.
    dict(slice="Txn · Transactions", tab=T_TXN, dataset="vw_shipment_detail", kind="raw",
         row_limit=200,
         cols=["event_time", "shipment_id", "business_ref", "doc_type", "direction",
               "partner", "processing_status", "reason_category", "control_number",
               "incoming_file", "outgoing_file"],
         order=[("event_time", False)]),
    # Raw payload panels — the rawest information, for the SELECTED message (MSG
    # drill: a REQUIRED business_ref filter, empty until a message is chosen).
    # Incoming = the format the platform received; Outgoing = the translated form
    # it emitted. EDI faces the partner, JSON the internal system; which is which
    # flips with direction (handled in vw_shipment_detail).
    dict(slice="Txn · Incoming payload", tab=T_TXN, dataset="vw_shipment_detail", kind="raw",
         cols=["business_ref", "doc_type", "incoming_file", "incoming_payload"],
         order=[("event_time", False)], row_limit=5),
    dict(slice="Txn · Outgoing payload", tab=T_TXN, dataset="vw_shipment_detail", kind="raw",
         cols=["business_ref", "doc_type", "outgoing_file", "outgoing_payload"],
         order=[("event_time", False)], row_limit=5),

    # ===== SLA view — on-time delivery vs. breaches (single data world) =====
    # Order grain on public.vw_shipment for the headline + worklist; message grain
    # on the rollup (breached_count) for the trend/partner bars. Both use the SAME
    # breach test (sla_due_at < now() AND NOT terminal), so they reconcile.
    dict(slice="SLA · On-time %", tab=T_SLA, dataset="vw_shipment", **KPI,
         kind="bignum", number_format=".1f", subheader="orders within SLA",
         metric=("ontime", "100.0*AVG((NOT sla_breached)::int)")),
    dict(slice="SLA · Breached orders", tab=T_SLA, dataset="vw_shipment", **KPI,
         kind="bignum", subheader="overdue, not closed",
         metric=("br", "SUM(sla_breached::int)")),
    dict(slice="SLA · Orders in scope", tab=T_SLA, dataset="vw_shipment", **KPI,
         kind="bignum", subheader="orders measured", metric=("n", "COUNT(*)")),
    dict(slice="SLA · $ at risk", tab=T_SLA, dataset="vw_shipment", **KPI,
         kind="bignum", number_format="$,.0f", subheader="order value on breached orders",
         metric=("risk", "SUM(CASE WHEN sla_breached THEN value_usd ELSE 0 END)")),
    dict(slice="SLA · Breach trend", tab=T_SLA, dataset="vw_rollup",
         kind="ts", x="bucket", chart="line",
         metric=("br", "SUM(breached_count)")),
    dict(slice="SLA · Breaches by partner", tab=T_SLA, dataset="vw_rollup",
         kind="bar", dim="partner", row_limit=15, metric=("br", "SUM(breached_count)")),
    dict(slice="SLA · Breaches by LOB", tab=T_SLA, dataset="vw_rollup",
         kind="bar", dim="lob", row_limit=15, metric=("br", "SUM(breached_count)")),
    # At-risk worklist: the breached orders, newest first.
    dict(slice="SLA · At-risk orders", tab=T_SLA, dataset="vw_shipment",
         kind="raw", row_limit=200,
         cols=["shipment_id", "partner", "lob", "completeness_status",
               "update_count", "exception_cnt", "value_usd", "last_msg_ts"],
         filters=[("sla_breached", "==", True)],
         order=[("last_msg_ts", False)]),

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
    dict(slice="API · Exceptions", tab=T_API, dataset="vw_rollup_api", **KPI,
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
        # Row 1: status-aligned headline (matches the Transaction view lifecycle).
        ("Total messages", 3, KH),
        ("Home · Processed", 3, KH, "Processed"),
        ("Home · Stuck", 3, KH, "Stuck"),
        ("Home · Failed", 3, KH, "Failed"),
        # Row 2: the order lifecycle (204 -> 990 -> 214 -> 210).
        ("Home · Order", 3, KH, "Order"),
        ("Home · Order confirmation", 3, KH, "Order confirmation"),
        ("Home · Order updates", 3, KH, "Order updates"),
        ("Home · Invoice", 3, KH, "Invoice"),
        # Status mix across the async lifecycle (same language as Transaction view).
        ("Home · Status mix", 12, CH, "Status mix"),
        # Volume: wide trend + protocol split pie.
        ("Message volume trend", 8, CH), ("EDI vs API split", 4, CH),
        # Exceptions: wide trend + reason pie.
        ("Exceptions Trend", 8, CH),
        ("Home · Exceptions by reason", 4, CH, "Exceptions by reason"),
        # Breakdown bars get full breathing room (were squeezed at width 3).
        ("Volume by message type", 6, CH), ("Exceptions by partner", 6, CH),
    ]),
    (T_SHIP, [   # Shipment rollup of the SHARED txn source (public.vw_shipment)
        # KPI row 1: scale + flow completeness (split into 2 rows for legibility).
        ("Shipments in scope", 6, KH),
        ("Flow complete %", 6, KH),
        # KPI row 2: integration health.
        ("Invoice coverage", 6, KH), ("Shipments w/ exceptions", 6, KH),
        ("Completeness mix", 6, CH), ("Shipment message mix", 6, CH),
        ("Shipment worklist", 12, BH),
        ("Shipment message set", 12, TH),
    ]),
    (T_TXN, [   # Per-message grid (public.vw_shipment_detail) + payload drill
        # KPI strip: message scale + status-focused (Processed / Stuck / Failed).
        ("Txn · In scope", 3, KH, "Messages"),
        ("Txn · Inbound msgs", 3, KH, "Processed"),
        ("Txn · Outbound msgs", 3, KH, "Stuck"),
        ("Txn · Exceptions", 3, KH, "Failed"),
        # Status mix across the async lifecycle.
        ("Txn · Status mix", 12, CH, "Status mix"),
        # Master grid — one row per message (reference Details shape).
        ("Txn · Transactions", 12, BH, "Messages"),
        # Drill-gated: pick a message's business_ref above -> its two payloads.
        ("Txn · Incoming payload", 6, TH, "Incoming payload (received)"),
        ("Txn · Outgoing payload", 6, TH, "Outgoing payload (translated)"),
    ]),
    (T_ISSUE, [
        ("Failed (period)", 4, KH), ("Rejected (period)", 4, KH), ("Duplicates suppressed", 4, KH),
        ("Exceptions by reason", 6, CH), ("Partner vs platform", 6, CH),
        ("Exception queue", 12, BH),
        ("Failure signatures", 12, CH),
    ]),
    (T_SLA, [   # On-time delivery vs. SLA breaches (public.vw_shipment + rollup)
        ("SLA · On-time %", 3, KH, "On-time %"),
        ("SLA · Breached orders", 3, KH, "Breached orders"),
        ("SLA · Orders in scope", 3, KH, "Orders in scope"),
        ("SLA · $ at risk", 3, KH, "$ at risk"),
        ("SLA · Breach trend", 12, CH, "Breach trend"),
        ("SLA · Breaches by partner", 6, CH, "Breaches by partner"),
        ("SLA · Breaches by LOB", 6, CH, "Breaches by LOB"),
        ("SLA · At-risk orders", 12, BH, "At-risk orders"),
    ]),
    (T_CHAN, [   # Arrival & channel health (Q1 cockpit, reused in place)
        # Top: scope KPI + the silent-monitor catch.
        ("Monitors reporting", 3, KH), ("Stale / silent monitors", 9, 40),
        # Endpoint / channel posture.
        ("Channel health", 6, 42), ("Dead / degraded connections", 6, 42),
        # Arrival gaps + processing back-pressure.
        ("Missing expected feeds", 6, 42), ("Hung pipelines", 6, 40),
        # Stuck in-flight work.
        ("Landed, not picked up", 6, 42), ("Stuck / aging transactions", 6, 44),
        # Forward-looking risk.
        ("Cert / key expiry", 12, 42),
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
        ("API · Transactions", 4, KH), ("API · Auto-processed %", 4, KH), ("API · Exceptions", 4, KH),
        ("API · Volume by partner", 6, CH), ("API · Volume by message type", 6, CH),
        ("API · Throughput", 8, CH), ("API · Status mix", 4, CH),
    ]),
    (T_PART, [   # EXTERNAL — Q17 + reused SLA
        ("Partners tracked", 3, KH), ("Partners flagged", 3, KH),
        ("Total $ at risk", 3, KH), ("Avg exception rate %", 3, KH),
        ("Volume by partner", 4, CH), ("Exception rate by partner %", 4, CH),
        ("Dollars at risk by partner", 4, CH),
        ("Partner 360 scorecard", 12, TH),
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
    ("Direction", "direction"),
]

# Task 5 — drill-down "Shipment message set": EMPTY until a shipment is chosen.
# Declared here so the build engine can emit a REQUIRED native filter on
# shipment_id, targeting the shared txn-detail dataset, scoped to ONLY the
# "Shipment message set" chart. A required select filter with
# enableEmptyFilter=true returns zero rows until the user selects a shipment_id
# -> exactly the desired drill behavior, no faked/empty static WHERE.
#
# NOTE FOR ORCHESTRATOR: build_value.native_filters() currently emits only
# dashboard-wide (all-charts) select filters with enableEmptyFilter=False against
# the rollup dataset. To honor this entry it must additionally emit a filter that:
#   * filterType "filter_select", targets column shipment_id on dataset
#     get_dataset_id(sc, dataset),
#   * controlValues.enableEmptyFilter=True (required, so empty by default),
#   * controlValues.multiSelect=False, defaultToFirstItem=False,
#   * defaultDataMask.filterState={} (no default value),
#   * chartsInScope=[<id of slice>], scope.excluded=[<all other chart ids>]
#     (or scope.rootPath to the Shipment-view tab) so it ONLY affects this chart.
# This is a build_value.py change (orchestrator-owned); the contract is declared
# below. The chart itself stays empty until the filter supplies a shipment_id.
SHIP_DRILLDOWN_FILTER = dict(
    name="Shipment (drill-down)", column="shipment_id",
    dataset="vw_shipment_detail", slice="Shipment message set", required=True,
)

# Generalised drill-down contract. Each entry is a REQUIRED, chart-scoped
# shipment_id filter that stays EMPTY until the user selects a transaction in the
# master grid -> the listed detail charts then populate with that transaction's
# rows only. `slices` (list) lets one filter feed several panels (the Transaction
# view drives BOTH its inbound and outbound leg panels from a single selection).
# build_value_sla.native_filters() iterates this list. (SHIP_DRILLDOWN_FILTER
# above is kept as a back-compat alias for the single Shipment-view drill.)
DRILLDOWN_FILTERS = [
    dict(name="Shipment (drill-down)", column="shipment_id",
         dataset="vw_shipment_detail", slices=["Shipment message set"], required=True),
    # NOTE: the Transaction-view payload panels are no longer a required-dropdown
    # drill — they load on row-click (cross-filter) from the master grid. See
    # CROSS_FILTER_SOURCE below + build_value_sla.cross_filter_config().
]

# Click-to-load: the master grid emits a cross-filter (on whatever cell is
# clicked, e.g. business_ref) that is SCOPED to ONLY the two payload panels, so
# clicking a message row lazily loads just its incoming/outgoing payload without
# collapsing the KPIs / status-mix. build_value_sla emits the chart_configuration.
CROSS_FILTER_SOURCE = dict(
    source="Txn · Transactions",
    targets=["Txn · Incoming payload", "Txn · Outgoing payload"],
)
