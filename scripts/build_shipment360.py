#!/usr/bin/env python3
"""Add the **Shipment Integration 360** tab to dashboard 12 (Shipment Anomaly
Control Tower). Integration-scoped shipment drill: per shipment, the message set
we processed and the INTEGRATION health of that flow (choreography completeness,
204->990 response latency vs partner target, ACK coverage, flow anomalies).

Strictly additive + idempotent:
  * datasets are physical, on the same DB connection dashboard 12 uses (id 4,
    "mp_demo (Neon)", schema edi_anomaly_dashboard_dataset);
  * charts are NEW objects (matched by slice_name -> update in place) linked only
    to dashboard 12;
  * the tab + all its layout nodes are prefixed SI360 so they never collide with
    the imported dashboard's nodes; a previous SI360 tab is removed before
    re-adding, so existing baseline tabs are never touched.

Run sql/07_shipment_integration_360.sql first (creates the two views).

Usage:  python build_shipment360.py        # datasets -> charts -> tab -> verify
"""
import json, uuid, sys
import build_cockpit as B
from preset_client import client

DB_ID = 4
SCHEMA = "edi_anomaly_dashboard_dataset"
DASH_ID = 12
TAB_TITLE = "Shipment Integration 360"
PFX = "SI360"

DS = {
    "sum":     "vw_shipment_integration_summary",
    "detail":  "vw_shipment_message_detail",
    "journey": "vw_shipment_journey_timeline",
}

# ---- chart specs (slice, dataset key, kind, layout w/h) ---------------------
CHARTS = [
    # KPI row — integration health of the in-scope shipment set
    dict(slice="Shipments tracked", dataset="sum", kind="bignum", w=2, h=14,
         metric=("Shipments", "COUNT(*)"), subheader="shipments in integration scope"),
    dict(slice="Choreography complete %", dataset="sum", kind="bignum", w=3, h=14,
         metric=("Complete %", "100.0*AVG(choreography_complete)"),
         number_format=".1f", subheader="all expected messages present"),
    dict(slice="Response-SLA met %", dataset="sum", kind="bignum", w=3, h=14,
         metric=("Resp SLA %", "100.0*SUM(response_sla_met)/NULLIF(SUM(CASE WHEN cnt_204>0 AND cnt_990>0 THEN 1 ELSE 0 END),0)"),
         number_format=".1f", subheader="204->990 within partner target"),
    dict(slice="ACK coverage %", dataset="sum", kind="bignum", w=2, h=14,
         metric=("ACK %", "100.0*SUM(ack_received_cnt)/NULLIF(SUM(ack_required_cnt),0)"),
         number_format=".1f", subheader="ACKs received vs required"),
    dict(slice="Shipments with flow anomalies", dataset="sum", kind="bignum", w=2, h=14,
         metric=("With anomalies", "SUM(CASE WHEN anomaly_count>0 THEN 1 ELSE 0 END)"),
         subheader="integration anomalies detected"),
    # distribution row
    dict(slice="Choreography completeness", dataset="sum", kind="donut", w=4, h=50,
         groupby="choreography_status", metric=("Shipments", "COUNT(*)")),
    dict(slice="Response latency by partner (min)", dataset="sum", kind="bar", w=4, h=50,
         dim="partner_name", metric=("Avg response (min)", "AVG(response_minutes)")),
    dict(slice="Message mix by type", dataset="detail", kind="bar", w=4, h=50,
         dim="transaction_type", metric=("Messages", "COUNT(*)")),
    # master worklist — cross-filter source on shipment_id
    dict(slice="Shipment integration worklist", dataset="sum", kind="raw", w=12, h=60,
         cols=["shipment_id", "partner_name", "transport_mode", "total_messages",
               "choreography_status", "response_minutes", "expected_204_990_minutes",
               "ack_pending", "error_cnt", "anomaly_count", "critical_anomaly_count",
               "business_impact_amount", "shipment_status"],
         order=[("anomaly_count", False), ("business_impact_amount", False)], row_limit=200),
    # per-shipment drill (receives cross-filter on shipment_id)
    dict(slice="Message set (selected shipment)", dataset="detail", kind="raw", w=6, h=60,
         cols=["shipment_id", "transaction_type", "transaction_direction",
               "transaction_timestamp", "processing_status", "ack_required",
               "ack_received", "error_code", "control_number"],
         order=[("transaction_timestamp", True)], row_limit=300),
    dict(slice="Status journey (selected shipment)", dataset="journey", kind="raw", w=6, h=60,
         cols=["shipment_id", "status_sequence", "status_code", "status_timestamp",
               "city", "partner_name"],
         order=[("status_sequence", True)], row_limit=300),
]

NS = uuid.UUID("51360000-0000-4000-8000-000000000000")
def uid(s): return str(uuid.uuid5(NS, s))


def ensure_datasets(sc):
    by_name = {}
    for d in sc.get_datasets():
        if d.get("table_name") in DS.values():
            by_name[d["table_name"]] = d["id"]
    ids = {}
    for key, table in DS.items():
        if table in by_name:
            ids[key] = by_name[table]
            print(f"  dataset {table}: exists (id={ids[key]})")
        else:
            res = sc.create_dataset(database=DB_ID, schema=SCHEMA, table_name=table)
            ids[key] = res["id"]
            print(f"  dataset {table}: created (id={ids[key]})")
    return ids


