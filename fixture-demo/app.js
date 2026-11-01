const STORAGE_KEY = "opsdesk-fixture-v3";
const VALID_TICKETS = new Set(["T-0044", "T-0078"]);
const PHASES_T0044 = new Set([
  "proposed",
  "approved_plus",
  "plus_executed",
  "revised_basic",
  "approved_basic",
  "basic_executed",
]);
const PHASES_T0078 = new Set(["proposed", "rejected"]);

/** @typedef {{ phase: string, plan: string, approvalId?: string, supersededApproval?: string, receipt?: { id: string, plan: string }, message?: string, rejectReason?: string }} TicketSimState */
/** @typedef {{ activeTicketId: string, tickets: Record<string, TicketSimState> }} Store */

async function loadFixtures() {
  const res = await fetch("fixtures.json");
  if (!res.ok) throw new Error("fixtures.json failed to load");
  return res.json();
}

/** @param {string} ticketId @returns {TicketSimState} */
function defaultTicketState(ticketId) {
  if (ticketId === "T-0078") {
    return { phase: "proposed", plan: "escalate", message: "", rejectReason: "" };
  }
  return { phase: "proposed", plan: "Plus", message: "" };
}

/** @param {unknown} raw @param {string} fallbackTicketId @returns {Store} */
function sanitizeStore(raw, fallbackTicketId) {
  const fresh = (tid) => ({
    activeTicketId: tid,
    tickets: { [tid]: defaultTicketState(tid) },
  });
  if (!raw || typeof raw !== "object") return fresh(fallbackTicketId);
  const o = /** @type {Record<string, unknown>} */ (raw);
  const active = typeof o.activeTicketId === "string" && VALID_TICKETS.has(o.activeTicketId)
    ? o.activeTicketId
    : fallbackTicketId;
  const ticketsIn = o.tickets && typeof o.tickets === "object" ? /** @type {Record<string, unknown>} */ (o.tickets) : {};
  /** @type {Record<string, TicketSimState>} */
  const tickets = {};
  for (const tid of VALID_TICKETS) {
    const src = ticketsIn[tid];
    if (!src || typeof src !== "object") {
      tickets[tid] = defaultTicketState(tid);
      continue;
    }
    const s = /** @type {Record<string, unknown>} */ (src);
    const phase = typeof s.phase === "string" ? s.phase : "proposed";
    const allowed = tid === "T-0078" ? PHASES_T0078 : PHASES_T0044;
    if (!allowed.has(phase)) {
      tickets[tid] = defaultTicketState(tid);
      continue;
    }
    tickets[tid] = {
      phase,
      plan: typeof s.plan === "string" ? s.plan.slice(0, 32) : defaultTicketState(tid).plan,
      message: typeof s.message === "string" ? s.message.slice(0, 500) : "",
      approvalId: typeof s.approvalId === "string" ? s.approvalId.slice(0, 64) : undefined,
      supersededApproval: typeof s.supersededApproval === "string" ? s.supersededApproval.slice(0, 64) : undefined,
      rejectReason: typeof s.rejectReason === "string" ? s.rejectReason.slice(0, 300) : "",
      receipt:
        s.receipt && typeof s.receipt === "object"
          ? {
              id: String(/** @type {Record<string, unknown>} */ (s.receipt).id || "").slice(0, 64),
              plan: String(/** @type {Record<string, unknown>} */ (s.receipt).plan || "").slice(0, 32),
            }
          : undefined,
    };
  }
  return { activeTicketId: active, tickets };
}

/** @returns {Store} */
function readStore(fallbackTicketId) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return sanitizeStore(JSON.parse(raw), fallbackTicketId);
  } catch (_) {
    /* corrupt storage */
  }
  localStorage.removeItem("opsdesk-fixture-v2");
  localStorage.removeItem("opsdesk-fixture-v1");
  return sanitizeStore(null, fallbackTicketId);
}

/** @param {Store} store */
function writeStore(store) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sanitizeStore(store, store.activeTicketId)));
}

function ticketState(store, ticketId) {
  if (!store.tickets[ticketId]) store.tickets[ticketId] = defaultTicketState(ticketId);
  return store.tickets[ticketId];
}

function setActive(store, ticketId) {
  store.activeTicketId = ticketId;
  ticketState(store, ticketId);
  writeStore(store);
  return store;
}

