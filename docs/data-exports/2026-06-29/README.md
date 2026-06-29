# Data Exports — 2026-06-29

Point-in-time exports captured from the Integration Cockpit on 2026-06-29,
kept for reference. Filenames preserve the original export timestamps.

> Note: these CSVs contain real operational data (partner names, shipment
> numbers, EDI control numbers). Treat as internal-only.

## Files

| File | Description |
|---|---|
| `20260629_145815.csv` | Message-tracking export — one row per message (message_type, partner_name, sender/receiver, shipment_number, status, EDI service IDs, in/out filenames). ~1.1 MB. |
| `20260629_150245.csv` | Process/message log export — process runs (processname, filename, message_status, flow/process IDs, timestamps). |
| `20260629_150304.csv` | Single `inbound_payload` — raw EDI **322** (terminal operations / rail) interchange. |
| `20260629_150315.csv` | Single `outbound_payload` — raw EDI **210** (motor carrier freight invoice) interchange. |
| `dashboard-export-2026-06-29T15-03-22.pdf` | Rendered PDF export of the Integration Cockpit dashboard (~44 MB). Extracted from the original `dashboardexport20260629T150322.zip`. |
