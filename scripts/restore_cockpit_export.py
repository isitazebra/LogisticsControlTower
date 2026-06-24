#!/usr/bin/env python3
"""Restore the *original* Integration Visibility Cockpit from its point-in-time
export bundle (superset/exports/dashboard_export_20260623T160821/) as an
INDEPENDENT, working dashboard for side-by-side comparison.

Why not a plain re-import: the merge reused the cockpit's chart objects in place
(restyled pie->donut / timebar->ts and relinked them to the merged Integration
Control Tower, id 10). The export carries those same chart UUIDs, so importing
as-is would overwrite the live objects and corrupt the merged tower. So we:
  * regenerate the dashboard UUID + every chart UUID (rewriting both the chart
    files and the dashboard position's meta.uuid refs) -> brand-new objects,
    fully decoupled from the merged tower;
  * keep the dataset + database UUIDs and import with overwrite=False, so the
    existing datasets/DB are reused (skipped), never mutated;
  * inject the real DB URI into the bundle's database yaml so the masked
    password can never block the import (the bundle is built in memory and
    never written to disk / git).

Result: a published "Integration Visibility Cockpit (original)" dashboard with
the original 81 charts and original styling, beside the merged tower.
"""
import os, re, io, uuid, zipfile
from preset_client import client, neon_uri

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(ROOT, "superset", "exports", "dashboard_export_20260623T160821")
NEW_TITLE = "Integration Visibility Cockpit (original)"
NEW_SLUG = "integration-cockpit-original"


def read_bundle():
    files = {}
    for dirpath, _, names in os.walk(BUNDLE):
        for n in names:
            full = os.path.join(dirpath, n)
            arc = os.path.relpath(full, BUNDLE)
            with open(full, "rb") as f:
                files[arc] = f.read()
    return files


def main():
    files = read_bundle()
    uuid_map = {}  # old chart uuid -> new chart uuid

    # 1) charts: give each a fresh uuid (top-level `uuid:` line only)
    for arc, raw in list(files.items()):
        if not arc.startswith("charts/"):
            continue
        text = raw.decode("utf-8")
        m = re.search(r"^uuid:\s*(\S+)\s*$", text, re.M)
        if not m:
            raise SystemExit(f"no uuid in {arc}")
        old = m.group(1)
        new = str(uuid.uuid4())
        uuid_map[old] = new
        files[arc] = text.replace(old, new).encode("utf-8")
    print(f"re-keyed {len(uuid_map)} charts")

    # 2) dashboard: new uuid, new title/slug, remap every chart uuid ref
    darc = next(a for a in files if a.startswith("dashboards/"))
    dtext = files[darc].decode("utf-8")
    dtext = re.sub(r"^dashboard_title:.*$", f"dashboard_title: {NEW_TITLE}", dtext, count=1, flags=re.M)
    dtext = re.sub(r"^slug:.*$", f"slug: {NEW_SLUG}", dtext, count=1, flags=re.M)
    dtext = re.sub(r"^uuid:\s*\S+\s*$", f"uuid: {uuid.uuid4()}", dtext, count=1, flags=re.M)
    for old, new in uuid_map.items():
        dtext = dtext.replace(old, new)
    files[darc] = dtext.encode("utf-8")

    # 3) database: inject the real URI so a masked password can't block import
    real = neon_uri()
    for arc, raw in list(files.items()):
        if arc.startswith("databases/"):
            text = raw.decode("utf-8")
            text = re.sub(r"^(\s*sqlalchemy_uri:).*$", lambda mm: f"{mm.group(1)} {real}",
                          text, count=1, flags=re.M)
            files[arc] = text.encode("utf-8")

    # 4) zip in memory and import (overwrite=False -> datasets/DB reused, not touched)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, raw in files.items():
            z.writestr(os.path.join("bundle", arc), raw)
    buf.seek(0)

    sc = client()
    if [x for x in sc.get_dashboards() if x.get("dashboard_title") == NEW_TITLE]:
        raise SystemExit(f"'{NEW_TITLE}' already exists - delete it first to re-restore.")
    ok = sc.import_zip("dashboard", buf, overwrite=False)
    print("import OK:", ok)

    # 5) relink the layout. This export's position references charts by numeric
    #    meta.chartId, and its meta.uuid values never matched the chart files'
    #    real uuids - so once we re-key the charts, Superset cannot remap the
    #    layout and every tile renders "no chart definition". Re-anchor each
    #    CHART component by its stable meta.sliceName -> the imported chart's id.
    d = [x for x in sc.get_dashboards() if x.get("dashboard_title") == NEW_TITLE][0]
    did = d["id"]
    on = {c["slice_name"]: (c["id"], c.get("uuid")) for c in sc.get_charts()
          if did in [dd.get("id") for dd in c.get("dashboards", [])]}
    import json as _json
    pos = _json.loads(sc.session.get(sc.baseurl / "api/v1/dashboard" / str(did))
                      .json()["result"]["position_json"])
    fixed = 0
    for node in pos.values():
        if isinstance(node, dict) and node.get("type") == "CHART":
            sn = node["meta"].get("sliceName")
            if sn in on:
                node["meta"]["chartId"], u = on[sn]
                if u:
                    node["meta"]["uuid"] = u
                fixed += 1
    sc.update_dashboard(did, position_json=_json.dumps(pos))
    print(f"relinked {fixed} chart placements")

    # 6) report
    print(f"restored dashboard id={did} published={d.get('published')} charts={len(on)}")
    print("URL:", str(sc.baseurl).rstrip("/") + f"/superset/dashboard/{NEW_SLUG}/")


if __name__ == "__main__":
    main()
