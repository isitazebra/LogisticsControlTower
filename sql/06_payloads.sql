-- 06_payloads.sql — Sprint R (reference baseline): master-detail raw payloads.
-- Adds a per-event message body so the LOB Cockpit "Details" tab can show
-- Incoming / Outgoing / Ack payload panels (mirrors the reference dashboards'
-- Incoming Data / Outgoing Data / Ack Data New cards).
--
-- "Postgres is the contract": NiFi will write `payload` inline per event later;
-- the seed fills a bounded recent window now so the drill is demo-able. Idempotent.

ALTER TABLE txn_events ADD COLUMN IF NOT EXISTS payload text;

-- Synthesize a compact, representative body only for the recent window
-- (keeps the column small; older rows stay NULL and simply show nothing on drill).
UPDATE txn_events
SET payload = CASE
    WHEN doc_type IN ('997','CONTRL') THEN
        '{"ackType":"' || doc_type ||
        '","controlNumber":"' || coalesce(control_number,'n/a') ||
        '","ackStatus":"' || coalesce(status,'') ||
        '","partner":"' || coalesce(partner,'') || '"}'
    WHEN direction = 'out' THEN
        '{"shipmentRef":"' || coalesce(business_ref,'') ||
        '","docType":"' || coalesce(doc_type,'') ||
        '","direction":"out","partner":"' || coalesce(partner,'') ||
        '","status":"' || coalesce(status,'') ||
        '","controlNumber":"' || coalesce(control_number,'') || '"}'
    ELSE
        '{"interchangeId":"' || coalesce(interchange_id,'') ||
        '","docType":"' || coalesce(doc_type,'') ||
        '","direction":"in","partner":"' || coalesce(partner,'') ||
        '","ref":"' || coalesce(business_ref,'') ||
        '","stage":"' || coalesce(stage,'') ||
        '","status":"' || coalesce(status,'') ||
        '","kchar":' || coalesce(round(kchar)::text,'0') || '}'
    END
WHERE event_time >= now() - interval '3 days'
  AND payload IS NULL;
