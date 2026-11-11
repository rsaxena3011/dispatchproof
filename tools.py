"""tools.py — the ONLY place dispatch() exists.

Dispatch is a tool call, not a language act: it re-verifies the approval
through the gate (§4b) inside the store lock before converting reservations
into dispatch rows. The dispatch state machine is sent -> acknowledged ->
completed; states are separate, never merged, never skipped, and only this
module may move a dispatch between them.
"""

import json

import gate as gate_mod
import templates_hi
from fixtures import INCIDENT_SUMMARIES
from store import now_iso


def _reject_dispatch(store, plan_id, approval_id, reason, expected_version):
    """A rejection is a COMMITTED operation (challenge correction C6):
    the DISPATCH_REJECTED_STALE row is written and committed BEFORE the
    409 returns. No exception path exists here that could roll it back."""
    store.audit("DISPATCH_REJECTED_STALE", "system",
                {"plan_id": plan_id, "approval_id": approval_id,
                 "reason": reason,
                 "expected_version": expected_version,
                 "actual_version": store.resource_version(),
                 "via": "dispatch_verify"})
    store.commit()
    return {"ok": False, "error": "STALE", "http": 409, "reason": reason}


def _reject_forbidden(store, endpoint, actor, ref):
    store.audit("AUTHORIZATION_REJECTED", "system",
                {"endpoint": endpoint, "actor": actor, "ref": ref})
    store.commit()
    return {"ok": False, "error": "FORBIDDEN", "http": 403,
            "reason": "agent_identity_cannot_" + endpoint}


def dispatch(store, plan_id, approval_id, actor="operator", idempotency_key=None):
    """§4b — gate.verify() + conversion, atomically under the store lock.

    Authorization (C8): an agent identity is rejected server-side, before
    any verification. Idempotency (C7): a replayed key returns the original
    result and creates nothing."""
    with store.lock:
        if actor in ("agent_a", "agent_b"):
            return _reject_forbidden(store, "dispatch", actor, plan_id)
        if idempotency_key:
            seen = store.one("SELECT * FROM idempotency_keys WHERE key=?",
                             (idempotency_key,))
            if seen:
                result = json.loads(seen["result"])
                result["idempotent_replay"] = True
                return result
        approval = store.one("SELECT * FROM approvals WHERE approval_id=?",
                             (approval_id,))
        plan = store.one("SELECT * FROM plans WHERE plan_id=?", (plan_id,))

        reason = None
        if approval is None or plan is None or approval["plan_id"] != plan_id:
            reason = "approval_missing"
        elif approval["consumed"]:
            reason = "approval_consumed"
        else:
            recomputed = gate_mod.plan_hash(
                plan["plan_id"], plan["incident_id"],
                json.loads(plan["assignments"]))
            if recomputed != plan["plan_hash"] or approval["plan_hash"] != plan["plan_hash"]:
                reason = "hash_mismatch"
            elif approval["based_on_version"] != store.resource_version():
                reason = "version_mismatch"

        if reason:
            return _reject_dispatch(
                store, plan_id, approval_id, reason,
                approval["based_on_version"] if approval else None)

        # F2 fix (Review Round 1): a dispatch that would convert NOTHING
        # must fail loudly, never report success. The approval is NOT
        # consumed by this failure.
        reservations = store.all(
            "SELECT * FROM reservations WHERE plan_id=? AND state='held'",
            (plan_id,))
        if plan["state"] != "approved" or not reservations:
            return _reject_dispatch(
                store, plan_id, approval_id,
                "plan_not_dispatchable" if plan["state"] != "approved"
                else "no_held_reservation",
                approval["based_on_version"])

        store.execute("UPDATE approvals SET consumed=1 WHERE approval_id=?",
                      (approval_id,))
        incident = store.one("SELECT * FROM incidents WHERE incident_id=?",
                             (plan["incident_id"],))
        facts = json.loads(incident["facts"])
        location = store.one("SELECT * FROM locations WHERE code=?",
                             (incident["location_code"],))
        dispatch_ids = []
        reservations = store.all(
            "SELECT * FROM reservations WHERE plan_id=? AND state='held'",
            (plan_id,))
        for res in reservations:
            n = int(store.get_meta("dispatch_counter", "0")) + 1
            store.set_meta("dispatch_counter", str(n))
            dispatch_id = f"DSP-{n:03d}"
            crew = store.one("SELECT * FROM crews WHERE crew_id=?",
                             (res["crew_id"],))
            assignment = json.loads(plan["assignments"])[0]
            message = templates_hi.task_message(
                assignment["capability_required"],
                {"code": location["code"], "name": location["name"]},
                INCIDENT_SUMMARIES.get(
                    location["code"], incident["reason"] or "सूचना सत्यापित है।"),
                facts.get("people_count"), incident["reason"])
            store.execute(
                "INSERT INTO dispatches(dispatch_id, plan_id, approval_id, "
                "incident_id, crew_id, state, task_message, sent_at) "
                "VALUES(?, ?, ?, ?, ?, 'sent', ?, ?)",
                (dispatch_id, plan_id, approval_id, plan["incident_id"],
                 res["crew_id"], message, now_iso()))
            store.execute("UPDATE reservations SET state='converted' WHERE id=?",
                          (res["id"],))
            store.execute("UPDATE crews SET status='on_assignment' WHERE crew_id=?",
                          (res["crew_id"],))
            new_version = store.bump_resource_version()
            store.audit("RESOURCE_STATE_CHANGED", "system",
                        {"crew_id": res["crew_id"], "from": crew["status"],
                         "to": "on_assignment", "via": "dispatch",
                         "resource_version": new_version})
            store.audit("DISPATCH_SENT", "system",
                        {"dispatch_id": dispatch_id, "plan_id": plan_id,
                         "crew_id": res["crew_id"],
                         "incident_id": plan["incident_id"],
                         "via": "tool_call: tools.dispatch"})
            dispatch_ids.append(dispatch_id)
        store.execute("UPDATE plans SET state='dispatched' WHERE plan_id=?",
                      (plan_id,))
        result = {"ok": True, "dispatch_ids": dispatch_ids}
        if idempotency_key:
            store.execute(
                "INSERT INTO idempotency_keys (key, result, created_at) "
                "VALUES (?, ?, ?)",
                (idempotency_key, json.dumps(result), now_iso()))
        store.commit()
        return result


