-- Q15 -- Predictive anomaly / silent-partner detection (cockpit world).
-- "Which partner/feed is behaving abnormally, BEFORE it's an incident?"
-- Deterministic (no ML): a trailing volume baseline (mean/sigma) per
-- partner.feed, the current window vs that baseline as a z-score + drop %, and
-- a silent flag for feeds that were active in baseline but have gone quiet.
-- Powered by txn_rollup_hourly. Aggregate-first; the worklist is small.
--
-- Window logic: "asof" = the last COMPLETE day (the newest day is partial in
-- steady state), baseline = the 21 complete days ending asof-7, current = the
-- trailing 7 complete days. All days are full days, so a partial tail never
-- fakes a drop. Source of truth stays txn_events; this only reads the rollup.

DROP VIEW IF EXISTS public.q15_partner_anomaly        CASCADE;
DROP VIEW IF EXISTS public.q15_feed_anomaly           CASCADE;
DROP VIEW IF EXISTS public.q15_feed_daily             CASCADE;
DROP VIEW IF EXISTS public.v_anomaly_asof             CASCADE;

-- asof anchor (last complete day) -------------------------------------------
CREATE VIEW public.v_anomaly_asof AS
SELECT (SELECT max(date_trunc('day',bucket))::date
          FROM public.txn_rollup_hourly
         WHERE bucket < date_trunc('day',(SELECT max(bucket) FROM public.txn_rollup_hourly))
       ) AS asof_day;

-- per partner.feed daily volume (+ error/breach context) --------------------
CREATE VIEW public.q15_feed_daily AS
SELECT environment, partner, doc_type,
       date_trunc('day',bucket)::date AS day,
       SUM(txn_count)                          AS txns,
       SUM(failed_count + rejected_count)      AS errors,
       SUM(breached_count)                     AS breached
FROM public.txn_rollup_hourly
GROUP BY 1,2,3,4;

-- per partner.feed baseline vs current -> z-score / drop% / status ----------
-- NOTE: a silent feed has NO rows on its quiet days (absence, not a zero row),
-- so we divide by a FIXED window length (21 / 7 days), not COUNT(*) of present
-- rows -- otherwise an averaged-over-present-days mean would hide the silence.
CREATE VIEW public.q15_feed_anomaly AS
WITH a AS (SELECT asof_day FROM public.v_anomaly_asof),
base AS (
  SELECT environment, partner, doc_type,
         SUM(txns)/21.0          AS base_mean,
         COALESCE(STDDEV_POP(txns),0) AS base_sd,
         COUNT(*)                AS base_days
  FROM public.q15_feed_daily, a
  WHERE day BETWEEN a.asof_day - 27 AND a.asof_day - 7
  GROUP BY 1,2,3),
cur AS (
  SELECT environment, partner, doc_type,
         SUM(txns)/7.0            AS cur_mean,
         SUM(txns)                AS cur_txns
  FROM public.q15_feed_daily, a
  WHERE day BETWEEN a.asof_day - 6 AND a.asof_day
  GROUP BY 1,2,3),
seen AS (   -- last day the feed had any volume, across all history
  SELECT environment, partner, doc_type, MAX(day) AS last_active_day
  FROM public.q15_feed_daily WHERE txns > 0 GROUP BY 1,2,3)
SELECT
  b.environment, b.partner, b.doc_type,
  ROUND(b.base_mean,1)                              AS base_mean,
  ROUND(b.base_sd,1)                                AS base_sd,
  ROUND(COALESCE(c.cur_mean,0),1)                   AS cur_mean,
  NULLIF(GREATEST(((SELECT asof_day FROM a) - s.last_active_day), 0), 0) AS days_silent,
  s.last_active_day,
  ROUND( ((COALESCE(c.cur_mean,0) - b.base_mean) / NULLIF(b.base_sd,0))::numeric, 2) AS zscore,
  ROUND( (100.0*(b.base_mean - COALESCE(c.cur_mean,0)) / NULLIF(b.base_mean,0))::numeric, 0) AS drop_pct,
  CASE
    WHEN b.base_mean >= 5 AND COALESCE(c.cur_mean,0) = 0                         THEN 'Silent'
    WHEN (COALESCE(c.cur_mean,0) - b.base_mean)/NULLIF(b.base_sd,0) <= -2
      OR 100.0*(b.base_mean - COALESCE(c.cur_mean,0))/NULLIF(b.base_mean,0) >= 50 THEN 'Severe drop'
    WHEN (COALESCE(c.cur_mean,0) - b.base_mean)/NULLIF(b.base_sd,0) <= -1         THEN 'Watch'
    ELSE 'Normal'
  END AS status
FROM base b
LEFT JOIN cur c USING (environment, partner, doc_type)
LEFT JOIN seen s USING (environment, partner, doc_type)
WHERE b.base_mean >= 3;   -- ignore negligible feeds

-- partner-level rollup of feed anomalies (one row per partner) --------------
CREATE VIEW public.q15_partner_anomaly AS
SELECT
  environment, partner,
  COUNT(*)                                          AS feeds,
  COUNT(*) FILTER (WHERE status='Silent')           AS silent_feeds,
  COUNT(*) FILTER (WHERE status='Severe drop')      AS severe_drop_feeds,
  COUNT(*) FILTER (WHERE status IN ('Silent','Severe drop','Watch')) AS abnormal_feeds,
  ROUND(MIN(zscore),2)                              AS worst_z,
  ROUND(MAX(drop_pct),0)                            AS worst_drop_pct,
  ROUND(SUM(base_mean),0)                           AS base_mean_total,
  ROUND(SUM(cur_mean),0)                            AS cur_mean_total,
  CASE
    WHEN COUNT(*) FILTER (WHERE status='Silent') > 0       THEN 'Silent'
    WHEN COUNT(*) FILTER (WHERE status='Severe drop') > 0  THEN 'Severe drop'
    WHEN COUNT(*) FILTER (WHERE status='Watch') > 0        THEN 'Watch'
    ELSE 'Normal'
  END AS status
FROM public.q15_feed_anomaly
GROUP BY 1,2;
