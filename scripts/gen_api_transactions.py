#!/usr/bin/env python3
"""Infuse a synthetic **API integration channel** alongside the EDI feed.

The mp_demo dump is 100% EDI X12 -- there are no API transactions to point an
"API visibility" tab at. This generator creates a coherent `api_transactions`
table: SYNTHETIC but anchored to the REAL shipments/partners already in
`edi_anomaly_dashboard_dataset`, with API-native integration semantics
(endpoints, HTTP status, latency vs partner target, retries, rate-limits,
webhook delivery). It is NOT a system of record -- it describes the integration
CALLS we exchanged, exactly the same transient-integration lens as the EDI side.

Scope: a partial, realistic API footprint -- only API-adopting partners flow via
API, with per-partner adoption that ramps across the shipment window. The rest
stay EDI-only. Idempotent: drops + recreates the table and reinserts on each run
(deterministic seed), so it can be re-run safely.

Run:  python gen_api_transactions.py
"""
import sys, random, hashlib
import psycopg2
from psycopg2.extras import execute_values
from preset_client import neon_uri

SCHEMA = "edi_anomaly_dashboard_dataset"
SEED = 360
random.seed(SEED)

# API-adopting partners and their integration profile (target latency ms, base
# success rate, adoption share that ramps to this by end of window).
API_PARTNERS = {
    "P005": dict(name="TechZone Electronics", target_ms=400, success=0.995, adopt=0.75),  # API-native
    "P006": dict(name="Amazon",               target_ms=300, success=0.992, adopt=0.45),  # e-comm, modern
    "P010": dict(name="Lowes",                target_ms=600, success=0.980, adopt=0.25),  # migrating
}

# operation -> (endpoint, method, is_webhook). The choreography mirrors EDI:
# Book ~ 204 tender, Confirm ~ 990, StatusUpdate ~ 214, plus API-only RateQuote.
OPS = {
    "Book":        ("/v1/shipments",               "POST", False),
    "Confirm":     ("/webhooks/tender-response",   "POST", True),
    "StatusUpdate":("/webhooks/status",            "POST", True),
    "Tracking":    ("/v1/shipments/{id}/tracking", "GET",  False),
    "RateQuote":   ("/v1/rates",                   "POST", False),
}

CLIENT_ERRS = {400: "VALIDATION_FAILED", 401: "AUTH_EXPIRED", 404: "NOT_FOUND",
               409: "IDEMPOTENCY_CONFLICT", 422: "SCHEMA_INVALID", 429: "RATE_LIMITED"}
SERVER_ERRS = {500: "INTERNAL_ERROR", 502: "BAD_GATEWAY", 503: "UNAVAILABLE", 504: "GATEWAY_TIMEOUT"}

DDL = f"""
DROP TABLE IF EXISTS {SCHEMA}.api_transactions;
CREATE TABLE {SCHEMA}.api_transactions (
  api_call_id      varchar PRIMARY KEY,
  shipment_id      varchar,
  partner_id       varchar,
  carrier_id       varchar,
  api_operation    varchar,
  endpoint         varchar,
  http_method      varchar,
  is_webhook       boolean,
  request_ts       timestamp,
  response_ts      timestamp,
  latency_ms       integer,
  http_status      integer,
  status_class     varchar,
  success          boolean,
  retry_count      integer,
  rate_limited     boolean,
  webhook_delivered boolean,
  error_code       varchar,
  error_message    text,
  correlation_id   varchar,
  target_latency_ms integer,
  sla_met          boolean,
  created_at       timestamp DEFAULT now()
);
"""


def uid(*parts):
    return "API-" + hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12]


def lat(target):
    """Lognormal-ish latency centred a bit under target with a breach tail."""
    base = random.lognormvariate(0, 0.5) * target * 0.7
    if random.random() < 0.08:           # heavy tail: slow calls
        base *= random.uniform(2.5, 6.0)
    return max(15, int(base))


def pick_status(success_rate):
    """Return (http_status, status_class, success). Errors split client/server."""
    if random.random() < success_rate:
        return random.choice([200, 200, 200, 201, 202]), "2xx", True
    if random.random() < 0.6:            # client error
        return random.choice(list(CLIENT_ERRS)), "4xx", False
    return random.choice(list(SERVER_ERRS)), "5xx", False


def adoption(prof, ts, win0, win1):
    """Per-shipment API adoption that ramps linearly to prof['adopt']."""
    frac = (ts - win0).total_seconds() / max(1, (win1 - win0).total_seconds())
    return random.random() < prof["adopt"] * (0.4 + 0.6 * frac)


