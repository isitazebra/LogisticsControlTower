#!/usr/bin/env python3
"""Authenticated Superset/Preset client for this workspace, plus small helpers.

Reuses preset-cli's PresetAuth + SupersetClient. Reads creds from .env.
Import this module from build scripts:

    from preset_client import client, DB_NAME, neon_uri
    sc = client()
"""
import os
from yarl import URL
from preset_cli.auth.preset import PresetAuth
from preset_cli.api.clients.superset import SupersetClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = "Neon — Integration Cockpit"

def load_env():
    env = {}
    with open(os.path.join(ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

ENV = load_env()

def neon_uri():
    """SQLAlchemy URI Superset will use. Same Neon DB, sslmode required."""
    return ENV["NEON_DATABASE_URL"]

def workspace_url():
    return ENV["PRESET_WORKSPACE_URL"].rstrip("/")

def client():
    auth = PresetAuth(
        URL("https://api.app.preset.io/"),
        ENV["PRESET_API_TOKEN"],
        ENV["PRESET_API_SECRET"],
    )
    return SupersetClient(workspace_url(), auth)

def get_database_id(sc, name=DB_NAME):
    for db in sc.get_databases():
        if db.get("database_name") == name:
            return db["id"]
    return None

def get_dataset_id(sc, table_name):
    for ds in sc.get_datasets():
        if ds.get("table_name") == table_name:
            return ds["id"]
    return None

if __name__ == "__main__":
    sc = client()
    print("workspace:", workspace_url())
    print("databases:", [(d["id"], d["database_name"]) for d in sc.get_databases()])
