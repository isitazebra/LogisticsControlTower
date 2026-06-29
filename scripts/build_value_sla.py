#!/usr/bin/env python3
"""Assemble the **Integration Command Center** (value_spec) — the single-world,
demo-quality unification of the merged Control Tower (dash 10) + Minimal
(dash 13) + Q12/Q15/Q17, re-sourced entirely onto the cockpit contract
(db3 / public.txn_*) so partner names, doc types, LOB and the native FILTERS
are consistent across every tab.

Reuse-first: most charts already exist (cockpit_spec + Q15/Q17) and are reused
in place; only the protocol-scoped EDI/API charts are net-new. Creates a NEW
dashboard and APPENDS it to each chart's dashboards list, so dashboards 10/12/13
stay intact as a fallback for the demo.

Usage:
  python build_value.py            # datasets -> charts -> dashboard -> verify
  python build_value.py verify     # just re-render every chart in the layout
"""
import sys, json, uuid
import build_cockpit as B
from preset_client import client, get_database_id, get_dataset_id
import value_spec_sla as V

# point the shared engine at our spec
B.S = V

NS = uuid.UUID("ca11ce00-0000-4000-8000-000000000000")
def uid(s): return str(uuid.uuid5(NS, s))

STATUS_COLORS = {
    "failed": "#e04355", "rejected": "#f59f00", "duplicate": "#adb5bd",
    "ok": "#2f9e44", "delivered": "#2f9e44", "acked": "#2f9e44",
    "met": "#2f9e44", "missed": "#e04355", "at_risk": "#f59f00", "pending": "#adb5bd",
    "received": "#2f9e44", "missing": "#e04355",
    "edi": "#3b82f6", "api": "#8b5cf6",
    # processing_status lifecycle (Transaction view): early -> done -> error.
    "Received": "#adb5bd", "Validated": "#4dabf7", "Processing": "#f59f00",
    "Processed": "#2f9e44", "Failed": "#e04355", "Rejected": "#f76707",
    "Duplicate": "#868e96",
    "Silent": "#e04355", "Severe drop": "#f59f00", "Watch": "#fcc419", "Normal": "#2f9e44",
}


def resolve_chart_ids(sc, ensured):
    """slice -> chart id for every slice referenced in the LAYOUT.
    Ensured charts come from ensure_charts; the rest (Q15/Q17 externals) are
    looked up among existing Preset charts by slice name."""
    by_name = {c["slice_name"]: c["id"] for c in sc.get_charts()}
    ids, missing = {}, []
    for _tab, rows in V.LAYOUT:
        for entry in rows:
            slice_name = entry[0]
            if slice_name in ensured:
                ids[slice_name] = ensured[slice_name]
            elif slice_name in by_name:
                ids[slice_name] = by_name[slice_name]
            else:
                missing.append(slice_name)
    if missing:
        raise SystemExit(f"layout references charts that don't exist: {missing}")
    return ids


