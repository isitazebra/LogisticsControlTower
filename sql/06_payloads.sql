-- 06_payloads.sql — per-message payloads for the Transaction-view drill.
-- TRANSLATE-AND-FORWARD: each message has a partner-facing EDI/X12 form and a
-- system-facing JSON form. vw_shipment_detail maps these to incoming/outgoing by
-- direction (in: EDI received -> JSON emitted; out: JSON received -> EDI emitted).
-- We store the two formats here as edi_payload / json_payload; the view does the
-- direction flip. "Postgres is the contract": NiFi writes these inline per event
-- later. Idempotent. Window is relative to the DATA's own max event_time (the
-- world is seeded with historical timestamps, so an absolute now() window would
-- match zero rows); the master grid orders by recency so the window covers what
-- a user sees. EDI shape per doc_type: 204 (SM), 990 (GF), 214 (QM), 210 (IM).

ALTER TABLE txn_events ADD COLUMN IF NOT EXISTS edi_payload  text;
ALTER TABLE txn_events ADD COLUMN IF NOT EXISTS json_payload text;

UPDATE txn_events e
SET edi_payload = bld.edi, json_payload = bld.js
FROM (
  SELECT event_id,
    -- ---- EDI/X12 envelope (the partner-facing format) -----------------------
    'ISA*00*          *00*          *ZZ*' || rpad(edi_s,15) || '*ZZ*' || rpad(edi_r,15)
      || '*' || isa_d || '*' || isa_t || '*U*00401*' || ctrl || '*0*P*>~' || chr(10)
    || 'GS*' || gs_fg || '*' || edi_s || '*' || edi_r || '*' || gs_d || '*' || gs_t
      || '*' || (ctrl::int)::text || '*X*004010~' || chr(10)
    || 'ST*' || doc_type || '*0001~' || chr(10)
    || body_segs
    || 'SE*' || (n_segs)::text || '*0001~' || chr(10)
    || 'GE*1*' || (ctrl::int)::text || '~' || chr(10)
    || 'IEA*1*' || ctrl || '~' AS edi,
    -- ---- JSON canonical record (the system-facing format) -------------------
    '{' || chr(10)
    || '  "messageId": "'     || business_ref                                  || '",' || chr(10)
    || '  "interchange": "'   || coalesce(interchange_id,'')                   || '",' || chr(10)
    || '  "docType": "'       || doc_type                                      || '",' || chr(10)
    || '  "partner": "'       || coalesce(partner,'')                          || '",' || chr(10)
    || '  "system": "'        || syscode                                       || '",' || chr(10)
    || '  "controlNumber": "' || coalesce(control_number, ctrl)                || '",' || chr(10)
    || '  "status": "'        || coalesce(status,'')                           || '",' || chr(10)
    || '  "eventTime": "'     || to_char(event_time,'YYYY-MM-DD"T"HH24:MI:SS') || '",' || chr(10)
    || '  "valueUsd": '       || coalesce(round(value_usd,2)::text,'0')        ||        chr(10)
    || '}' AS js
  FROM (
    SELECT *,
      CASE doc_type
        WHEN '204' THEN
          'B2**' || pcode || '**' || business_ref || '**PP~' || chr(10)
          || 'B2A*00~' || chr(10) || 'L11*' || business_ref || '*SI~' || chr(10)
          || 'G62*64*' || gs_d || '~' || chr(10) || 'N1*SH*' || partner || '~' || chr(10)
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
          || 'C3*USD~' || chr(10) || 'N9*BM*' || business_ref || '~' || chr(10)
          || 'N1*BT*' || partner || '~' || chr(10)
        ELSE 'N9*REF*' || business_ref || '~' || chr(10)
      END AS body_segs,
      2 + length(CASE doc_type WHEN '204' THEN '00000' WHEN '210' THEN '0000'
                               WHEN '214' THEN '0000' WHEN '990' THEN '00' ELSE '0' END) AS n_segs
    FROM (
      SELECT *,
        left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10)              AS pcode,
        left(upper(regexp_replace(coalesce(lob,'INTSYS'), '[^A-Za-z0-9]', '', 'g')), 8) AS syscode,
        lpad((abs(hashtext(business_ref)) % 1000000000)::text, 9, '0')                 AS ctrl,
        -- EDI sender/receiver: the EDI form always faces the PARTNER. For an
        -- inbound message the partner sends it (sender=partner); for an outbound
        -- message the platform sends it to the partner (sender=hub).
        CASE WHEN direction = 'in'
             THEN left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10)
             ELSE 'LCTHUB' END                                                         AS edi_s,
        CASE WHEN direction = 'in' THEN 'LCTHUB'
             ELSE left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10) END AS edi_r,
        to_char(event_time, 'YYMMDD')   AS isa_d, to_char(event_time, 'HH24MI')   AS isa_t,
        to_char(event_time, 'YYYYMMDD') AS gs_d,  to_char(event_time, 'HH24MISS') AS gs_t,
        CASE doc_type WHEN '204' THEN 'SM' WHEN '990' THEN 'GF'
                      WHEN '214' THEN 'QM' WHEN '210' THEN 'IM' ELSE 'GS' END          AS gs_fg
      FROM txn_events
      WHERE event_time >= (SELECT max(event_time) FROM txn_events) - interval '7 days'
        AND edi_payload IS NULL
    ) base
  ) enriched
) bld
WHERE e.event_id = bld.event_id;
