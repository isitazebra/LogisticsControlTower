#!/usr/bin/env python3
"""Build the Integration Cockpit in Preset from cockpit_spec.py — idempotent.
Creates virtual datasets, charts, the tabbed dashboard, then renders every
chart through the data API and reports row counts.

Usage:
  python build_cockpit.py datasets     # create/refresh datasets
  python build_cockpit.py charts       # create/update charts
  python build_cockpit.py dashboard    # assemble tabbed dashboard + filters
  python build_cockpit.py verify       # render every chart, report rows
  python build_cockpit.py all          # datasets -> charts -> dashboard -> verify
"""
import sys, json, time, uuid
from preset_client import client, get_database_id, get_dataset_id
import cockpit_spec as S

NS = uuid.UUID("11111111-2222-3333-4444-555555555555")  # stable uuid namespace

def m(label, sql):
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label, "hasCustomLabel": True}

def base_query(extra=None):
    q = {"filters": [], "extras": {"having": "", "where": ""}, "applied_time_extras": {},
         "annotation_layers": [], "url_params": {}, "custom_params": {}, "custom_form_data": {},
         "row_limit": 1000, "orderby": []}
    if extra:
        q.update(extra)
    return q

def qc(ds_id, form_data, query):
    return {"datasource": {"id": ds_id, "type": "table"}, "force": False,
            "queries": [query], "form_data": form_data,
            "result_format": "json", "result_type": "full"}

# --- per-kind config builders: return (viz_type, params, query_context) -----

