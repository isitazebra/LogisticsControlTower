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

def build_position(chart_ids):
    pos = {"DASHBOARD_VERSION_KEY": "v2"}
    pos["ROOT_ID"] = {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]}
    pos["HEADER_ID"] = {"type": "HEADER", "id": "HEADER_ID",
                        "meta": {"text": S.DASHBOARD_TITLE}}
    tabs_id = "TABS-COCKPIT"
    pos["GRID_ID"] = {"type": "GRID", "id": "GRID_ID", "children": [tabs_id],
                      "parents": ["ROOT_ID"]}
    tab_ids = []
    for ti, tab in enumerate(S.TAB_ORDER):
        tab_id = f"TAB-{ti}"
        tab_ids.append(tab_id)
        # charts in this tab, packed into rows of <=12 width
        charts = [c for c in S.CHARTS if c["tab"] == tab]
        rows, cur, curw = [], [], 0
        for c in charts:
            w = c["w"]
            if curw + w > 12 and cur:
                rows.append(cur); cur, curw = [], 0
            cur.append(c); curw += w
        if cur: rows.append(cur)
        row_ids = []
        for ri, row in enumerate(rows):
            rid = f"ROW-{ti}-{ri}"
            child_ids = []
            for c in row:
                cid = chart_ids[c["slice"]]
                comp = chart_component(cid, c["slice"], c["w"], c["h"])
                comp["parents"] = ["ROOT_ID", "GRID_ID", tabs_id, tab_id, rid]
                pos[comp["id"]] = comp
                child_ids.append(comp["id"])
            pos[rid] = {"type": "ROW", "id": rid, "children": child_ids,
                        "meta": {"background": "BACKGROUND_TRANSPARENT"},
                        "parents": ["ROOT_ID", "GRID_ID", tabs_id, tab_id]}
            row_ids.append(rid)
        pos[tab_id] = {"type": "TAB", "id": tab_id, "meta": {"text": tab},
                       "children": row_ids, "parents": ["ROOT_ID", "GRID_ID", tabs_id]}
    pos[tabs_id] = {"type": "TABS", "id": tabs_id, "children": tab_ids,
                    "parents": ["ROOT_ID", "GRID_ID"]}
    return pos

def native_filters(rollup_ds_id, chart_ids):
    cfgs = []
    all_charts = list(chart_ids.values())
    # time range filter (applies to time-aware charts)
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
    return cfgs

def main():
    sc = client()
    chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                 if c["slice_name"] in {x["slice"] for x in S.CHARTS}}
    missing = [c["slice"] for c in S.CHARTS if c["slice"] not in chart_ids]
    if missing:
        raise SystemExit(f"charts not built yet: {missing}")
    rollup_ds = get_dataset_id(sc, "vw_rollup")
    position = build_position(chart_ids)
    metadata = {
        "native_filter_configuration": native_filters(rollup_ds, chart_ids),
        "cross_filters_enabled": True,
        "refresh_frequency": 60,
        "color_scheme": "supersetColors",
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
