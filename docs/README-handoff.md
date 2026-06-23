# Integration Cockpit — Hand-off Package (README)

A B2B integration visibility cockpit for an enterprise moving millions of EDI + API transactions across multiple lines of business. Built to run **on Preset/Superset over Postgres (Neon)**, with **NiFi wired in later** — the initial demo runs entirely on seed data.

**The core design principle:** Postgres is the contract. The dashboard reads tables; the seed script fills them now; NiFi fills them later. Switching from seed to live changes nothing in the dashboard.

---

## What's in this package, and what to do with each

| # | File | What it is | What to do with it |
|---|---|---|---|
| 1 | **cockpit-product-brief-for-claude-code.md** | The product brief / PRD — the story, users, all 11 questions with priorities and **acceptance criteria**, the schema contract, performance NFRs, and the NiFi-sourcing map. | **Read first.** This is the "why and what." Hand it to Claude Code as the spec. It tells Code what to build and how to know it's done. |
| 2 | **superset-build-pack-full-sequence.md** | The complete build pack — all six phases (Q1–Q11) in order, each with dataset SQL, chart specs, alerts, and "done-when" checks. | **The "how, in order."** Give to Claude Code with the brief. Code works it top-to-bottom. Start with Phase 0–2 (the P0 commit). |
| 3 | **demo-seed.sql** | The seed script — bulk volume plus every edge case the acceptance criteria test (missing feed, hung pipeline, rejected, duplicate, replayed, file rejected-at-receipt, file missing transactions, at-risk 204→990), prod + UAT. | **Run this in Preset's SQL Lab right after the Phase 0 DDL.** It stands in for NiFi so the whole dashboard renders with believable data on day one. |
| 4 | **nifi-integration-must-do.md** | The NiFi integration contract — what NiFi must produce later so the same tables fill from live flows. Required attributes, write-on-receipt rule, monitor jobs, the 3 gating facts, seed→live cutover. | **Use later, in the NiFi phase.** Hand to whoever wires NiFi. Nothing in the dashboard changes when it goes live. |
| 5 | **q1-arrival-stuck-monitor.html** | Clickable prototype of the Q1 "Arrival & Stuck" view (channel health, missing feeds, hung pipeline, sweep integrity). | **Stakeholder demo / design reference.** Open in a browser; click "Re-run sweep." Shows the differentiated capability before the Superset build exists. Not the build itself. |
| 6 | **integration-operating-cockpit.html** | Clickable prototype of the executive operating view (money, throughput, continuity, LOB matrix). | **Executive/CEO demo.** The "what an enterprise runs on" framing. Design reference, not the scoped build (we deliberately narrowed to a transaction cockpit). |
| 7 | **integration-control-tower.html** | Clickable prototype of the multi-LOB control tower (per-LOB health, exception drill, AI action plan). | **Multi-LOB design reference** for AIT/RXO conversations. Shows the LOB-switching concept. |

> The HTML files (5–7) are **design prototypes for demos and stakeholder buy-in** — they communicate intent. The **real build** is the Superset/Preset dashboard described in #1–3. Don't try to import the HTML into Superset.

---

## Recommended order

**To build the demo (your 2 days, no NiFi):**
1. Read the **brief** (#1).
2. In Preset: connect Neon, run the **Phase 0 DDL** from the **build pack** (#2), then run **demo-seed.sql** (#3).
3. Work the build pack **Phase 1 → Phase 2** (Q1, Q3, Q2) — that's the P0 dashboard. Phases 3–5 are upside on the same tables.
4. Use the **HTML prototypes** (#5–7) in parallel for stakeholder demos while the real dashboard is built.

**To go live later:**
5. Follow the **NiFi must-do guide** (#4): wire the inline writes + monitor jobs into the same tables, validate parity, stop the seed, cut over. No dashboard changes.

---

## The first instruction to give Claude Code

> "Use the attached product brief and full-sequence build pack. Connect to Neon, run the Phase 0 schema, then run demo-seed.sql. Build Phase 1 and Phase 2 against the seed. Stop at the Phase 1 acceptance criteria and show me the Q1 dashboard before continuing. We are NOT wiring NiFi yet — the seed stands in for it."

## Two decisions to make before you start
- **Preset tier:** Alerts need the Professional plan. Start the 14-day Pro trial for the demo, or use auto-refreshing table charts on free Starter.
- **Seed volume:** demo-seed.sql loads ~300k rows for speed; bump the `generate_series` to 2–5M to load-test the < 2s performance target.

## Note
The earlier **superset-build-pack-q1-q2.md** is **superseded** by the full-sequence pack (#2). Use the full-sequence one.
