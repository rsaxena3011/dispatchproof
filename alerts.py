"""Ward alerts — the PS-03 completeness feature (challenge correction C10).

One small feature, not a subsystem: a synthetic alert (flood update, road
closure) enters through the same public API as everything else, Agent A
correlates it with nearby incidents, Agent B flags affected live plans,
and the console's ward situation update (Hindi + English) reflects it.
Every alert is labelled synthetic in storage, in the audit and on screen.
"""

import agent_a
import agent_b
from store import now_iso


def ingest_alert(store, alert):
    """alert: {alert_id?, type, location_hint, text}. Returns the outcome."""
    with store.lock:
        n = int(store.get_meta("alert_counter", "0")) + 1
        store.set_meta("alert_counter", str(n))
        alert_id = alert.get("alert_id") or f"ALERT-{n:03d}"
        row = {
            "alert_id": alert_id,
            "type": alert.get("type", "flood_update"),
            "location_hint": alert.get("location_hint"),
            "text": alert.get("text", ""),
            "synthetic": True,
        }
        store.execute(
            "INSERT INTO alerts(alert_id, type, location_hint, text, "
            "synthetic, received_at) VALUES(?, ?, ?, ?, 1, ?)",
            (row["alert_id"], row["type"], row["location_hint"],
             row["text"], now_iso()))
        store.audit("ALERT_RECEIVED", "system",
                    {"alert_id": alert_id, "type": row["type"],
                     "location_hint": row["location_hint"],
                     "synthetic": True, "test_condition": True})
        store.commit()

    correlated = []
    with store.lock:
        correlated = agent_a.correlate_alert(store, row)
        store.commit()

    flagged = []
    note = (f"Route risk: {row['type']} alert at "
            f"{row['location_hint'] or 'ward'} — {row['text']}")
    for incident_id in correlated:
        live = store.all(
            "SELECT * FROM plans WHERE incident_id=? AND state IN "
            "('proposed', 'reserved', 'approved', 'dispatched')",
            (incident_id,))
        for plan in live:
            agent_b.flag_plan_risk(store, plan["plan_id"], note)
            flagged.append(plan["plan_id"])
    return {"ok": True, "alert_id": alert_id, "synthetic": True,
            "correlated_incidents": correlated, "flagged_plans": flagged}
