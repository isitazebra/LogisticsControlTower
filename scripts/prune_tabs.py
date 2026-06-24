#!/usr/bin/env python3
"""Remove tabs from the Integration Value Cockpit (dashboard 12) -- layout-only.

We retire the **Root Cause Analysis** and **Exception Workbench** tabs: neither
fits the integration lens (root-cause / exception triage is a shipment-anomaly /
TMS concern, not integration-flow health). This prunes the TAB subtree from
position_json -- the tab node, its descendant ROW/CHART layout nodes, and its
entry in the parent TABS children -- WITHOUT deleting the underlying chart
objects. So it is non-destructive and reversible (re-add the layout to bring a
chart back). Idempotent: removing an already-absent tab is a no-op.

Usage:  python prune_tabs.py
"""
import json
from preset_client import client

DASH_ID = 12
# tab node ids to remove (resolved from position_json by title earlier)
TABS_TO_REMOVE = {
    "TAB-1YVtcf3Qqikp7AUYEqROi": "Root Cause Analysis",
    "TAB-WWu3nbnCOydaF9t34Iqq2": "Exception Workbench",
}


def descendants(pos, node_id):
    """All layout node ids in the subtree rooted at node_id (inclusive)."""
    out, stack = set(), [node_id]
    while stack:
        nid = stack.pop()
        if nid in out or nid not in pos:
            continue
        out.add(nid)
        node = pos[nid]
        if isinstance(node, dict):
            stack.extend(node.get("children", []) or [])
    return out


def main():
    sc = client()
    r = sc.session.get(sc.baseurl / "api/v1/dashboard" / str(DASH_ID)).json()["result"]
    pos = json.loads(r["position_json"])

    removed = []
    for tab_id, title in TABS_TO_REMOVE.items():
        if tab_id not in pos:
            print(f"  tab '{title}' ({tab_id}): already absent -- skip")
            continue
        kill = descendants(pos, tab_id)
        # detach from any parent's children list
        for node in pos.values():
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                node["children"] = [c for c in node["children"] if c != tab_id]
        for nid in kill:
            pos.pop(nid, None)
        removed.append(title)
        print(f"  tab '{title}' ({tab_id}): removed {len(kill)} layout nodes")

    if not removed:
        print("nothing to do (both tabs already absent)")
        return

    sc.update_dashboard(DASH_ID, position_json=json.dumps(pos))
    print(f"pruned {len(removed)} tab(s) from dashboard {DASH_ID}: {', '.join(removed)}")
    print("(chart objects left intact -- layout-only, reversible)")


if __name__ == "__main__":
    main()
