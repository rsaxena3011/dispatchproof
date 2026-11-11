"""DispatchProof predeclared suite — T01..T10 from PREP_TEST_CASES.md.

The cases were declared before the build; expected values come from
PREP_RUBRIC_TABLE.md and PREP_FIXTURES_SPEC.md. Run:
    python -m pytest tests/ -q
"""

import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_a
import agent_b
import gate
import replay
import rubric
import tools
from fixtures import REPORTS, SOS_REPORT
from store import Store


def fresh(tmp_path, name="t.db"):
    path = str(tmp_path / name)
    return replay.fresh_store(path), path


def load(store):
    return replay.load_reports(store)


def events(store):
    return [r["event"] for r in store.all("SELECT event FROM audit ORDER BY seq")]


def incident(store, iid):
    return store.one("SELECT * FROM incidents WHERE incident_id=?", (iid,))


# ---------------------------------------------------------------- T01
def test_t01_stale_approval_rejected(tmp_path):
    s, _ = fresh(tmp_path)
    res = load(s)
    inc1 = res[0]["incident_id"]
    plan = agent_b.propose_plan(s, inc1)["plan"]
    assert plan["assignments"][0]["crew_id"] == "CREW-2"
    assert plan["based_on_version"] == 1
    replay.inject_flip(s, "CREW-2")
    out = gate.approve(s, plan["plan_id"], plan["based_on_version"])
    assert out["ok"] is False and out["http"] == 409 and out["error"] == "STALE"
    ev = events(s)
    i_flip = max(i for i, e in enumerate(ev) if e == "RESOURCE_STATE_CHANGED")
    i_rej = ev.index("APPROVAL_REJECTED_STALE")
    assert i_flip < i_rej
    rej = s.one("SELECT payload FROM audit WHERE event='APPROVAL_REJECTED_STALE'")
    payload = json.loads(rej["payload"])
    assert payload["approved_version"] == 1 and payload["current_version"] == 2
    assert s.one("SELECT state FROM plans WHERE plan_id=?",
                 (plan["plan_id"],))["state"] == "stale-rejected"
    assert s.all("SELECT * FROM dispatches") == []


# ---------------------------------------------------------------- T02
def test_t02_revised_plan_and_single_use_approval(tmp_path):
    s, _ = fresh(tmp_path)
    res = load(s)
    inc1 = res[0]["incident_id"]
    p1 = agent_b.propose_plan(s, inc1)["plan"]
    replay.inject_flip(s, "CREW-2")
    gate.approve(s, p1["plan_id"], p1["based_on_version"])
    p2 = agent_b.propose_plan(s, inc1)["plan"]
    assert p2["assignments"][0]["crew_id"] == "CREW-5"
    assert p2["based_on_version"] == 2
    granted = gate.approve(s, p2["plan_id"], p2["based_on_version"])
    assert granted["ok"] is True
    sent = tools.dispatch(s, p2["plan_id"], granted["approval_id"])
    assert sent["ok"] is True
    again = tools.dispatch(s, p2["plan_id"], granted["approval_id"])
    assert again["ok"] is False and again["http"] == 409
    assert again["reason"] == "approval_consumed"
    assert len(s.all("SELECT * FROM dispatches")) == 1


# ---------------------------------------------------------------- T03
def test_t03_ack_and_persistence_after_refresh(tmp_path):
    s, path = fresh(tmp_path)
    replay.run_scenario(s)
    d = s.one("SELECT * FROM dispatches")
    assert d["state"] == "acknowledged"
    assert d["completed_at"] is None
    ev_before = events(s)
    s.close()
    s2 = Store(path)  # reopen: state must come from SQLite alone
    d2 = s2.one("SELECT * FROM dispatches")
    assert d2["state"] == "acknowledged"
    assert events(s2) == ev_before
    assert s2.resource_version() == 3
    s2.close()


# ---------------------------------------------------------------- T04
def test_t04_duplicates_link_never_discard(tmp_path):
    s, _ = fresh(tmp_path)
    r1 = agent_a.ingest(s, REPORTS[0])
    r2 = agent_a.ingest(s, REPORTS[1])
    assert r2["is_duplicate"] is True and r2["incident_id"] == r1["incident_id"]
    assert len(s.all("SELECT * FROM incidents")) == 1
    links = s.all("SELECT * FROM incident_links")
    assert len(links) == 1 and links[0]["relation"] == "duplicate_of"
    texts = s.all("SELECT text FROM reports WHERE incident_id=?",
                  (r1["incident_id"],))
    assert len(texts) == 2
    inc = incident(s, r1["incident_id"])
    assert json.loads(inc["facts"])["people_count"] == 5
    ev = events(s)
    assert ev.index("REPORT_RECEIVED") < ev.index("INCIDENT_CREATED")
    assert "REPORT_LINKED_DUPLICATE" in ev
    # order independence
    s2, _ = fresh(tmp_path, "t2.db")
    a = agent_a.ingest(s2, REPORTS[1])
    b = agent_a.ingest(s2, REPORTS[0])
    assert b["is_duplicate"] is True and b["incident_id"] == a["incident_id"]
    assert len(s2.all("SELECT * FROM incidents")) == 1


