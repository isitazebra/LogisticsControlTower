#!/usr/bin/env python3
"""Integration-lens cleanup pass on dashboard 12.

Strictly additive + idempotent. Two changes:
  1) rename the dashboard + slug to "Integration Control Tower" (it is our
     integration-flow monitor, not a shipment-anomaly / TMS tool; APIs land
     here later alongside EDI);
  2) add a Transport mode (LOB) native filter. partner_type is uniformly
     "Retailer" so it is useless as a line of business; transport_mode is the
     real service line. The filter is SCOPED to the Shipment Integration 360
     tab because only the shipment-grain datasets carry transport_mode -- the
     baseline aggregate views are partner-level, so widening the LOB slice to
     them is a view-regeneration job parked under Gap 1, not faked here.

Run after build_shipment360.py (needs the SI360 tab + its datasets present).
"""
import json
from preset_client import client

DASH_ID = 12
NEW_TITLE = "Integration Value Cockpit"
NEW_SLUG = "integration-value-cockpit"
LOB_DS_TABLE = "vw_shipment_integration_summary"   # source of transport_mode options
LOB_COL = "transport_mode"
FILTER_ID = "NATIVE_FILTER-transport-mode-lob"
# both channel tabs carry transport_mode (EDI shipment-grain + API detail), so
# the LOB slice spans both; baseline aggregate tabs lack the column (Gap 1).
LOB_TABS = ["SI360-TAB", "APIX-TAB"]


def lob_filter(dataset_id, chart_ids):
    return {
        "id": FILTER_ID,
        "name": "Transport mode (LOB)",
        "filterType": "filter_select",
        "type": "NATIVE_FILTER",
        "targets": [{"datasetId": dataset_id, "column": {"name": LOB_COL}}],
        "controlValues": {
            "multiSelect": True, "enableEmptyFilter": False,
            "inverseSelection": False, "searchAllOptions": False,
            "defaultToFirstItem": False,
        },
        "defaultDataMask": {"filterState": {}, "extraFormData": {}},
        "cascadeParentIds": [],
        "scope": {"rootPath": LOB_TABS, "excluded": []},
        "chartsInScope": chart_ids,
        "tabsInScope": LOB_TABS,
        "description": "Line of business (service line) - spans the EDI + API integration tabs.",
    }


def main():
    sc = client()

    # datasets that actually carry transport_mode -> the charts the LOB slice can bite
    LOB_TABLES = {"vw_shipment_integration_summary", "vw_shipment_message_detail",
                  "vw_api_transaction_detail"}
    lob_ds_ids = {d["id"] for d in sc.get_datasets() if d.get("table_name") in LOB_TABLES}
    ds_id = next(d["id"] for d in sc.get_datasets() if d.get("table_name") == LOB_DS_TABLE)
    in_scope = [c["id"] for c in sc.get_charts()
                if DASH_ID in [d.get("id") for d in c.get("dashboards", [])]
                and c.get("datasource_id") in lob_ds_ids]

    r = sc.session.get(sc.baseurl / "api/v1/dashboard" / str(DASH_ID)).json()["result"]
    jm = json.loads(r.get("json_metadata") or "{}")
    nfc = jm.get("native_filter_configuration", [])
    # idempotent: drop any prior copy of our LOB filter, then append fresh
    nfc = [f for f in nfc if f.get("id") != FILTER_ID]
    nfc.append(lob_filter(ds_id, in_scope))
    jm["native_filter_configuration"] = nfc

    # title (duplicates allowed) + filter always apply; slug only if free, since
    # a superseded earlier merge may still hold it -- retire that first to claim it.
    sc.update_dashboard(DASH_ID, dashboard_title=NEW_TITLE, json_metadata=json.dumps(jm))
    print(f"renamed dashboard {DASH_ID} -> '{NEW_TITLE}'")
    print(f"LOB filter on {LOB_COL} (dataset {ds_id}) scoped to {LOB_TABS}, "
          f"{len(in_scope)} charts in scope")
    try:
        sc.update_dashboard(DASH_ID, slug=NEW_SLUG)
        print(f"  slug set -> {NEW_SLUG}")
    except Exception as e:
        print(f"  slug '{NEW_SLUG}' not free (held by another dashboard); left unchanged")


if __name__ == "__main__":
    main()
