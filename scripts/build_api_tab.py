#!/usr/bin/env python3
"""Add the **API Integration** tab to the Integration Value Cockpit (dashboard 12).

The API-channel counterpart to Shipment Integration 360, under the same
transient-integration lens: connectivity + request/response choreography,
latency vs partner target, success/error/retry, rate-limiting, and webhook
delivery. No business outcomes. Powered by the synthetic-but-coherent
api_transactions feed (gen_api_transactions.py) + sql/08 views.

Strictly additive + idempotent (APIX-prefixed layout nodes, charts matched by
slice_name). Run gen_api_transactions.py and sql/08_api_integration.sql first.

Usage:  python build_api_tab.py
"""
import json, uuid
import build_cockpit as B
from preset_client import client

DB_ID = 4
SCHEMA = "edi_anomaly_dashboard_dataset"
DASH_ID = 12
TAB_TITLE = "API Integration"
PFX = "APIX"

DS = {
    "detail": "vw_api_transaction_detail",
    "sum":    "vw_api_integration_summary",
}

KPI = dict(w=4, h=22, header_font_size=0.3, subheader_font_size=0.125)
CHARTS = [
    # KPI row -- API integration health (two rows of 3 wide tiles for legibility)
    dict(slice="API calls tracked", dataset="detail", kind="bignum", **KPI,
         metric=("API calls", "COUNT(*)"), subheader="integration calls exchanged"),
    dict(slice="API success rate %", dataset="detail", kind="bignum", **KPI,
         metric=("Success %", "100.0*AVG(success::int)"),
         number_format=".1f", subheader="2xx responses"),
    dict(slice="API latency-SLA met %", dataset="detail", kind="bignum", **KPI,
         metric=("Latency SLA %", "100.0*AVG(sla_met::int)"),
         number_format=".1f", subheader="within partner target"),
    dict(slice="API avg latency (ms)", dataset="detail", kind="bignum", **KPI,
         metric=("Avg ms", "AVG(latency_ms)"), number_format=",d",
         subheader="request -> response"),
    dict(slice="API server-error rate %", dataset="detail", kind="bignum", **KPI,
         metric=("5xx %", "100.0*AVG((status_class='5xx')::int)"),
         number_format=".2f", subheader="5xx of all calls"),
    dict(slice="API webhook delivery %", dataset="detail", kind="bignum", **KPI,
         metric=("Webhook %", "100.0*SUM(CASE WHEN is_webhook AND webhook_delivered THEN 1 ELSE 0 END)/NULLIF(SUM(is_webhook::int),0)"),
         number_format=".1f", subheader="webhooks delivered"),
    # distribution row
    dict(slice="API response class mix", dataset="detail", kind="donut", w=3, h=50,
         groupby="status_class", metric=("Calls", "COUNT(*)")),
    dict(slice="API calls by operation", dataset="detail", kind="bar", w=3, h=50,
         dim="api_operation", metric=("Calls", "COUNT(*)")),
    dict(slice="API avg latency by partner (ms)", dataset="detail", kind="bar", w=3, h=50,
         dim="partner_name", metric=("Avg latency (ms)", "AVG(latency_ms)")),
    dict(slice="API success rate by partner %", dataset="detail", kind="bar", w=3, h=50,
         dim="partner_name", metric=("Success %", "100.0*AVG(success::int)")),
    # trend
    dict(slice="API call volume by response class", dataset="detail", kind="ts", w=12, h=50,
         x="request_date", series="status_class", chart="bar", grain="P1D",
         metric=("Calls", "COUNT(*)")),
    # error worklist -- cross-filter source
    dict(slice="API error worklist", dataset="detail", kind="raw", w=12, h=60,
         cols=["partner_name", "api_operation", "endpoint", "http_status",
               "error_code", "latency_ms", "retry_count", "rate_limited", "request_ts"],
         filters=[("success", "==", False)],
         order=[("request_ts", False)], row_limit=300),
    # partner API scorecard
    dict(slice="Partner API health", dataset="sum", kind="raw", w=12, h=60,
         cols=["partner_name", "total_calls", "success_pct", "avg_latency_ms",
               "p95_latency_ms", "latency_sla_pct", "err_4xx", "err_5xx",
               "rate_limited_cnt", "retry_pct", "webhook_delivery_pct"],
         order=[("total_calls", False)], row_limit=50),
]

NS = uuid.UUID("a91d0000-0000-4000-8000-000000000000")
def uid(s): return str(uuid.uuid5(NS, s))


def ensure_datasets(sc):
    by_name = {d.get("table_name"): d["id"] for d in sc.get_datasets()
               if d.get("table_name") in DS.values()}
    ids = {}
    for key, table in DS.items():
        if table in by_name:
            ids[key] = by_name[table]
            print(f"  dataset {table}: exists (id={ids[key]})")
        else:
            ids[key] = sc.create_dataset(database=DB_ID, schema=SCHEMA, table_name=table)["id"]
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
               and "GRID_ID" in (v.get("parents") or []) and len(v.get("parents")) == 2)
    topnode = pos[top]

    for k in [k for k in pos if isinstance(k, str) and k.startswith(PFX)]:
        del pos[k]
    topnode["children"] = [c for c in topnode["children"] if not c.startswith(PFX)]

    tab_id = f"{PFX}-TAB"
    parents_tab = ["ROOT_ID", "GRID_ID", top]
    rows, cur, curw = [], [], 0
    for c in CHARTS:
        if curw + c["w"] > 12 and cur:
            rows.append(cur); cur, curw = [], 0
        cur.append(c); curw += c["w"]
    if cur: rows.append(cur)

    row_ids = []
    for ri, row in enumerate(rows):
        rid = f"{PFX}-ROW{ri}"
        child_ids = []
        for c in row:
            cid = chart_ids[c["slice"]]
            comp_id = f"{PFX}-CHART-{cid}"
            pos[comp_id] = {"type": "CHART", "id": comp_id, "children": [],
                            "meta": {"chartId": cid, "width": c["w"], "height": c["h"],
                                     "sliceName": c["slice"], "uuid": uid(f"chart-{cid}")},
                            "parents": parents_tab + [tab_id, rid]}
            child_ids.append(comp_id)
        pos[rid] = {"type": "ROW", "id": rid, "children": child_ids,
                    "meta": {"background": "BACKGROUND_TRANSPARENT"}, "parents": parents_tab + [tab_id]}
        row_ids.append(rid)

    pos[tab_id] = {"type": "TAB", "id": tab_id,
                   "meta": {"text": TAB_TITLE, "defaultText": "Tab title", "placeholder": "Tab title"},
                   "children": row_ids, "parents": parents_tab}
    topnode["children"].append(tab_id)

    sc.update_dashboard(DASH_ID, position_json=json.dumps(pos))
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
    print("URL:", str(sc.baseurl).rstrip("/") + "/superset/dashboard/integration-value-cockpit/")


if __name__ == "__main__":
    main()
