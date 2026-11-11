"""Fixture loading, labelled injects, reset, and the one-button scenario.

Every inject is labelled a TEST CONDITION in its audit payload and on
screen. Reset rebuilds the drill database file from scratch (the audit
table's append-only triggers make an in-place wipe impossible by design —
that is the point of them).
"""

import os

import agent_a
import agent_b
import gate as gate_mod
import tools
from fixtures import CREWS, LOCATIONS, REPORTS, SOS_REPORT
from store import Store


def seed(store):
    with store.lock:
        for loc in LOCATIONS:
            store.execute(
                "INSERT OR REPLACE INTO locations(code, name, landmark, "
                "flood_prone, confined) VALUES(?, ?, ?, ?, ?)",
                (loc["code"], loc["name"], loc["landmark"],
                 1 if loc["flood_prone"] else 0, 1 if loc["confined"] else 0))
        for crew in CREWS:
            import json
            store.execute(
                "INSERT OR REPLACE INTO crews(crew_id, name, capabilities, "
                "capacity, home, status) VALUES(?, ?, ?, ?, ?, ?)",
                (crew["crew_id"], crew["name"],
                 json.dumps(crew["capabilities"]), crew["capacity"],
                 crew["home"], crew["status"]))
        # Fixture-load state: resource_version starts at 1 (fixtures spec).
        store.set_meta("resource_version", "1")
        store.commit()


def fresh_store(path):
    if os.path.exists(path):
        os.remove(path)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    store = Store(path)
    seed(store)
    return store


def load_reports(store):
    return [agent_a.ingest(store, r) for r in REPORTS]


def inject_flip(store, crew_id="CREW-2"):
    """TEST CONDITION: a crew flips unavailable; version bumps in the
    same transaction (gate contract)."""
    with store.lock:
        crew = store.one("SELECT * FROM crews WHERE crew_id=?", (crew_id,))
        if crew is None:
            return {"ok": False, "error": "CREW_NOT_FOUND", "http": 404}
        old_version = store.resource_version()
        store.execute("UPDATE crews SET status='unavailable' WHERE crew_id=?",
                      (crew_id,))
        new_version = store.bump_resource_version()
        store.audit("RESOURCE_STATE_CHANGED", "test_inject",
                    {"crew_id": crew_id, "from": crew["status"],
                     "to": "unavailable", "test_condition": True,
                     "from_version": old_version,
                     "resource_version": new_version})
        store.commit()
        return {"ok": True, "crew_id": crew_id,
                "resource_version": new_version}


def inject_sos(store):
    return agent_a.ingest(store, SOS_REPORT)


def run_scenario(store):
    """The centrepiece, end to end, on the judges' own code path:
    load -> plan -> INJECT flip -> stale approve (409) -> revised plan ->
    approve -> dispatch -> ack. Returns a step transcript."""
    steps = []
    results = load_reports(store)
    inc1 = results[0]["incident_id"]
    steps.append({"step": "reports_loaded",
                  "incidents": [r["incident_id"] for r in results]})

    p1 = agent_b.propose_plan(store, inc1)
    plan1 = p1["plan"]
    steps.append({"step": "plan_proposed", "plan_id": plan1["plan_id"],
                  "crew_id": plan1["assignments"][0]["crew_id"],
                  "based_on_version": plan1["based_on_version"],
                  "plan_hash": plan1["plan_hash_short"]})

    flip = inject_flip(store, plan1["assignments"][0]["crew_id"])
    steps.append({"step": "inject_flip", **flip})

    stale = gate_mod.approve(store, plan1["plan_id"], plan1["based_on_version"])
    steps.append({"step": "approve_stale", **stale})

    p2 = agent_b.propose_plan(store, inc1)
    plan2 = p2["plan"]
    steps.append({"step": "revised_plan", "plan_id": plan2["plan_id"],
                  "crew_id": plan2["assignments"][0]["crew_id"],
                  "based_on_version": plan2["based_on_version"]})

    granted = gate_mod.approve(store, plan2["plan_id"], plan2["based_on_version"])
    steps.append({"step": "approve_revised", **granted})

    sent = tools.dispatch(store, plan2["plan_id"], granted["approval_id"])
    steps.append({"step": "dispatch", **sent})

    ack = tools.acknowledge(store, sent["dispatch_ids"][0])
    steps.append({"step": "acknowledge", **ack})
    return {"ok": True, "steps": steps}