def build_position(chart_ids):
    """Flat top-level tabs (vision flow) -> rows (<=12 wide) -> charts."""
    pos = {"DASHBOARD_VERSION_KEY": "v2"}
    pos["ROOT_ID"] = {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]}
    pos["HEADER_ID"] = {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": V.DASHBOARD_TITLE}}
    top = "TABS-TOP"
    pos["GRID_ID"] = {"type": "GRID", "id": "GRID_ID", "children": [top], "parents": ["ROOT_ID"]}
    tab_ids = []
    for ti, (tab_title, rows_spec) in enumerate(V.LAYOUT):
        tab_id = f"TAB-V{ti}"
        tab_ids.append(tab_id)
        parents_tab = ["ROOT_ID", "GRID_ID", top, tab_id]
        # pack into rows
        rows, cur, curw = [], [], 0
        for entry in rows_spec:
            w = entry[1]
            if curw + w > 12 and cur:
                rows.append(cur); cur, curw = [], 0
            cur.append(entry); curw += w
        if cur: rows.append(cur)
        row_ids = []
        for ri, row in enumerate(rows):
            rid = f"ROW-V{ti}-{ri}"
            child_ids = []
            for entry in row:
                # entry = (slice, w, h) or (slice, w, h, display_title_override)
                slice_name, w, h = entry[0], entry[1], entry[2]
                override = entry[3] if len(entry) > 3 else None
                cid = chart_ids[slice_name]
                comp_id = f"CHART-V{ti}-{cid}"
                meta = {"chartId": cid, "width": w, "height": h,
                        "sliceName": slice_name, "uuid": uid(f"chart-{ti}-{cid}")}
                if override:
                    meta["sliceNameOverride"] = override
                pos[comp_id] = {"type": "CHART", "id": comp_id, "children": [],
                                "meta": meta, "parents": parents_tab + [rid]}
                child_ids.append(comp_id)
            pos[rid] = {"type": "ROW", "id": rid, "children": child_ids,
                        "meta": {"background": "BACKGROUND_TRANSPARENT"},
                        "parents": parents_tab}
            row_ids.append(rid)
        pos[tab_id] = {"type": "TAB", "id": tab_id,
                       "meta": {"text": tab_title, "defaultText": "Tab title", "placeholder": "Tab title"},
                       "children": row_ids, "parents": ["ROOT_ID", "GRID_ID", top]}
    pos[top] = {"type": "TABS", "id": top, "children": tab_ids, "parents": ["ROOT_ID", "GRID_ID"]}
    return pos


def native_filters(sc, rollup_ds, chart_ids):
    all_charts = list(dict.fromkeys(chart_ids.values()))
    cfgs = [{
        "id": "NATIVE_FILTER-time-range", "name": "Time range",
        "filterType": "filter_time", "targets": [{}], "controlValues": {},
        "defaultDataMask": {"filterState": {"value": "No filter"}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "chartsInScope": all_charts,
    }]
    for label, col in V.NATIVE_FILTERS:
        cfgs.append({
            "id": f"NATIVE_FILTER-{col}", "name": label, "filterType": "filter_select",
            "targets": [{"column": {"name": col}, "datasetId": rollup_ds}],
            "controlValues": {"multiSelect": True, "enableEmptyFilter": False,
                              "defaultToFirstItem": False, "searchAllOptions": False,
                              "inverseSelection": False},
            "defaultDataMask": {"filterState": {}},
            "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "type": "NATIVE_FILTER", "chartsInScope": all_charts,
        })
    # Chart-scoped REQUIRED drill-downs: a shipment_id filter that stays EMPTY
    # until a row is selected, so the target detail charts ask for a selection
    # instead of dumping all rows. enableEmptyFilter=True makes it required;
    # scope.excluded + chartsInScope confine it to ONLY its target charts.
    # DRILLDOWN_FILTERS (list, each with `slices`) is the general contract; the
    # single SHIP_DRILLDOWN_FILTER (`slice`) is the back-compat fallback.
    drills = getattr(V, "DRILLDOWN_FILTERS", None)
    if not drills:
        one = getattr(V, "SHIP_DRILLDOWN_FILTER", None)
        drills = [one] if one else []
    for i, f in enumerate(drills):
        slices = f.get("slices") or ([f["slice"]] if f.get("slice") else [])
        targets = [chart_ids[s] for s in slices if s in chart_ids]
        if not targets:
            continue
        detail_ds = get_dataset_id(sc, f["dataset"])
        others = [c for c in all_charts if c not in targets]
        cfgs.append({
            "id": f"NATIVE_FILTER-drilldown-{i}", "name": f["name"],
            "filterType": "filter_select",
            "targets": [{"column": {"name": f["column"]}, "datasetId": detail_ds}],
            "controlValues": {"multiSelect": False,
                              "enableEmptyFilter": bool(f.get("required", True)),
                              "defaultToFirstItem": False, "searchAllOptions": True,
                              "inverseSelection": False},
            "defaultDataMask": {"filterState": {}},
            "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": others},
            "type": "NATIVE_FILTER", "chartsInScope": targets,
        })
    return cfgs


def link_append(sc, did, chart_ids):
    """Add `did` to each chart's dashboards list (preserve existing links)."""
    base = str(sc.baseurl).rstrip("/")
    for cid in dict.fromkeys(chart_ids.values()):
        try:
            cur = sc.session.get(f"{base}/api/v1/chart/{cid}").json()["result"]
            dash_ids = [d["id"] for d in (cur.get("dashboards") or [])]
        except Exception:
            dash_ids = []
        if did not in dash_ids:
            dash_ids.append(did)
        sc.update_chart(cid, dashboards=dash_ids)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    sc = client()
    db_id = get_database_id(sc)

    if cmd in ("all",):
        print("== datasets =="); ds_ids = B.ensure_datasets(sc, db_id)
        print("== charts =="); ensured = B.ensure_charts(sc, ds_ids)
    else:
        ds_ids = {n: get_dataset_id(sc, n) for n in V.DATASETS}
        ensured = {c["slice"]: None for c in V.CHARTS}
        ensured = {k: v for k, v in
                   {c["slice_name"]: c["id"] for c in sc.get_charts()}.items()
                   if k in {x["slice"] for x in V.CHARTS}}

    chart_ids = resolve_chart_ids(sc, ensured)
    print(f"  resolved {len(chart_ids)} charts across {len(V.LAYOUT)} tabs")

    if cmd == "verify":
        print("== verify =="); B.verify(sc, chart_ids); return

    print("== dashboard ==")
    rollup_ds = get_dataset_id(sc, "vw_rollup")
    position = build_position(chart_ids)
    metadata = {
        "native_filter_configuration": native_filters(sc, rollup_ds, chart_ids),
        "cross_filters_enabled": True, "refresh_frequency": 60,
        "color_scheme": "supersetColors", "label_colors": STATUS_COLORS,
        "shared_label_colors": {}, "color_scheme_domain": [],
    }
    payload = dict(dashboard_title=V.DASHBOARD_TITLE, slug=V.SLUG,
                   position_json=json.dumps(position),
                   json_metadata=json.dumps(metadata), published=True)
    existing = [d for d in sc.get_dashboards() if d.get("dashboard_title") == V.DASHBOARD_TITLE]
    if existing:
        did = existing[0]["id"]; sc.update_dashboard(did, **payload)
        print(f"  dashboard updated (id={did})")
    else:
        did = sc.create_dashboard(**payload)["id"]
        print(f"  dashboard created (id={did})")

    print("== link =="); link_append(sc, did, chart_ids)
    print(f"  linked {len(set(chart_ids.values()))} charts to dashboard {did}")
    print("== verify =="); B.verify(sc, chart_ids)
    print("URL:", str(sc.baseurl).rstrip("/") + f"/superset/dashboard/{V.SLUG}/")


if __name__ == "__main__":
    main()