def ensure_charts(sc, ds_ids):
    existing = {c["slice_name"]: c["id"] for c in sc.get_charts()}
    ids = {}
    for c in CHARTS:
        ds_id = ds_ids[c["dataset"]]
        viz, params, qctx = B.build(c["kind"], ds_id, c)
        payload = dict(slice_name=c["slice"], viz_type=viz, datasource_id=ds_id,
                       datasource_type="table", params=json.dumps(params),
                       query_context=json.dumps(qctx))
        if c["slice"] in existing:
            cid = existing[c["slice"]]; sc.update_chart(cid, **payload)
            print(f"  chart {c['slice']}: updated (id={cid})")
        else:
            cid = sc.create_resource("chart", **payload)["id"]
            print(f"  chart {c['slice']}: created (id={cid})")
        ids[c["slice"]] = cid
    return ids


def add_tab(sc, chart_ids):
    r = sc.session.get(sc.baseurl / "api/v1/dashboard" / str(DASH_ID)).json()["result"]
    pos = json.loads(r["position_json"])
    top = next(k for k, v in pos.items() if isinstance(v, dict) and v.get("type") == "TABS"
               and v.get("parents") == ["ROOT_ID", "GRID_ID"] or
               (isinstance(v, dict) and v.get("type") == "TABS" and "GRID_ID" in (v.get("parents") or []) and len(v.get("parents")) == 2))
    topnode = pos[top]

    # remove any prior SI360 nodes (idempotent) ------------------------------
    for k in [k for k in pos if isinstance(k, str) and k.startswith(PFX)]:
        del pos[k]
    topnode["children"] = [c for c in topnode["children"] if not c.startswith(PFX)]

    tab_id = f"{PFX}-TAB"
    parents_tab = ["ROOT_ID", "GRID_ID", top]
    # pack charts into rows of width<=12 in spec order
    rows, cur, curw = [], [], 0
    for c in CHARTS:
        if curw + c["w"] > 12 and cur:
            rows.append(cur); cur, curw = [], 0
        cur.append(c); curw += c["w"]
    if cur: rows.append(cur)

    row_ids = []
    for ri, row in enumerate(rows):
        rid = f"{PFX}-ROW{ri}"
        parents_row = parents_tab + [tab_id]
        child_ids = []
        for c in row:
            cid = chart_ids[c["slice"]]
            comp_id = f"{PFX}-CHART-{cid}"
            pos[comp_id] = {"type": "CHART", "id": comp_id, "children": [],
                            "meta": {"chartId": cid, "width": c["w"], "height": c["h"],
                                     "sliceName": c["slice"], "uuid": uid(f"chart-{cid}")},
                            "parents": parents_row + [rid]}
            child_ids.append(comp_id)
        pos[rid] = {"type": "ROW", "id": rid, "children": child_ids,
                    "meta": {"background": "BACKGROUND_TRANSPARENT"}, "parents": parents_tab + [tab_id]}
        row_ids.append(rid)

    pos[tab_id] = {"type": "TAB", "id": tab_id,
                   "meta": {"text": TAB_TITLE, "defaultText": "Tab title", "placeholder": "Tab title"},
                   "children": row_ids, "parents": parents_tab}
    topnode["children"].append(tab_id)

    sc.update_dashboard(DASH_ID, position_json=json.dumps(pos))
    # link the new charts to dashboard 12 (their only dashboard)
    for cid in chart_ids.values():
        sc.update_chart(cid, dashboards=[DASH_ID])
    print(f"added tab '{TAB_TITLE}' with {len(chart_ids)} charts to dashboard {DASH_ID}")


def verify(sc, chart_ids):
    base = str(sc.baseurl).rstrip("/")
    ok = bad = 0
    for sn, cid in chart_ids.items():
        try:
            rr = sc.session.get(f"{base}/api/v1/chart/{cid}/data/", params={"force": "false"}, timeout=90)
            if rr.status_code == 200:
                res = rr.json().get("result", [{}]); n = len(res[0].get("data", [])) if res else 0
                print(f"  ok {sn:<40} rows={n}"); ok += 1
            else:
                msg = rr.json().get("message", rr.text[:160]) if rr.headers.get("content-type","").startswith("application/json") else rr.text[:160]
                print(f"  XX {sn:<40} HTTP {rr.status_code}: {msg}"); bad += 1
        except Exception as e:
            print(f"  XX {sn:<40} ERROR {e}"); bad += 1
    print(f"\n  rendered OK: {ok}   failed: {bad}")


def main():
    sc = client()
    print("== datasets =="); ds = ensure_datasets(sc)
    print("== charts =="); cids = ensure_charts(sc, ds)
    print("== tab =="); add_tab(sc, cids)
    print("== verify =="); verify(sc, cids)
    print("URL:", str(sc.baseurl).rstrip("/") + "/superset/dashboard/shipment-anomaly-control-tower/")


if __name__ == "__main__":
    main()
