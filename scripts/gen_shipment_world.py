#!/usr/bin/env python3
# gen_shipment_world.py
# ===========================================================================
# Regenerate public.txn_events as a REAL shipment / order world.
#
# Per user contract (2026-06-24): "All the data needs to be on shipments,
# which is essentially an order. Each order will have a 990 order confirmation,
# order update 214 (multiple) and finally 210 (invoice). NO need for any other
# message types. The total, splits, etc. all need to add up."
#
# Model:  shipment_id = ORD-NNNNNN  (one order == one interchange_id)
#   * exactly one  990  (order confirmation)            -> direction 'out'
#   * 1..5         214  (order updates, multiple)        -> direction 'in'
#   * exactly one  210  (invoice)  [omitted if open]     -> direction 'in'
#
# All dimensions (partner, lob, channel, protocol, environment) are CONSTANT
# within an order -- that is the core fix vs. the old random interchange.
# value_usd lives on the 210 invoice only, so order value == sum(value_usd).
#
# Reconciliation guarantees by construction:
#   total messages = #990 + #214 + #210
#   #990 == #210(closed) == closed-order count ;  #orders == #990
#
# Idempotent: TRUNCATEs txn_events (autocommit, reclaims partition storage)
# then bulk-inserts, then rebuilds txn_rollup_hourly via the sql/02 contract.
# ===========================================================================
import random
import datetime as dt

import db
from psycopg2.extras import execute_values

random.seed(42)

PARTNERS = ["Hapag", "Kroger", "Werner", "DHL", "Flextronics", "Maersk", "Target"]
LOBS = ["wh", "customs", "air", "ocean", "home", "ground", "po"]
EDI_CHANNELS = ["as2", "sftp", "van", "mq"]
REASONS = [
    "validation_error", "business_rule_violation", "connectivity", "mapping_defect",
    "duplicate_interchange", "bad_input_file", "partner_config", "ack_timeout",
    "envelope_error", "invalid_doc_type", "system_error",
]

START = dt.datetime(2026, 4, 25, tzinfo=dt.timezone.utc)
END = dt.datetime(2026, 6, 23, 6, tzinfo=dt.timezone.utc)
SPAN = (END - START).total_seconds()

N_ORDERS = 60000
P_EXCEPTION = 0.09   # order carries one failed/rejected message
P_OPEN = 0.15        # order still in progress: 990 + 214s, no invoice yet
P_DUP_214 = 0.01     # a 214 update lands as a duplicate

COLS = [
    "event_time", "interchange_id", "business_ref", "environment", "lob", "partner",
    "channel", "protocol", "direction", "doc_type", "stage", "status",
    "reason_category", "terminal", "sla_due_at", "value_usd", "kchar",
    "error_code", "replayed", "replay_count", "control_number", "payload",
]


def build_rows():
    rows = []
    cn = 1
    for i in range(N_ORDERS):
        sid = f"ORD-{i:06d}"
        partner = random.choice(PARTNERS)
        lob = random.choice(LOBS)
        env = "prod" if random.random() < 0.88 else "uat"
        if random.random() < 0.26:
            protocol, channel = "api", "api"
        else:
            protocol, channel = "edi", random.choice(EDI_CHANNELS)
        order_value = round(random.lognormvariate(7.1, 1.0), 2)  # ~$1.2k median

        roll = random.random()
        has_exc = roll < P_EXCEPTION
        is_open = (not has_exc) and roll < P_EXCEPTION + P_OPEN

        # lifecycle: 990 -> n_updates*214 -> (210 unless open)
        n_upd = random.randint(1, 5)
        t0 = START + dt.timedelta(seconds=random.random() * SPAN * 0.80)
        msgs = [("990", 0.0, "out")]
        off = 0.0
        for _ in range(n_upd):
            off += random.random() * 36 * 3600          # up to 1.5d between updates
            msgs.append(("214", off, "in"))
        if not is_open:
            off += random.random() * 24 * 3600
            msgs.append(("210", off, "in"))

        exc_idx = random.randrange(len(msgs)) if has_exc else -1

        for j, (doc, secs, direction) in enumerate(msgs):
            et = t0 + dt.timedelta(seconds=secs)
            if et > END:
                et = END
            if j == exc_idx:
                status = random.choice(["failed", "rejected"])
                reason = random.choice(REASONS)
            elif doc == "214" and random.random() < P_DUP_214:
                status, reason = "duplicate", "duplicate_interchange"
            else:
                status, reason = "ok", None
            is_invoice = doc == "210"
            terminal = status in ("ok", "duplicate")           # open/failed stay non-terminal
            sla_due = et + dt.timedelta(hours=random.choice([4, 8, 24]))
            rows.append((
                et, sid, f"{sid}-{doc}-{j}", env, lob, partner, channel, protocol,
                direction, doc, "acked", status, reason, terminal, sla_due,
                order_value if is_invoice else 0, round(random.uniform(5, 40), 1),
                None, False, 0, f"CN{cn:09d}", None,
            ))
            cn += 1
    return rows


def main():
    rows = build_rows()
    print(f"generated {len(rows):,} messages across {N_ORDERS:,} orders")

    conn = db.connect()
    conn.autocommit = True
    cur = conn.cursor()

    # 1) reclaim partition storage first (Neon 512MB ceiling)
    cur.execute("TRUNCATE public.txn_events")
    print("txn_events truncated")

    # 2) bulk insert
    sql = f"INSERT INTO public.txn_events ({', '.join(COLS)}) VALUES %s"
    execute_values(cur, sql, rows, page_size=5000)
    cur.execute("SELECT count(*) FROM public.txn_events")
    print(f"inserted; txn_events now {cur.fetchone()[0]:,} rows")

    # 3) rebuild rollup via the canonical sql/02 aggregation contract
    cur.execute("TRUNCATE txn_rollup_hourly")
    cur.execute("""
        INSERT INTO txn_rollup_hourly
        SELECT date_trunc('hour', event_time), environment, lob, partner, channel,
               protocol, direction, doc_type, status,
               count(*), sum(value_usd), sum(kchar),
               count(*) FILTER (WHERE status='failed'),
               count(*) FILTER (WHERE status='rejected'),
               count(*) FILTER (WHERE status='duplicate'),
               count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
        FROM txn_events
        GROUP BY 1,2,3,4,5,6,7,8,9
    """)
    cur.execute("SELECT sum(txn_count) FROM txn_rollup_hourly")
    print(f"rollup rebuilt; txn_count total = {cur.fetchone()[0]:,}")

    # quick splits
    for col in ("doc_type", "status"):
        cur.execute(f"SELECT {col}, count(*) FROM txn_events GROUP BY 1 ORDER BY 2 DESC")
        print(f"  {col}: {cur.fetchall()}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
