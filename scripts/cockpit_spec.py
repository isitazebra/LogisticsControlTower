#!/usr/bin/env python3
"""Shared spec library for the Logistics cockpit (datasets + charts).

History: this once described the full standalone "Integration Visibility
Cockpit" dashboard. That dashboard has been retired — dashboard 15
("Integration Command Center · Logistics") is the sole production dashboard,
assembled by value_spec.py / value_spec_sla.py.

This module now survives ONLY as the library those specs import:
  * value_spec.py merges C.DATASETS into its dataset map, and
  * value_spec.py `_pick`s a fixed set of REUSED charts out of C.CHARTS.

So this file is deliberately slimmed to exactly that reused subset — every
dataset references a live table, and every chart here is placed on dash 15.
The retired dashboard's exclusive datasets/charts/sections (and everything
that referenced the dropped txn_current / doc_type_catalog / sla_rules /
diagnostic_rules / v_files_missing_txns objects) have been removed.

SQL lineage: docs/superset-build-pack-full-sequence.md.
"""

# ---------------------------------------------------------------------------
# VIRTUAL DATASETS  (name -> SQL [, temporal column])
# Only the datasets dash 15 reuses; all reference live tables.
# ---------------------------------------------------------------------------
DATASETS = {
    # Shared hourly rollup — powers the reused Flow/Exception headline numbers.
    "vw_rollup": dict(
        sql="SELECT * FROM txn_rollup_hourly",
        dttm="bucket",
    ),

    # ---- Arrival & Channel Health (ops monitor tables) ----------------------
    "vw_missing_feeds": dict(sql="""
        SELECT partner, doc_type, channel, environment, expected_next_at, last_seen_at,
               round(extract(epoch FROM (now()-expected_next_at))/60) AS mins_overdue
        FROM ops_expected_feeds
        WHERE now() > expected_next_at + make_interval(mins => grace_minutes)
          AND (last_seen_at IS NULL OR last_seen_at < expected_next_at)"""),

    "vw_hung_pipeline": dict(sql="""
        SELECT pipeline, environment, queue_depth, mq_depth, consume_rate, last_consumed_at
        FROM ops_pipeline_health
        WHERE state='running' AND (queue_depth>0 OR mq_depth>0) AND consume_rate=0"""),

    "vw_sweep_integrity": dict(sql="""
        SELECT monitor_name, channel, environment, last_run_at, expected_interval_sec,
               (now()-last_run_at) > make_interval(secs => expected_interval_sec) AS is_stale
        FROM ops_monitor_heartbeat"""),

    # Same signal, pre-filtered to the silent monitors (sweep-integrity catch).
    "vw_stale_monitors": dict(sql="""
        SELECT monitor_name, channel, environment, last_run_at,
               round(extract(epoch FROM (now()-last_run_at))/60) AS mins_silent
        FROM ops_monitor_heartbeat
        WHERE (now()-last_run_at) > make_interval(secs => expected_interval_sec)"""),

    # Repointed off the dropped txn_current onto txn_events (current state == the
    # single row per business_ref). Aliases keep the chart-facing column names
    # (current_stage / last_event_at) stable so no chart config needs touching.
    "vw_stuck_transactions": dict(sql="""
        SELECT business_ref, lob, partner, channel, doc_type, stage AS current_stage, environment,
               event_time AS last_event_at, round(extract(epoch FROM (now()-event_time))/60) AS age_min, value_usd
        FROM txn_events
        WHERE NOT terminal AND now()-event_time > interval '20 minutes'"""),

    "vw_landed_not_picked": dict(sql="""
        SELECT business_ref, lob, partner, channel, doc_type, environment, event_time AS last_event_at,
               round(extract(epoch FROM (now()-event_time))/60) AS age_min
        FROM txn_events
        WHERE NOT terminal AND stage='received'
          AND now()-event_time > interval '10 minutes'"""),

    "vw_channel_health": dict(sql="""
        SELECT channel, endpoint, partner, environment, status, last_ok_at, cert_expires_at
        FROM ops_endpoint_health"""),

    "vw_endpoint_down": dict(sql="""
        SELECT channel, endpoint, partner, environment, status, last_ok_at
        FROM ops_endpoint_health WHERE status <> 'up'"""),

    "vw_cert_expiry": dict(sql="""
        SELECT channel, endpoint, partner, environment, cert_expires_at,
               (cert_expires_at - now()::date) AS days_left
        FROM ops_endpoint_health WHERE cert_expires_at < now()+interval '14 days'"""),

    # ---- Exceptions ---------------------------------------------------------
    "vw_exceptions_by_reason": dict(sql="""
        SELECT coalesce(reason_category,'unknown') AS reason_category,
               count(*) AS occurrences, sum(value_usd) AS value_exposed
        FROM txn_events WHERE status IN ('failed','rejected')
        GROUP BY 1 ORDER BY 2 DESC"""),

    "vw_exception_queue": dict(sql="""
        SELECT event_time, business_ref, partner, doc_type, channel, environment, status,
               coalesce(reason_category,'unknown') AS reason_category, value_usd,
               round(extract(epoch FROM (now()-event_time))/60) AS age_min
        FROM txn_events WHERE status IN ('failed','rejected')
        ORDER BY event_time DESC""", dttm="event_time"),

    # ---- Files --------------------------------------------------------------
    "vw_files": dict(sql="""
        SELECT interchange_id, file_name, direction, partner, channel, protocol,
               received_at, completed_at, status, reason_category, declared_txn_count, kchar
        FROM txn_files""", dttm="received_at"),

    # ---- Acknowledgments ----------------------------------------------------
    "vw_acks": dict(sql="""
        SELECT o.business_ref, o.partner, o.doc_type, o.event_time AS sent_at,
               a.event_time AS ack_at, a.status AS ack_status,
               CASE WHEN a.event_time IS NULL AND now() > o.sla_due_at THEN 'missing'
                    WHEN a.status='rejected' THEN 'rejected'
                    WHEN a.event_time IS NOT NULL THEN 'received'
                    ELSE 'pending' END AS fa_state
        FROM txn_events o
        LEFT JOIN txn_events a
          ON a.control_number=o.control_number AND a.doc_type IN ('997','CONTRL') AND a.partner=o.partner
        WHERE o.direction='out' AND o.doc_type NOT IN ('997','CONTRL')
          AND o.business_ref LIKE 'ACK-%'""", dttm="sent_at"),

    # ---- Diagnostics --------------------------------------------------------
    "vw_failure_signatures": dict(sql="""
        SELECT reason_category, error_code, stage, partner,
          count(*) AS occurrences, min(event_time) AS onset, max(event_time) AS latest,
          count(DISTINCT business_ref) AS refs, sum(value_usd) AS value_exposed
        FROM txn_events WHERE status IN ('failed','rejected') AND reason_category IS NOT NULL
        GROUP BY 1,2,3,4 ORDER BY occurrences DESC"""),

    "vw_failure_attribution": dict(sql="""
        -- Attribution follows the status/reason contract of sql/13: a 'rejected'
        -- message was refused by the receiving partner/their validation (theirs),
        -- and a bad_input_file is a partner-supplied bad file (theirs); a 'failed'
        -- message broke in our transport/translation/system stack (ours).
        SELECT CASE WHEN status='rejected' OR reason_category='bad_input_file'
                    THEN 'partner (theirs)' ELSE 'platform (ours)' END AS attribution,
               count(*) AS occurrences
        FROM txn_events WHERE status IN ('failed','rejected') AND reason_category IS NOT NULL
        GROUP BY 1"""),
}