def build(kind, ds_id, c):
    ds = f"{ds_id}__table"
    NO_TIME = "No filter"
    if kind == "raw":
        # Include any order-by column in the selected set, else the data API groups
        # by the selected columns and ORDER BY on a non-grouped column 400s.
        cols = list(c["cols"])
        for o in c.get("order", []):
            if o[0] not in cols:
                cols.append(o[0])
        filt = [{"col": f[0], "op": f[1], "val": f[2]} for f in c.get("filters", [])]
        order = [[o[0], o[1]] for o in c.get("order", [])]
        rl = c.get("row_limit", 100)
        tr = c.get("time_range", NO_TIME)
        params = {"datasource": ds, "viz_type": "table", "query_mode": "raw",
                  "all_columns": cols, "order_by_cols": [json.dumps([o[0], o[1]]) for o in c.get("order", [])],
                  "row_limit": rl, "server_page_length": 25, "time_range": tr}
        if c.get("col_fmt"):
            params["column_config"] = c["col_fmt"]
        if c.get("heat"):
            params["conditional_formatting"] = c["heat"]
        q = base_query({"columns": cols, "metrics": [], "filters": filt,
                        "orderby": order, "row_limit": rl})
        return "table", params, qc(ds_id, params, q)

    if kind == "agg":
        gb = c["groupby"]; mets = [m(l, s) for l, s in c["metrics"]]
        order = [[m(*c["metrics"][0]), False]]
        rl = c.get("row_limit", 1000)
        params = {"datasource": ds, "viz_type": "table", "query_mode": "aggregate",
                  "groupby": gb, "metrics": mets, "row_limit": rl,
                  "server_page_length": 25, "order_desc": True, "time_range": NO_TIME}
        if c.get("col_fmt"):
            params["column_config"] = c["col_fmt"]
        if c.get("heat"):
            params["conditional_formatting"] = c["heat"]
        q = base_query({"columns": gb, "metrics": mets, "orderby": order, "row_limit": rl})
        return "table", params, qc(ds_id, params, q)

    if kind == "bignum":
        met = m(*c["metric"])
        if c.get("trend"):  # big number WITH sparkline (needs a temporal column)
            tcol = c.get("trend_col", "bucket")
            params = {"datasource": ds, "viz_type": "big_number", "metric": met,
                      "subheader": c.get("subheader", ""), "time_range": NO_TIME,
                      "granularity_sqla": tcol, "time_grain_sqla": "P1D", "compare_lag": "",
                      "y_axis_format": c.get("number_format", "SMART_NUMBER")}
            q = base_query({"columns": [], "metrics": [met], "is_timeseries": True,
                            "granularity": tcol, "row_limit": 10000,
                            "extras": {"having": "", "where": "", "time_grain_sqla": "P1D"}})
            return "big_number", params, qc(ds_id, params, q)
        params = {"datasource": ds, "viz_type": "big_number_total", "metric": met,
                  "subheader": c.get("subheader", ""), "time_range": NO_TIME,
                  "y_axis_format": c.get("number_format", "SMART_NUMBER")}
        q = base_query({"columns": [], "metrics": [met], "row_limit": 1})
        return "big_number_total", params, qc(ds_id, params, q)

    if kind == "treemap":
        met = m(*c["metric"]); gb = c["groupby"]  # list of dims (outer..inner)
        params = {"datasource": ds, "viz_type": "treemap_v2", "metrics": [met],
                  "groupby": gb, "row_limit": c.get("row_limit", 100), "time_range": NO_TIME}
        q = base_query({"columns": gb, "metrics": [met], "orderby": [[met, False]],
                        "row_limit": c.get("row_limit", 100)})
        return "treemap_v2", params, qc(ds_id, params, q)

    if kind == "gauge":
        met = m(*c["metric"])
        params = {"datasource": ds, "viz_type": "gauge_chart", "metric": met,
                  "row_limit": 1, "time_range": NO_TIME,
                  "min_val": c.get("min_val", 0), "max_val": c.get("max_val", 100)}
        q = base_query({"columns": [], "metrics": [met], "row_limit": 1})
        return "gauge_chart", params, qc(ds_id, params, q)

    if kind == "pie":
        met = m(*c["metric"]); gb = [c["groupby"]]
        params = {"datasource": ds, "viz_type": "pie", "groupby": gb, "metric": met,
                  "row_limit": 100, "time_range": NO_TIME, "show_legend": True,
                  "label_type": "key_value"}
        q = base_query({"columns": gb, "metrics": [met], "orderby": [[met, False]], "row_limit": 100})
        return "pie", params, qc(ds_id, params, q)

    if kind == "donut":
        met = m(*c["metric"]); gb = [c["groupby"]]
        params = {"datasource": ds, "viz_type": "pie", "groupby": gb, "metric": met,
                  "row_limit": 100, "time_range": NO_TIME, "show_legend": True,
                  "innerRadius": 45, "donut": True, "label_type": "key_percent",
                  "show_labels": True, "labels_outside": True, "number_format": "SMART_NUMBER"}
        q = base_query({"columns": gb, "metrics": [met], "orderby": [[met, False]], "row_limit": 100})
        return "pie", params, qc(ds_id, params, q)

    if kind == "pivot":
        met = m(*c["metric"]); rows = c["rows"]; cols_ = c["columns"]
        params = {"datasource": ds, "viz_type": "pivot_table_v2",
                  "groupbyRows": rows, "groupbyColumns": cols_, "metrics": [met],
                  "aggregateFunction": "Sum", "rowTotals": True, "colTotals": True,
                  "row_limit": c.get("row_limit", 1000), "time_range": NO_TIME,
                  "valueFormat": c.get("number_format", "SMART_NUMBER"),
                  "metricsLayout": "COLUMNS"}
        q = base_query({"columns": rows + cols_, "metrics": [met],
                        "orderby": [[met, False]], "row_limit": c.get("row_limit", 1000)})
        return "pivot_table_v2", params, qc(ds_id, params, q)

    if kind == "ts":
        # generalised time series (line|bar) with a configurable temporal x column,
        # so views with metric_date / shipment_date / transaction_date all work
        # (the legacy `timebar` hardcodes the rollup's `bucket`).
        met = m(*c["metric"]); series = c.get("series"); xcol_name = c.get("x", "bucket")
        grain = c.get("grain", "P1D")
        viz = "echarts_timeseries_line" if c.get("chart", "line") == "line" else "echarts_timeseries_bar"
        xcol = {"timeGrain": grain, "columnType": "BASE_AXIS", "sqlExpression": xcol_name,
                "label": xcol_name, "expressionType": "SQL"}
        rl = c.get("row_limit", 10000)
        params = {"datasource": ds, "viz_type": viz, "x_axis": xcol_name,
                  "time_grain_sqla": grain, "groupby": ([series] if series else []),
                  "metrics": [met], "row_limit": rl, "time_range": NO_TIME,
                  "y_axis_format": c.get("number_format", "SMART_NUMBER")}
        q = base_query({"columns": [xcol] + ([series] if series else []), "metrics": [met],
                        "orderby": [[met, False]], "row_limit": rl,
                        "extras": {"having": "", "where": "", "time_grain_sqla": grain},
                        "series_columns": ([series] if series else [])})
        return viz, params, qc(ds_id, params, q)

    if kind == "bar":
        met = m(*c["metric"]); dim = c["dim"]; series = c.get("series")
        cols = [dim] + ([series] if series else [])
        rl = c.get("row_limit", 100)
        params = {"datasource": ds, "viz_type": "echarts_timeseries_bar",
                  "x_axis": dim, "groupby": ([series] if series else []),
                  "metrics": [met], "row_limit": rl, "time_range": NO_TIME,
                  "x_axis_sort_asc": False, "x_axis_sort_series": "name",
                  "order_desc": True}
        q = base_query({"columns": cols, "metrics": [met], "orderby": [[met, False]],
                        "row_limit": rl, "series_columns": ([series] if series else [])})
        return "echarts_timeseries_bar", params, qc(ds_id, params, q)

    if kind == "timebar":
        met = m(*c["metric"]); series = c.get("series")
        xcol = {"timeGrain": "P1D", "columnType": "BASE_AXIS", "sqlExpression": "bucket",
                "label": "bucket", "expressionType": "SQL"}
        params = {"datasource": ds, "viz_type": "echarts_timeseries_bar",
                  "x_axis": "bucket", "time_grain_sqla": "P1D",
                  "groupby": ([series] if series else []), "metrics": [met],
                  "row_limit": 10000, "time_range": NO_TIME}
        q = base_query({"columns": [xcol] + ([series] if series else []), "metrics": [met],
                        "orderby": [[met, False]], "row_limit": 10000,
                        "extras": {"having": "", "where": "", "time_grain_sqla": "P1D"},
                        "series_columns": ([series] if series else [])})
        return "echarts_timeseries_bar", params, qc(ds_id, params, q)

    raise ValueError(kind)

