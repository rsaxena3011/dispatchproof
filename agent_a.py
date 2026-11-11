"""Agent A — Intake & Verification.

Extracts facts, links duplicates without discarding, flags missing info.
Agent A has NO import path to dispatch: it imports store, rubric and llm
only (checked statically by test T10). It never scores by itself — scoring
is rubric.py's pure function — and it never plans.
"""

import json

import llm
import rubric
from store import now_iso


def _next_id(store, key, prefix):
    n = int(store.get_meta(key, "0")) + 1
    store.set_meta(key, str(n))
    return f"{prefix}-{n:03d}"


def queue_order(incidents):
    """Published queue rule: medical_override first (newest first among
    themselves), then score descending, then arrival ascending."""
    med = [i for i in incidents if i["medical_override"]]
    rest = [i for i in incidents if not i["medical_override"]]
    med.sort(key=lambda i: -i["created_seq"])
    rest.sort(key=lambda i: (-i["score"], i["created_seq"]))
    return med + rest


def recompute_ranks(store, actor="system"):
    incidents = store.all("SELECT * FROM incidents")
    for i in incidents:
        i["medical_override"] = bool(i["medical_override"])
    ordered = queue_order(incidents)
    moves = []
    for idx, inc in enumerate(ordered, start=1):
        if inc["queue_rank"] != idx:
            if inc["queue_rank"] is not None:
                moves.append({"incident_id": inc["incident_id"],
                              "from": inc["queue_rank"], "to": idx})
            store.execute("UPDATE incidents SET queue_rank=? WHERE incident_id=?",
                          (idx, inc["incident_id"]))
    if moves:
        store.audit("QUEUE_RESORTED", actor,
                    {"moves": moves, "order": [i["incident_id"] for i in ordered]})
    return ordered


def ingest(store, report):
    """Ingest one raw report. Returns the Agent A output object (§1)."""
    with store.lock:
        location = store.one("SELECT * FROM locations WHERE code=?",
                             (report.get("location_hint"),))
        facts, missing, mode = llm.extract(report, location)
        facts["_missing_info"] = missing

        store.execute(
            "INSERT OR REPLACE INTO reports(report_id, channel, lang, text, "
            "location_hint, people_affected, received_at, parse_mode) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (report["report_id"], report.get("channel"), report.get("lang"),
             report["text"], report.get("location_hint"),
             report.get("people_affected"), now_iso(), mode))
        store.audit("REPORT_RECEIVED", "agent_a",
                    {"report_id": report["report_id"],
                     "channel": report.get("channel"), "parse_mode": mode})
        if mode == "rules":
            store.audit("PARSE_FALLBACK_RULES", "agent_a",
                        {"report_id": report["report_id"], "parse_mode": "rules"})

        # Duplicate linking: same location + same verified people count as an
        # existing incident -> link, never discard, never double-count.
        duplicate_of = None
        if facts.get("people_count") is not None:
            candidates = store.all(
                "SELECT * FROM incidents WHERE location_code=?",
                (facts["location_code"],))
            for cand in candidates:
                cfacts = json.loads(cand["facts"])
                if cfacts.get("people_count") == facts["people_count"]:
                    duplicate_of = cand["incident_id"]
                    break

        if duplicate_of:
            store.execute(
                "INSERT INTO incident_links(report_id, incident_id, relation) "
                "VALUES(?, ?, 'duplicate_of')", (report["report_id"], duplicate_of))
            store.execute("UPDATE reports SET incident_id=? WHERE report_id=?",
                          (duplicate_of, report["report_id"]))
            store.audit("REPORT_LINKED_DUPLICATE", "agent_a",
                        {"report_id": report["report_id"], "incident_id": duplicate_of})
            store.commit()
            return {"report_id": report["report_id"], "incident_id": duplicate_of,
                    "is_duplicate": True, "duplicate_of": duplicate_of,
                    "facts": facts, "missing_info": missing, "parse_mode": mode,
                    "raw_text_preserved": True}

        incident_id = _next_id(store, "incident_counter", "INC")
        result = rubric.score(facts, location)
        seq_row = store.one("SELECT COALESCE(MAX(seq), 0) AS s FROM audit")
        store.execute(
            "INSERT INTO incidents(incident_id, location_code, hazard_type, facts, "
            "missing_info, score, band, factor_breakdown, reason, provisional, "
            "medical_override, queue_rank, created_seq) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (incident_id, facts["location_code"], facts["hazard_type"],
             json.dumps(facts, ensure_ascii=False), json.dumps(missing),
             result["score"], result["band"],
             json.dumps(result["factor_breakdown"]), result["reason"],
             1 if result["provisional"] else 0,
             1 if result["medical_override"] else 0, seq_row["s"]))
        store.execute("UPDATE reports SET incident_id=? WHERE report_id=?",
                      (incident_id, report["report_id"]))
        store.audit("INCIDENT_CREATED", "agent_a",
                    {"incident_id": incident_id, "report_id": report["report_id"],
                     "location_code": facts["location_code"]})
        store.audit("INCIDENT_SCORED", "agent_a",
                    {"incident_id": incident_id, "score": result["score"],
                     "band": result["band"], "reason": result["reason"],
                     "medical_override": result["medical_override"]})
        if missing:
            store.audit("MISSING_INFO_FLAGGED", "agent_a",
                        {"incident_id": incident_id, "missing_info": missing})
        recompute_ranks(store)
        store.commit()
        return {"report_id": report["report_id"], "incident_id": incident_id,
                "is_duplicate": False, "duplicate_of": None, "facts": facts,
                "missing_info": missing, "parse_mode": mode,
                "raw_text_preserved": True, "score": result}
