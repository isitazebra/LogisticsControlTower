#!/usr/bin/env python3
"""Assemble the tabbed Integration Cockpit dashboard: lay out charts into tabs,
wire native filters (env/lob/partner/protocol/channel/doc_type + time),
cross-filtering, and 60s auto-refresh. Idempotent (updates if it exists)."""
import json, uuid
from preset_client import client, get_database_id, get_dataset_id
import cockpit_spec as S

NS = uuid.UUID("11111111-2222-3333-4444-555555555555")
def uid(s): return str(uuid.uuid5(NS, s))

def chart_component(cid, slice_name, w, h):
    return {"type": "CHART", "id": f"CHART-{cid}",
            "children": [], "meta": {"chartId": cid, "width": w, "height": h,
                                     "sliceName": slice_name, "uuid": uid(f"chart-{cid}")}}

def make_rows(pos, charts, chart_ids, parents, prefix):
    """Pack charts (in spec order) into rows of width <=12; emit ROW + CHART
    components; return the list of row ids."""
    rows, cur, curw = [], [], 0
    for c in charts:
        if curw + c["w"] > 12 and cur:
            rows.append(cur); cur, curw = [], 0
        cur.append(c); curw += c["w"]
    if cur: rows.append(cur)
    row_ids = []
    for ri, row in enumerate(rows):
        rid = f"ROW-{prefix}-{ri}"
        child_ids = []
        for c in row:
            comp = chart_component(chart_ids[c["slice"]], c["slice"], c["w"], c["h"])
            comp["parents"] = parents + [rid]
            pos[comp["id"]] = comp
            child_ids.append(comp["id"])
        pos[rid] = {"type": "ROW", "id": rid, "children": child_ids,
                    "meta": {"background": "BACKGROUND_TRANSPARENT"}, "parents": parents}
        row_ids.append(rid)
    return row_ids

