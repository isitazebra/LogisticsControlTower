-- 13_exception_reason_backfill.sql
-- Backfill txn_events.reason_category for failed/rejected events that were NULL
-- (they surfaced as a single dominant "unknown" slice on the Exceptions-by-reason
-- charts). Assign a realistic, status-aware spread of standard B2B/EDI integration
-- exception reasons. Existing non-NULL reasons (connectivity, mapping_defect,
-- bad_input_file already present) are preserved.
--
--   rejected  -> data / business / compliance reasons (the partner or validation
--                refused the message)
--   failed    -> transport / system / translation reasons (processing broke)
--
-- Idempotent-ish: only touches rows where reason_category IS NULL.

WITH picks AS (
    SELECT event_id, status, random() AS r
    FROM txn_events
    WHERE status IN ('failed', 'rejected')
      AND reason_category IS NULL
)
UPDATE txn_events t
SET reason_category = CASE
    WHEN p.status = 'rejected' THEN
        CASE
            WHEN p.r < 0.30 THEN 'validation_error'
            WHEN p.r < 0.52 THEN 'business_rule_violation'
            WHEN p.r < 0.67 THEN 'duplicate_interchange'
            WHEN p.r < 0.79 THEN 'partner_config'
            WHEN p.r < 0.88 THEN 'invalid_doc_type'
            WHEN p.r < 0.95 THEN 'bad_input_file'
            ELSE 'compliance_reject'
        END
    ELSE  -- failed
        CASE
            WHEN p.r < 0.28 THEN 'connectivity'
            WHEN p.r < 0.52 THEN 'mapping_defect'
            WHEN p.r < 0.68 THEN 'ack_timeout'
            WHEN p.r < 0.80 THEN 'envelope_error'
            WHEN p.r < 0.90 THEN 'system_error'
            WHEN p.r < 0.96 THEN 'gateway_timeout'
            ELSE 'bad_input_file'
        END
    END
FROM picks p
WHERE t.event_id = p.event_id;
