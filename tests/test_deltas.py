"""Delta suite — T11..T16, added 2026-11-11 by the GPT Think hostile-judge
review (BRAINSTORM_CHALLENGE_2026-11-11.md, corrections C6–C10).

These cases are reported SEPARATELY from the predeclared T01–T10: they test
the review deltas (committed rejection, server-side authorization,
idempotency, concurrency, alert correlation, visible tool traces), not the
Nov 7 blueprint. Run: python -m pytest tests/ -q
"""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_a
import agent_b
import alerts
import gate
import main as main_mod
import replay
import tools
from store import Store


def fresh(tmp_path, name="d.db"):
    path = str(tmp_path / name)
    return replay.fresh_store(path), path


def load(store):
    return replay.load_reports(store)


def events(store):
    return [r["event"] for r in store.all("SELECT event FROM audit ORDER BY seq")]


def approved_plan(store):
    """Load, plan INC-001, approve at the live version. Returns (plan, granted)."""
    res = load(store)
    inc1 = res[0]["incident_id"]
    plan = agent_b.propose_plan(store, inc1)["plan"]
    granted = gate.approve(store, plan["plan_id"], plan["based_on_version"])
    assert granted["ok"] is True
    return plan, granted


# ---------------------------------------------------------------- T11
def test_t11_server_side_authorization_boundary(tmp_path):
    s, _ = fresh(tmp_path)
    plan, granted = approved_plan(s)
    # An agent identity cannot dispatch — rejected by the tool layer itself.
    out = tools.dispatch(s, plan["plan_id"], granted["approval_id"],
                         actor="agent_b")
    assert out["ok"] is False and out["http"] == 403
    assert s.all("SELECT * FROM dispatches") == []
    # Nor approve, nor acknowledge.
    out = gate.approve(s, plan["plan_id"], plan["based_on_version"],
                       actor="agent_a")
    assert out["ok"] is False and out["http"] == 403
    sent = tools.dispatch(s, plan["plan_id"], granted["approval_id"])
    assert sent["ok"] is True
    did = sent["dispatch_ids"][0]
    out = tools.acknowledge(s, did, actor="operator")
    assert out["ok"] is False and out["http"] == 403
    out = tools.acknowledge(s, did, actor="agent_a")
    assert out["ok"] is False and out["http"] == 403
    ok = tools.acknowledge(s, did)  # responder default
    assert ok["ok"] is True
    assert events(s).count("AUTHORIZATION_REJECTED") == 4


# ---------------------------------------------------------------- T12
def test_t12_idempotent_dispatch(tmp_path):
    s, _ = fresh(tmp_path)
    plan, granted = approved_plan(s)
    first = tools.dispatch(s, plan["plan_id"], granted["approval_id"],
                           idempotency_key="req-001")
    assert first["ok"] is True
    replayed = tools.dispatch(s, plan["plan_id"], granted["approval_id"],
                              idempotency_key="req-001")
    assert replayed["ok"] is True and replayed["idempotent_replay"] is True
    assert replayed["dispatch_ids"] == first["dispatch_ids"]
    assert len(s.all("SELECT * FROM dispatches")) == 1
    # A different key does not bypass the single-use approval.
    other = tools.dispatch(s, plan["plan_id"], granted["approval_id"],
                           idempotency_key="req-002")
    assert other["ok"] is False and other["reason"] == "approval_consumed"


# ---------------------------------------------------------------- T13
def test_t13_concurrent_dispatch_and_flip_race(tmp_path):
    # (a) Two concurrent dispatches on one approval: exactly one wins.
    s, _ = fresh(tmp_path)
    plan, granted = approved_plan(s)
    results = []

    def attempt():
        results.append(tools.dispatch(s, plan["plan_id"],
                                      granted["approval_id"]))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r["ok"]]
    assert len(winners) == 1
    assert len(s.all("SELECT * FROM dispatches")) == 1

    # (b) A version-bumping flip racing the dispatch: the outcome is always
    # consistent — either the dispatch landed first (its DISPATCH_SENT
    # precedes the flip in the audit), or it was rejected version_mismatch.
    for i in range(8):
        s2, _ = fresh(tmp_path, f"race{i}.db")
        plan2, granted2 = approved_plan(s2)
        barrier = threading.Barrier(2)
        outcome = {}

        def do_flip():
            barrier.wait()
            replay.inject_flip(s2, "CREW-4")

        def do_dispatch():
            barrier.wait()
            outcome["r"] = tools.dispatch(s2, plan2["plan_id"],
                                          granted2["approval_id"])

        t1 = threading.Thread(target=do_flip)
        t2 = threading.Thread(target=do_dispatch)
        t1.start(); t2.start(); t1.join(); t2.join()
        r = outcome["r"]
        rows = s2.all("SELECT event FROM audit ORDER BY seq")
        seq = {e["event"]: i for i, e in enumerate(rows)
               if e["event"] in ("DISPATCH_SENT",)}
        flips = [i for i, e in enumerate(rows)
                 if e["event"] == "RESOURCE_STATE_CHANGED"]
        if r["ok"]:
            assert len(s2.all("SELECT * FROM dispatches")) == 1
            assert seq["DISPATCH_SENT"] < max(flips)
        else:
            assert r["http"] == 409 and r["reason"] == "version_mismatch"
            assert s2.all("SELECT * FROM dispatches") == []


