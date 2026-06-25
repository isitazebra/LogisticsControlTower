#!/usr/bin/env python3
# gen_shipment_world.py
# ===========================================================================
# Regenerate public.txn_events as a REAL shipment / order world.
#
# Per user contract (2026-06-24): "essentially an order ... Order is 204, 990 is
# order confirmation, order update 214 (multiple), finally 210 (invoice). Add
# 204,990,214,210. The total, splits, etc. all need to add up."  + surface SLA.
#
# Model:  shipment_id = ORD-NNNNNN  (one order == one interchange_id)
#   * exactly one  204  (order / load tender)           -> direction 'out'
#   * exactly one  990  (order confirmation)            -> direction 'in'
#   * 1..5         214  (order updates, multiple)        -> direction 'in'
#   * exactly one  210  (invoice)  [omitted if open]     -> direction 'in'
#
# All dimensions (partner, lob, channel, protocol, environment) are CONSTANT
# within an order -- that is the core fix vs. the old random interchange.
# value_usd lives on the 204 order, so order value == sum(value_usd) across
# every order (open or closed).
#
# SLA: a breach == a message overdue and not terminal (sla_due_at < now() AND
# NOT terminal, the sql/02 rollup contract). Two INDEPENDENT breach sources so
# SLA is its own signal, not a clone of exceptions:
#   * exception orders            -> the failed/rejected message stays open
#   * breached-open orders (~7%)  -> healthy order, no invoice, last update overdue
# Healthy in-progress orders (~8%) keep their last update terminal/within-SLA so
# they do NOT breach.
#
# Reconciliation guarantees by construction:
#   total messages = #204 + #990 + #214 + #210
#   #204 == #990 == order count ;  #210 == closed-order count
#
# Idempotent: TRUNCATEs txn_events (autocommit, reclaims partition storage)
# then bulk-inserts, then rebuilds txn_rollup_hourly via the sql/02 contract.
# ===========================================================================
import random
import datetime as dt

import db
from psycopg2.extras import execute_values

random.seed(42)
# INDEPENDENT stream for the 204->990 confirmation latency only. Drawing it from
# its own generator (NOT the main `random`) keeps the main draw sequence -- and
# therefore every dimension, value_usd, status and SLA-breach decision -- byte
# for byte identical to the previous world. The ONLY thing that moves is each
# 990's event_time, which shifts a few minutes-to-hours after its 204 so the
# clone's 204->990 pair-SLA has a real latency distribution to track. 990 stays
# terminal/ok, so the now()-based breach test is unaffected and the Integration
# Command Center (dash 14) renders the same numbers.
rng_conf = random.Random(99)
CONF_MU, CONF_SIGMA = 3.4, 1.0   # lognormal minutes: ~30min median, tail to hrs

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
P_EXCEPTION = 0.09       # order carries one failed/rejected message (also breaches SLA)
P_BREACHED_OPEN = 0.07   # healthy order, no invoice, last update overdue -> SLA breach
P_HEALTHY_OPEN = 0.08    # in progress, no invoice, within SLA (does NOT breach)
P_DUP_214 = 0.01         # a 214 update lands as a duplicate
NOW = dt.datetime(2026, 6, 24, tzinfo=dt.timezone.utc)  # matches dashboard "today"

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
        breached_open = (not has_exc) and roll < P_EXCEPTION + P_BREACHED_OPEN
        healthy_open = (not has_exc and not breached_open) \
            and roll < P_EXCEPTION + P_BREACHED_OPEN + P_HEALTHY_OPEN
        is_open = breached_open or healthy_open   # no invoice issued yet

        # lifecycle: 204 order -> 990 confirmation -> n*214 updates -> (210 unless open)
        n_upd = random.randint(1, 5)
        t0 = START + dt.timedelta(seconds=random.random() * SPAN * 0.80)
        # confirmation lands conf_secs after the order (independent RNG, see top)
        conf_secs = rng_conf.lognormvariate(CONF_MU, CONF_SIGMA) * 60.0
        msgs = [("204", 0.0, "out"), ("990", conf_secs, "in")]
        off = 0.0
        for _ in range(n_upd):
            off += random.random() * 36 * 3600          # up to 1.5d between updates
            msgs.append(("214", off, "in"))
        if not is_open:
            off += random.random() * 24 * 3600
            msgs.append(("210", off, "in"))

        exc_idx = random.randrange(len(msgs)) if has_exc else -1
        last_idx = len(msgs) - 1

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

            terminal = status in ("ok", "duplicate")      # failed/rejected stay open -> breach
            sla_due = et + dt.timedelta(hours=random.choice([4, 8, 24]))
            # SLA breach engineering, independent of exceptions:
            if breached_open and j == last_idx:
                # healthy order whose last update blew its window and never closed
                terminal = False
                sla_due = et + dt.timedelta(hours=2)      # historical et -> sla_due < NOW
            elif healthy_open and j == last_idx:
                # in progress but on track: closed-for-now and within SLA
                terminal = True
                sla_due = NOW + dt.timedelta(days=2)

            is_order = doc == "204"
            rows.append((
                et, sid, f"{sid}-{doc}-{j}", env, lob, partner, channel, protocol,
                direction, doc, "acked", status, reason, terminal, sla_due,
                order_value if is_order else 0, round(random.uniform(5, 40), 1),
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
