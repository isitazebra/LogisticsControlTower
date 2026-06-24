#!/usr/bin/env python3
"""Build the merged **Integration Control Tower** dashboard from
cockpit_merged_spec.py. Reuses the cockpit chart engine (build_cockpit) and the
nested-tab layout (build_dashboard) by pointing their spec global at the merged
spec. Charts are reused/updated in place (names are stable), so this also
relinks them off the two source dashboards onto the merged one.

Usage:
  python build_merged.py datasets   # ensure all datasets exist (both schemas)
  python build_merged.py charts     # create/update + restyle all charts
  python build_merged.py dashboard  # assemble the merged tabbed dashboard
  python build_merged.py verify     # render every chart, report rows
  python build_merged.py export     # write YAML bundle to superset/assets/merged/
  python build_merged.py retire     # unpublish the two source dashboards (8, 9)
  python build_merged.py all        # datasets -> charts -> dashboard -> verify
"""
import os, sys, json
import cockpit_merged_spec as M
import build_cockpit as B
import build_dashboard as D
import export_assets as E
from preset_client import client, get_database_id, get_dataset_id

# Redirect both engines' spec global to the merged spec.
B.S = M
D.S = M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, "superset", "assets", "merged")
SOURCE_DASHBOARDS = ("Integration Visibility Cockpit", "EDI Anomaly Control Tower")


def native_filters(sc, chart_ids):
    """Time-range + the merged select filters, grouped under labelled DIVIDERs
    (one per world) so the panel reads as two clearly separated subject areas.
    Two Partner filters (partner_id/shipments, partner/transactions) because the
    worlds key partners differently and cannot cross-filter."""
    all_charts = list(chart_ids.values())
    cfgs = [{
        "id": "NATIVE_FILTER-time-range", "name": "Time range",
        "filterType": "filter_time", "targets": [{}], "controlValues": {},
        "defaultDataMask": {"filterState": {"value": "No filter"}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "chartsInScope": all_charts,
    }]
    seed = {}
    for gi, (group, filters) in enumerate(M.FILTER_GROUPS):
        cfgs.append({
            "id": f"NATIVE_FILTER_DIVIDER-{gi}", "type": "DIVIDER",
            "title": group, "description": "",
        })
        for label, col, dsname in filters:
            if dsname not in seed:
                seed[dsname] = get_dataset_id(sc, dsname)
            cfgs.append({
                "id": f"NATIVE_FILTER-{col}", "name": label, "filterType": "filter_select",
                "targets": [{"column": {"name": col}, "datasetId": seed[dsname]}],
                "controlValues": {"multiSelect": True, "enableEmptyFilter": False,
                                  "defaultToFirstItem": False, "searchAllOptions": False,
                                  "inverseSelection": False},
                "defaultDataMask": {"filterState": {}}, "cascadeParentIds": [],
                "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
                "type": "NATIVE_FILTER", "chartsInScope": all_charts,
            })
    return cfgs


def build_dashboard(sc):
    chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                 if c["slice_name"] in {x["slice"] for x in M.CHARTS}}
    missing = [c["slice"] for c in M.CHARTS if c["slice"] not in chart_ids]
    if missing:
        raise SystemExit(f"charts not built yet: {missing}")
    position = D.build_position(chart_ids)
    metadata = {
        "native_filter_configuration": native_filters(sc, chart_ids),
        "cross_filters_enabled": True,
        "refresh_frequency": 60,
        "color_scheme": "supersetColors",
        "label_colors": D.STATUS_COLORS,
        "shared_label_colors": {}, "color_scheme_domain": [],
    }
    payload = dict(dashboard_title=M.DASHBOARD_TITLE, slug=M.DASHBOARD_SLUG,
                   position_json=json.dumps(position),
                   json_metadata=json.dumps(metadata), published=True)
    existing = [d for d in sc.get_dashboards() if d.get("dashboard_title") == M.DASHBOARD_TITLE]
    if existing:
        did = existing[0]["id"]; sc.update_dashboard(did, **payload)
        print(f"dashboard updated (id={did})")
    else:
        did = sc.create_dashboard(**payload)["id"]
        print(f"dashboard created (id={did})")
    for cid in chart_ids.values():
        sc.update_chart(cid, dashboards=[did])
    print(f"linked {len(chart_ids)} charts to dashboard {did}")
    print("URL:", str(sc.baseurl).rstrip("/") + f"/superset/dashboard/{M.DASHBOARD_SLUG}/")


def rename_anomaly(sc):
    """Drop the "EDI · " prefix from the live anomaly charts (in place, so the
    existing objects are reused, not orphaned). Must run before ensure_charts,
    which keys off slice_name. Idempotent: skips charts already renamed."""
    by_name = {c["slice_name"]: c["id"] for c in sc.get_charts()}
    n = 0
    for old, new in M.ANOM_RENAMES.items():
        if old in by_name and new not in by_name:
            sc.update_chart(by_name[old], slice_name=new)
            n += 1
    print(f"renamed {n} anomaly charts (dropped 'EDI · ' prefix)")


def retire(sc):
    """Unpublish the two source dashboards now that the merged one is the base."""
    for title in SOURCE_DASHBOARDS:
        for d in sc.get_dashboards():
            if d.get("dashboard_title") == title:
                sc.update_dashboard(d["id"], published=False)
                print(f"unpublished '{title}' (id={d['id']})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    sc = client()
    db_id = get_database_id(sc)
    if cmd in ("datasets", "all"):
        print("== datasets =="); ds_ids = B.ensure_datasets(sc, db_id)
    else:
        ds_ids = {n: get_dataset_id(sc, n) for n in M.DATASETS}
    if cmd in ("charts", "all"):
        print("== rename =="); rename_anomaly(sc)
        print("== charts =="); B.ensure_charts(sc, ds_ids)
    if cmd in ("dashboard", "all"):
        print("== dashboard =="); build_dashboard(sc)
    if cmd in ("verify", "all"):
        chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                     if c["slice_name"] in {x["slice"] for x in M.CHARTS}}
        print("== verify =="); B.verify(sc, chart_ids)
    if cmd == "export":
        print("== export =="); os.makedirs(ASSET_DIR, exist_ok=True)
        E.export(M.DASHBOARD_TITLE, ASSET_DIR)
    if cmd == "retire":
        print("== retire =="); retire(sc)


if __name__ == "__main__":
    main()
