#!/usr/bin/env python3
"""Infuse two coherent volume anomalies into the cockpit rollup (for Q15).

The seeded cockpit feed (`txn_rollup_hourly`) is deliberately flat -- every
partner-feed runs at a steady ~90 txns/day with no drift, so a predictive
"silent / abnormal partner" view has nothing to detect (worst real z-score is
~-0.85). To make Q15 demonstrate its capability we inject two realistic,
deterministic signals into the *rollup* (the documented Q15 input):

  * SILENT feed   -- Werner / 214 (Transportation status updates) goes dark for
    the last 6 complete days. Baseline-active -> zero == a partner-feed that
    stopped reporting (the classic silent-partner incident, caught early).
  * SEVERE DROP   -- Target / 850 (purchase orders) loses ~65% of volume over
    the last 7 complete days (keep only the first 8 hours of each day).

Both are scoped to environment='prod' and applied by DETERMINISTIC predicate,
so the script is idempotent (re-running lands the same state). It is also fully
REVERSIBLE: `txn_events` is never touched, so `--restore` rebuilds the entire
rollup from the intact event source (same statement the seed uses).

Usage:
  python gen_cockpit_anomalies.py            # infuse the two anomalies
  python gen_cockpit_anomalies.py --restore  # rebuild rollup from txn_events
"""
import sys
import psycopg2
from preset_client import neon_uri

# target feeds (environment='prod')
SILENT = ("Werner", "214")          # goes fully silent for the trailing window
SILENT_DAYS = 7                     # covers the full 7-day current window -> cur_mean=0
DROP = ("Target", "850")            # ~65% volume loss over the trailing window
DROP_DAYS = 7
DROP_KEEP_HOURS = 8                 # keep hours 0..7 only -> ~1/3 of volume kept

# asof = last COMPLETE day = the latest day strictly before the newest (partial) day
ASOF_SQL = """
  (SELECT max(date_trunc('day',bucket))::date
     FROM txn_rollup_hourly
    WHERE bucket < date_trunc('day', (SELECT max(bucket) FROM txn_rollup_hourly)))
"""

REBUILD_ROLLUP = """
TRUNCATE txn_rollup_hourly;
INSERT INTO txn_rollup_hourly
SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
  count(*), sum(value_usd), sum(kchar),
  count(*) FILTER (WHERE status='failed'), count(*) FILTER (WHERE status='rejected'),
  count(*) FILTER (WHERE status='duplicate'),
  count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
FROM txn_events
GROUP BY 1,2,3,4,5,6,7,8,9;
"""


def restore(cur):
    print("restoring rollup from txn_events (full rebuild) ...")
    cur.execute(REBUILD_ROLLUP)
    cur.execute("SELECT count(*) FROM txn_rollup_hourly")
    print(f"  rollup rebuilt: {cur.fetchone()[0]:,} rows")


def infuse(cur):
    cur.execute(f"SELECT {ASOF_SQL}")
    asof = cur.fetchone()[0]
    print(f"asof (last complete day) = {asof}")

    # SILENT: delete the feed's rows from the window start through the newest
    # (partial) day -- open-ended so the silence is unbroken to "now".
    cur.execute(
        """DELETE FROM txn_rollup_hourly
            WHERE environment='prod' AND partner=%s AND doc_type=%s
              AND bucket::date >= %s - (%s - 1)""",
        (SILENT[0], SILENT[1], asof, SILENT_DAYS),
    )
    print(f"  SILENT  {SILENT[0]}/{SILENT[1]}: removed {cur.rowcount} rollup rows "
          f"(last {SILENT_DAYS} days -> 0)")

    # SEVERE DROP: keep only the first DROP_KEEP_HOURS hours/day from window start on
    cur.execute(
        """DELETE FROM txn_rollup_hourly
            WHERE environment='prod' AND partner=%s AND doc_type=%s
              AND bucket::date >= %s - (%s - 1)
              AND EXTRACT(hour FROM bucket) >= %s""",
        (DROP[0], DROP[1], asof, DROP_DAYS, DROP_KEEP_HOURS),
    )
    print(f"  DROP    {DROP[0]}/{DROP[1]}: removed {cur.rowcount} rollup rows "
          f"(kept hours 0..{DROP_KEEP_HOURS-1} -> ~{round(100*DROP_KEEP_HOURS/24)}% of volume)")


def main():
    cx = psycopg2.connect(neon_uri().replace("postgresql+psycopg2", "postgresql"))
    cx.autocommit = False
    cur = cx.cursor()
    if "--restore" in sys.argv:
        restore(cur)
    else:
        # idempotent: rebuild target window from events first? no -- deterministic
        # deletes are themselves idempotent. Just apply.
        infuse(cur)
    cx.commit()
    cur.close(); cx.close()


if __name__ == "__main__":
    main()
