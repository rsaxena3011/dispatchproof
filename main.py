"""DispatchProof — operator console server.

One process: FastAPI + SQLite, one static page. Run:
    uvicorn main:app --host 0.0.0.0 --port 8000
or `python main.py`. The drill database lives at DP_DB (default
./data/dispatchproof.db next to this file).
"""

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import agent_a
import agent_b
import gate as gate_mod
import inbox
import replay
import templates_hi
import tools
from store import Store

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DP_DB", os.path.join(BASE, "data", "dispatchproof.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_store = Store(DB_PATH)
if _store.one("SELECT COUNT(*) AS n FROM locations")["n"] == 0:
    replay.seed(_store)


def get_store():
    return _store


def reset_store():
    global _store
    try:
        _store.close()
    except Exception:
        pass
    _store = replay.fresh_store(DB_PATH)
    return _store


def incident_view(store, inc):
    facts = json.loads(inc["facts"])
    reports = store.all(
        "SELECT report_id, channel, lang, text, parse_mode FROM reports "
        "WHERE incident_id=? ORDER BY report_id", (inc["incident_id"],))
    plans = [agent_b._plan_view(p) for p in store.all(
        "SELECT * FROM plans WHERE incident_id=? ORDER BY plan_id",
        (inc["incident_id"],))]
    return {
        "incident_id": inc["incident_id"],
        "location_code": inc["location_code"],
        "score": inc["score"], "band": inc["band"],
        "reason": inc["reason"],
        "factor_breakdown": json.loads(inc["factor_breakdown"] or "{}"),
        "provisional": bool(inc["provisional"]),
        "medical_override": bool(inc["medical_override"]),
        "missing_info": json.loads(inc["missing_info"]),
        "queue_rank": inc["queue_rank"],
        "facts": {k: v for k, v in facts.items() if not k.startswith("_")},
        "reports": reports,
        "plans": plans,
    }


def state_view(store):
    incidents = store.all("SELECT * FROM incidents ORDER BY queue_rank IS NULL, queue_rank")
    queue = [incident_view(store, i) for i in incidents]
    crews = []
    for c in store.all("SELECT * FROM crews ORDER BY crew_id"):
        c = dict(c)
        c["capabilities"] = json.loads(c["capabilities"])
        crews.append(c)
    audit = []
    for row in store.audit_rows(300):
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
        audit.append(row)
    last_resort = next((a for a in audit if a["event"] == "QUEUE_RESORTED"), None)
    locations = {l["code"]: l for l in store.all("SELECT * FROM locations")}
    inbox_rows = inbox.inbox_view(store)
    area_updates = []
    for inc in queue:
        mine = [d for d in inbox_rows if d["incident_id"] == inc["incident_id"]]
        if any(d["state"] in ("acknowledged", "completed") for d in mine):
            status = "acknowledged"
        elif mine:
            status = "dispatched"
        else:
            status = "new"
        crew_name = mine[0]["crew_name"] if mine else None
        loc = locations[inc["location_code"]]
        area_updates.append(templates_hi.area_update(
            {"code": loc["code"], "name": loc["name"]}, status, crew_name))
    return {
        "ward": "Ghatauli",
        "synthetic": True,
        "resource_version": store.resource_version(),
        "queue": queue,
        "crews": crews,
        "inbox": inbox.inbox_view(store),
        "audit": audit,
        "last_resort": last_resort,
        "area_updates": area_updates,
        "rules_mode": True,
        "rules_banner": templates_hi.RULES_MODE_LINE,
    }


app = FastAPI(title="DispatchProof")


def _json(result):
    status = result.pop("http", 200) if isinstance(result, dict) else 200
    if isinstance(result, dict) and result.get("ok") is False and status == 200:
        status = 400
    return JSONResponse(result, status_code=status)


@app.get("/api/state")
def api_state():
    return state_view(get_store())


@app.post("/api/reset")
def api_reset():
    reset_store()
    return {"ok": True, "resource_version": get_store().resource_version()}


@app.post("/api/load-reports")
def api_load_reports():
    return {"ok": True, "results": replay.load_reports(get_store())}


@app.post("/api/ingest")
async def api_ingest(request: Request):
    body = await request.json()
    from fixtures import REPORTS, SOS_REPORT
    known = {r["report_id"]: r for r in REPORTS + [SOS_REPORT]}
    if body.get("report_id") in known:
        report = known[body["report_id"]]
    else:
        report = {
            "report_id": body.get("report_id") or "LIVE-001",
            "channel": body.get("channel", "citizen_message"),
            "lang": body.get("lang", "en"),
            "location_hint": body.get("location_hint", "LOC-01"),
            "people_affected": body.get("people_affected"),
            "text": body.get("text", ""),
        }
    return agent_a.ingest(get_store(), report)


@app.post("/api/plan")
async def api_plan(request: Request):
    body = await request.json()
    return _json(agent_b.propose_plan(get_store(), body["incident_id"]))


@app.post("/api/inject-flip")
async def api_flip(request: Request):
    body = await request.json()
    return _json(replay.inject_flip(get_store(), body.get("crew_id", "CREW-2")))


@app.post("/api/inject-sos")
def api_sos():
    return replay.inject_sos(get_store())


@app.post("/api/approve")
async def api_approve(request: Request):
    body = await request.json()
    return _json(gate_mod.approve(get_store(), body["plan_id"],
                                  int(body["seen_version"])))


@app.post("/api/dispatch")
async def api_dispatch(request: Request):
    body = await request.json()
    return _json(tools.dispatch(get_store(), body["plan_id"],
                                body["approval_id"]))


@app.post("/api/ack")
async def api_ack(request: Request):
    body = await request.json()
    return _json(tools.acknowledge(get_store(), body["dispatch_id"]))


@app.post("/api/complete")
async def api_complete(request: Request):
    body = await request.json()
    return _json(tools.complete(get_store(), body["dispatch_id"]))


@app.post("/api/scenario")
def api_scenario():
    store = reset_store()
    result = replay.run_scenario(store)
    return result


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")),
          name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