def build_position(chart_ids):
    """Two-level nested tabs: top sections -> sub-tabs -> rows -> charts."""
    pos = {"DASHBOARD_VERSION_KEY": "v2"}
    pos["ROOT_ID"] = {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]}
    pos["HEADER_ID"] = {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": S.DASHBOARD_TITLE}}
    top = "TABS-TOP"
    pos["GRID_ID"] = {"type": "GRID", "id": "GRID_ID", "children": [top], "parents": ["ROOT_ID"]}
    section_ids = []
    for si, (section, subtabs) in enumerate(S.SECTIONS):
        stab = f"TAB-S{si}"
        section_ids.append(stab)
        if len(subtabs) == 1:
            charts = [c for c in S.CHARTS if c["tab"] == subtabs[0]]
            parents = ["ROOT_ID", "GRID_ID", top, stab]
            row_ids = make_rows(pos, charts, chart_ids, parents, f"{si}")
            pos[stab] = {"type": "TAB", "id": stab, "meta": {"text": section},
                         "children": row_ids, "parents": ["ROOT_ID", "GRID_ID", top]}
        else:
            inner = f"TABS-S{si}"
            sub_ids = []
            for sj, sub in enumerate(subtabs):
                subtab = f"TAB-S{si}-{sj}"
                sub_ids.append(subtab)
                charts = [c for c in S.CHARTS if c["tab"] == sub]
                parents = ["ROOT_ID", "GRID_ID", top, stab, inner, subtab]
                row_ids = make_rows(pos, charts, chart_ids, parents, f"{si}{sj}")
                pos[subtab] = {"type": "TAB", "id": subtab, "meta": {"text": sub},
                               "children": row_ids,
                               "parents": ["ROOT_ID", "GRID_ID", top, stab, inner]}
            pos[inner] = {"type": "TABS", "id": inner, "children": sub_ids,
                          "parents": ["ROOT_ID", "GRID_ID", top, stab]}
            pos[stab] = {"type": "TAB", "id": stab, "meta": {"text": section},
                         "children": [inner], "parents": ["ROOT_ID", "GRID_ID", top]}
    pos[top] = {"type": "TABS", "id": top, "children": section_ids, "parents": ["ROOT_ID", "GRID_ID"]}
    return pos

STATUS_COLORS = {
    "failed": "#e04355", "rejected": "#f59f00", "duplicate": "#adb5bd",
    "ok": "#2f9e44", "delivered": "#2f9e44", "acked": "#2f9e44",
    "met": "#2f9e44", "missed": "#e04355", "at_risk": "#f59f00", "pending": "#adb5bd",
    "received": "#2f9e44", "missing": "#e04355",
    "edi": "#3b82f6", "api": "#8b5cf6",
}

def native_filters(rollup_ds_id, alltxn_ds_id, chart_ids):
    cfgs = []
    all_charts = list(chart_ids.values())
    cfgs.append({
        "id": "NATIVE_FILTER-time-range", "name": "Time range",
        "filterType": "filter_time", "targets": [{}],
        "controlValues": {}, "defaultDataMask": {"filterState": {"value": "No filter"}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "chartsInScope": all_charts,
    })
    for label, col in S.NATIVE_FILTERS:
        cfgs.append({
            "id": f"NATIVE_FILTER-{col}", "name": label, "filterType": "filter_select",
            "targets": [{"column": {"name": col}, "datasetId": rollup_ds_id}],
            "controlValues": {"multiSelect": True, "enableEmptyFilter": False,
                              "defaultToFirstItem": False, "searchAllOptions": False,
                              "inverseSelection": False},
            "defaultDataMask": {"filterState": {}},
            "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "type": "NATIVE_FILTER", "chartsInScope": all_charts,
        })
    # high-cardinality transaction search (targets the explorer dataset)
    cfgs.append({
        "id": "NATIVE_FILTER-business_ref", "name": "Business ref (search)",
        "filterType": "filter_select",
        "targets": [{"column": {"name": "business_ref"}, "datasetId": alltxn_ds_id}],
        "controlValues": {"multiSelect": True, "enableEmptyFilter": False,
                          "defaultToFirstItem": False, "searchAllOptions": True,
                          "inverseSelection": False},
        "defaultDataMask": {"filterState": {}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "chartsInScope": all_charts,
    })
    return cfgs

def main():
    sc = client()
    chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                 if c["slice_name"] in {x["slice"] for x in S.CHARTS}}
    missing = [c["slice"] for c in S.CHARTS if c["slice"] not in chart_ids]
    if missing:
        raise SystemExit(f"charts not built yet: {missing}")
    rollup_ds = get_dataset_id(sc, "vw_rollup")
    alltxn_ds = get_dataset_id(sc, "q_all_txn")
    position = build_position(chart_ids)
    metadata = {
        "native_filter_configuration": native_filters(rollup_ds, alltxn_ds, chart_ids),
        "cross_filters_enabled": True,
        "refresh_frequency": 60,
        "color_scheme": "supersetColors",
        "label_colors": STATUS_COLORS,
        "shared_label_colors": {}, "color_scheme_domain": [],
    }
    payload = dict(
        dashboard_title=S.DASHBOARD_TITLE,
        slug="integration-cockpit",
        position_json=json.dumps(position),
        json_metadata=json.dumps(metadata),
        published=True,
    )
    existing = [d for d in sc.get_dashboards() if d.get("dashboard_title") == S.DASHBOARD_TITLE]
    if existing:
        did = existing[0]["id"]
        sc.update_dashboard(did, **payload)
        print(f"dashboard updated (id={did})")
    else:
        res = sc.create_dashboard(**payload)
        did = res["id"]
        print(f"dashboard created (id={did})")

    # Superset doesn't derive chart<->dashboard links from position_json over the
    # API, so set each chart's dashboards field explicitly (required for the tabs
    # to render the slices and for native filters to scope correctly).
    linked = 0
    for cid in chart_ids.values():
        sc.update_chart(cid, dashboards=[did]); linked += 1
    print(f"linked {linked} charts to dashboard {did}")
    print("URL:", str(sc.baseurl).rstrip("/") + f"/superset/dashboard/integration-cockpit/")

if __name__ == "__main__":
    main()