# ---------------------------------------------------------------- T14
def test_t14_alert_correlation_and_situation(tmp_path):
    s, _ = fresh(tmp_path)
    res = load(s)
    inc1 = res[0]["incident_id"]  # LOC-01
    plan = agent_b.propose_plan(s, inc1)["plan"]
    out = alerts.ingest_alert(s, {
        "type": "road_closure", "location_hint": "LOC-01",
        "text": "Synthetic alert: road closure at LOC-01."})
    assert out["ok"] is True and out["synthetic"] is True
    assert inc1 in out["correlated_incidents"]
    assert plan["plan_id"] in out["flagged_plans"]
    prow = s.one("SELECT * FROM plans WHERE plan_id=?", (plan["plan_id"],))
    assert prow["risk_note"] and "road_closure" in prow["risk_note"]
    ev = events(s)
    assert "ALERT_RECEIVED" in ev and "ALERT_CORRELATED" in ev
    assert "PLAN_RISK_FLAGGED" in ev
    view = main_mod.state_view(s)
    assert "Ghatauli" in view["situation"]["en"]
    assert "घाटौली" in view["situation"]["hi"]
    assert view["situation"]["synthetic"] is True
    assert len(view["situation"]["alerts"]) == 1


# ---------------------------------------------------------------- T15
def test_t15_visible_tool_traces(tmp_path):
    s, _ = fresh(tmp_path)
    res = load(s)
    agent_b.propose_plan(s, res[0]["incident_id"])
    rows = s.all("SELECT actor, payload FROM audit WHERE event='TOOL_CALL'")
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["actor"], set()).add(
            json.loads(r["payload"])["tool"])
    assert {"intake", "extract", "validate", "calculate_urgency",
            "publish"} <= by_agent["agent_a"]
    assert {"get_available_crews", "evaluate", "build_plan", "reserve",
            "request_approval"} <= by_agent["agent_b"]
    view = main_mod.state_view(s)
    assert view["tool_traces"]["agent_a"]
    assert view["tool_traces"]["agent_b"]


# ---------------------------------------------------------------- T16
def test_t16_dispatch_rejection_is_committed(tmp_path):
    s, path = fresh(tmp_path)
    plan, granted = approved_plan(s)
    replay.inject_flip(s, "CREW-4")  # world moves after approval
    out = tools.dispatch(s, plan["plan_id"], granted["approval_id"])
    assert out["ok"] is False and out["http"] == 409
    assert out["reason"] == "version_mismatch"
    s.close()
    # Reopen from disk alone: the rejection row must have survived, which
    # proves it was committed, not rolled back with the failed dispatch.
    s2 = Store(path)
    row = s2.one("SELECT payload FROM audit "
                 "WHERE event='DISPATCH_REJECTED_STALE'")
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["expected_version"] == 1
    assert payload["actual_version"] == 2
    assert s2.all("SELECT * FROM dispatches") == []
    s2.close()


# ------------------------------------------------- R1 regression tests
# Added for Review Round 1's close condition (F1 + F2): a wrong or duplicate
# approve must never destroy a live plan, and dispatch must never report
# success while dispatching nobody.

# ---------------------------------------------------------------- T17
def test_t17_wrong_or_duplicate_approve_never_destroys_plan(tmp_path):
    s, _ = fresh(tmp_path)
    res = load(s)
    plan = agent_b.propose_plan(s, res[0]["incident_id"])["plan"]
    # Wrong seen_version on a fresh, current plan: refused, plan survives.
    bad = gate.approve(s, plan["plan_id"], 999)
    assert bad["ok"] is False and bad["http"] == 409
    row = s.one("SELECT state FROM plans WHERE plan_id=?", (plan["plan_id"],))
    assert row["state"] == "reserved"
    res_held = s.all("SELECT * FROM reservations WHERE plan_id=? AND state='held'",
                     (plan["plan_id"],))
    assert len(res_held) == 1
    # Correct approve then works.
    granted = gate.approve(s, plan["plan_id"], plan["based_on_version"])
    assert granted["ok"] is True
    # Duplicate approve (double click / retry): refused, plan stays approved,
    # reservation stays held, the live approval stays unconsumed.
    dup = gate.approve(s, plan["plan_id"], plan["based_on_version"])
    assert dup["ok"] is False and dup["reason"] == "plan_not_approvable"
    row = s.one("SELECT state FROM plans WHERE plan_id=?", (plan["plan_id"],))
    assert row["state"] == "approved"
    assert len(s.all("SELECT * FROM reservations WHERE plan_id=? AND state='held'",
                     (plan["plan_id"],))) == 1
    # And the dispatch that follows really dispatches somebody.
    sent = tools.dispatch(s, plan["plan_id"], granted["approval_id"])
    assert sent["ok"] is True and len(sent["dispatch_ids"]) == 1
    assert len(s.all("SELECT * FROM dispatches")) == 1


# ---------------------------------------------------------------- T18
def test_t18_dispatch_never_succeeds_with_zero_conversions(tmp_path):
    s, _ = fresh(tmp_path)
    plan, granted = approved_plan(s)
    # Release the reservation behind the gate's back (the F1-era failure
    # shape): dispatch must fail loudly and leave the approval unconsumed.
    s.execute("UPDATE reservations SET state='released' WHERE plan_id=?",
              (plan["plan_id"],))
    s.commit()
    out = tools.dispatch(s, plan["plan_id"], granted["approval_id"])
    assert out["ok"] is False and out["http"] == 409
    assert out["reason"] == "no_held_reservation"
    assert s.all("SELECT * FROM dispatches") == []
    assert "DISPATCH_SENT" not in events(s)
    appr = s.one("SELECT consumed FROM approvals WHERE approval_id=?",
                 (granted["approval_id"],))
    assert appr["consumed"] == 0
