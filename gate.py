"""The approval gate — the trust mechanism at the centre of DispatchProof.

An approval binds (plan_hash, based_on_version): a fingerprint of the plan's
contents and the version of the resource state it was built on. Approval is
checked TWICE (PREP_TOOL_CONTRACTS §4): once at the operator's Approve click
against the version the operator saw, and again inside dispatch() before
anything moves. resource_version is one ward-level monotonic integer, bumped
in the same transaction as ANY crew state change — so if it moved, the world
the plan was approved against no longer exists, and the approval is void by
construction.
"""

import hashlib
import json


def plan_hash(plan_id, incident_id, assignments):
    canonical = json.dumps(
        {"plan_id": plan_id, "incident_id": incident_id,
         "assignments": assignments},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approve(store, plan_id, seen_version):
    """§4a — operator action. seen_version is the version on the plan card."""
    with store.lock:
        plan = store.one("SELECT * FROM plans WHERE plan_id=?", (plan_id,))
        if plan is None:
            return {"ok": False, "error": "PLAN_NOT_FOUND", "http": 404}
        current = store.resource_version()
        recomputed = plan_hash(plan["plan_id"], plan["incident_id"],
                               json.loads(plan["assignments"]))
        stale_reason = None
        if plan["state"] not in ("proposed", "reserved"):
            stale_reason = "plan_not_approvable"
        elif seen_version != current or plan["based_on_version"] != current:
            stale_reason = "version_mismatch"
        elif recomputed != plan["plan_hash"]:
            stale_reason = "hash_mismatch"

        if stale_reason:
            store.execute("UPDATE plans SET state='stale-rejected' WHERE plan_id=?",
                          (plan_id,))
            store.execute("UPDATE reservations SET state='released' "
                          "WHERE plan_id=? AND state='held'", (plan_id,))
            store.audit("APPROVAL_REJECTED_STALE", "operator",
                        {"plan_id": plan_id, "approved_version": seen_version,
                         "current_version": current, "reason": stale_reason})
            store.commit()
            return {"ok": False, "error": "STALE", "http": 409,
                    "reason": stale_reason}

        n = int(store.get_meta("approval_counter", "0")) + 1
        store.set_meta("approval_counter", str(n))
        approval_id = f"APR-{n:03d}"
        from store import now_iso
        store.execute(
            "INSERT INTO approvals(approval_id, plan_id, plan_hash, "
            "based_on_version, consumed, created_at) VALUES(?, ?, ?, ?, 0, ?)",
            (approval_id, plan_id, plan["plan_hash"], plan["based_on_version"],
             now_iso()))
        store.execute("UPDATE plans SET state='approved' WHERE plan_id=?",
                      (plan_id,))
        store.audit("APPROVAL_GRANTED", "operator",
                    {"plan_id": plan_id, "approval_id": approval_id,
                     "plan_hash": plan["plan_hash"][:12],
                     "based_on_version": plan["based_on_version"]})
        store.commit()
        return {"ok": True, "approval_id": approval_id, "plan_id": plan_id,
                "based_on_version": plan["based_on_version"]}
