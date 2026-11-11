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
2. **Scenario: stale approval** — one button runs the centrepiece on the real
   code path: plan proposed for the rooftop family → the reserved crew flips
   unavailable (labelled TEST CONDITION) → approving the stale plan returns
   **409 REJECTED_STALE** → a revised plan is proposed, approved, dispatched by
   tool call → the responder acknowledges → refresh the page: everything
   persists from SQLite.
3. **INJECT: SOS-001** — a medical SOS arrives at the relief point and takes
   queue rank 1 under the published queue rule (medical first), with the re-sort
   written to the audit.
4. **Reset drill** — rebuilds the drill state in one click.

## Architecture — the separation is in the code, not the prompts

```
agent_a  → {store, rubric, llm}        Intake & Verification: facts, dedupe,
                                        missing-info flags. NO path to dispatch.
agent_b  → {store, gate}               Resource Coordination: proposes plans and
                                        reserves crews. Cannot approve, cannot
                                        dispatch, severity is read-only.
gate     → plan_hash + resource_version, checked at Approve AND in dispatch
tools    → {gate, store}               The ONLY module containing dispatch();
                                        owns sent → acknowledged → completed.
store    → SQLite (WAL), append-only audit (DB triggers block UPDATE/DELETE)
rubric   → the published urgency rubric as data + a pure scoring function
```

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
rules-mode fallback, structural agent separation).

```
pip install -r requirements.txt
python -m pytest tests/ -q     # 10 passed
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

## Tech stack

Python 3.12 · FastAPI · SQLite (WAL) · vanilla HTML/CSS/JS (one page, no build
step) · pytest · Docker
