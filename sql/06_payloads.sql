-- 06_payloads.sql — per-event raw EDI payload for the Transaction-view drill.
-- Backfills a representative X12 envelope per message so the "Payloads" panel can
-- show the rawest information (mirrors the reference inbound/outbound payload
-- cards). "Postgres is the contract": NiFi writes `payload` inline per event
-- later; the seed fills a bounded recent window now. Idempotent.
--
-- Window is relative to the DATA's own max event_time (the world is seeded with
-- historical timestamps, so an absolute now()-3d window matches zero rows). The
-- master grid orders by recency, so the most-recent window covers what a user
-- sees. Envelope shape is per doc_type: 204 load tender (SM), 990 response (GF),
-- 214 status (QM), 210 invoice (IM). Sender/receiver follow platform-relative
-- direction (hub 'LCTHUB' <-> partner code), consistent with vw_shipment_detail.

ALTER TABLE txn_events ADD COLUMN IF NOT EXISTS payload text;

UPDATE txn_events e
SET payload = bld.body
FROM (
  SELECT event_id,
    -- ISA / GS envelope + ST header + doc-type body + SE/GE/IEA trailers.
    'ISA*00*          *00*          *ZZ*' || rpad(s_id,15) || '*ZZ*' || rpad(r_id,15)
      || '*' || isa_d || '*' || isa_t || '*U*00401*' || ctrl || '*0*P*>~' || chr(10)
    || 'GS*' || gs_fg || '*' || s_id || '*' || r_id || '*' || gs_d || '*' || gs_t
      || '*' || (ctrl::int)::text || '*X*004010~' || chr(10)
    || 'ST*' || doc_type || '*0001~' || chr(10)
    || body_segs
    || 'SE*' || (n_segs)::text || '*0001~' || chr(10)
    || 'GE*1*' || (ctrl::int)::text || '~' || chr(10)
    || 'IEA*1*' || ctrl || '~' AS body
  FROM (
    SELECT *,
      CASE doc_type
        WHEN '204' THEN
          'B2**' || pcode || '**' || business_ref || '**PP~' || chr(10)
          || 'B2A*00~' || chr(10)
          || 'L11*' || business_ref || '*SI~' || chr(10)
          || 'G62*64*' || gs_d || '~' || chr(10)
          || 'N1*SH*' || partner || '~' || chr(10)
        WHEN '990' THEN
          'B1*' || pcode || '*' || business_ref || '*' || gs_d || '*'
            || CASE WHEN status IN ('failed','rejected') THEN 'D' ELSE 'A' END || '~' || chr(10)
          || 'N9*CR*' || business_ref || '~' || chr(10)
        WHEN '214' THEN
          'B10*' || ctrl || '*' || business_ref || '*' || pcode || '~' || chr(10)
          || 'LX*1~' || chr(10)
          || 'AT7*' || CASE WHEN status IN ('failed','rejected') THEN 'AG' ELSE 'X6' END
            || '***NS*' || gs_d || '*' || gs_t || '*LT~' || chr(10)
          || 'MS1*' || upper(coalesce(channel,'NA')) || '*' || upper(coalesce(lob,'NA')) || '~' || chr(10)
        WHEN '210' THEN
          'B3**' || ctrl || '**' || business_ref || '*TP*' || coalesce(round(value_usd)::text,'0')
            || '**' || gs_d || '***' || pcode || '~' || chr(10)
          || 'C3*USD~' || chr(10)
          || 'N9*BM*' || business_ref || '~' || chr(10)
          || 'N1*BT*' || partner || '~' || chr(10)
        ELSE
          'N9*REF*' || business_ref || '~' || chr(10)
      END AS body_segs,
      -- segment count = ST + body lines + SE (counted from the newlines in body).
      2 + (length(
        CASE doc_type WHEN '204' THEN '00000' WHEN '210' THEN '0000'
                      WHEN '214' THEN '0000' WHEN '990' THEN '00' ELSE '0' END)) AS n_segs
    FROM (
      SELECT *,
        left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10)        AS pcode,
        lpad((abs(hashtext(business_ref)) % 1000000000)::text, 9, '0')           AS ctrl,
        CASE WHEN direction = 'in'
             THEN left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10)
             ELSE 'LCTHUB' END                                                   AS s_id,
        CASE WHEN direction = 'in' THEN 'LCTHUB'
             ELSE left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10) END AS r_id,
        to_char(event_time, 'YYMMDD')   AS isa_d, to_char(event_time, 'HH24MI')  AS isa_t,
        to_char(event_time, 'YYYYMMDD') AS gs_d,  to_char(event_time, 'HH24MISS') AS gs_t,
        CASE doc_type WHEN '204' THEN 'SM' WHEN '990' THEN 'GF'
                      WHEN '214' THEN 'QM' WHEN '210' THEN 'IM' ELSE 'GS' END    AS gs_fg
      FROM txn_events
      WHERE event_time >= (SELECT max(event_time) FROM txn_events) - interval '7 days'
        AND payload IS NULL
    ) base
  ) enriched
) bld
WHERE e.event_id = bld.event_id;