def gen_calls(ship, prof, win0, win1):
    sid, pid, cid, sdate = ship
    if not adoption(prof, sdate, win0, win1):
        return []
    rows, corr = [], uid("corr", sid)
    seq = ["Book", "Confirm"] + ["StatusUpdate"] * random.randint(1, 4)
    if random.random() < 0.5:
        seq.append("Tracking")
    t = sdate
    for i, op in enumerate(seq):
        endpoint, method, is_wh = OPS[op]
        t = t.__class__.fromtimestamp(t.timestamp() + random.uniform(30, 4 * 3600))
        status, sclass, ok = pick_status(prof["success"])
        retries = 0
        rate_limited = (status == 429)
        if not ok and random.random() < 0.7:           # transient -> retried
            retries = random.randint(1, 3)
            if random.random() < 0.6:                   # retry eventually succeeds
                status, sclass, ok = 200, "2xx", True
        l = lat(prof["target_ms"]) * (1 + 0.4 * retries)
        l = int(l)
        err = None if ok else (CLIENT_ERRS.get(status) or SERVER_ERRS.get(status))
        wh_delivered = None
        if is_wh:                                       # webhook delivery success
            wh_delivered = ok and random.random() < 0.985
        rows.append((
            uid(sid, op, i), sid, pid, cid, op, endpoint.replace("{id}", sid),
            method, is_wh, t,
            t.__class__.fromtimestamp(t.timestamp() + l / 1000.0), l,
            status, sclass, ok, retries, rate_limited, wh_delivered,
            err, (f"{op} {err}" if err else None), corr,
            prof["target_ms"], (ok and l <= prof["target_ms"]),
        ))
    return rows


def gen_rate_quotes(pid, prof, shipments, win0, win1):
    """Some standalone (non-shipment) rate-shopping calls per API partner."""
    rows = []
    n = int(len(shipments) * prof["adopt"] * 0.3)
    for k in range(n):
        sdate = random.choice(shipments)[3]
        status, sclass, ok = pick_status(prof["success"])
        l = lat(prof["target_ms"])
        err = None if ok else (CLIENT_ERRS.get(status) or SERVER_ERRS.get(status))
        rows.append((
            uid("rq", pid, k), None, pid, None, "RateQuote", "/v1/rates", "POST",
            False, sdate, sdate.__class__.fromtimestamp(sdate.timestamp() + l / 1000.0),
            l, status, sclass, ok, 0, status == 429, None, err,
            (f"RateQuote {err}" if err else None), uid("rqcorr", pid, k),
            prof["target_ms"], (ok and l <= prof["target_ms"]),
        ))
    return rows


def main():
    cx = psycopg2.connect(neon_uri().replace("postgresql+psycopg2", "postgresql"))
    cx.autocommit = False
    cur = cx.cursor()
    cur.execute(f"select min(shipment_date), max(shipment_date) from {SCHEMA}.shipment_header")
    win0, win1 = cur.fetchone()

    print("creating api_transactions table ...")
    cur.execute(DDL)

    cols = ("api_call_id,shipment_id,partner_id,carrier_id,api_operation,endpoint,"
            "http_method,is_webhook,request_ts,response_ts,latency_ms,http_status,"
            "status_class,success,retry_count,rate_limited,webhook_delivered,"
            "error_code,error_message,correlation_id,target_latency_ms,sla_met")
    insert_sql = f"insert into {SCHEMA}.api_transactions ({cols}) values %s"

    total = 0
    for pid, prof in API_PARTNERS.items():
        cur.execute(f"""select shipment_id, partner_id, carrier_id, shipment_date
                        from {SCHEMA}.shipment_header where partner_id=%s
                        order by shipment_date""", (pid,))
        ships = cur.fetchall()
        batch = []
        for ship in ships:
            batch.extend(gen_calls(ship, prof, win0, win1))
        batch.extend(gen_rate_quotes(pid, prof, ships, win0, win1))
        execute_values(cur, insert_sql, batch, page_size=2000)
        total += len(batch)
        print(f"  {pid} {prof['name']:<22} shipments={len(ships):>5}  api_calls={len(batch):>6,}")

    cx.commit()
    cur.execute(f"select count(*), round(100.0*avg(success::int),1), round(avg(latency_ms)) from {SCHEMA}.api_transactions")
    n, succ, avlat = cur.fetchone()
    print(f"\ninserted {n:,} api calls  | success {succ}%  | avg latency {avlat}ms")
    cur.close(); cx.close()


if __name__ == "__main__":
    main()
