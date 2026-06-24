#!/usr/bin/env python3
"""Merged spec: the **Integration Control Tower** — one dashboard that folds the
transaction-centric Integration Cockpit (public.txn_*) and the shipment-centric
EDI Anomaly Control Tower (edi_anomaly_dashboard_dataset.vw_*) into a single
base, with the anomaly Control Tower as the spine.

Design decisions (see merge plan):
  * Logical semantic layer — both schemas stay; this module just unions their
    declarative specs. The two are disjoint subject areas (no shared partner /
    shipment keys), so there is no row-level join: dedup happens at the
    presentation layer (one owner per KPI, one landing page).
  * Restyle to the new format — cockpit charts migrated here are rewritten to
    the anomaly visual idiom: pie -> donut, timebar -> ts, uniform KPI tiles,
    shared colour scheme + number formats.
  * Names kept stable — anomaly slices keep their "EDI · " prefix as a
    subject-area provenance marker (shipment world); cockpit slices stay
    unprefixed (transaction world). This avoids collisions (both define e.g.
    "Status distribution") and reuses the existing chart objects in place.
  * Overview retired — cockpit's standalone Overview tab is dropped; five
    cross-cutting integration-health tiles are promoted into Control Tower,
    the rest were duplicates of canonical charts and are not re-emitted.

The engine (build_cockpit.build) and layout (build_dashboard.build_position)
consume this exactly like the other specs; build_merged.py points S at it.
"""
import copy
import cockpit_spec as C
import anomaly_spec as A

SCHEMA = A.SCHEMA  # the anomaly views' schema (cockpit reads public.*)

# ---------------------------------------------------------------------------
# DATASETS — union of both specs (names already disjoint: q*/vw_rollup vs edi_*)
# ---------------------------------------------------------------------------
DATASETS = {**C.DATASETS, **A.DATASETS}

# ---------------------------------------------------------------------------
# MERGED TAB LABELS  (single namespace; migrated charts get reassigned here)
# ---------------------------------------------------------------------------
# -- base spine (anomaly world) --
T_CT       = "Control Tower"
T_ANOM     = "Anomalies"
T_PART     = "Partner Health & Risk"
T_FLOW_EDI = "EDI Flow & SLA"
T_SHIP     = "Shipments"
# -- folded-in transaction world --
T_PART_SLA = "Message SLA & Penalties"   # cockpit partner SLA, under Partners
T_ACKS     = "Acknowledgments"           # under EDI Flow & SLA
T_ALLTXN   = "All Transactions"
T_LOOKUP   = "Lookup & Replay"
T_FILES    = "Files"
T_FLOW_VOL = "Volume & Throughput"       # cockpit Q2 EDI/API volume summary
T_TYPES    = "Transaction Types"
T_ARRIVAL  = "Arrival & Channel Health"
T_EXC      = "Exceptions"
T_DIAG     = "Diagnostics"
T_RESP     = "Response-SLA"
T_USAGE    = "Usage"
# -- per-LOB template (kept verbatim from cockpit) --
T_LOB_OV   = C.T_LOB_OV
T_LOB_DET  = C.T_LOB_DET
T_LOB_TRAF = C.T_LOB_TRAF
T_LOB_EXC  = C.T_LOB_EXC

# Map each *source* cockpit tab -> merged tab. Overview handled separately.
COCKPIT_TAB_MAP = {
    C.T_FLOW:   T_FLOW_VOL,
    C.T_TYPES:  T_TYPES,
    C.T_FILES:  T_FILES,
    C.T_LOOKUP: T_LOOKUP,
    C.T_ACKS:   T_ACKS,
    C.T_SLA:    T_PART_SLA,
    C.T_USAGE:  T_USAGE,
    C.T_RESP:   T_RESP,
    C.T_DIAG:   T_DIAG,
    C.T_TYPES:  T_TYPES,
    C.T_ALLTXN: T_ALLTXN,
    C.T_ARRIVAL: T_ARRIVAL,
    C.T_EXC:    T_EXC,
    C.T_LOB_OV: T_LOB_OV,
    C.T_LOB_DET: T_LOB_DET,
    C.T_LOB_TRAF: T_LOB_TRAF,
    C.T_LOB_EXC: T_LOB_EXC,
}

