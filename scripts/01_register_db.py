#!/usr/bin/env python3
"""Register the Neon Postgres database in the Preset workspace (idempotent)."""
import json
from preset_client import client, get_database_id, neon_uri, DB_NAME

def main():
    sc = client()
    existing = get_database_id(sc)
    if existing:
        print(f"Database '{DB_NAME}' already registered (id={existing}); updating URI.")
        sc.update_database(existing, sqlalchemy_uri=neon_uri())
        db_id = existing
    else:
        payload = dict(
            database_name=DB_NAME,
            sqlalchemy_uri=neon_uri(),
            expose_in_sqllab=True,
            allow_ctas=False,
            allow_cvas=False,
            allow_dml=False,
            cache_timeout=120,                     # match rollup cadence
            extra=json.dumps({
                "allows_virtual_table_explore": True,
                "metadata_cache_timeout": {},
                "schemas_allowed_for_file_upload": [],
            }),
        )
        res = sc.create_database(**payload)
        db_id = res["id"] if isinstance(res, dict) else res
        print(f"Created database '{DB_NAME}' (id={db_id}).")

    # smoke test through Superset itself
    df = sc.run_query(database_id=db_id, sql="select count(*) as n from txn_events")
    print("Superset -> Neon query OK. txn_events count:", int(df.iloc[0, 0]))
    print("DB_ID", db_id)

if __name__ == "__main__":
    main()
