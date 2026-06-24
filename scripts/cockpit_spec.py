#!/usr/bin/env python3
"""Declarative spec for the Integration Cockpit: datasets, charts, dashboard tabs.
Pure data — the engine in build_cockpit.py turns this into Superset resources.
SQL is lifted verbatim from docs/superset-build-pack-full-sequence.md."""

# ---------------------------------------------------------------------------
# VIRTUAL DATASETS  (name -> SQL [, temporal column])
# ---------------------------------------------------------------------------
DATASETS = {
    # Shared hourly rollup — powers all Q2 aggregates + Q3 headline numbers.
    "vw_rollup": dict(
        sql="SELECT * FROM txn_rollup_hourly",
        dttm="bucket",
    ),

    # ---- Q1 Arrival & Stuck -------------------------------------------------
    "q1_missing_feeds": dict(sql="""
        SELECT partner, doc_type, channel, environment, expected_next_at, last_seen_at,
               round(extract(epoch FROM (now()-expected_next_at))/60) AS mins_overdue
        FROM expected_feeds
        WHERE now() > expected_next_at + make_interval(mins => grace_minutes)
          AND (last_seen_at IS NULL OR last_seen_at < expected_next_at)"""),

    "q1_hung_pipeline": dict(sql="""
        SELECT pipeline, environment, queue_depth, mq_depth, consume_rate, last_consumed_at
        FROM pipeline_health
        WHERE state='running' AND (queue_depth>0 OR mq_depth>0) AND consume_rate=0"""),

    "q1_sweep_integrity": dict(sql="""
        SELECT monitor_name, channel, environment, last_run_at, expected_interval_sec,
               (now()-last_run_at) > make_interval(secs => expected_interval_sec) AS is_stale
        FROM monitor_heartbeat"""),

    # Same signal, pre-filtered to the silent monitors (sweep-integrity catch).
    "q1_stale_monitors": dict(sql="""
        SELECT monitor_name, channel, environment, last_run_at,
               round(extract(epoch FROM (now()-last_run_at))/60) AS mins_silent
        FROM monitor_heartbeat
        WHERE (now()-last_run_at) > make_interval(secs => expected_interval_sec)"""),

    "q1_stuck": dict(sql="""
        SELECT business_ref, lob, partner, channel, doc_type, current_stage, environment,
               last_event_at, round(extract(epoch FROM (now()-last_event_at))/60) AS age_min, value_usd
        FROM txn_current
        WHERE NOT terminal AND now()-last_event_at > interval '20 minutes'"""),

    "q1_landed_not_picked": dict(sql="""
        SELECT business_ref, lob, partner, channel, doc_type, environment, last_event_at,
               round(extract(epoch FROM (now()-last_event_at))/60) AS age_min
        FROM txn_current
        WHERE NOT terminal AND current_stage='received'
          AND now()-last_event_at > interval '10 minutes'"""),

    "q1_channel_health": dict(sql="""
        SELECT channel, endpoint, partner, environment, status, last_ok_at, cert_expires_at
        FROM endpoint_health"""),

    "q1_endpoint_down": dict(sql="""
        SELECT channel, endpoint, partner, environment, status, last_ok_at
        FROM endpoint_health WHERE status <> 'up'"""),

    "q1_cert_expiry": dict(sql="""
        SELECT channel, endpoint, partner, environment, cert_expires_at,
               (cert_expires_at - now()::date) AS days_left
        FROM endpoint_health WHERE cert_expires_at < now()+interval '14 days'"""),

    # ---- Q3 Exceptions ------------------------------------------------------
    "q3_exceptions_by_reason": dict(sql="""
        SELECT coalesce(reason_category,'unknown') AS reason_category,
               count(*) AS occurrences, sum(value_usd) AS value_exposed
        FROM txn_events WHERE status IN ('failed','rejected')
        GROUP BY 1 ORDER BY 2 DESC"""),

    "q3_exception_queue": dict(sql="""
        SELECT event_time, business_ref, partner, doc_type, channel, environment, status,
               coalesce(reason_category,'unknown') AS reason_category, value_usd,
               round(extract(epoch FROM (now()-event_time))/60) AS age_min
        FROM txn_events WHERE status IN ('failed','rejected')
        ORDER BY event_time DESC""", dttm="event_time"),

    # ---- Q4 Files + Lookup / Q8 Replay / Q5 Acks ----------------------------
    "q4_files": dict(sql="""
        SELECT interchange_id, file_name, direction, partner, channel, protocol,
               received_at, completed_at, status, reason_category, declared_txn_count, kchar
        FROM txn_files""", dttm="received_at"),

    "q4_file_feed": dict(sql="""
        SELECT direction, status, count(*) AS files,
               sum(declared_txn_count) AS txns, sum(kchar) AS kchar
        FROM txn_files WHERE received_at >= now()-interval '24 hours'
        GROUP BY 1,2"""),

    "q4_files_missing": dict(sql="SELECT * FROM v_files_missing_txns"),

    "q4_rejected_receipt": dict(sql="""
        SELECT interchange_id, file_name, partner, channel, received_at, reason_category
        FROM txn_files WHERE status='rejected'""", dttm="received_at"),

    "q4_txn_lookup": dict(sql="""
        SELECT business_ref, environment, lob, partner, channel, doc_type,
               current_stage, current_status, last_event_at, sla_due_at, value_usd,
               terminal, replayed, replayed_at, replay_count
        FROM txn_current""", dttm="last_event_at"),

    "q4_step_history": dict(sql="""
        SELECT event_time, business_ref, interchange_id, stage, status,
               coalesce(reason_category,'') AS reason_category
        FROM txn_events""", dttm="event_time"),

    "q8_replayed": dict(sql="""
        SELECT business_ref, partner, doc_type, replayed_at, replay_count, current_status
        FROM txn_current WHERE replayed = true""", dttm="replayed_at"),

    "q5_acks": dict(sql="""
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

    # ---- Q6 Partner SLA / Q7 Activity / Q9 Usage ----------------------------
    "q6_partner_sla": dict(sql="""
        SELECT c.partner,
          count(*) AS total,
          count(*) FILTER (WHERE terminal_at <= sla_due_at) AS met,
          count(*) FILTER (WHERE terminal_at > sla_due_at OR (terminal_at IS NULL AND now()>sla_due_at)) AS missed,
          round(100.0*count(*) FILTER (WHERE terminal_at <= sla_due_at)/nullif(count(*),0),1) AS pct_met,
          round(avg(extract(epoch FROM (terminal_at-first_event_at))/60)) AS avg_min,
          round(max(extract(epoch FROM (terminal_at-first_event_at))/60)) AS max_min,
          coalesce(sum(p.penalty_usd) FILTER (WHERE terminal_at > sla_due_at),0) AS penalty_usd
        FROM txn_current c LEFT JOIN partner_penalty p ON p.partner=c.partner
        GROUP BY c.partner"""),

    "q7_partner_activity": dict(sql="""
        WITH cur AS (SELECT partner, sum(txn_count) v, sum(failed_count+rejected_count) e
                     FROM txn_rollup_hourly WHERE bucket >= now()-interval '7 days' GROUP BY partner),
             prv AS (SELECT partner, sum(txn_count) v FROM txn_rollup_hourly
                     WHERE bucket >= now()-interval '14 days' AND bucket < now()-interval '7 days' GROUP BY partner)
        SELECT cur.partner, cur.v AS volume, cur.e AS exceptions,
               round(100.0*(cur.v-prv.v)/nullif(prv.v,0)) AS pct_change
        FROM cur LEFT JOIN prv USING (partner) ORDER BY cur.v DESC"""),

    "q9_usage": dict(sql="""
        SELECT date_trunc('month',bucket) AS month, partner, protocol, doc_type, channel,
               sum(txn_count) AS txns, sum(kchar_sum) AS kchar
        FROM txn_rollup_hourly GROUP BY 1,2,3,4,5""", dttm="month"),

    # ---- Q10 Response-SLA / Q11 Diagnostics + KB ----------------------------
    "q10_sla_pairs": dict(sql="""
        SELECT r.rule_id, r.name, t.partner, t.business_ref, t.event_time AS trigger_at,
          resp.event_time AS response_at,
          round(extract(epoch FROM (resp.event_time - t.event_time))/60) AS elapsed_min,
          r.threshold_minutes,
          CASE
            WHEN resp.event_time IS NOT NULL
                 AND extract(epoch FROM (resp.event_time-t.event_time))/60 <= r.threshold_minutes THEN 'met'
            WHEN resp.event_time IS NOT NULL THEN 'missed'
            WHEN now()-t.event_time > make_interval(mins=>r.threshold_minutes)            THEN 'missed'
            WHEN now()-t.event_time > make_interval(mins=>(r.threshold_minutes*0.8)::int) THEN 'at_risk'
            ELSE 'pending' END AS sla_state
        FROM sla_rules r
        JOIN txn_events t ON t.doc_type=r.trigger_doc_type AND t.direction=r.trigger_direction
          AND (r.partner IS NULL OR t.partner=r.partner) AND t.environment=r.environment
        LEFT JOIN LATERAL (
          SELECT event_time FROM txn_events x
          WHERE x.doc_type=r.response_doc_type AND x.direction=r.response_direction
            AND x.business_ref=t.business_ref AND x.event_time >= t.event_time
          ORDER BY x.event_time LIMIT 1) resp ON true
        WHERE r.trigger_doc_type IS NOT NULL AND t.business_ref LIKE 'LOAD%'""", dttm="trigger_at"),

    "q11_signatures": dict(sql="""
        SELECT reason_category, error_code, stage, partner,
          count(*) AS occurrences, min(event_time) AS onset, max(event_time) AS latest,
          count(DISTINCT business_ref) AS refs, sum(value_usd) AS value_exposed
        FROM txn_events WHERE status IN ('failed','rejected') AND reason_category IS NOT NULL
        GROUP BY 1,2,3,4 ORDER BY occurrences DESC"""),

    "q11_attribution": dict(sql="""
        -- Attribution follows the status/reason contract of sql/13: a 'rejected'
        -- message was refused by the receiving partner/their validation (theirs),
        -- and a bad_input_file is a partner-supplied bad file (theirs); a 'failed'
        -- message broke in our transport/translation/system stack (ours).
        SELECT CASE WHEN status='rejected' OR reason_category='bad_input_file'
                    THEN 'partner (theirs)' ELSE 'platform (ours)' END AS attribution,
               count(*) AS occurrences
        FROM txn_events WHERE status IN ('failed','rejected') AND reason_category IS NOT NULL
        GROUP BY 1"""),

    "q11_replay_refail": dict(sql="""
        SELECT business_ref, partner, doc_type, replay_count, current_status
        FROM txn_current WHERE replay_count>0 AND current_status IN ('failed','rejected')"""),

    "q11_dup_source": dict(sql="""
        SELECT partner, control_number, count(*) AS dupes
        FROM txn_events WHERE status='duplicate'
        GROUP BY 1,2 HAVING count(*)>1 ORDER BY 3 DESC"""),

    # ---- All Transactions explorer (every single transaction + drill) --------
    "q_all_txn": dict(sql="""
        SELECT business_ref, environment, lob, partner, channel, protocol, doc_type,
               current_stage, current_status, last_event_at,
               round(extract(epoch FROM (now()-last_event_at))/60) AS age_min,
               value_usd, terminal, replayed, replay_count
        FROM txn_current""", dttm="last_event_at"),

    "q_txn_status": dict(sql="""
        SELECT current_status, count(*) AS txns FROM txn_current GROUP BY 1"""),

    "q_all_events": dict(sql="""
        SELECT event_time, business_ref, interchange_id, partner, doc_type, stage, status,
               coalesce(reason_category,'') AS reason_category, value_usd
        FROM txn_events""", dttm="event_time"),

    # ---- Q12 Document-type / transaction-type command center (Sprint 6) ------
    "q12_doctype_grid": dict(sql="""
        SELECT coalesce(c.business_family,'Other') AS family,
               coalesce(c.label, r.doc_type) AS doc_label, r.doc_type,
               sum(r.txn_count) AS txns, sum(r.failed_count) AS failed,
               sum(r.rejected_count) AS rejected, sum(r.duplicate_count) AS dupes,
               sum(r.value_sum) AS value_usd,
               round(100.0*(1-sum(r.failed_count+r.rejected_count)::numeric/nullif(sum(r.txn_count),0)),1) AS ok_pct
        FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
        GROUP BY 1,2,3"""),

    "q12_family": dict(sql="""
        SELECT coalesce(c.business_family,'Other') AS family,
               count(DISTINCT r.doc_type) AS types, sum(r.txn_count) AS txns,
               sum(r.failed_count) AS failed, sum(r.rejected_count) AS rejected,
               sum(r.duplicate_count) AS dupes, sum(r.value_sum) AS value_usd
        FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
        GROUP BY 1"""),

    "q12_type_protocol": dict(sql="""
        SELECT coalesce(c.label, r.doc_type) AS doc_label, r.doc_type, r.protocol,
               sum(r.txn_count) AS txns
        FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
        GROUP BY 1,2,3"""),

    "q12_type_partner": dict(sql="""
        SELECT coalesce(c.label, r.doc_type) AS doc_label, r.doc_type, r.partner,
               sum(r.txn_count) AS txns
        FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
        GROUP BY 1,2,3"""),

    "q12_family_trend": dict(sql="""
        SELECT r.bucket, coalesce(c.business_family,'Other') AS family,
               sum(r.txn_count) AS txns
        FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
        GROUP BY 1,2""", dttm="bucket"),

    "q11_resolution_kb": dict(sql="""
        SELECT e.event_time, e.business_ref, e.partner, e.reason_category, e.error_code,
               coalesce(dp.likely_cause, dg.likely_cause)         AS likely_cause,
               coalesce(dp.suggested_action, dg.suggested_action) AS suggested_action,
               coalesce(dp.runbook_url, dg.runbook_url)           AS runbook_url
        FROM txn_events e
        LEFT JOIN diagnostic_rules dp ON dp.partner=e.partner
          AND dp.reason_category=e.reason_category AND dp.error_code=e.error_code
        LEFT JOIN diagnostic_rules dg ON dg.partner IS NULL
          AND dg.reason_category=e.reason_category AND dg.error_code=e.error_code
        WHERE e.status IN ('failed','rejected') AND e.reason_category IS NOT NULL""", dttm="event_time"),

    # ---- Sprint R: per-LOB Cockpit (reference-dashboard baseline) ------------
    # All expose `lob` so the LOB native filter scopes them to one line of
    # business, reproducing the reference Brokerage/MT/B2B per-LOB template.
    "q_lob_last": dict(sql="""
        SELECT lob, max(last_event_at) AS last_received, count(*) AS live_refs
        FROM txn_current GROUP BY lob"""),

    "q_lob_details": dict(sql="""
        SELECT lob, business_ref, partner, doc_type, channel, protocol,
               current_stage, current_status, last_event_at, value_usd,
               replayed, replay_count
        FROM txn_current""", dttm="last_event_at"),

    "q_lob_incoming": dict(sql="""
        SELECT lob, business_ref, partner, doc_type, event_time, stage, status, payload
        FROM txn_events
        WHERE direction='in' AND payload IS NOT NULL""", dttm="event_time"),

    "q_lob_outgoing": dict(sql="""
        SELECT lob, business_ref, partner, doc_type, event_time, stage, status, payload
        FROM txn_events
        WHERE direction='out' AND doc_type NOT IN ('997','CONTRL')
          AND payload IS NOT NULL""", dttm="event_time"),

    "q_lob_ack": dict(sql="""
        SELECT lob, business_ref, partner, doc_type, control_number, event_time,
               status AS ack_status, payload AS ack_payload
        FROM txn_events
        WHERE doc_type IN ('997','CONTRL') AND payload IS NOT NULL""", dttm="event_time"),

    "q_lob_exceptions": dict(sql="""
        SELECT lob, event_time, business_ref, partner, doc_type, stage, status,
               coalesce(reason_category,'unknown') AS reason_category,
               coalesce(error_code,'') AS error_code, replay_count, payload AS comments,
               round(extract(epoch FROM (now()-event_time))/60) AS age_min
        FROM txn_events WHERE status IN ('failed','rejected')""", dttm="event_time"),

    # daily partner x day matrix for the Traffic-Report pivot crosstab
    "q_traffic_pivot": dict(sql="""
        SELECT lob, partner, date_trunc('day',bucket)::date AS day, txn_count
        FROM txn_rollup_hourly"""),
}

# ---------------------------------------------------------------------------
# CHARTS.  kind drives the viz config the engine builds.
#   raw    : Table V2, raw columns          (cols, [filters], [order])
#   agg    : Table V2, grouped + metrics     (groupby, metrics)
#   bignum : Big Number total                (metric, subheader)
#   pie    : Pie                             (groupby, metric)
#   bar    : categorical bar                 (dim, metric, [series], [row_limit])
#   timebar: time-series bar                 (metric, [series])
# metrics are (label, sql_expression) pairs.
# ---------------------------------------------------------------------------
T_ARRIVAL = "Arrival & Channel Health"
T_EXC     = "Exceptions"
T_FLOW    = "Flow — EDI & API summary"
T_FILES   = "Files"
T_LOOKUP  = "Lookup & Replay"
T_ACKS    = "Acknowledgments"
T_SLA     = "Partner SLA & Activity"
T_USAGE   = "Usage"
T_RESP    = "Response-SLA"
T_DIAG    = "Diagnostics"
T_TYPES   = "Transaction Types"
T_ALLTXN  = "All Transactions"
T_OVERVIEW = "Overview"
# Sprint R — per-LOB Cockpit (reference-dashboard template)
T_LOB_OV   = "LOB · Overview"
T_LOB_DET  = "LOB · Details"
T_LOB_TRAF = "LOB · Traffic"
T_LOB_EXC  = "LOB · Exceptions"

# ---- column-format helpers for Table V2 (column_config) --------------------
CUR  = {"d3NumberFormat": "$,.0f"}                       # currency
CURB = {"d3NumberFormat": "$,.0f", "showCellBars": True} # currency + data bar
NUMB = {"d3NumberFormat": ",.0f", "showCellBars": True}  # integer + data bar
PCTB = {"d3NumberFormat": ".1f",  "showCellBars": True}  # percent value + bar
BARS = {"showCellBars": True}                            # data bar only

CHARTS = [
    # ===== Q1 — Arrival & Channel Health =====
    dict(slice="Monitors reporting", tab=T_ARRIVAL, dataset="q1_sweep_integrity",
         kind="bignum", metric=("reporting", "SUM((NOT is_stale)::int)"),
         subheader="monitors not silent", w=3, h=40),
    dict(slice="Stale / silent monitors", tab=T_ARRIVAL, dataset="q1_stale_monitors",
         kind="raw", cols=["monitor_name","channel","environment","last_run_at","mins_silent"],
         order=[("mins_silent", False)], w=3, h=40),
    dict(slice="Hung pipelines", tab=T_ARRIVAL, dataset="q1_hung_pipeline",
         kind="raw", cols=["pipeline","environment","queue_depth","mq_depth","consume_rate","last_consumed_at"],
         w=6, h=40),
    dict(slice="Missing expected feeds", tab=T_ARRIVAL, dataset="q1_missing_feeds",
         kind="raw", cols=["partner","doc_type","channel","environment","mins_overdue","last_seen_at"],
         order=[("mins_overdue", False)], w=6, h=42),
    dict(slice="Dead / degraded connections", tab=T_ARRIVAL, dataset="q1_endpoint_down",
         kind="raw", cols=["channel","endpoint","partner","environment","status","last_ok_at"], w=6, h=42),
    dict(slice="Channel health", tab=T_ARRIVAL, dataset="q1_channel_health",
         kind="raw", cols=["channel","endpoint","partner","environment","status","last_ok_at","cert_expires_at"],
         w=6, h=44),
    dict(slice="Landed, not picked up", tab=T_ARRIVAL, dataset="q1_landed_not_picked",
         kind="raw", cols=["business_ref","partner","channel","doc_type","environment","age_min"],
         order=[("age_min", False)], w=6, h=42),
    dict(slice="Stuck / aging transactions", tab=T_ARRIVAL, dataset="q1_stuck",
         kind="raw", cols=["business_ref","partner","channel","doc_type","current_stage","age_min","value_usd"],
         order=[("age_min", False)], col_fmt={"age_min": BARS, "value_usd": CUR}, w=6, h=44),
    dict(slice="Cert / key expiry", tab=T_ARRIVAL, dataset="q1_cert_expiry",
         kind="raw", cols=["endpoint","partner","channel","environment","cert_expires_at","days_left"],
         order=[("days_left", True)], col_fmt={"days_left": BARS},
         heat=[{"column":"days_left","operator":"<","targetValue":7,"colorScheme":"#e04355"}], w=6, h=42),

    # ===== Q3 — Exceptions =====
    dict(slice="Failed (period)", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("failed","SUM(failed_count)"), subheader="hard failures", trend=True, w=4, h=40),
    dict(slice="Rejected (period)", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("rejected","SUM(rejected_count)"),
         subheader="rejected — its own state", trend=True, w=4, h=40),
    dict(slice="Duplicates suppressed", tab=T_EXC, dataset="vw_rollup",
         kind="bignum", metric=("duplicate","SUM(duplicate_count)"),
         subheader="counted, not alerted", w=4, h=40),
    dict(slice="Exceptions by reason", tab=T_EXC, dataset="q3_exceptions_by_reason",
         kind="bar", dim="reason_category", metric=("occurrences","SUM(occurrences)"),
         row_limit=20, w=6, h=50),
    dict(slice="Exception queue", tab=T_EXC, dataset="q3_exception_queue",
         kind="raw",
         cols=["event_time","business_ref","partner","doc_type","status","reason_category","value_usd","age_min"],
         order=[("event_time", False)], row_limit=200,
         col_fmt={"value_usd": CUR, "age_min": BARS}, w=6, h=50),

    # ===== Q2 — Flow (EDI & API summary), all from the rollup =====
    dict(slice="Total transactions", tab=T_FLOW, dataset="vw_rollup",
         kind="bignum", metric=("total","SUM(txn_count)"), subheader="all protocols", w=3, h=38),
    dict(slice="EDI transactions", tab=T_FLOW, dataset="vw_rollup",
         kind="bignum", metric=("edi","SUM(txn_count) FILTER (WHERE protocol='edi')"),
         subheader="EDI", w=3, h=38),
    dict(slice="API transactions", tab=T_FLOW, dataset="vw_rollup",
         kind="bignum", metric=("api","SUM(txn_count) FILTER (WHERE protocol='api')"),
         subheader="API", w=3, h=38),
    dict(slice="Auto-processed %", tab=T_FLOW, dataset="vw_rollup",
         kind="bignum",
         metric=("auto_pct","100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))"),
         subheader="straight-through %", number_format=".1f", w=3, h=38),
    dict(slice="Data volume (kchar)", tab=T_FLOW, dataset="vw_rollup",
         kind="bignum", metric=("kchar","SUM(kchar_sum)"), subheader="kilochars", trend=True, w=3, h=38),
    dict(slice="EDI vs API split", tab=T_FLOW, dataset="vw_rollup",
         kind="pie", groupby="protocol", metric=("txns","SUM(txn_count)"), w=4, h=50),
    dict(slice="Inbound vs outbound", tab=T_FLOW, dataset="vw_rollup",
         kind="bar", dim="direction", metric=("txns","SUM(txn_count)"), w=5, h=50),
    dict(slice="Volume by message type", tab=T_FLOW, dataset="vw_rollup",
         kind="bar", dim="doc_type", metric=("txns","SUM(txn_count)"), series="protocol", w=6, h=50),
    dict(slice="Throughput over time", tab=T_FLOW, dataset="vw_rollup",
         kind="timebar", metric=("txns","SUM(txn_count)"), series="protocol", w=6, h=50),
    dict(slice="Message-type volumetric grid", tab=T_FLOW, dataset="vw_rollup",
         kind="agg", groupby=["doc_type","protocol","direction"],
         metrics=[("txns","SUM(txn_count)"),("failed","SUM(failed_count)"),("rejected","SUM(rejected_count)")],
         order=[("txns", False)], w=6, h=52),
    dict(slice="Volume by partner (top 20)", tab=T_FLOW, dataset="vw_rollup",
         kind="bar", dim="partner", metric=("txns","SUM(txn_count)"), row_limit=20, w=6, h=50),
    dict(slice="Volume by LOB", tab=T_FLOW, dataset="vw_rollup",
         kind="bar", dim="lob", metric=("txns","SUM(txn_count)"), w=6, h=50),

    # ===== Q4 — Files =====
    dict(slice="Incoming vs outgoing files (24h)", tab=T_FILES, dataset="q4_file_feed",
         kind="bar", dim="direction", metric=("files","SUM(files)"), series="status", w=4, h=46),
    dict(slice="File explorer", tab=T_FILES, dataset="q4_files", kind="raw",
         cols=["received_at","file_name","direction","partner","channel","status","declared_txn_count","kchar"],
         order=[("received_at", False)], row_limit=200, w=8, h=46),
    dict(slice="Files missing transactions", tab=T_FILES, dataset="q4_files_missing", kind="raw",
         cols=["interchange_id","file_name","partner","declared_txn_count","actual_txns","missing_inside_file"],
         order=[("missing_inside_file", False)], w=6, h=42),
    dict(slice="Rejected at receipt (pre-parse)", tab=T_FILES, dataset="q4_rejected_receipt", kind="raw",
         cols=["received_at","file_name","partner","channel","reason_category"], w=6, h=42),

    # ===== Q4 lookup + Q8 replay =====
    dict(slice="Replays today", tab=T_LOOKUP, dataset="q8_replayed",
         kind="bignum", metric=("replays","COUNT(*)"), subheader="messages replayed", w=3, h=38),
    dict(slice="Transaction status (lookup)", tab=T_LOOKUP, dataset="q4_txn_lookup", kind="raw",
         cols=["business_ref","partner","doc_type","current_stage","current_status","replayed","replay_count","value_usd"],
         order=[("last_event_at", False)], row_limit=100, w=9, h=44),
    dict(slice="Step history", tab=T_LOOKUP, dataset="q4_step_history", kind="raw",
         cols=["event_time","business_ref","interchange_id","stage","status","reason_category"],
         order=[("event_time", False)], row_limit=200, w=6, h=46),
    dict(slice="Replayed messages", tab=T_LOOKUP, dataset="q8_replayed", kind="raw",
         cols=["business_ref","partner","doc_type","replay_count","replayed_at","current_status"],
         order=[("replayed_at", False)], w=6, h=46),

    # ===== Q5 — Acknowledgments =====
    dict(slice="Missing acks", tab=T_ACKS, dataset="q5_acks",
         kind="bignum", metric=("missing","SUM((fa_state='missing')::int)"), subheader="no 997/CONTRL in window", w=3, h=38),
    dict(slice="Rejected acks", tab=T_ACKS, dataset="q5_acks",
         kind="bignum", metric=("rejected","SUM((fa_state='rejected')::int)"), subheader="negative ack", w=3, h=38),
    dict(slice="FA tracking", tab=T_ACKS, dataset="q5_acks", kind="raw",
         cols=["sent_at","business_ref","partner","doc_type","ack_at","ack_status","fa_state"],
         order=[("sent_at", False)], w=6, h=44),

    # ===== Q6 SLA + Q7 activity =====
    dict(slice="Partner SLA scorecard", tab=T_SLA, dataset="q6_partner_sla", kind="raw",
         cols=["partner","total","met","missed","pct_met","avg_min","max_min","penalty_usd"],
         order=[("pct_met", True)],
         col_fmt={"total": NUMB, "pct_met": PCTB, "penalty_usd": CUR},
         heat=[{"column":"pct_met","operator":"<","targetValue":95,"colorScheme":"#fcc419"}], w=7, h=46),
    dict(slice="% Met by partner", tab=T_SLA, dataset="q6_partner_sla",
         kind="bar", dim="partner", metric=("pct_met","MAX(pct_met)"), w=5, h=46),
    dict(slice="Top partners by volume", tab=T_SLA, dataset="q7_partner_activity",
         kind="bar", dim="partner", metric=("volume","SUM(volume)"), w=4, h=44),
    dict(slice="Top partners by exceptions", tab=T_SLA, dataset="q7_partner_activity",
         kind="bar", dim="partner", metric=("exceptions","SUM(exceptions)"), w=4, h=44),
    dict(slice="Change vs prior period", tab=T_SLA, dataset="q7_partner_activity", kind="raw",
         cols=["partner","volume","exceptions","pct_change"], order=[("volume", False)], w=4, h=44),

    # ===== Q9 — Usage =====
    dict(slice="Monthly volume (export)", tab=T_USAGE, dataset="q9_usage", kind="agg",
         groupby=["month","partner","protocol"],
         metrics=[("txns","SUM(txns)"),("kchar","SUM(kchar)")], order=[("txns", False)],
         row_limit=500, w=8, h=48),
    dict(slice="Volume by protocol", tab=T_USAGE, dataset="q9_usage",
         kind="bar", dim="protocol", metric=("txns","SUM(txns)"), w=4, h=48),

    # ===== Q10 — Response-SLA compliance =====
    dict(slice="Overall compliance %", tab=T_RESP, dataset="q10_sla_pairs",
         kind="gauge",
         metric=("compliance","100.0*count(*) FILTER (WHERE sla_state='met')/NULLIF(count(*) FILTER (WHERE sla_state IN ('met','missed')),0)"),
         min_val=0, max_val=100, w=3, h=48),
    dict(slice="Compliance by rule", tab=T_RESP, dataset="q10_sla_pairs",
         kind="bar", dim="name", metric=("cnt","COUNT(*)"), series="sla_state", w=6, h=48),
    dict(slice="Responses due soon (at-risk)", tab=T_RESP, dataset="q10_sla_pairs", kind="raw",
         cols=["name","partner","business_ref","trigger_at","threshold_minutes"],
         filters=[("sla_state","==","at_risk")], order=[("trigger_at", False)], w=6, h=48),
    dict(slice="Breaches (missed)", tab=T_RESP, dataset="q10_sla_pairs", kind="raw",
         cols=["name","partner","business_ref","trigger_at","elapsed_min","threshold_minutes"],
         filters=[("sla_state","==","missed")], order=[("trigger_at", False)], row_limit=200, w=12, h=46),

    # ===== Q11 — Diagnostics + Resolution KB =====
    dict(slice="Failure signatures", tab=T_DIAG, dataset="q11_signatures", kind="raw",
         cols=["reason_category","error_code","stage","partner","occurrences","onset","refs","value_exposed"],
         order=[("occurrences", False)], w=8, h=46),
    dict(slice="Partner vs platform", tab=T_DIAG, dataset="q11_attribution",
         kind="pie", groupby="attribution", metric=("occurrences","SUM(occurrences)"), w=4, h=46),
    dict(slice="Re-failed replays", tab=T_DIAG, dataset="q11_replay_refail", kind="raw",
         cols=["business_ref","partner","doc_type","replay_count","current_status"], w=4, h=42),
    dict(slice="Duplicate sources", tab=T_DIAG, dataset="q11_dup_source", kind="raw",
         cols=["partner","control_number","dupes"], order=[("dupes", False)], w=4, h=42),
    dict(slice="Resolution KB (per exception)", tab=T_DIAG, dataset="q11_resolution_kb", kind="raw",
         cols=["business_ref","partner","reason_category","error_code","likely_cause","suggested_action","runbook_url"],
         order=[("event_time", False)], row_limit=200, w=12, h=48),

    # ===== Q12 — Transaction Types (Sprint 6, Cleo core: by message type / family) =====
    dict(slice="Document types", tab=T_TYPES, dataset="q12_doctype_grid",
         kind="bignum", metric=("types","COUNT(*)"), subheader="distinct doc types", w=3, h=38),
    dict(slice="Document value (period)", tab=T_TYPES, dataset="q12_doctype_grid",
         kind="bignum", metric=("value","SUM(value_usd)"), subheader="$ across all types", w=3, h=38),
    dict(slice="Volume by family", tab=T_TYPES, dataset="q12_doctype_grid",
         kind="treemap", groupby=["family","doc_label"], metric=("txns","SUM(txns)"), w=6, h=46),
    dict(slice="Exceptions by family", tab=T_TYPES, dataset="q12_family",
         kind="bar", dim="family", metric=("exceptions","SUM(failed)+SUM(rejected)"), w=6, h=46),
    dict(slice="Document-type grid", tab=T_TYPES, dataset="q12_doctype_grid", kind="raw",
         cols=["doc_label","family","txns","ok_pct","failed","rejected","dupes","value_usd"],
         order=[("txns", False)], row_limit=50,
         col_fmt={"txns": NUMB, "ok_pct": PCTB, "value_usd": CUR, "failed": BARS, "rejected": BARS}, w=8, h=50),
    dict(slice="EDI vs API by type", tab=T_TYPES, dataset="q12_type_protocol",
         kind="bar", dim="doc_label", metric=("txns","SUM(txns)"), series="protocol", w=4, h=50),
    dict(slice="Top partners (filter by type)", tab=T_TYPES, dataset="q12_type_partner",
         kind="bar", dim="partner", metric=("txns","SUM(txns)"), row_limit=20, w=6, h=46),
    dict(slice="Throughput by family", tab=T_TYPES, dataset="q12_family_trend",
         kind="timebar", metric=("txns","SUM(txns)"), series="family", w=6, h=46),

    # ===== All Transactions explorer (every transaction; drill target) =====
    dict(slice="Live transactions", tab=T_ALLTXN, dataset="q_all_txn",
         kind="bignum", metric=("live","COUNT(*)"), subheader="distinct business refs", w=3, h=38),
    dict(slice="In-flight (not terminal)", tab=T_ALLTXN, dataset="q_all_txn",
         kind="bignum", metric=("inflight","SUM((NOT terminal)::int)"), subheader="still moving", w=3, h=38),
    dict(slice="Terminal", tab=T_ALLTXN, dataset="q_all_txn",
         kind="bignum", metric=("terminal","SUM(terminal::int)"), subheader="completed/failed-final", w=3, h=38),
    dict(slice="Replayed (all-time)", tab=T_ALLTXN, dataset="q_all_txn",
         kind="bignum", metric=("replayed","SUM(replayed::int)"), subheader="reprocessed", w=3, h=38),
    dict(slice="Status distribution", tab=T_ALLTXN, dataset="q_txn_status",
         kind="bar", dim="current_status", metric=("txns","SUM(txns)"), w=4, h=46),
    dict(slice="All transactions", tab=T_ALLTXN, dataset="q_all_txn", kind="raw",
         cols=["business_ref","partner","lob","doc_type","channel","protocol",
               "current_stage","current_status","age_min","value_usd","replayed"],
         order=[("last_event_at", False)], row_limit=1000,
         col_fmt={"age_min": BARS, "value_usd": CUR}, w=8, h=56),
    dict(slice="Transaction events (drill by ref)", tab=T_ALLTXN, dataset="q_all_events", kind="raw",
         cols=["event_time","business_ref","interchange_id","partner","doc_type","stage","status","reason_category"],
         order=[("event_time", False)], row_limit=500, time_range="Last 30 days", w=12, h=50),

    # ===== Overview (curated landing) =====
    dict(slice="Overview: Transactions", tab=T_OVERVIEW, dataset="vw_rollup",
         kind="bignum", metric=("total","SUM(txn_count)"), subheader="total volume", w=3, h=40),
    dict(slice="Overview: Auto-processed %", tab=T_OVERVIEW, dataset="vw_rollup",
         kind="bignum",
         metric=("auto","100.0*(1 - SUM(failed_count+rejected_count)::numeric/NULLIF(SUM(txn_count),0))"),
         subheader="straight-through", number_format=".1f", w=3, h=40),
    dict(slice="Overview: Open exceptions", tab=T_OVERVIEW, dataset="vw_rollup",
         kind="bignum", metric=("exc","SUM(failed_count)+SUM(rejected_count)"),
         subheader="failed + rejected", w=3, h=40),
    dict(slice="Overview: In-flight", tab=T_OVERVIEW, dataset="q_all_txn",
         kind="bignum", metric=("inflight","SUM((NOT terminal)::int)"), subheader="still moving", w=3, h=40),
    dict(slice="Overview: Hung pipelines", tab=T_OVERVIEW, dataset="q1_hung_pipeline",
         kind="bignum", metric=("hung","COUNT(*)"), subheader="running, consuming nothing", w=3, h=36),
    dict(slice="Overview: Missing feeds", tab=T_OVERVIEW, dataset="q1_missing_feeds",
         kind="bignum", metric=("missing","COUNT(*)"), subheader="overdue past grace", w=3, h=36),
    dict(slice="Overview: Stale monitors", tab=T_OVERVIEW, dataset="q1_stale_monitors",
         kind="bignum", metric=("stale","COUNT(*)"), subheader="sweep silent", w=3, h=36),
    dict(slice="Overview: At-risk responses", tab=T_OVERVIEW, dataset="q10_sla_pairs",
         kind="bignum", metric=("atrisk","COUNT(*) FILTER (WHERE sla_state='at_risk')"),
         subheader="before breach", w=3, h=36),
    dict(slice="Overview: EDI vs API", tab=T_OVERVIEW, dataset="vw_rollup",
         kind="pie", groupby="protocol", metric=("txns","SUM(txn_count)"), w=4, h=46),
    dict(slice="Overview: Volume by family", tab=T_OVERVIEW, dataset="q12_doctype_grid",
         kind="treemap", groupby=["family","doc_label"], metric=("txns","SUM(txns)"), w=4, h=46),
    dict(slice="Overview: Exceptions by reason", tab=T_OVERVIEW, dataset="q3_exceptions_by_reason",
         kind="bar", dim="reason_category", metric=("occ","SUM(occurrences)"), row_limit=12, w=4, h=46),
    dict(slice="Overview: Hung pipeline worklist", tab=T_OVERVIEW, dataset="q1_hung_pipeline",
         kind="raw", cols=["pipeline","environment","queue_depth","consume_rate","last_consumed_at"], w=6, h=42),
    dict(slice="Overview: Missing feed worklist", tab=T_OVERVIEW, dataset="q1_missing_feeds",
         kind="raw", cols=["partner","doc_type","channel","mins_overdue","last_seen_at"],
         order=[("mins_overdue", False)], col_fmt={"mins_overdue": BARS}, w=6, h=42),

    # ===== Sprint R — LOB Cockpit · Overview (reference template) =====
    dict(slice="LOB: Total messages received", tab=T_LOB_OV, dataset="vw_rollup",
         kind="bignum", metric=("total","SUM(txn_count)"), subheader="all messages", w=3, h=40),
    dict(slice="LOB: Success", tab=T_LOB_OV, dataset="vw_rollup",
         kind="bignum", metric=("success","SUM(txn_count - failed_count - rejected_count)"),
         subheader="processed / validated", w=3, h=40),
    dict(slice="LOB: Failure", tab=T_LOB_OV, dataset="vw_rollup",
         kind="bignum", metric=("failure","SUM(failed_count + rejected_count)"),
         subheader="failed + rejected", w=3, h=40),
    dict(slice="LOB: Last message received", tab=T_LOB_OV, dataset="q_lob_last",
         kind="raw", cols=["lob","last_received","live_refs"], order=[("last_received", False)],
         col_fmt={"live_refs": NUMB}, w=3, h=40),
    dict(slice="LOB: Message by partner", tab=T_LOB_OV, dataset="vw_rollup",
         kind="bar", dim="partner", metric=("txns","SUM(txn_count)"), row_limit=15, w=6, h=50),
    dict(slice="LOB: Message by status", tab=T_LOB_OV, dataset="vw_rollup",
         kind="donut", groupby="status", metric=("txns","SUM(txn_count)"), w=6, h=50),
    dict(slice="LOB: Processing trend", tab=T_LOB_OV, dataset="vw_rollup",
         kind="timebar", metric=("txns","SUM(txn_count)"), w=6, h=46),
    dict(slice="LOB: Message by type", tab=T_LOB_OV, dataset="vw_rollup",
         kind="donut", groupby="doc_type", metric=("txns","SUM(txn_count)"), w=6, h=46),
    dict(slice="LOB: Count by message type", tab=T_LOB_OV, dataset="vw_rollup",
         kind="bar", dim="doc_type", metric=("txns","SUM(txn_count)"), series="status",
         row_limit=30, w=12, h=48),

    # ===== Sprint R — LOB Cockpit · Details (master-detail payload drill) =====
    dict(slice="LOB: Details", tab=T_LOB_DET, dataset="q_lob_details", kind="raw",
         cols=["business_ref","partner","doc_type","channel","current_stage",
               "current_status","last_event_at","value_usd","replay_count"],
         order=[("last_event_at", False)], row_limit=200,
         col_fmt={"value_usd": CUR}, w=12, h=52),
    dict(slice="LOB: Incoming data", tab=T_LOB_DET, dataset="q_lob_incoming", kind="raw",
         cols=["event_time","business_ref","doc_type","status","payload"],
         order=[("event_time", False)], row_limit=50, w=6, h=48),
    dict(slice="LOB: Outgoing data", tab=T_LOB_DET, dataset="q_lob_outgoing", kind="raw",
         cols=["event_time","business_ref","doc_type","status","payload"],
         order=[("event_time", False)], row_limit=50, w=6, h=48),
    dict(slice="LOB: Ack data", tab=T_LOB_DET, dataset="q_lob_ack", kind="raw",
         cols=["event_time","business_ref","doc_type","control_number","ack_status","ack_payload"],
         order=[("event_time", False)], row_limit=50, w=12, h=44),

    # ===== Sprint R — LOB Cockpit · Traffic (trend + pivot crosstab) =====
    dict(slice="LOB: Traffic trend", tab=T_LOB_TRAF, dataset="vw_rollup",
         kind="timebar", metric=("txns","SUM(txn_count)"), series="doc_type", w=12, h=48),
    dict(slice="LOB: Traffic report (partner x day)", tab=T_LOB_TRAF, dataset="q_traffic_pivot",
         kind="pivot", rows=["partner"], columns=["day"], metric=("txns","SUM(txn_count)"), w=12, h=56),

    # ===== Sprint R — LOB Cockpit · Exceptions (triage queue) =====
    dict(slice="LOB: Open exceptions", tab=T_LOB_EXC, dataset="q_lob_exceptions",
         kind="bignum", metric=("open","COUNT(*)"), subheader="failed + rejected", w=3, h=38),
    dict(slice="LOB: Resubmitted", tab=T_LOB_EXC, dataset="q_lob_exceptions",
         kind="bignum", metric=("resub","SUM((replay_count>0)::int)"), subheader="replayed at least once", w=3, h=38),
    dict(slice="LOB: Exception by reason", tab=T_LOB_EXC, dataset="q_lob_exceptions",
         kind="bar", dim="reason_category", metric=("occ","COUNT(*)"), row_limit=15, w=6, h=38),
    dict(slice="LOB: Exception queue", tab=T_LOB_EXC, dataset="q_lob_exceptions", kind="raw",
         cols=["event_time","business_ref","partner","doc_type","stage","status",
               "reason_category","error_code","replay_count","age_min","comments"],
         order=[("event_time", False)], row_limit=200,
         col_fmt={"age_min": BARS, "replay_count": BARS}, w=12, h=54),
]

# Top-level sections -> sub-tabs (nested tabs). Reordered by ops workflow.
SECTIONS = [
    ("Overview",        [T_OVERVIEW]),
    ("LOB Cockpit",     [T_LOB_OV, T_LOB_DET, T_LOB_TRAF, T_LOB_EXC]),
    ("Operations",      [T_ARRIVAL, T_EXC, T_DIAG]),
    ("Flow",            [T_FLOW, T_TYPES]),
    ("Transactions",    [T_ALLTXN, T_LOOKUP, T_FILES]),
    ("SLA & Partners",  [T_RESP, T_ACKS, T_SLA, T_USAGE]),
]

DASHBOARD_TITLE = "Integration Visibility Cockpit"
TAB_ORDER = [T_ARRIVAL, T_EXC, T_FLOW, T_TYPES, T_FILES, T_LOOKUP, T_ACKS, T_SLA, T_USAGE, T_RESP, T_DIAG]

# Native dashboard filters (column-scoped). Applied across all compatible charts.
NATIVE_FILTERS = [
    ("Environment", "environment"),
    ("LOB", "lob"),
    ("Partner", "partner"),
    ("Protocol", "protocol"),
    ("Channel", "channel"),
    ("Doc type", "doc_type"),
]
