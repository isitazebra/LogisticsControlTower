#!/usr/bin/env python3
"""Declarative spec for the **EDI Anomaly Control Tower** — a parallel dashboard
built on the reference `mp_demo` dataset (schema edi_anomaly_dashboard_dataset),
loaded into the same Neon DB alongside `public`.

Same engine as cockpit_spec.py (build_cockpit.build turns these dicts into Superset
resources); build_anomaly.py points the engine's spec global at this module so the
two dashboards never collide. Dataset names are prefixed `edi_` and chart slice
names `EDI · …` to keep both sets disjoint in the shared workspace.

Charts read the 24 pre-built analytic views (the dataset's own semantic layer):
control-tower KPIs, anomaly KPIs, partner health/risk, EDI funnel & SLA, shipment
status/aging. "Postgres is the contract" — Superset just reads the views."""

SCHEMA = "edi_anomaly_dashboard_dataset"
def _v(name): return f"SELECT * FROM {SCHEMA}.{name}"

# ---------------------------------------------------------------------------
# VIRTUAL DATASETS  (name -> SQL [, temporal column])
# Temporal cols cast to ::timestamp so Superset time-grain works on date views.
# ---------------------------------------------------------------------------
DATASETS = {
    "edi_ct_kpis":      dict(sql=_v("vw_control_tower_kpis")),
    "edi_ct_daily":     dict(sql=f"""
        SELECT metric_date::timestamp AS metric_date, total_shipments, active_partners,
               sla_met_count, sla_breach_count, sla_compliance_pct,
               total_shipment_value, avg_shipment_value
        FROM {SCHEMA}.vw_control_tower_kpis_daily""", dttm="metric_date"),
    "edi_anomaly_daily": dict(sql=f"""
        SELECT metric_date::timestamp AS metric_date, total_anomalies, critical_anomalies,
               high_anomalies, medium_anomalies, low_anomalies, open_anomalies,
               in_progress_anomalies, resolved_anomalies, total_business_impact
        FROM {SCHEMA}.vw_anomaly_kpis_daily""", dttm="metric_date"),
    "edi_root_cause":   dict(sql=_v("vw_root_cause_breakdown")),
    "edi_status_dist":  dict(sql=_v("vw_shipment_status_distribution")),
    "edi_exceptions":   dict(sql=_v("vw_exception_workbench"), dttm="detected_date"),
    "edi_partner_scorecard": dict(sql=_v("vw_partner_health_scorecard")),
    "edi_partner_risk": dict(sql=_v("vw_partner_risk_matrix")),
    "edi_funnel":       dict(sql=f"""
        SELECT transaction_date::timestamp AS transaction_date, partner_id, transaction_type,
               funnel_stage, funnel_order, shipment_count, transaction_count
        FROM {SCHEMA}.vw_edi_funnel""", dttm="transaction_date"),
    "edi_flow_complete": dict(sql=f"""
        SELECT shipment_date::timestamp AS shipment_date, partner_id, partner_name,
               has_204, has_990, has_214, has_210, edi_flow_status
        FROM {SCHEMA}.vw_edi_flow_completeness""", dttm="shipment_date"),
    "edi_msg_sla":      dict(sql=f"""
        SELECT metric_date::timestamp AS metric_date, partner_id, partner_name, sla_type,
               total_messages, within_sla_count, breach_count, missing_count, sla_percent
        FROM {SCHEMA}.vw_message_sla_daily""", dttm="metric_date"),
    "edi_ack_sla":      dict(sql=f"""
        SELECT metric_date::timestamp AS metric_date, partner_id, transaction_type,
               total_transactions, ack_within_sla_count, ack_breach_count,
               missing_ack_count, ack_sla_percent
        FROM {SCHEMA}.vw_ack_sla_daily""", dttm="metric_date"),
    "edi_txn_volume":   dict(sql=f"""
        SELECT transaction_date::timestamp AS transaction_date, transaction_hour, partner_id,
               transaction_type, processing_status, transaction_count,
               ack_received_count, ack_missing_count
        FROM {SCHEMA}.vw_edi_transaction_volume""", dttm="transaction_date"),
    "edi_daily_volume": dict(sql=f"""
        SELECT shipment_date::timestamp AS shipment_date, partner_id, partner_name,
               actual_volume, expected_volume, volume_deviation_pct
        FROM {SCHEMA}.vw_daily_shipment_volume""", dttm="shipment_date"),
    "edi_status_aging": dict(sql=_v("vw_shipment_status_aging"), dttm="status_timestamp"),
    "edi_delay_hist":   dict(sql=f"""
        SELECT shipment_id, partner_id, partner_name, carrier_name, origin_city,
               destination_city, shipment_date, delay_hours, delivery_performance,
               delay_severity, shipment_value
        FROM {SCHEMA}.vw_delivered_shipment_delay_history""", dttm="shipment_date"),
    "edi_sla_perf":     dict(sql=f"""
        SELECT shipment_date::timestamp AS shipment_date, partner_id, total_shipments,
               sla_met_count, sla_breach_count, sla_pct
        FROM {SCHEMA}.vw_sla_performance_daily""", dttm="shipment_date"),
}