# --- stages -----------------------------------------------------------------

def ensure_datasets(sc, db_id):
    ids = {}
    for name, d in S.DATASETS.items():
        did = get_dataset_id(sc, name)
        if did:
            print(f"  dataset {name}: exists (id={did})")
        else:
            res = sc.create_dataset(database=db_id, schema="public", table_name=name, sql=d["sql"])
            did = res["id"]
            print(f"  dataset {name}: created (id={did})")
            if d.get("dttm"):
                try:
                    sc.update_dataset(did, override_columns=False, main_dttm_col=d["dttm"])
                except Exception as e:
                    print(f"    (dttm set warn: {e})")
        ids[name] = did
    return ids

def ensure_charts(sc, ds_ids):
    existing = {c["slice_name"]: c["id"] for c in sc.get_charts()}
    ids = {}
    for c in S.CHARTS:
        ds_id = ds_ids[c["dataset"]]
        viz, params, query_context = build(c["kind"], ds_id, c)
        payload = dict(slice_name=c["slice"], viz_type=viz, datasource_id=ds_id,
                       datasource_type="table", params=json.dumps(params),
                       query_context=json.dumps(query_context))
        if c["slice"] in existing:
            cid = existing[c["slice"]]
            sc.update_chart(cid, **payload)
            print(f"  chart {c['slice']}: updated (id={cid})")
        else:
            res = sc.create_resource("chart", **payload)
            cid = res["id"]
            print(f"  chart {c['slice']}: created (id={cid})")
        ids[c["slice"]] = cid
    return ids

def verify(sc, chart_ids):
    base = str(sc.baseurl).rstrip("/")
    ok = bad = 0
    for slice_name, cid in chart_ids.items():
        url = f"{base}/api/v1/chart/{cid}/data/"
        try:
            r = sc.session.get(url, params={"force": "false"}, timeout=60)
            if r.status_code == 200:
                res = r.json().get("result", [{}])
                n = len(res[0].get("data", [])) if res else 0
                print(f"  ✓ {slice_name:<34} rows={n}")
                ok += 1
            else:
                msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type","").startswith("application/json") else r.text[:200]
                print(f"  ✗ {slice_name:<34} HTTP {r.status_code}: {msg}")
                bad += 1
        except Exception as e:
            print(f"  ✗ {slice_name:<34} ERROR {e}")
            bad += 1
    print(f"\n  rendered OK: {ok}   failed: {bad}")
    return bad == 0

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    sc = client()
    db_id = get_database_id(sc)
    if cmd in ("datasets", "all"):
        print("== datasets =="); ds_ids = ensure_datasets(sc, db_id)
    else:
        ds_ids = {n: get_dataset_id(sc, n) for n in S.DATASETS}
    if cmd in ("charts", "all"):
        print("== charts =="); chart_ids = ensure_charts(sc, ds_ids)
    else:
        chart_ids = {c["slice"]: None for c in S.CHARTS}
    if cmd == "verify" or cmd == "all":
        if any(v is None for v in chart_ids.values()):
            chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                         if c["slice_name"] in {x["slice"] for x in S.CHARTS}}
        print("== verify =="); verify(sc, chart_ids)
    # dashboard handled in build_dashboard.py

if __name__ == "__main__":
    main()