function resetTicket(store, ticketId) {
  store.tickets[ticketId] = defaultTicketState(ticketId);
  store.activeTicketId = ticketId;
  writeStore(store);
  return store;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function statusLabel(ticketId, state) {
  if (ticketId === "T-0078") {
    return state.phase === "rejected" ? "Escalated" : "Awaiting review";
  }
  const labels = {
    proposed: "Awaiting review",
    approved_plus: "Approved · Plus",
    plus_executed: "Executed · Plus",
    revised_basic: "Revised draft",
    approved_basic: "Approved · Basic",
    basic_executed: "Executed · Basic",
  };
  return labels[state.phase] || "Awaiting review";
}

function rationaleFor(ticket, state) {
  if (ticket.id !== "T-0044") return ticket.expected_after_reject?.rationale || "";
  if (["revised_basic", "approved_basic", "basic_executed"].includes(state.phase)) {
    return ticket.revised_proposal.rationale;
  }
  return ticket.initial_proposal.rationale;
}

function planFor(state) {
  if (state.receipt) return state.receipt.plan;
  if (["revised_basic", "approved_basic", "basic_executed"].includes(state.phase)) return "Basic";
  return state.plan || "Plus";
}

function actionLine(parent, action, plan) {
  const p = el("p", "action-line");
  const strong = el("strong", null, action);
  p.append(strong, document.createTextNode(" → plan "));
  const code = document.createElement("code");
  code.textContent = plan;
  p.appendChild(code);
  parent.appendChild(p);
}

function executeApproval(state, approvalId) {
  if (state.supersededApproval && state.supersededApproval === approvalId) {
    return { ok: false, message: "Refused: prior approval was voided by revision (superseded)." };
  }
  if (state.approvalId !== approvalId) {
    return { ok: false, message: "Refused: approval does not match the active proposal version." };
  }
  if (state.phase === "approved_plus") {
    return {
      ok: true,
      next: { ...state, phase: "plus_executed", message: "", receipt: { id: approvalId, plan: "Plus" } },
    };
  }
  if (state.phase === "approved_basic") {
    return {
      ok: true,
      next: { ...state, phase: "basic_executed", message: "", receipt: { id: approvalId, plan: "Basic" } },
    };
  }
  return { ok: false, message: "Refused: proposal is not approved for execution." };
}

function renderEvidence(root, ticket) {
  const ev = el("div", "card evidence");
  ev.appendChild(el("h2", "section", "Handbook evidence"));
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>Passage</th><th>Quote</th></tr>";
  for (const row of ticket.evidence) {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.append(el("code", null, row.passage_id), document.createElement("br"), document.createTextNode(row.title));
    const td2 = el("td", null, row.quote);
    tr.append(td1, td2);
    table.appendChild(tr);
  }
  ev.appendChild(table);
  root.appendChild(ev);
}

function renderQueue(queueEl, tickets, store, onSelect) {
  queueEl.replaceChildren(el("h2", null, "Queue"));
  for (const t of tickets) {
    const st = ticketState(store, t.id);
    const btn = el("button", "queue-item" + (store.activeTicketId === t.id ? " active" : ""));
    btn.type = "button";
    btn.appendChild(el("span", "id", t.id));
    btn.appendChild(el("span", "subject", t.subject));
    btn.appendChild(el("span", "status", statusLabel(t.id, st)));
    btn.addEventListener("click", () => onSelect(t.id));
    queueEl.appendChild(btn);
  }
}

function renderT0044(root, ticket, state, store, rerender) {
  root.replaceChildren();
  const head = el("div", "span-2");
  head.appendChild(el("h2", "section", `${ticket.id} · ${ticket.subject}`));
  head.appendChild(el("p", "meta", `Customer ${ticket.customer_id}`));
  const request = el("div", "card");
  request.appendChild(el("p", null, ticket.body));
  head.append(request);
  root.appendChild(head);

  const grid = el("div", "detail-grid");
  const left = el("div");
  renderEvidence(left, ticket);
  grid.appendChild(left);

  const right = el("div");
  const decision = el("div", "card");
  decision.appendChild(el("h2", "section", "Decision"));
  const pill = el("span", "pill pending");
  pill.textContent = statusLabel(ticket.id, state);
  decision.appendChild(pill);
  const p = ticket.initial_proposal;
  actionLine(decision, p.action, planFor(state));
  decision.appendChild(el("p", "meta", `Cites ${p.citation} · ${rationaleFor(ticket, state)}`));
  right.appendChild(decision);

  if (state.message) {
    const isDup = state.message.toLowerCase().includes("duplicate");
    right.appendChild(el("p", isDup ? "duplicate-note" : "refusal", state.message));
  }

  const toolbar = el("div", "toolbar");

  if (state.phase === "proposed") {
    const approve = el("button", "primary pending", "Approve for Plus");
    approve.dataset.testid = "approve-plus";
    approve.addEventListener("click", () => {
      store.tickets[ticket.id] = { ...state, phase: "approved_plus", approvalId: "A-fixture-001", message: "" };
      writeStore(store);
      rerender();
    });
    toolbar.appendChild(approve);
  }
  if (state.phase === "approved_plus") {
    const runPlus = el("button", "primary pending", "Run approved action");
    runPlus.dataset.testid = "run-plus";
    runPlus.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-001");
      store.tickets[ticket.id] = r.ok ? r.next : { ...state, message: r.message };
      writeStore(store);
      rerender();
    });
    const revise = el("button", null, "Edit plan to Basic");
    revise.dataset.testid = "revise-basic";
    revise.addEventListener("click", () => {
      store.tickets[ticket.id] = {
        ...state,
        phase: "revised_basic",
        supersededApproval: state.approvalId,
        approvalId: undefined,
        plan: "Basic",
        message: "Revision voids the prior approval. Approve the Basic draft before running.",
      };
      writeStore(store);
      rerender();
    });
    toolbar.append(runPlus, revise);
  }
  if (state.phase === "revised_basic") {
    const stale = el("button", "danger", "Run superseded approval");
    stale.dataset.testid = "run-stale";
    stale.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-001");
      store.tickets[ticket.id] = { ...state, message: r.message || "Refused." };
      writeStore(store);
      rerender();
    });
    const approve2 = el("button", "primary pending", "Approve for Basic");
    approve2.dataset.testid = "approve-basic";
    approve2.addEventListener("click", () => {
      store.tickets[ticket.id] = { ...state, phase: "approved_basic", approvalId: "A-fixture-002", message: "" };
      writeStore(store);
      rerender();
    });
    toolbar.append(stale, approve2);
  }
  if (state.phase === "approved_basic") {
    const run = el("button", "primary pending", "Run approved action");
    run.dataset.testid = "run-basic";
    run.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-002");
      store.tickets[ticket.id] = r.ok ? r.next : { ...state, message: r.message };
      writeStore(store);
      rerender();
    });
    toolbar.appendChild(run);
  }
  if (state.phase === "plus_executed" || state.phase === "basic_executed") {
    const replay = el("button", null, "Run approved action again");
    replay.dataset.testid = "replay";
    replay.addEventListener("click", () => {
      store.tickets[ticket.id] = {
        ...state,
        message: "Duplicate run ignored — mock was not called again.",
      };
      writeStore(store);
      rerender();
    });
    toolbar.appendChild(replay);
    const receipt = el("div", "card receipt");
    const rc = state.receipt;
    receipt.textContent = `MOCK RECEIPT · change_plan · customer ${ticket.customer_id} · plan=${rc.plan} · idempotency=${rc.id}`;
    right.appendChild(receipt);
  }

  const reset = el("button", null, "Reset this ticket");
  reset.dataset.testid = "reset-ticket";
  reset.addEventListener("click", () => {
    resetTicket(store, ticket.id);
    rerender();
  });
  toolbar.appendChild(reset);
  right.appendChild(toolbar);
  grid.appendChild(right);
  root.appendChild(grid);
}