# ---- table column-format helpers (mirror cockpit_spec) ---------------------
CUR  = {"d3NumberFormat": "$,.0f"}
CURB = {"d3NumberFormat": "$,.0f", "showCellBars": True}
NUMB = {"d3NumberFormat": ",.0f", "showCellBars": True}
PCTB = {"d3NumberFormat": ".1f",  "showCellBars": True}
BARS = {"showCellBars": True}

# ---- tab + section constants ----------------------------------------------
T_CT   = "Control Tower"
T_ANOM = "Anomalies"
T_PART = "Partners"
T_FLOW = "EDI Flow & SLA"
T_SHIP = "Shipments"

# ---------------------------------------------------------------------------
# CHARTS
# ---------------------------------------------------------------------------
CHARTS = [
    # ===== Control Tower =====
    dict(slice="EDI · Total shipments", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("shipments","MAX(total_shipments)"),
         subheader="shipments", w=2, h=38),
    dict(slice="EDI · Active partners", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("partners","MAX(active_partners)"),
         subheader="active partners", w=2, h=38),
    dict(slice="EDI · Total anomalies", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("anomalies","MAX(total_anomalies)"),
         subheader="anomalies", w=2, h=38),
    dict(slice="EDI · Critical anomalies", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("critical","MAX(critical_anomalies)"),
         subheader="critical", w=2, h=38),
    dict(slice="EDI · SLA compliance %", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("sla","MAX(sla_compliance_pct)"),
         subheader="% SLA met", number_format=".1f", w=2, h=38),
    dict(slice="EDI · Avg partner health", tab=T_CT, dataset="edi_ct_kpis",
         kind="bignum", metric=("health","MAX(avg_partner_health_score)"),
         subheader="avg health score", number_format=".0f", w=2, h=38),

    dict(slice="EDI · SLA compliance (gauge)", tab=T_CT, dataset="edi_ct_kpis",
         kind="gauge", metric=("sla","MAX(sla_compliance_pct)"),
         min_val=0, max_val=100, w=4, h=46),
    dict(slice="EDI · Shipment status mix", tab=T_CT, dataset="edi_status_dist",
         kind="donut", groupby="current_status", metric=("shipments","SUM(shipment_count)"),
         w=4, h=46),
    dict(slice="EDI · Anomalies by severity", tab=T_CT, dataset="edi_root_cause",
         kind="donut", groupby="severity", metric=("anomalies","SUM(anomaly_count)"),
         w=4, h=46),

    dict(slice="EDI · Daily shipment volume", tab=T_CT, dataset="edi_daily_volume",
         kind="ts", chart="bar", x="shipment_date", metric=("actual","SUM(actual_volume)"),
         w=6, h=48),
    dict(slice="EDI · SLA compliance trend", tab=T_CT, dataset="edi_ct_daily",
         kind="ts", chart="line", x="metric_date", metric=("sla_pct","MAX(sla_compliance_pct)"),
         number_format=".1f", w=6, h=48),

    # ===== Anomalies =====
    dict(slice="EDI · Anomalies (total)", tab=T_ANOM, dataset="edi_exceptions",
         kind="bignum", metric=("n","COUNT(*)"), subheader="open + resolved", w=3, h=38),
    dict(slice="EDI · Critical (count)", tab=T_ANOM, dataset="edi_exceptions",
         kind="bignum", metric=("n","COUNT(*) FILTER (WHERE severity='Critical')"),
         subheader="critical severity", w=3, h=38),
    dict(slice="EDI · Open anomalies", tab=T_ANOM, dataset="edi_exceptions",
         kind="bignum", metric=("n","COUNT(*) FILTER (WHERE anomaly_status='Open')"),
         subheader="status = open", w=3, h=38),
    dict(slice="EDI · Business impact $", tab=T_ANOM, dataset="edi_exceptions",
         kind="bignum", metric=("impact","SUM(business_impact_amount)"),
         subheader="total impact", number_format="$,.0f", w=3, h=38),

    dict(slice="EDI · Anomalies by type", tab=T_ANOM, dataset="edi_root_cause",
         kind="bar", dim="anomaly_type", metric=("anomalies","SUM(anomaly_count)"),
         row_limit=20, w=6, h=48),
    dict(slice="EDI · Anomaly trend (daily)", tab=T_ANOM, dataset="edi_anomaly_daily",
         kind="ts", chart="bar", x="metric_date", metric=("anomalies","SUM(total_anomalies)"),
         w=6, h=48),

    dict(slice="EDI · Anomalies by status", tab=T_ANOM, dataset="edi_exceptions",
         kind="donut", groupby="anomaly_status", metric=("n","COUNT(*)"), w=4, h=46),
    dict(slice="EDI · Business impact trend", tab=T_ANOM, dataset="edi_anomaly_daily",
         kind="ts", chart="line", x="metric_date", metric=("impact","SUM(total_business_impact)"),
         number_format="$,.0f", w=8, h=46),

    dict(slice="EDI · Exception workbench", tab=T_ANOM, dataset="edi_exceptions",
         kind="raw",
         cols=["detected_date","partner_name","shipment_id","anomaly_type","severity",
               "anomaly_status","business_impact_amount","root_cause_category","recommended_action"],
         order=[("detected_date", False)], row_limit=200,
         col_fmt={"business_impact_amount": CUR}, w=12, h=52),

    # ===== Partners =====
    dict(slice="EDI · Partner health scorecard", tab=T_PART, dataset="edi_partner_scorecard",
         kind="raw",
         cols=["partner_name","shipment_count","anomaly_count","critical_anomalies",
               "sla_compliance_pct","health_score","partner_risk_status"],
         order=[("health_score", True)],
         col_fmt={"shipment_count": NUMB, "sla_compliance_pct": PCTB, "health_score": PCTB},
         heat=[{"column":"health_score","operator":"<","targetValue":70,"colorScheme":"#e04355"}],
         w=12, h=44),
    dict(slice="EDI · Partner risk matrix", tab=T_PART, dataset="edi_partner_risk",
         kind="raw",
         cols=["partner_name","shipment_volume","anomaly_count","severe_anomaly_count",
               "anomaly_rate_pct","shipment_value","health_score"],
         order=[("anomaly_rate_pct", False)],
         col_fmt={"shipment_volume": NUMB, "anomaly_rate_pct": PCTB,
                  "shipment_value": CUR, "health_score": PCTB}, w=12, h=44),
    dict(slice="EDI · Anomaly rate by partner", tab=T_PART, dataset="edi_partner_risk",
         kind="bar", dim="partner_name", metric=("rate","MAX(anomaly_rate_pct)"),
         number_format=".1f", w=6, h=48),
    dict(slice="EDI · Shipment volume by partner", tab=T_PART, dataset="edi_partner_risk",
         kind="bar", dim="partner_name", metric=("vol","SUM(shipment_volume)"), w=6, h=48),

    # ===== EDI Flow & SLA =====
    dict(slice="EDI · EDI funnel", tab=T_FLOW, dataset="edi_funnel",
         kind="bar", dim="funnel_stage", metric=("txns","SUM(transaction_count)"),
         row_limit=20, w=6, h=48),
    dict(slice="EDI · Flow completeness", tab=T_FLOW, dataset="edi_flow_complete",
         kind="donut", groupby="edi_flow_status", metric=("n","COUNT(*)"), w=6, h=48),
    dict(slice="EDI · Message SLA % by type", tab=T_FLOW, dataset="edi_msg_sla",
         kind="bar", dim="sla_type", metric=("sla","AVG(sla_percent)"),
         number_format=".1f", w=6, h=48),
    dict(slice="EDI · ACK SLA trend", tab=T_FLOW, dataset="edi_ack_sla",
         kind="ts", chart="line", x="metric_date", metric=("ack","AVG(ack_sla_percent)"),
         number_format=".1f", w=6, h=48),
    dict(slice="EDI · Volume by hour", tab=T_FLOW, dataset="edi_txn_volume",
         kind="bar", dim="transaction_hour", metric=("txns","SUM(transaction_count)"),
         row_limit=24, w=6, h=48),
    dict(slice="EDI · SLA performance trend", tab=T_FLOW, dataset="edi_sla_perf",
         kind="ts", chart="line", x="shipment_date", metric=("sla","AVG(sla_pct)"),
         number_format=".1f", w=6, h=48),

    # ===== Shipments =====
    dict(slice="EDI · Status distribution", tab=T_SHIP, dataset="edi_status_dist",
         kind="bar", dim="current_status", metric=("ships","SUM(shipment_count)"),
         row_limit=40, w=6, h=48),
    dict(slice="EDI · Delivery performance mix", tab=T_SHIP, dataset="edi_delay_hist",
         kind="donut", groupby="delivery_performance", metric=("n","COUNT(*)"), w=6, h=48),
    dict(slice="EDI · Delay severity", tab=T_SHIP, dataset="edi_delay_hist",
         kind="donut", groupby="delay_severity", metric=("n","COUNT(*)"), w=4, h=46),
    dict(slice="EDI · Volume actual vs expected", tab=T_SHIP, dataset="edi_daily_volume",
         kind="ts", chart="line", x="shipment_date", metric=("actual","SUM(actual_volume)"),
         w=8, h=46),
    dict(slice="EDI · Status aging worklist", tab=T_SHIP, dataset="edi_status_aging",
         kind="raw",
         cols=["shipment_id","partner_name","current_status","actual_hours_in_status",
               "expected_hours_in_status","aging_ratio","aging_severity","aging_insight"],
         order=[("aging_ratio", False)], row_limit=200,
         col_fmt={"aging_ratio": BARS}, w=12, h=52),
]

# ---------------------------------------------------------------------------
# DASHBOARD assembly metadata
# ---------------------------------------------------------------------------
SECTIONS = [
    ("Control Tower",  [T_CT]),
    ("Anomalies",      [T_ANOM]),
    ("Partners",       [T_PART]),
    ("EDI Flow & SLA", [T_FLOW]),
    ("Shipments",      [T_SHIP]),
]

DASHBOARD_TITLE = "EDI Anomaly Control Tower"
DASHBOARD_SLUG  = "edi-anomaly-control-tower"

# Native filters (column-scoped, name-based). Value list seeded from edi_exceptions
# (vw_exception_workbench has all four columns); applies to any chart whose dataset
# exposes a same-named column.
NATIVE_FILTERS = [
    ("Partner",          "partner_id"),
    ("Severity",         "severity"),
    ("Anomaly status",   "anomaly_status"),
    ("Transaction type", "transaction_type"),
]
FILTER_DATASET = "edi_exceptions"
