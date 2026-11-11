"""Agent B — Resource Coordination.

Matches crews to a scored incident (capability -> best-fit capacity ->
proximity, deterministic), places an atomic reservation, and proposes a
plan. Agent B can propose and reserve; it cannot approve and cannot
dispatch. Severity is read-only to B: this module never writes an
incident's score (checked by test T10).
"""

import json

import gate as gate_mod
from rubric import CAPABILITY_FOR


def _loc_num(code):
    try:
        return int(code.split("-")[1])
    except Exception:
        return 0


def _reserved_crew_ids(store):
    rows = store.all(
        "SELECT r.crew_id FROM reservations r JOIN plans p ON p.plan_id = r.plan_id "
        "WHERE r.state='held' AND p.state IN ('proposed', 'reserved', 'approved')")
    return {r["crew_id"] for r in rows}


def propose_plan(store, incident_id):
    """Propose (or return the live) plan for one incident. §3 contract."""
    with store.lock:
        incident = store.one("SELECT * FROM incidents WHERE incident_id=?",
                             (incident_id,))
        if incident is None:
            return {"ok": False, "error": "INCIDENT_NOT_FOUND", "http": 404}
        live = store.one(
            "SELECT * FROM plans WHERE incident_id=? AND state IN "
            "('proposed', 'reserved', 'approved') ORDER BY plan_id DESC",
            (incident_id,))
        if live:
            return {"ok": True, "plan": _plan_view(live), "existing": True}

        facts = json.loads(incident["facts"])
        capability = CAPABILITY_FOR[facts["life_risk_condition"]]
        people = facts.get("people_count") or 0
        reserved = _reserved_crew_ids(store)
        crews = store.all("SELECT * FROM crews WHERE status='available'")
        capable = [c for c in crews
                   if capability in json.loads(c["capabilities"])
                   and c["crew_id"] not in reserved]
        checks = {"capability": "pass" if capable else "fail",
                  "capacity": "fail", "availability": "pass" if capable else "fail"}
        if not capable:
            store.commit()
            return {"ok": False, "error": "NO_CAPABLE_CREW", "http": 422,
                    "checks": checks}

        fitting = [c for c in capable if c["capacity"] >= people]
        if fitting:
            checks["capacity"] = "pass"
            pool = fitting
            pool.sort(key=lambda c: (c["capacity"],
                                     abs(_loc_num(c["home"]) - _loc_num(incident["location_code"]))))
        else:
            pool = sorted(capable, key=lambda c: -c["capacity"])
        crew = pool[0]

        n = int(store.get_meta("plan_counter", "0")) + 1
        store.set_meta("plan_counter", str(n))
        plan_id = f"PLAN-{n}"
        based_on = store.resource_version()
        assignments = [{"crew_id": crew["crew_id"],
                        "capability_required": capability,
                        "people_assigned": people}]
        plan_hash = gate_mod.plan_hash(plan_id, incident_id, assignments)
        store.execute(
            "INSERT INTO plans(plan_id, incident_id, assignments, checks, "
            "matching_trace, based_on_version, plan_hash, state) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, 'reserved')",
            (plan_id, incident_id, json.dumps(assignments), json.dumps(checks),
             "capability → best-fit capacity → proximity (deterministic)",
             based_on, plan_hash))
        store.execute(
            "INSERT INTO reservations(plan_id, crew_id, people_assigned, state) "
            "VALUES(?, ?, ?, 'held')", (plan_id, crew["crew_id"], people))
        store.audit("PLAN_PROPOSED", "agent_b",
                    {"plan_id": plan_id, "incident_id": incident_id,
                     "crew_id": crew["crew_id"], "based_on_version": based_on,
                     "plan_hash": plan_hash[:12]})
        store.audit("RESOURCE_RESERVED", "agent_b",
                    {"plan_id": plan_id, "crew_id": crew["crew_id"],
                     "people_assigned": people})
        store.commit()
        plan = store.one("SELECT * FROM plans WHERE plan_id=?", (plan_id,))
        return {"ok": True, "plan": _plan_view(plan)}


def _plan_view(plan):
    return {
        "plan_id": plan["plan_id"],
        "incident_id": plan["incident_id"],
        "assignments": json.loads(plan["assignments"]),
        "checks": json.loads(plan["checks"]),
        "matching_trace": plan["matching_trace"],
        "based_on_version": plan["based_on_version"],
        "plan_hash": plan["plan_hash"],
        "plan_hash_short": plan["plan_hash"][:12],
        "state": plan["state"],
    }
