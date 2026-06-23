#!/usr/bin/env python3
"""Build the **EDI Anomaly Control Tower** — a parallel dashboard on the reference
edi_anomaly_dashboard_dataset views. Reuses build_cockpit's chart engine and
build_dashboard's nested-tab layout by pointing their spec global at anomaly_spec.
Leaves the existing Integration Cockpit untouched (disjoint dataset/chart names).

Usage:
  python build_anomaly.py datasets   # create/refresh edi_* datasets
  python build_anomaly.py charts     # create/update EDI · … charts
  python build_anomaly.py dashboard  # assemble the parallel tabbed dashboard
  python build_anomaly.py verify     # render every chart, report rows
  python build_anomaly.py all        # datasets -> charts -> dashboard -> verify
"""
import os, sys, json
import anomaly_spec as A
import build_cockpit as B
import build_dashboard as D
import export_assets as E
from preset_client import client, get_database_id, get_dataset_id

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, "superset", "assets", "anomaly")

# Redirect both engines' spec global to the anomaly spec.
B.S = A
D.S = A


def native_filters(filter_ds_id, all_charts):
    cfgs = [{
        "id": "NATIVE_FILTER-time-range", "name": "Time range",
        "filterType": "filter_time", "targets": [{}], "controlValues": {},
        "defaultDataMask": {"filterState": {"value": "No filter"}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "chartsInScope": all_charts,
    }]
    for label, col in A.NATIVE_FILTERS:
        cfgs.append({
            "id": f"NATIVE_FILTER-{col}", "name": label, "filterType": "filter_select",
            "targets": [{"column": {"name": col}, "datasetId": filter_ds_id}],
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
                 if c["slice_name"] in {x["slice"] for x in A.CHARTS}}
    missing = [c["slice"] for c in A.CHARTS if c["slice"] not in chart_ids]
    if missing:
        raise SystemExit(f"charts not built yet: {missing}")
    filter_ds = get_dataset_id(sc, A.FILTER_DATASET)
    position = D.build_position(chart_ids)
    metadata = {
        "native_filter_configuration": native_filters(filter_ds, list(chart_ids.values())),
        "cross_filters_enabled": True,
        "refresh_frequency": 60,
        "color_scheme": "supersetColors",
        "label_colors": D.STATUS_COLORS,
        "shared_label_colors": {}, "color_scheme_domain": [],
    }
    payload = dict(dashboard_title=A.DASHBOARD_TITLE, slug=A.DASHBOARD_SLUG,
                   position_json=json.dumps(position),
                   json_metadata=json.dumps(metadata), published=True)
    existing = [d for d in sc.get_dashboards() if d.get("dashboard_title") == A.DASHBOARD_TITLE]
    if existing:
        did = existing[0]["id"]; sc.update_dashboard(did, **payload)
        print(f"dashboard updated (id={did})")
    else:
        did = sc.create_dashboard(**payload)["id"]
        print(f"dashboard created (id={did})")
    for cid in chart_ids.values():
        sc.update_chart(cid, dashboards=[did])
    print(f"linked {len(chart_ids)} charts to dashboard {did}")
    print("URL:", str(sc.baseurl).rstrip("/") + f"/superset/dashboard/{A.DASHBOARD_SLUG}/")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    sc = client()
    db_id = get_database_id(sc)
    if cmd in ("datasets", "all"):
        print("== datasets =="); ds_ids = B.ensure_datasets(sc, db_id)
    else:
        ds_ids = {n: get_dataset_id(sc, n) for n in A.DATASETS}
    if cmd in ("charts", "all"):
        print("== charts =="); B.ensure_charts(sc, ds_ids)
    if cmd in ("dashboard", "all"):
        print("== dashboard =="); build_dashboard(sc)
    if cmd in ("verify", "all"):
        chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()
                     if c["slice_name"] in {x["slice"] for x in A.CHARTS}}
        print("== verify =="); B.verify(sc, chart_ids)
    if cmd in ("export", "all"):
        print("== export =="); os.makedirs(ASSET_DIR, exist_ok=True)
        E.export(A.DASHBOARD_TITLE, ASSET_DIR)


if __name__ == "__main__":
    main()
