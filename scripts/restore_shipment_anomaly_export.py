#!/usr/bin/env python3
"""Stand up the *external* "Shipment Anomaly Control Tower" dashboard from its
native Superset export bundle (superset/exports/shipment_anomaly_control_tower/)
as an INDEPENDENT, working dashboard on our Preset workspace.

This is the genuine reference dashboard that complements the mp_demo data dump
(the `edi_anomaly_dashboard_dataset` schema we loaded into Neon). The bundle was
exported from a foreign workspace whose database `mp_demo` lived on an internal
host (postgresql://cloud-user@10.73.123.248/mp_demo). The 64 charts hang off 12
physical datasets, all on schema `edi_anomaly_dashboard_dataset` - and every one
of those views/tables already exists in our Neon `neondb`.

So to make it work here we only need to re-point the connection, not rebuild
anything:
  * inject our real Neon URI into the bundle's database yaml, replacing the
    foreign host + masked password (so the import creates a working connection);
  * null the datasets' `catalog: mp_demo` (Neon's database is `neondb`, not
    `mp_demo`; the schema `edi_anomaly_dashboard_dataset` resolves directly, and
    a stale catalog would otherwise poison query generation);
  * keep every UUID (dashboard / charts / datasets / theme) so the imported
    objects are fully independent and share nothing with dashboards 9/10/11.

The bundle is rewritten in memory and never written back to disk / git, so the
masked password trick and the committed bundle stay clean.
"""
import os, re, io, zipfile
from preset_client import client, neon_uri

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(ROOT, "superset", "exports", "shipment_anomaly_control_tower")
TITLE = "Shipment Anomaly Control Tower"
DB_LABEL = "mp_demo (Neon)"


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
    real = neon_uri()

    # 1) database: swap foreign host + masked password for our Neon URI, relabel
    for arc, raw in list(files.items()):
        if arc.startswith("databases/"):
            text = raw.decode("utf-8")
            text = re.sub(r"^(\s*sqlalchemy_uri:).*$", lambda m: f"{m.group(1)} {real}",
                          text, count=1, flags=re.M)
            text = re.sub(r"^(database_name:).*$", f"\\1 {DB_LABEL}", text, count=1, flags=re.M)
            files[arc] = text.encode("utf-8")

    # 2) datasets: null the mp_demo catalog so queries hit neondb.<schema>.<table>
    ncat = 0
    for arc, raw in list(files.items()):
        if arc.startswith("datasets/"):
            text = raw.decode("utf-8")
            new = re.sub(r"^catalog:.*$", "catalog: null", text, count=1, flags=re.M)
            if new != text:
                ncat += 1
            files[arc] = new.encode("utf-8")
    print(f"re-pointed database -> Neon, nulled catalog on {ncat} datasets")

    # 3) zip in memory, import (overwrite=False -> independent objects created)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, raw in files.items():
            z.writestr(os.path.join("bundle", arc), raw)
    buf.seek(0)

    sc = client()
    if [x for x in sc.get_dashboards() if x.get("dashboard_title") == TITLE]:
        raise SystemExit(f"'{TITLE}' already exists - delete it first to re-restore.")
    ok = sc.import_zip("dashboard", buf, overwrite=False)
    print("import OK:", ok)

    # 4) locate the imported dashboard + its charts
    d = [x for x in sc.get_dashboards() if x.get("dashboard_title") == TITLE][0]
    did = d["id"]
    on_charts = [c for c in sc.get_charts()
                 if did in [dd.get("id") for dd in c.get("dashboards", [])]]
    chart_ids = {c["slice_name"]: c["id"] for c in on_charts}
    print(f"imported dashboard id={did} published={d.get('published')} charts={len(chart_ids)}")

    # 5) verify every tile renders against real Neon data
    base = str(sc.baseurl).rstrip("/")
    ok = bad = 0
    for sn, cid in chart_ids.items():
        try:
            r = sc.session.get(f"{base}/api/v1/chart/{cid}/data/", params={"force": "false"}, timeout=60)
            if r.status_code == 200:
                ok += 1
            else:
                msg = r.json().get("message", r.text[:160]) if r.headers.get("content-type","").startswith("application/json") else r.text[:160]
                print(f"  x {sn:<40} HTTP {r.status_code}: {msg}")
                bad += 1
        except Exception as e:
            print(f"  x {sn:<40} ERROR {e}")
            bad += 1
    print(f"\n  rendered OK: {ok}   failed: {bad}")

    slug = d.get("slug") or did
    print("URL:", base + f"/superset/dashboard/{slug}/")


if __name__ == "__main__":
    main()
