---
title: DispatchProof
emoji: 🚑
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# DispatchProof — Multi-Agent Disaster Response Coordinator

**AgentX Hackathon 2026 (Hack4Hack) · Problem statement PS-03 · Team "Ratnesh's team" (CB9B1D)**

During floods, alerts, rescue requests and relief supplies are still coordinated
by hand. DispatchProof takes fragmented Hindi and English flood reports from one
ward and produces an **acknowledged, evidence-backed responder assignment** that
a duty officer can trust.

**One-line pitch:** Two cooperating agents triage and dispatch flood-response
crews — and a code-enforced approval gate makes it impossible to execute a plan
against a world that no longer exists.

## The idea in one paragraph

A plan is approved against a snapshot: a hash of the plan's contents plus the
version of the resource state it was built on. If any crew's status, capacity or
assignment changes after that, the version moves — and the approval is void by
construction. Approval is checked twice: at the operator's Approve click
(`409 APPROVAL_REJECTED_STALE`) and again inside the dispatch tool call before
anything moves. Dispatch is never a language act: it exists only as a tool call
behind the gate, and *sent*, *acknowledged* and *completed* are separate states
that are never merged or skipped.

## What you can do in the demo

1. **Load reports** — four scripted drill reports (Hindi + English) are ingested
   through the same path a live feed would use. Agent A extracts facts, links the
   duplicate pair (both texts stay visible, people are never double-counted) and
   flags the report that gives no people count instead of guessing.
2. **Scenario setup: stale approval** — one button runs the setup on the real
   code path: plan proposed for the rooftop family → the reserved crew flips
   unavailable (labelled TEST CONDITION) → approving the stale plan returns
   **409 REJECTED_STALE**, with the approved-vs-current versions and the plan
   hash on the card: *"Dispatch blocked. Rejection recorded. Fresh approval
   required."* From there **you** drive — propose the revised plan, approve it
   with a real click, dispatch it, watch the responder acknowledge. Nothing
   after the setup is scripted. Refresh the page: everything persists from
   SQLite.
3. **INJECT: SOS-001** — a medical SOS arrives at the relief point and takes
   queue rank 1 under the published queue rule (medical first), with the re-sort
   written to the audit.
4. **INJECT: road-closure alert** — a synthetic ward alert enters through a
   real API endpoint; Agent A correlates it with nearby incidents, Agent B
   flags affected live plans, and the ward situation update (Hindi + English)
   reflects it.
5. **Reset drill** — rebuilds the drill state in one click.

Both agent cards show their tool list, their live tool-execution history, and
the line **"Dispatch permission: NOT GRANTED"** — enforced server-side, not
just printed: the approve/dispatch/ack endpoints reject an agent identity
with 403 (test T11).

## Architecture — the separation is in the code, not the prompts

```
agent_a  → {store, rubric, llm}        Intake & Verification: facts, dedupe,
                                        missing-info flags, alert correlation.
                                        NO path to dispatch.
agent_b  → {gate, rubric}              Resource Coordination (store passed in):
                                        proposes plans, reserves crews, flags
                                        at-risk plans. Cannot approve, cannot
                                        dispatch, severity is read-only.
gate     → plan_hash + resource_version, checked at Approve AND in dispatch;
                                        operator identity required to approve
tools    → {gate, store, templates_hi, fixtures}
                                        The ONLY module containing dispatch();
                                        owns sent → acknowledged → completed;
                                        idempotency-key safe.
alerts   → {agent_a, agent_b, store}   Ward alerts: receive, correlate, flag.
store    → SQLite (WAL), tamper-evident audit — append-only, enforced by DB
                                        triggers that block UPDATE/DELETE
rubric   → the published urgency rubric as data + a pure scoring function
```

- **Rejection is a committed operation.** A stale dispatch writes a
  `DISPATCH_REJECTED_STALE` audit row (expected vs actual version) and commits
  it *before* returning 409 — a rejection can never be rolled back with the
  failed operation it refused (test T16 reopens the database from disk to
  prove it).
- **Atomicity note.** The tool contracts describe the dispatch verify as one
  `BEGIN IMMEDIATE` transaction; this single-process implementation provides
  the same guarantee with a process-wide lock over a single SQLite
  connection. Concurrency is tested, not asserted (T13: two concurrent
  dispatches on one approval → exactly one dispatch; a crew flip racing the
  dispatch check → always a consistent outcome).

- **Scoring is code, not a model.** The LLM layer (when a provider key is
  configured) only extracts facts; with no key the deterministic parser runs and
  every report's audit row says `parse_mode: rules`. Degraded is a displayed
  state, never a silent failure.
- **Shared memory** is the SQLite case store: reports, incidents, links, plans,
  reservations, approvals, dispatches, acknowledgements and the audit log.
  State survives refresh and process restart (test T08).

## Tests — declared before the build, run cold

Ten cases were predeclared before any code existed (duplicate linking, rubric
exactness, stale rejection, single-use approval, ack + persistence, audit
completeness and append-only enforcement, SOS queue jump, restart recovery,
rules-mode fallback, structural agent separation). Eight more were added by
the two independent review rounds and are reported separately in
`tests/test_deltas.py`: committed rejection, server-side authorization,
idempotency, concurrency, alert correlation, visible tool traces, and the
Round-1 regressions (a wrong or duplicate approve never destroys a live
plan; dispatch never reports success with zero conversions).

```
pip install -r requirements.txt
python -m pytest tests/ -q     # 18 passed
```

## Run it locally

```
pip install -r requirements.txt
python main.py                 # http://localhost:8000
```

## Honesty labels (read before judging the numbers)

- **All data is synthetic**: the fictional ward "Ghatauli", scripted reports,
  fictional crews — labelled on every screen.
- The responder inbox is a **labelled simulation**; no real SMS or WhatsApp.
- The crew flip is an **injected test condition**, disclosed here, on screen
  and in the audit payload.
- Impact figures are **stated targets, not measured results**. We did not mark
  any incident "rescued"; we established acknowledged assignments, with
  evidence of what happened.
- Hosted-demo note: this deployment's drill database resets on redeploy
  (use **Reset drill**, then **Load reports**). Run locally, the same code
  persists state across restarts — that property is test T08.
- **The bounded claim.** What is claimed: stale-plan dispatch is prevented
  under the tested assumptions — committed rejections, single-use approvals,
  an authorization boundary enforced server-side, a tamper-evident audit.
  What is not claimed: that this system makes emergency response safe. It
  guards one failure class — acting on a plan whose world no longer exists.
- One spec wrinkle, disclosed: the SOS incident's on-screen reason carries
  three phrases under the rubric's top-3 composition rule; the prep table's
  printed SOS example shows two. The code follows the rule; the
  T05-verified INC-001 line reproduces its worked example byte-for-byte.

## Tech stack

Python 3.12 · FastAPI · SQLite (WAL) · vanilla HTML/CSS/JS (one page, no build
step) · pytest · Docker
