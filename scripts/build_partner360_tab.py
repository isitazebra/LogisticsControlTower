#!/usr/bin/env python3
"""Add the **Partner 360** tab (Q17) to the Integration Value Cockpit.

One shareable scorecard row per partner: volume, exception rate, SLA %,
duplicate rate, open/breaching, $-at-risk, last-seen, onboarding tier/status,
and the Q15 anomaly flag. Reads public.q17_partner_360 (sql/10).

Cockpit world -> DB 3 ("Neon - Integration Cockpit"), schema public.
Additive + idempotent (P360-prefixed nodes, charts matched by slice).
Run sql/09 (Q15) + sql/10 (Q17) first.

Usage:  python build_partner360_tab.py
"""
import json, uuid
import build_cockpit as B
from preset_client import client

DB_ID = 3
SCHEMA = "public"
DASH_ID = 12
TAB_TITLE = "Partner 360"
PFX = "P360"

DS = {"p360": "q17_partner_360"}

KPI = dict(kind="bignum", w=3, h=22, header_font_size=0.3, subheader_font_size=0.125)
CHARTS = [
    dict(slice="Partners tracked", dataset="p360", **KPI,
         metric=("Partners", "COUNT(DISTINCT partner)"),
         subheader="in integration scope"),
    dict(slice="Partners flagged", dataset="p360", **KPI,
         metric=("Flagged", "COUNT(DISTINCT CASE WHEN anomaly_status<>'Normal' THEN partner END)"),
         subheader="silent or abnormal volume"),
    dict(slice="Total $ at risk", dataset="p360", **KPI,
         metric=("$ at risk", "SUM(dollars_at_risk)"), number_format="$,d",
         subheader="penalty exposure on exceptions"),
    dict(slice="Avg exception rate %", dataset="p360", **KPI,
         metric=("Exception %", "AVG(exception_pct)"), number_format=".2f",
         subheader="failed + rejected / volume"),
    # distributions
    dict(slice="Volume by partner", dataset="p360", kind="bar", w=4, h=50,
         dim="partner", metric=("Transactions", "SUM(refs)")),
    dict(slice="Exception rate by partner %", dataset="p360", kind="bar", w=4, h=50,
         dim="partner", metric=("Exception %", "MAX(exception_pct)")),
    dict(slice="Dollars at risk by partner", dataset="p360", kind="bar", w=4, h=50,
         dim="partner", metric=("$ at risk", "SUM(dollars_at_risk)")),
    # the scorecard -- centerpiece
    dict(slice="Partner 360 scorecard", dataset="p360", kind="raw", w=12, h=70,
         cols=["partner", "tier", "onboarding_status", "region", "environment",
               "refs", "exception_pct", "duplicate_pct", "sla_pct", "breaching",
               "dollars_at_risk", "anomaly_status", "silent_feeds", "last_seen"],
         order=[("dollars_at_risk", False)], row_limit=50),
]

NS = uuid.UUID("a17d0000-0000-4000-8000-000000000000")
def uid(s): return str(uuid.uuid5(NS, s))


def ensure_datasets(sc):
    by_name = {d.get("table_name"): d["id"] for d in sc.get_datasets()
               if d.get("table_name") in DS.values() and
               (d.get("database") or {}).get("id") == DB_ID}
    ids = {}
    for key, table in DS.items():
        if table in by_name:
            ids[key] = by_name[table]; print(f"  dataset {table}: exists (id={ids[key]})")
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
        rid = f"{PFX}-ROW{ri}"; child_ids = []
        for c in row:
            cid = chart_ids[c["slice"]]; comp_id = f"{PFX}-CHART-{cid}"
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
    base = str(sc.baseurl).rstrip("/"); ok = bad = 0
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
