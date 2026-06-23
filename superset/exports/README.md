# Superset Exports

Point-in-time Superset dashboard export bundles, kept as-is for reference.
These are standalone exports and do **not** override the working assets in
`superset/assets/`.

## Bundles

- `dashboard_export_20260623T160821/` — Integration Visibility Cockpit export
  (exported 2026-06-23T16:08:21Z). Importable archive of databases, datasets,
  charts, and the dashboard definition (122 asset files).

To re-import a bundle into Superset, zip the export folder and use
**Settings → Import Dashboards**, or import via the Superset/preset CLI.
