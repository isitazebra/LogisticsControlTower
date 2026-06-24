-- API Integration channel views -- the API-native parallel to the EDI views,
-- under the SAME transient-integration lens: these describe the integration
-- CALLS we exchanged and the health of those flows (connectivity, request/
-- response choreography, latency vs partner target, success/error/retry,
-- rate-limiting, webhook delivery). NOT a system of record -- no business
-- outcomes, no shipment economics. Source: edi_anomaly_dashboard_dataset.
-- api_transactions (synthetic, anchored to real shipments/partners; see
-- scripts/gen_api_transactions.py).

DROP VIEW IF EXISTS edi_anomaly_dashboard_dataset.vw_api_integration_summary;
DROP VIEW IF EXISTS edi_anomaly_dashboard_dataset.vw_api_transaction_detail;

-- 1) One row per API call, enriched for drills + channel slicing -------------
CREATE VIEW edi_anomaly_dashboard_dataset.vw_api_transaction_detail AS
SELECT
  a.api_call_id, a.shipment_id, a.partner_id, p.partner_name, p.industry_segment,
  a.carrier_id, s.transport_mode,
  a.api_operation, a.endpoint, a.http_method, a.is_webhook,
  a.request_ts, a.request_ts::date AS request_date, a.response_ts,
  a.latency_ms, a.target_latency_ms, a.sla_met,
  a.http_status, a.status_class, a.success,
  a.retry_count, (a.retry_count > 0) AS retried, a.rate_limited,
  a.webhook_delivered, a.error_code, a.error_message, a.correlation_id
FROM edi_anomaly_dashboard_dataset.api_transactions a
LEFT JOIN edi_anomaly_dashboard_dataset.trading_partner_master p ON p.partner_id = a.partner_id
LEFT JOIN edi_anomaly_dashboard_dataset.shipment_header s        ON s.shipment_id = a.shipment_id;

-- 2) Per-partner API integration scorecard -----------------------------------
CREATE VIEW edi_anomaly_dashboard_dataset.vw_api_integration_summary AS
SELECT
  a.partner_id, p.partner_name, p.industry_segment,
  COUNT(*)                                                       AS total_calls,
  ROUND(100.0*AVG(a.success::int), 1)                           AS success_pct,
  ROUND(AVG(a.latency_ms))                                       AS avg_latency_ms,
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY a.latency_ms)::numeric) AS p95_latency_ms,
  ROUND(100.0*AVG(a.sla_met::int), 1)                           AS latency_sla_pct,
  COUNT(*) FILTER (WHERE a.status_class='4xx')                   AS err_4xx,
  COUNT(*) FILTER (WHERE a.status_class='5xx')                   AS err_5xx,
  COUNT(*) FILTER (WHERE a.rate_limited)                         AS rate_limited_cnt,
  ROUND(100.0*AVG((a.retry_count>0)::int), 1)                   AS retry_pct,
  COUNT(*) FILTER (WHERE a.is_webhook)                           AS webhook_calls,
  ROUND(100.0 * SUM(CASE WHEN a.is_webhook AND a.webhook_delivered THEN 1 ELSE 0 END)
              / NULLIF(SUM(a.is_webhook::int), 0), 1)            AS webhook_delivery_pct,
  MIN(a.request_ts) AS first_call_ts, MAX(a.request_ts) AS last_call_ts
FROM edi_anomaly_dashboard_dataset.api_transactions a
LEFT JOIN edi_anomaly_dashboard_dataset.trading_partner_master p ON p.partner_id = a.partner_id
GROUP BY a.partner_id, p.partner_name, p.industry_segment;