# ---------------------------------------------------------------- T05
def test_t05_rubric_exact_and_reproducible(tmp_path):
    s, _ = fresh(tmp_path)
    load(s)
    inc1 = incident(s, "INC-001")
    assert inc1["score"] == 82 and inc1["band"] == "CRITICAL"
    assert inc1["reason"] == ("People trapped (rooftop); 5 people affected; "
                              "elderly person present")
    inc2 = incident(s, "INC-002")
    assert inc2["score"] == 14 and inc2["band"] == "LOW"
    assert inc2["provisional"] == 1
    assert set(json.loads(inc2["missing_info"])) == {"people_count", "hazard_trend"}
    inc3 = incident(s, "INC-003")
    assert inc3["score"] == 70 and inc3["band"] == "HIGH"
    assert inc3["medical_override"] == 1
    for inc in (inc1, inc2, inc3):
        facts = json.loads(inc["facts"])
        loc = s.one("SELECT * FROM locations WHERE code=?",
                    (inc["location_code"],))
        loc = {"flood_prone": bool(loc["flood_prone"]),
               "confined": bool(loc["confined"])}
        again = rubric.score(facts, loc)
        assert again["score"] == inc["score"]
        assert sum(again["factor_breakdown"].values()) == inc["score"]


# ---------------------------------------------------------------- T06
def test_t06_audit_completeness_and_append_only(tmp_path):
    s, _ = fresh(tmp_path)
    replay.run_scenario(s)
    ev = [e for e in events(s) if e != "PARSE_FALLBACK_RULES"]
    expected = [
        "REPORT_RECEIVED", "INCIDENT_CREATED", "INCIDENT_SCORED",
        "REPORT_RECEIVED", "REPORT_LINKED_DUPLICATE",
        "REPORT_RECEIVED", "INCIDENT_CREATED", "INCIDENT_SCORED",
        "MISSING_INFO_FLAGGED",
        "REPORT_RECEIVED", "INCIDENT_CREATED", "INCIDENT_SCORED",
        "QUEUE_RESORTED",
        "PLAN_PROPOSED", "RESOURCE_RESERVED",
        "RESOURCE_STATE_CHANGED", "APPROVAL_REJECTED_STALE",
        "PLAN_PROPOSED", "RESOURCE_RESERVED",
        "APPROVAL_GRANTED", "RESOURCE_STATE_CHANGED", "DISPATCH_SENT",
        "DISPATCH_ACKNOWLEDGED",
    ]
    assert ev == expected
    n_before = s.one("SELECT COUNT(*) AS n FROM audit")["n"]
    for bad in ("UPDATE audit SET event='X'", "DELETE FROM audit"):
        try:
            s.execute(bad)
            s.commit()
            raise AssertionError("audit mutation was allowed")
        except sqlite3.IntegrityError:
            s.conn.rollback()
    assert s.one("SELECT COUNT(*) AS n FROM audit")["n"] == n_before


# ---------------------------------------------------------------- T07
def test_t07_medical_sos_jumps_queue(tmp_path):
    s, _ = fresh(tmp_path)
    load(s)
    assert incident(s, "INC-003")["queue_rank"] == 1
    assert incident(s, "INC-001")["queue_rank"] == 2
    assert incident(s, "INC-002")["queue_rank"] == 3
    out = replay.inject_sos(s)
    sos = incident(s, out["incident_id"])
    assert sos["score"] == 64 and sos["band"] == "HIGH"
    assert sos["medical_override"] == 1
    assert sos["queue_rank"] == 1
    assert incident(s, "INC-003")["queue_rank"] == 2
    assert incident(s, "INC-001")["queue_rank"] == 3
    assert incident(s, "INC-002")["queue_rank"] == 4
    assert "QUEUE_RESORTED" in events(s)


# ---------------------------------------------------------------- T08
def test_t08_restart_recovery(tmp_path):
    s, path = fresh(tmp_path)
    replay.run_scenario(s)
    snapshot = {
        "version": s.resource_version(),
        "audit": events(s),
        "dispatch_state": s.one("SELECT state FROM dispatches")["state"],
        "ranks": {r["incident_id"]: r["queue_rank"]
                  for r in s.all("SELECT * FROM incidents")},
    }
    s.close()
    s2 = Store(path)
    assert s2.resource_version() == snapshot["version"] == 3
    assert events(s2) == snapshot["audit"]
    assert s2.one("SELECT state FROM dispatches")["state"] == "acknowledged"
    assert {r["incident_id"]: r["queue_rank"]
            for r in s2.all("SELECT * FROM incidents")} == snapshot["ranks"]
    s2.close()


# ---------------------------------------------------------------- T09
def test_t09_rules_mode_fallback(tmp_path):
    assert not os.environ.get("DP_LLM_API_KEY")
    s, _ = fresh(tmp_path)
    res = load(s)
    assert all(r["parse_mode"] == "rules" for r in res)
    fallbacks = s.all("SELECT * FROM audit WHERE event='PARSE_FALLBACK_RULES'")
    assert len(fallbacks) == 4
    assert incident(s, "INC-001")["score"] == 82
    assert incident(s, "INC-003")["score"] == 70


# ---------------------------------------------------------------- T10
def test_t10_agent_separation_is_structural(tmp_path):
    base = os.path.join(os.path.dirname(__file__), "..")
    src_a = open(os.path.join(base, "agent_a.py")).read()
    imported = set()
    for line in src_a.splitlines():
        m = re.match(r"\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", line)
        if m:
            imported.add(m.group(1))
    assert "tools" not in imported
    assert imported <= {"json", "llm", "rubric", "store"}
    assert not hasattr(agent_a, "dispatch")
    src_b = open(os.path.join(base, "agent_b.py")).read()
    assert "UPDATE incidents" not in src_b
    s, _ = fresh(tmp_path)
    res = load(s)
    before = incident(s, "INC-001")["score"]
    agent_b.propose_plan(s, "INC-001")
    assert incident(s, "INC-001")["score"] == before
