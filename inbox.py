"""Mock responder inbox (labelled simulation — no real SMS/WhatsApp).

A crew's inbox is its dispatch rows: the Hindi task message (template H2),
the acknowledgement request line (H3), and the two responder actions. The
operator console can display the inbox but can never acknowledge on a
responder's behalf — ack/complete call tools.py from the responder side.
"""

import templates_hi


def inbox_view(store):
    rows = store.all(
        "SELECT d.*, c.name AS crew_name FROM dispatches d "
        "JOIN crews c ON c.crew_id = d.crew_id ORDER BY d.dispatch_id")
    view = []
    for d in rows:
        view.append({
            "dispatch_id": d["dispatch_id"],
            "crew_id": d["crew_id"],
            "crew_name": d["crew_name"],
            "incident_id": d["incident_id"],
            "state": d["state"],
            "task_message": d["task_message"],
            "ack_line": (templates_hi.ACK_REQUEST if d["state"] == "sent"
                         else (f"पुष्टि हो गई — {templates_hi._time_ist(d['ack_at'])}"
                               if d["ack_at"] else "")),
            "sent_at": d["sent_at"], "ack_at": d["ack_at"],
            "completed_at": d["completed_at"],
        })
    return view
