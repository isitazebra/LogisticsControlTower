#!/usr/bin/env python3
"""Export the built dashboard (with its charts, datasets, and database) to a
native YAML bundle under superset/assets/ for version control — the spec's
'keep all datasets/charts/dashboards as YAML' deliverable. The database YAML
has its password masked by Superset on export (safe to commit)."""
import os, io, zipfile
from preset_client import client
import cockpit_spec as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "superset", "assets")

def export(title, out_dir):
    sc = client()
    did = [d for d in sc.get_dashboards()
           if d["dashboard_title"] == title][0]["id"]
    buf = sc.export_zip("dashboard", [did])
    # clear old export dirs (keep the folder)
    for sub in ("databases", "datasets", "charts", "dashboards", "metadata.yaml"):
        p = os.path.join(out_dir, sub)
        if os.path.isdir(p):
            for root, _, files in os.walk(p, topdown=False):
                for f in files: os.remove(os.path.join(root, f))
    n = 0
    with zipfile.ZipFile(buf) as z:
        for member in z.namelist():
            if member.endswith("/"): continue
            # strip the top-level export folder name
            rel = member.split("/", 1)[1] if "/" in member else member
            dest = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
            n += 1
    print(f"exported {n} YAML files to {os.path.relpath(out_dir, ROOT)}/")

def main():
    export(S.DASHBOARD_TITLE, OUT)

if __name__ == "__main__":
    main()
