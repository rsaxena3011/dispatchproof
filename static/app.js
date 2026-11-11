const approvals = {};   // plan_id -> approval_id (this session)
const lastResults = {};  // plan_id -> last approve/dispatch result note
const idemKeys = {};     // plan_id -> idempotency key (this session)

async function post(path, body, actor) {
  const headers = {"Content-Type": "application/json"};
  if (actor) headers["X-Actor"] = actor;
  const resp = await fetch(path, {
    method: "POST",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await resp.json(); } catch (e) {}
  data._status = resp.status;
  return data;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function planCard(plan) {
  const assign = plan.assignments.map(a =>
    `${esc(a.crew_id)} · ${esc(a.capability_required)} · ${a.people_assigned} people`).join("; ");
  const checks = Object.entries(plan.checks).map(([k, v]) => `${k}: ${v}`).join(" · ");
  let note = "";
  if (plan.state === "stale-rejected" && plan.rejection) {
    note = `<div class="stale-note">Approved version: v${plan.rejection.approved_version} · ` +
      `Current version: v${plan.rejection.current_version} · plan_hash ${esc(plan.rejection.plan_hash_short)}…<br>` +
      `<strong>Dispatch blocked. Rejection recorded. Fresh approval required.</strong></div>`;
  } else if (plan.state === "stale-rejected") {
    note = `<div class="stale-note">REJECTED_STALE — this plan can never be approved. Agent B must propose a new plan.</div>`;
  } else if (lastResults[plan.plan_id]) {
    note = `<div class="ok-note">${esc(lastResults[plan.plan_id])}</div>`;
  }
  const risk = plan.risk_note
    ? `<div class="missing">⚠ ${esc(plan.risk_note)}</div>` : "";
  const canApprove = ["proposed", "reserved"].includes(plan.state);
  const canDispatch = plan.state === "approved";
  return `<div class="plan ${esc(plan.state)}">
    <div class="row"><strong>${esc(plan.plan_id)}</strong><span class="meta">state: ${esc(plan.state)}</span></div>
    <div class="meta">assign: ${assign}</div>
    <div class="meta">checks: ${esc(checks)} · ${esc(plan.matching_trace || "")}</div>
    <div class="hash">plan_hash ${esc(plan.plan_hash_short)}… · based_on_version ${plan.based_on_version}</div>
    ${risk}
    ${note}
    <div class="actions">
      <button ${canApprove ? "" : "disabled"} onclick="approvePlan('${plan.plan_id}', ${plan.based_on_version})">Approve (as seen: v${plan.based_on_version})</button>
      <button ${canDispatch ? "" : "disabled"} onclick="dispatchPlan('${plan.plan_id}')">Dispatch (tool call)</button>
    </div>
  </div>`;
}

function incidentCard(inc) {
  const reports = inc.reports.map(r =>
    `<div class="report"><span class="tag">${esc(r.report_id)}</span> (${esc(r.channel)}, parse: ${esc(r.parse_mode)})<br>${esc(r.text)}</div>`).join("");
  const plans = inc.plans.map(planCard).join("");
  const missing = inc.missing_info.length
    ? `<div class="missing">missing info: ${esc(inc.missing_info.join(", "))} — verification requested</div>` : "";
  const med = inc.medical_override ? ` <span class="band HIGH">MEDICAL OVERRIDE</span>` : "";
  const prov = inc.provisional ? ` <span class="missing">PROVISIONAL</span>` : "";
  const review = inc.human_review ? ` <span class="band CRITICAL">HUMAN REVIEW</span>` : "";
  const f = inc.facts || {};
  const factVal = v => (v === null || v === undefined || v === "unknown")
    ? `<span class="missing">UNKNOWN</span>` : esc(v);
  const facts = `<div class="facts">
    <div>life risk: <strong>${factVal(f.life_risk_condition)}</strong>${f.life_risk_detail ? " (" + esc(f.life_risk_detail) + ")" : ""}</div>
    <div>people: <strong>${factVal(f.people_count)}</strong></div>
    <div>vulnerability: <strong>${factVal(f.vulnerability)}</strong>${f.vulnerability_detail ? " (" + esc(f.vulnerability_detail) + ")" : ""}</div>
    <div>hazard trend: <strong>${factVal(f.hazard_progression)}</strong></div>
    <div>hazard: <strong>${factVal(f.hazard_type)}</strong> · location type: <strong>${factVal(f.location_type)}</strong></div>
  </div>`;
  const breakdown = Object.entries(inc.factor_breakdown || {}).map(([k, v]) =>
    `<span class="factor">${esc(k)} <strong>${v}</strong></span>`).join(" ");
  return `<div class="card">
    <div class="row">
      <span><span class="rank">${inc.queue_rank || "–"}</span> <strong>${esc(inc.incident_id)}</strong> · ${esc(inc.location_code)}${med}${prov}${review}</span>
      <span><span class="band ${esc(inc.band)}">${esc(inc.band)}</span> <span class="score">${inc.score}</span></span>
    </div>
    <div class="reason">${esc(inc.reason)}</div>
    <div class="breakdown">${breakdown}</div>
    ${facts}
    ${missing}
    ${reports}
    <button onclick="proposePlan('${inc.incident_id}')">Agent B: propose plan</button>
    ${plans}
  </div>`;
}

function render(state) {
  document.getElementById("resource-version").textContent = state.resource_version;
  document.getElementById("rules-banner").textContent =
    "Extraction mode: RULES (deterministic parser) — " + state.rules_banner;
  document.getElementById("queue").innerHTML =
    state.queue.length ? state.queue.map(incidentCard).join("") :
    `<div class="card">Queue is empty. Load the drill reports to begin.</div>`;
  document.getElementById("crews").innerHTML = state.crews.map(c =>
    `<div class="crew"><span><strong>${esc(c.crew_id)}</strong> ${esc(c.name)} · ${esc(c.capabilities.join(", "))} · cap ${c.capacity} · home ${esc(c.home)}</span><span class="status ${esc(c.status)}">${esc(c.status)}</span></div>`).join("");
  document.getElementById("inbox").innerHTML = state.inbox.length ? state.inbox.map(d =>
    `<div class="card">
       <div class="row"><strong>${esc(d.dispatch_id)} → ${esc(d.crew_name)}</strong><span class="dispatch-state ${esc(d.state)}">${esc(d.state)}</span></div>
       <div class="inbox-msg">${esc(d.task_message)}</div>
       <div class="meta">${esc(d.ack_line || "")}</div>
       <div class="plan actions">
         <button ${d.state === "sent" ? "" : "disabled"} onclick="ackDispatch('${d.dispatch_id}')">पुष्टि करें (acknowledge)</button>
         <button ${d.state === "acknowledged" ? "" : "disabled"} onclick="completeDispatch('${d.dispatch_id}')">Mark task done (complete)</button>
       </div>
     </div>`).join("") : `<div class="card">No dispatches yet.</div>`;
  document.getElementById("area").innerHTML = state.area_updates.map(a =>
    `<div class="area-line">${esc(a)}</div>`).join("") || `<div class="card">No active incidents.</div>`;
  const traceLine = t => `<div class="trace-row">${esc(t.tool)} — ${esc(t.status)}${t.ref ? " · " + esc(t.ref) : ""}</div>`;
  document.getElementById("trace-a").innerHTML =
    (state.tool_traces.agent_a || []).map(traceLine).join("") || `<div class="meta">no tool calls yet</div>`;
  document.getElementById("trace-b").innerHTML =
    (state.tool_traces.agent_b || []).map(traceLine).join("") || `<div class="meta">no tool calls yet</div>`;
  document.getElementById("situation").innerHTML = state.situation ?
    `<div class="card"><div>${esc(state.situation.en)}</div><div class="area-line">${esc(state.situation.hi)}</div>` +
    `<div class="meta">synthetic ward data — PS-03 area-wise update</div></div>` : "";
  const rb = document.getElementById("resort-banner");
  if (state.last_resort) {
    const moves = state.last_resort.payload.moves.map(m =>
      `${m.incident_id}: rank ${m.from} → ${m.to}`).join(" · ");
    rb.textContent = "QUEUE_RESORTED — " + moves;
    rb.classList.remove("hidden");
  } else {
    rb.classList.add("hidden");
  }
  document.getElementById("audit").innerHTML = state.audit.map(a => {
    const rej = a.event.includes("REJECTED") ? " rej" : "";
    return `<div class="row">#${a.seq} <span class="ev${rej}">${esc(a.event)}</span> ` +
      `<span class="actor">${esc(a.actor)}</span> ${esc(JSON.stringify(a.payload))}</div>`;
  }).join("");
}

async function refresh() {
  const resp = await fetch("/api/state");
  render(await resp.json());
}

async function proposePlan(incidentId) {
  await post("/api/plan", {incident_id: incidentId});
  refresh();
}

async function approvePlan(planId, seenVersion) {
  const r = await post("/api/approve", {plan_id: planId, seen_version: seenVersion}, "operator");
  if (r.ok) {
    approvals[planId] = r.approval_id;
    lastResults[planId] = `APPROVAL_GRANTED (${r.approval_id}) at version ${r.based_on_version}. Approval is single-use.`;
  } else {
    lastResults[planId] = `HTTP ${r._status} — ${r.error} (${r.reason || "stale"})`;
  }
  refresh();
}

async function dispatchPlan(planId) {
  const approvalId = approvals[planId];
  if (!approvalId) {
    lastResults[planId] = "Approve first — dispatch needs the approval id from this session.";
    refresh();
    return;
  }
  if (!idemKeys[planId]) {
    idemKeys[planId] = "ui-" + planId + "-" + Math.random().toString(36).slice(2, 10);
  }
  const r = await post("/api/dispatch", {plan_id: planId, approval_id: approvalId,
    idempotency_key: idemKeys[planId]}, "operator");
  lastResults[planId] = r.ok ? `DISPATCH_SENT: ${r.dispatch_ids.join(", ")}${r.idempotent_replay ? " (idempotent replay — no duplicate created)" : ""}` :
    `HTTP ${r._status} — ${r.error} (${r.reason || "stale"})`;
  refresh();
}

async function ackDispatch(id) { await post("/api/ack", {dispatch_id: id}, "responder"); refresh(); }
async function completeDispatch(id) { await post("/api/complete", {dispatch_id: id}, "responder"); refresh(); }

document.getElementById("btn-load").onclick = async () => { await post("/api/load-reports"); refresh(); };
document.getElementById("btn-scenario").onclick = async () => { await post("/api/scenario-setup"); refresh(); };
document.getElementById("btn-flip").onclick = async () => { await post("/api/inject-flip", {crew_id: "CREW-2"}); refresh(); };
document.getElementById("btn-sos").onclick = async () => { await post("/api/inject-sos"); refresh(); };
document.getElementById("btn-alert").onclick = async () => { await post("/api/alert", {}); refresh(); };
document.getElementById("btn-reset").onclick = async () => { await post("/api/reset"); refresh(); };

refresh();
setInterval(refresh, 2000);