function renderT0078(root, ticket, state, store, rerender) {
  root.replaceChildren();
  root.appendChild(el("h2", "section", `${ticket.id} · ${ticket.subject}`));
  const request = el("div", "card span-2");
  request.appendChild(el("p", null, ticket.body));
  root.appendChild(request);
  renderEvidence(root, ticket);

  const grid = el("div", "detail-grid");
  const wrong = el("div", "card");
  wrong.appendChild(el("h2", "section", "Proposal under review"));
  wrong.appendChild(el("span", "pill bad", "Needs rejection"));
  const wp = ticket.wrong_proposal;
  actionLine(wrong, wp.action, `${wp.params.amount_eur} EUR`);
  grid.appendChild(wrong);

  const side = el("div");
  if (state.message) side.appendChild(el("p", "refusal", state.message));
  const toolbar = el("div", "toolbar");
  if (state.phase === "proposed") {
    const reason = document.createElement("input");
    reason.type = "text";
    reason.placeholder = "Rejection reason";
    reason.value = state.rejectReason || "";
    reason.setAttribute("aria-label", "Rejection reason");
    const reject = el("button", "primary danger", "Reject with reason");
    reject.dataset.testid = "reject-refund";
    reject.addEventListener("click", () => {
      const rr = reason.value.trim() || "Ticket text is not an approval.";
      store.tickets[ticket.id] = { ...state, phase: "rejected", rejectReason: rr, message: `Rejected — ${rr}` };
      writeStore(store);
      rerender();
    });
    toolbar.append(reason, reject);
  }
  if (state.phase === "rejected") {
    const ok = el("div", "card");
    ok.appendChild(el("h2", "section", "Follow-up proposal"));
    ok.appendChild(el("span", "pill ok", ticket.expected_after_reject.action));
    ok.appendChild(el("p", null, ticket.expected_after_reject.rationale));
    side.appendChild(ok);
  }
  const reset = el("button", null, "Reset this ticket");
  reset.dataset.testid = "reset-ticket";
  reset.addEventListener("click", () => {
    resetTicket(store, ticket.id);
    rerender();
  });
  toolbar.appendChild(reset);
  side.appendChild(toolbar);
  grid.appendChild(side);
  root.appendChild(grid);
}

async function main() {
  const queueEl = document.getElementById("queue");
  const root = document.getElementById("content");
  try {
    const data = await loadFixtures();
    let store = readStore(data.tickets[0].id);
    writeStore(store);

    const rerender = () => {
      renderQueue(queueEl, data.tickets, store, (tid) => {
        store = setActive(store, tid);
        rerender();
      });
      const ticket = data.tickets.find((x) => x.id === store.activeTicketId) || data.tickets[0];
      const state = ticketState(store, ticket.id);
      if (ticket.id === "T-0078") renderT0078(root, ticket, state, store, rerender);
      else renderT0044(root, ticket, state, store, rerender);
    };
    rerender();
  } catch (e) {
    root.replaceChildren(el("p", "refusal", String(e)));
  }
}

main();
