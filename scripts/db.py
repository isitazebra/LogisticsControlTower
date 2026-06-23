#!/usr/bin/env python3
"""Tiny psql substitute for this project. Loads .env, runs a .sql file or an
inline query against Neon. Usage:
    python scripts/db.py run sql/00_schema.sql
    python scripts/db.py q "select count(*) from txn_events"
"""
import sys, os, psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def connect():
    return psycopg2.connect(load_env()["NEON_DATABASE_URL"])

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    mode, arg = sys.argv[1], sys.argv[2]
    conn = connect(); conn.autocommit = True
    cur = conn.cursor()
    if mode == "run":
        path = arg if os.path.isabs(arg) else os.path.join(ROOT, arg)
        sql = open(path).read()
        cur.execute(sql)
        print(f"OK ran {arg}")
    elif mode == "q":
        cur.execute(arg)
        if cur.description:
            cols = [d[0] for d in cur.description]
            print(" | ".join(cols))
            for row in cur.fetchall():
                print(" | ".join("" if v is None else str(v) for v in row))
        else:
            print("OK (no rows)")
    else:
        print("unknown mode", mode); sys.exit(1)
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
