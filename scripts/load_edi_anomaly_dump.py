#!/usr/bin/env python3
"""Load the mp_demo reference dump (schema edi_anomaly_dashboard_dataset) into our
existing Neon `neondb`, alongside `public`. Standalone "parallel" reference dataset
for the new EDI-anomaly control-tower dashboard.

The dump is a pg_dump --inserts file for a *separate database* (mp_demo) owned by
roles that don't exist on Neon. We therefore strip the Neon-incompatible directives
(DROP/CREATE/ALTER DATABASE, \\connect, OWNER TO, GRANT/REVOKE) and stream the rest
in batches. Idempotent: drops + recreates the target schema first.

Usage:
    python scripts/load_edi_anomaly_dump.py /path/to/dump-mp_demo-*.sql
"""
import sys, os, re, time, psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "edi_anomaly_dashboard_dataset"
BATCH = 2000  # statements per round-trip

def load_env():
    env = {}
    with open(os.path.join(ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

SKIP_PREFIX = ("--", "\\")
SKIP_RE = re.compile(
    r"^\s*(DROP DATABASE|CREATE DATABASE|ALTER DATABASE|GRANT |REVOKE )", re.I)
OWNER_RE = re.compile(r"OWNER TO", re.I)

def statements(path):
    """Yield complete SQL statements, dropping Neon-incompatible lines."""
    buf = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            stripped = s.lstrip()
            # Only filter when NOT mid-statement (buf empty) to avoid corrupting
            # multi-line string values; dump's noise lines are all top-level.
            if not buf:
                if stripped == "" or stripped.startswith(SKIP_PREFIX):
                    continue
                if SKIP_RE.match(stripped) or OWNER_RE.search(stripped):
                    continue
            buf.append(s)
            if s.rstrip().endswith(";"):
                yield "\n".join(buf)
                buf = []
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            yield tail

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    dump = sys.argv[1]
    conn = psycopg2.connect(load_env()["NEON_DATABASE_URL"])
    conn.autocommit = True
    cur = conn.cursor()
    print(f"== reset schema {SCHEMA} ==", flush=True)
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")

    batch, n, t0 = [], 0, time.time()
    def flush():
        nonlocal batch, n
        if not batch:
            return
        try:
            cur.execute("\n".join(batch))
        except Exception as e:
            print(f"\n!! batch failed (stmts {n-len(batch)+1}..{n}): {e}", flush=True)
            for st in batch:  # isolate the offender
                try:
                    cur.execute(st)
                except Exception as e2:
                    print(f"   OFFENDER: {st[:200]}\n   -> {e2}", flush=True)
                    raise
        batch = []

    for st in statements(dump):
        batch.append(st); n += 1
        if len(batch) >= BATCH:
            flush()
            if n % 50000 == 0:
                print(f"  {n:>7} stmts  ({time.time()-t0:5.1f}s)", flush=True)
    flush()
    print(f"== done: {n} statements in {time.time()-t0:.1f}s ==", flush=True)
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
