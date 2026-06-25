# Cleanup deletions log

Record of objects removed during the post-demo consolidation onto the single
**Integration Command Center · Logistics** dashboard (id=15). Kept: dashboard 15,
its 113 charts and their datasets/DB objects, plus the Superset stock example
dashboards (ids 1–7).

## 2026-06-24 — fallback-dashboard consolidation

### Dashboards deleted (6)
| id | title |
|----|-------|
| 9  | EDI Anomaly Control Tower |
| 10 | Integration Control Tower |
| 11 | Integration Visibility Cockpit (original) |
| 12 | Integration Value Cockpit |
| 13 | Integration Control Tower - Minimal |
| 14 | Integration Command Center |

### Charts deleted
- 303 orphan charts — every chart left referenced by **no** remaining dashboard
  after the six deletions above. None belonged to dashboard 15 (verified against
  its 113-chart keep-set before deletion). Includes the retired cockpit-world
  Partner-SLA tiles ("Partner SLA scorecard", "% Met by partner").

### Alerts / reports deleted (1)
| id | name | reason |
|----|------|--------|
| 6  | At-risk response | Pointed at chart 120, an orphan tile on a deleted fallback dashboard. Not reconcilable with the new pairwise-SLA model on dashboard 15; removed rather than repointed. |

**Alerts retained (5)** — all point at charts still live on dashboard 15:
Hung pipeline (1, chart 77), Missing feed (2, chart 78), Channel down (3, chart 79),
Rejected message (4, chart 85), Cert expiring (5, chart 83).

### Superset datasets deleted (68)
All virtual/physical datasets left unreferenced by any surviving dashboard's
charts **or** native filters after the chart cleanup — the data layer behind the
six retired dashboards (q4_*, q7–q12_*, q_lob_*, q_all_*, edi_* / vw_* anomaly &
shipment-journey families, anomaly_registry, edi_transactions, etc.).
Kept: the 42 datasets feeding dashboards 1–7 (stock) and 15. The 4 unused Slack
stock-example datasets (4, 10, 12, 13) were left with the demos, not deleted.

### Physical Neon objects dropped (11)
Computed by transitive dependency closure from dashboard 15's 26 surviving Neon
datasets (pg_rewrite view→base edges); every object below is referenced by **no**
surviving dataset and by no kept view.

| object | kind | note |
|--------|------|------|
| deploys | table | dead since build start; no consumer |
| sla_rules | table | reference q10 model input; vw_sla_pairs computes thresholds inline instead |
| diagnostic_rules | table | legacy rule lookup, no surviving consumer |
| doc_type_catalog | table | reference lookup, unreferenced |
| cockpit_partner_map | view | cockpit-world partner map, unreferenced |
| v_files_missing_txns | view | fallback-dashboard view |
| vw_shipment_integration | view | shipment360 (deleted dash) |
| vw_shipment_messages | view | shipment360 (deleted dash) |
| vw_shipment_journey | view | shipment journey (deleted dash) |
| vw_txn_detail | view | fallback-dashboard view |
| vw_txn_shipment | view | fallback-dashboard view |

Kept Neon objects (18 in closure): txn_events (+ partitions), txn_current,
txn_rollup_hourly, txn_files, monitor_heartbeat, endpoint_health, expected_feeds,
pipeline_health, partner_penalty, partner_profile, q15_*/q17_360 views,
v_anomaly_asof, vw_shipment, vw_shipment_detail, vw_sla_pairs.

## 2026-06-25 — stray attached-but-unplaced charts on dash 15

Follow-up: dash 15 had 113 charts **attached** (M2M link) but only **91 placed**
on the canvas — 22 residual charts left linked by earlier iterative rebuilds.
They were not on any tab (invisible on the canvas) but surfaced as stray items in
the dashboard's chart inventory. All 22 were attached to dash 15 only.

- **20 duplicates**: a same-named chart was already placed on the canvas under a
  different id (e.g. "Channel health" placed as 80, stale duplicate 282 deleted;
  "Rejected (period)" placed as 261, stale duplicate 85 deleted). Deleting the
  stale twins also removes the rebuild ambiguity in `resolve_chart_ids`.
- **2 unique**: "Partner SLA scorecard" (112) and "% Met by partner" (113) — the
  cockpit-world tiles pulled from the SLA-tab layout earlier but never detached.

Deleted chart ids: 84, 85, 86, 87, 88, 96, 102, 111, 112, 113, 122, 123, 260,
276, 278, 281, 282, 285, 289, 303, 306, 317.

**Alert 4 "Rejected message"** pointed at stale duplicate chart 85; repointed to
its live canvas twin chart 261 (identical: vw_rollup / big_number) before the
delete, so the alert is preserved. Alerts 1/2/3/5 already targeted live canvas
charts (77/78/79/83) and were untouched.

Result: dash 15 now has 91 placed == 91 attached, 0 stray, 0 broken tiles.
Workspace chart total 184 → 162.