# ---------------------------------------------------------------------------
# CHARTS.  kind drives the viz config the engine builds.  value_spec._pick
# resolves these by `slice` name and overrides `tab` for dash 15 placement,
# so the tab constants below are only nominal groupings.
#   raw    : Table V2, raw columns          (cols, [filters], [order])
#   bignum : Big Number total                (metric, subheader)
#   bar    : categorical bar                 (dim, metric, [series], [row_limit])
#   pie    : Pie                             (groupby, metric)
#   timebar: time-series bar                 (metric, [series])
# metrics are (label, sql_expression) pairs.
# ---------------------------------------------------------------------------
T_ARRIVAL = "Arrival & Channel Health"
T_EXC     = "Exceptions"
T_FLOW    = "Flow — EDI & API summary"
T_FILES   = "Files"
T_ACKS    = "Acknowledgments"
T_DIAG    = "Diagnostics"

# ---- column-format helpers for Table V2 (column_config) --------------------
CUR  = {"d3NumberFormat": "$,.0f"}                       # currency
CURB = {"d3NumberFormat": "$,.0f", "showCellBars": True} # currency + data bar
NUMB = {"d3NumberFormat": ",.0f", "showCellBars": True}  # integer + data bar
PCTB = {"d3NumberFormat": ".1f",  "showCellBars": True}  # percent value + bar
BARS = {"showCellBars": True}                            # data bar only

