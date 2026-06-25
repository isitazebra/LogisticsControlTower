-- Q17 -- Partner 360 / network scorecard (cockpit world).
-- "What is each partner's full integration health, in one shareable row?"
-- One row per partner x environment: volume, exception rate, SLA %, duplicate
-- rate, open/breaching, $-at-risk (penalty exposure on current exceptions),
-- last-seen, onboarding tier/status, and the Q15 anomaly flag.
--
-- Inputs: txn_events (current state == the row; 1 event per txn), txn_rollup_hourly (SLA), ref_partner_penalty
-- ($ exposure), ref_partner_profile (onboarding config, seeded below), and
-- vw_partner_anomaly (anomaly flag -> requires sql/09 applied first).
--
-- Notes / honest scope:
--  * ack-health is intentionally omitted here -- the cockpit feed has no ack
--    linkage; real ACK coverage lives in the EDI/Shipment Integration 360 tab.
--  * RLS ("a partner sees only their row") is deferred -- it is a Superset
--    security-config step, not a data artifact, and is tracked as follow-up.

-- onboarding config (small deterministic seed, like doc_type_catalog) ---------
CREATE TABLE IF NOT EXISTS public.ref_partner_profile (
  partner           text PRIMARY KEY,
  tier              text,         -- Strategic | Preferred | Standard
  onboarding_status text,         -- Live | Onboarding
  region            text
);
TRUNCATE public.ref_partner_profile;
INSERT INTO public.ref_partner_profile (partner, tier, onboarding_status, region) VALUES
  ('DHL',         'Strategic', 'Live',       'EMEA'),
  ('Maersk',      'Strategic', 'Live',       'EMEA'),
  ('Hapag',       'Preferred', 'Live',       'EMEA'),
  ('Werner',      'Preferred', 'Live',       'NA'),
  ('Kroger',      'Strategic', 'Live',       'NA'),
  ('Target',      'Strategic', 'Live',       'NA'),
  ('Flextronics', 'Standard',  'Onboarding', 'APAC');

DROP VIEW IF EXISTS public.vw_partner_360 CASCADE;
CREATE VIEW public.vw_partner_360 AS
WITH cur AS (
  SELECT environment, partner,
         COUNT(*)                                                   AS refs,
         COUNT(*) FILTER (WHERE status IN ('failed','rejected'))    AS exceptions,
         COUNT(*) FILTER (WHERE status = 'duplicate')               AS duplicates,
         COUNT(*) FILTER (WHERE NOT terminal)                       AS open_refs,
         COUNT(*) FILTER (WHERE sla_due_at < now() AND NOT terminal) AS breaching,
         MAX(event_time)                                            AS last_seen
  FROM public.txn_events GROUP BY 1,2),
sla AS (
  SELECT environment, partner,
         SUM(txn_count)     AS txns,
         SUM(breached_count) AS breached
  FROM public.txn_rollup_hourly GROUP BY 1,2),
risk AS (   -- penalty exposure on current exceptions, by doc_type
  SELECT c.environment, c.partner, SUM(pp.penalty_usd) AS dollars_at_risk
  FROM public.txn_events c
  JOIN public.ref_partner_penalty pp
    ON pp.partner = c.partner AND pp.doc_type = c.doc_type
  WHERE c.status IN ('failed','rejected')
  GROUP BY 1,2)
SELECT
  cur.environment, cur.partner,
  pr.tier, pr.onboarding_status, pr.region,
  cur.refs, cur.exceptions,
  ROUND(100.0*cur.exceptions / NULLIF(cur.refs,0), 2)        AS exception_pct,
  ROUND(100.0*cur.duplicates / NULLIF(cur.refs,0), 2)        AS duplicate_pct,
  cur.open_refs, cur.breaching,
  ROUND(100.0*(1 - s.breached::numeric / NULLIF(s.txns,0)), 2) AS sla_pct,
  COALESCE(r.dollars_at_risk, 0)                            AS dollars_at_risk,
  cur.last_seen,
  EXTRACT(epoch FROM (now() - cur.last_seen))/3600.0        AS hours_since_seen,
  COALESCE(qa.status, 'Normal')                            AS anomaly_status,
  COALESCE(qa.silent_feeds, 0)                             AS silent_feeds,
  COALESCE(qa.abnormal_feeds, 0)                           AS abnormal_feeds
FROM cur
LEFT JOIN sla s             USING (environment, partner)
LEFT JOIN risk r            USING (environment, partner)
LEFT JOIN public.ref_partner_profile pr ON pr.partner = cur.partner
LEFT JOIN public.vw_partner_anomaly qa
       ON qa.environment = cur.environment AND qa.partner = cur.partner;