# Five cross-cutting integration-health tiles salvaged from the retired
# Overview tab into Control Tower (slice name -> merged tab).
OVERVIEW_SALVAGE = {
    "Overview: Transactions":      T_CT,   # transaction-volume headline (demoted)
    "Overview: Hung pipelines":    T_CT,
    "Overview: Missing feeds":     T_CT,
    "Overview: Stale monitors":    T_CT,
    "Overview: At-risk responses": T_CT,
}


def _restyle(c):
    """Rewrite a migrated cockpit chart to the new (anomaly) visual format."""
    if c["kind"] == "pie":
        c["kind"] = "donut"
    elif c["kind"] == "timebar":
        c["kind"] = "ts"
        c.setdefault("x", "bucket")
        c.setdefault("chart", "bar")
    return c


# Anomaly tabs are mostly named identically; only Partners is relabelled in the
# merged layout (it becomes a sub-tab beside the cockpit Message-SLA scorecard).
ANOM_TAB_MAP = {
    A.T_CT:   T_CT,
    A.T_ANOM: T_ANOM,
    A.T_PART: T_PART,
    A.T_FLOW: T_FLOW_EDI,
    A.T_SHIP: T_SHIP,
}


def _build_charts():
    out = []
    # 1) anomaly charts — base spine, kept verbatim (already the new format)
    for c in A.CHARTS:
        cc = copy.deepcopy(c)
        cc["tab"] = ANOM_TAB_MAP[c["tab"]]
        out.append(cc)
    # 2) cockpit charts — migrate + restyle, excluding the retired Overview tab
    #    (salvage five tiles, drop the rest of Overview as canonical duplicates)
    for c in C.CHARTS:
        if c["tab"] == C.T_OVERVIEW:
            if c["slice"] in OVERVIEW_SALVAGE:
                cc = _restyle(copy.deepcopy(c))
                cc["tab"] = OVERVIEW_SALVAGE[c["slice"]]
                out.append(cc)
            continue
        cc = _restyle(copy.deepcopy(c))
        cc["tab"] = COCKPIT_TAB_MAP[c["tab"]]
        out.append(cc)
    return out


CHARTS = _build_charts()

# ---------------------------------------------------------------------------
# DASHBOARD assembly — sections in user-journey order (glance -> problem ->
# blame -> process -> object -> individual -> catalog -> infra -> segment ->
# reporting). Business-first: platform Operations sits deep, not up front.
# ---------------------------------------------------------------------------
SECTIONS = [
    ("Control Tower",        [T_CT]),
    ("Anomalies",            [T_ANOM]),
    ("Partners",             [T_PART, T_PART_SLA]),
    ("EDI Flow & SLA",       [T_FLOW_EDI, T_ACKS]),
    ("Shipments",            [T_SHIP]),
    ("Transactions",         [T_ALLTXN, T_LOOKUP, T_FILES]),
    ("Flow & Message Types", [T_FLOW_VOL, T_TYPES]),
    ("Operations",           [T_ARRIVAL, T_EXC, T_DIAG]),
    ("LOB Cockpit",          [T_LOB_OV, T_LOB_DET, T_LOB_TRAF, T_LOB_EXC]),
    ("Usage & Response-SLA", [T_RESP, T_USAGE]),
]

DASHBOARD_TITLE = "Integration Control Tower"
DASHBOARD_SLUG  = "integration-control-tower"

# ---------------------------------------------------------------------------
# NATIVE FILTERS — (label, column, seed-dataset). Two Partner filters because
# the two worlds key partners differently (partner_id vs partner text) and
# cannot cross-filter; each is scoped + labelled to its subject area. Shared
# dimensions seed from whichever world owns them.
# ---------------------------------------------------------------------------
NATIVE_FILTERS = [
    ("Partner (shipments)",    "partner_id",       "edi_exceptions"),
    ("Severity",               "severity",         "edi_exceptions"),
    ("Anomaly status",         "anomaly_status",   "edi_exceptions"),
    ("Transaction type",       "transaction_type", "edi_exceptions"),
    ("Partner (transactions)", "partner",          "vw_rollup"),
    ("Environment",            "environment",      "vw_rollup"),
    ("Protocol",               "protocol",         "vw_rollup"),
    ("Channel",                "channel",          "vw_rollup"),
    ("Doc type",               "doc_type",         "vw_rollup"),
    ("LOB",                    "lob",              "vw_rollup"),
]