def acknowledge(store, dispatch_id, actor="responder"):
    """Responder-side only: sent -> acknowledged. Agent and operator
    identities are rejected server-side (C8)."""
    with store.lock:
        if actor != "responder":
            return _reject_forbidden(store, "acknowledge", actor, dispatch_id)
        d = store.one("SELECT * FROM dispatches WHERE dispatch_id=?",
                      (dispatch_id,))
        if d is None:
            return {"ok": False, "error": "DISPATCH_NOT_FOUND", "http": 404}
        if d["state"] != "sent":
            return {"ok": False, "error": "BAD_STATE", "http": 409,
                    "state": d["state"]}
        store.execute("UPDATE dispatches SET state='acknowledged', ack_at=? "
                      "WHERE dispatch_id=?", (now_iso(), dispatch_id))
        store.audit("DISPATCH_ACKNOWLEDGED", "responder",
                    {"dispatch_id": dispatch_id, "crew_id": d["crew_id"]})
        store.commit()
        return {"ok": True, "dispatch_id": dispatch_id, "state": "acknowledged"}


def complete(store, dispatch_id, actor="responder"):
    """Responder-side only: acknowledged -> completed. No shortcut exists.
    Agent and operator identities are rejected server-side (C8)."""
    with store.lock:
        if actor != "responder":
            return _reject_forbidden(store, "complete", actor, dispatch_id)
        d = store.one("SELECT * FROM dispatches WHERE dispatch_id=?",
                      (dispatch_id,))
        if d is None:
            return {"ok": False, "error": "DISPATCH_NOT_FOUND", "http": 404}
        if d["state"] != "acknowledged":
            return {"ok": False, "error": "BAD_STATE", "http": 409,
                    "state": d["state"]}
        store.execute("UPDATE dispatches SET state='completed', completed_at=? "
                      "WHERE dispatch_id=?", (now_iso(), dispatch_id))
        store.audit("DISPATCH_COMPLETED", "responder",
                    {"dispatch_id": dispatch_id, "crew_id": d["crew_id"]})
        store.commit()
        return {"ok": True, "dispatch_id": dispatch_id, "state": "completed"}