CHARTS = [
    # ===== Arrival & Channel Health =====
    dict(slice="Monitors reporting", tab=T_ARRIVAL, dataset="vw_sweep_integrity",
         kind="bignum", metric=("reporting", "SUM((NOT is_stale)::int)"),
         subheader="monitors not silent", w=3, h=40),
    dict(slice="Stale / silent monitors", tab=T_ARRIVAL, dataset="vw_stale_monitors",
         kind="raw", cols=["monitor_name","channel","environment","last_run_at","mins_silent"],
         order=[("mins_silent", False)], w=3, h=40),
    dict(slice="Hung pipelines", tab=T_ARRIVAL, dataset="vw_hung_pipeline",
         kind="raw", cols=["pipeline","environment","queue_depth","mq_depth","consume_rate","last_consumed_at"],
         w=6, h=40),
    dict(slice="Missing expected feeds", tab=T_ARRIVAL, dataset="vw_missing_feeds",
         kind="raw", cols=["partner","doc_type","channel","environment","mins_overdue","last_seen_at"],
         order=[("mins_overdue", False)], w=6, h=42),
    dict(slice="Dead / degraded connections", tab=T_ARRIVAL, dataset="vw_endpoint_down",
         kind="raw", cols=["channel","endpoint","partner","environment","status","last_ok_at"], w=6, h=42),
    dict(slice="Channel health", tab=T_ARRIVAL, dataset="vw_channel_health",
         kind="raw", cols=["channel","endpoint","partner","environment","status","last_ok_at","cert_expires_at"],
         w=6, h=44),
    dict(slice="Landed, not picked up", tab=T_ARRIVAL, dataset="vw_landed_not_picked",
         kind="raw", cols=["business_ref","partner","channel","doc_type","environment","age_min"],
         order=[("age_min", False)], w=6, h=42),
    dict(slice="Stuck / aging transactions", tab=T_ARRIVAL, dataset="vw_stuck_transactions",
         kind="raw", cols=["business_ref","partner","channel","doc_type","current_stage","age_min","value_usd"],
         order=[("age_min", False)], col_fmt={"age_min": BARS, "value_usd": CUR}, w=6, h=44),
    dict(slice="Cert / key expiry", tab=T_ARRIVAL, dataset="vw_cert_expiry",
         kind="raw", cols=["endpoint","partner","channel","environment","cert_expires_at","days_left"],
         order=[("days_left", True)], col_fmt={"days_left": BARS},
         heat=[{"column":"days_left","operator":"<","targetValue":7,"colorScheme":"#e04355"}], w=6, h=42),

    # ===== Exceptions =====
    dict(slice="Failed (period)", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("failed","SUM(failed_count)"), subheader="hard failures", trend=True, w=4, h=40),
    dict(slice="Rejected (period)", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("rejected","SUM(rejected_count)"),
         subheader="rejected — its own state", trend=True, w=4, h=40),
    dict(slice="Duplicates suppressed", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("duplicate","SUM(duplicate_count)"),
         subheader="counted, not alerted", w=4, h=40),
    dict(slice="Exceptions by reason", tab=T_EXC, dataset="vw_exceptions_by_reason",
         kind="bar", dim="reason_category", metric=("occurrences","SUM(occurrences)"),
         row_limit=20, w=6, h=50),
    dict(slice="Exception queue", tab=T_EXC, dataset="vw_exception_queue",
         kind="raw",
         cols=["event_time","business_ref","partner","doc_type","status","reason_category","value_usd","age_min"],
         order=[("event_time", False)], row_limit=200,
         col_fmt={"value_usd": CUR, "age_min": BARS}, w=6, h=50),

    # ===== Flow (EDI & API summary), from the rollup =====
    dict(slice="EDI vs API split", tab=T_FLOW, dataset="vw_rollup",
         kind="pie", groupby="protocol", metric=("txns","SUM(txn_count)"), w=4, h=50),
    dict(slice="Volume by message type", tab=T_FLOW, dataset="vw_rollup",
         kind="bar", dim="doc_type", metric=("txns","SUM(txn_count)"), series="protocol", w=6, h=50),

    # ===== Files =====
    dict(slice="File explorer", tab=T_FILES, dataset="vw_files", kind="raw",
         cols=["received_at","file_name","direction","partner","channel","status","declared_txn_count","kchar"],
         order=[("received_at", False)], row_limit=200, w=8, h=46),

    # ===== Acknowledgments =====
    dict(slice="FA tracking", tab=T_ACKS, dataset="vw_acks", kind="raw",
         cols=["sent_at","business_ref","partner","doc_type","ack_at","ack_status","fa_state"],
         order=[("sent_at", False)], w=6, h=44),

    # ===== Diagnostics =====
    dict(slice="Failure signatures", tab=T_DIAG, dataset="vw_failure_signatures", kind="raw",
         cols=["reason_category","error_code","stage","partner","occurrences","onset","refs","value_exposed"],
         order=[("occurrences", False)], w=8, h=46),
    dict(slice="Partner vs platform", tab=T_DIAG, dataset="vw_failure_attribution",
         kind="pie", groupby="attribution", metric=("occurrences","SUM(occurrences)"), w=4, h=46),
]
