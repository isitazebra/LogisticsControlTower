#!/usr/bin/env python3
"""Build the Phase 1 gap-closing alerts (Preset Pro). Created PAUSED by default
so seed-persistent conditions don't spam email; flip active=True (or run with
--activate) when you want delivery. Each alert runs a count SQL against Neon and
fires when the result > 0. Idempotent by name."""
import sys, json
from preset_client import client, get_database_id

RECIPIENT = "narayan.hd@gmail.com"   # account owner; edit in Preset to change

ALERTS = [
    dict(name="Hung pipeline", crontab="*/5 * * * *", chart="Hung pipelines",
         sql="SELECT count(*) FROM ops_pipeline_health "
             "WHERE state='running' AND (queue_depth>0 OR mq_depth>0) AND consume_rate=0",
         desc="A pipeline is running but consuming nothing (the retailer-204 signature)."),
    dict(name="Missing feed", crontab="*/15 * * * *", chart="Missing expected feeds",
         sql="SELECT count(*) FROM ops_expected_feeds "
             "WHERE now() > expected_next_at + make_interval(mins=>grace_minutes) "
             "AND (last_seen_at IS NULL OR last_seen_at < expected_next_at)",
         desc="An expected partner feed is overdue past its grace window."),
    dict(name="Channel down", crontab="*/5 * * * *", chart="Dead / degraded connections",
         sql="SELECT count(*) FROM ops_endpoint_health WHERE status <> 'up'",
         desc="An endpoint is down or degraded."),
    dict(name="Rejected message", crontab="*/15 * * * *", chart="Rejected (period)",
         sql="SELECT coalesce(sum(rejected_count),0) FROM txn_rollup_hourly "
             "WHERE bucket >= date_trunc('hour', now()) - interval '1 hour'",
         desc="Rejected messages in the last hour — closes today's no-alert-on-reject gap."),
    dict(name="Cert expiring", crontab="0 8 * * *", chart="Cert / key expiry",
         sql="SELECT count(*) FROM ops_endpoint_health WHERE cert_expires_at < now() + interval '7 days'",
         desc="An endpoint cert/key expires within 7 days."),
    # Phase 5 (Q10): proactive — fires while the response clock is running, before breach.
    dict(name="At-risk response", crontab="*/5 * * * *", chart="Responses due soon (at-risk)",
         sql="SELECT count(*) FROM txn_events t "
             "WHERE t.doc_type='204' AND t.direction='in' "
             "AND now()-t.event_time BETWEEN make_interval(mins=>24) AND make_interval(mins=>30) "
             "AND NOT EXISTS (SELECT 1 FROM txn_events x WHERE x.business_ref=t.business_ref "
             "AND x.doc_type='990' AND x.direction='out')",
         desc="A 204 tender is approaching its 990 response SLA — alert before the breach."),
]

def main():
    activate = "--activate" in sys.argv
    sc = client(); db = get_database_id(sc)
    existing = {r["name"]: r["id"] for r in sc.get_reports()}
    chart_ids = {c["slice_name"]: c["id"] for c in sc.get_charts()}
    for a in ALERTS:
        payload = dict(
            name=a["name"], type="Alert", description=a["desc"],
            crontab=a["crontab"], timezone="America/New_York",
            database=db, sql=a["sql"],
            chart=chart_ids[a["chart"]], report_format="PNG", force_screenshot=True,
            validator_type="operator",
            validator_config_json={"op": ">", "threshold": 0},
            recipients=[{"type": "Email",
                         "recipient_config_json": {"target": RECIPIENT}}],
            active=activate, grace_period=86400, working_timeout=3600,
            creation_method="alerts_reports",
        )
        if a["name"] in existing:
            rid = existing[a["name"]]
            sc.update_report(rid, **payload)
            print(f"  alert {a['name']:<18} updated (id={rid}) active={activate}")
        else:
            res = sc.create_report(**payload)
            rid = res["id"] if isinstance(res, dict) else res
            print(f"  alert {a['name']:<18} created (id={rid}) active={activate}")

if __name__ == "__main__":
    main()
