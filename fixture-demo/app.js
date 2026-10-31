const STORAGE_KEY = "opsdesk-fixture-v2";

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

/** @returns {Store} */
function readStore(fallbackTicketId) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.tickets && parsed.activeTicketId) return parsed;
    }
    const legacy = localStorage.getItem("opsdesk-fixture-v1");
    if (legacy) {
      localStorage.removeItem("opsdesk-fixture-v1");
    }
  } catch (_) {
    /* ignore corrupt storage */
  }
  return { activeTicketId: fallbackTicketId, tickets: { [fallbackTicketId]: defaultTicketState(fallbackTicketId) } };
}

/** @param {Store} store */
function writeStore(store) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
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

function executeApproval(state, approvalId) {
  if (state.supersededApproval && state.supersededApproval === approvalId) {
    return { ok: false, message: "Refused: approval A-fixture-001 is void (superseded after revision)." };
  }
  if (state.approvalId !== approvalId) {
    return { ok: false, message: "Refused: approval does not match the active proposal version." };
  }
  if (state.phase === "approved_plus") {
    return {
      ok: true,
      next: {
        ...state,
        phase: "plus_executed",
        message: "",
        receipt: { id: approvalId, plan: "Plus" },
      },
    };
  }
  if (state.phase === "approved_basic") {
    return {
      ok: true,
      next: {
        ...state,
        phase: "basic_executed",
        message: "",
        receipt: { id: approvalId, plan: "Basic" },
      },
    };
  }
  return { ok: false, message: "Refused: proposal is not approved for execution." };
}

function renderEvidence(root, ticket) {
  const ev = el("div", "card evidence");
  ev.appendChild(el("h2", null, "Handbook evidence (fixture)"));
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>Passage</th><th>Quote</th></tr>";
  for (const row of ticket.evidence) {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    const code = document.createElement("code");
    code.textContent = row.passage_id;
    td1.append(code, document.createElement("br"), document.createTextNode(row.title));
    const td2 = document.createElement("td");
    td2.textContent = row.quote;
    tr.append(td1, td2);
    table.appendChild(tr);
  }
  ev.appendChild(table);
  root.appendChild(ev);
}

function renderT0044(root, ticket, state, store) {
  root.replaceChildren();
  root.append(
    el("h1", null, `${ticket.id}: ${ticket.subject}`),
    el("p", "meta", `Customer ${ticket.customer_id} · fictional web form ticket`)
  );
  const card = el("div", "card");
  card.appendChild(el("p", null, ticket.body));
  root.appendChild(card);
  renderEvidence(root, ticket);

  const prop = el("div", "card");
  prop.appendChild(el("h2", null, "Proposed action"));
  const pill = el("span", "pill");
  pill.textContent = state.phase.replace(/_/g, " ");
  prop.appendChild(pill);
  const p = ticket.initial_proposal;
  const plan = planFor(state);
  const actionLine = el("p");
  actionLine.innerHTML = `<strong>${p.action}</strong> → plan <code>${plan}</code>`;
  prop.appendChild(actionLine);
  const rat = el("p", "meta");
  rat.textContent = `Cites ${p.citation} · ${rationaleFor(ticket, state)}`;
  prop.appendChild(rat);
  root.appendChild(prop);

  if (state.message) root.appendChild(el("p", "err", state.message));

  const toolbar = el("div", "toolbar");

  if (state.phase === "proposed") {
    const approve = el("button", "primary", "Approve original Plus proposal");
    approve.addEventListener("click", () => {
      const next = { ...state, phase: "approved_plus", approvalId: "A-fixture-001", message: "" };
      store.tickets[ticket.id] = next;
      writeStore(store);
      render(root, ticket, next, store);
    });
    toolbar.appendChild(approve);
  }

  if (state.phase === "approved_plus") {
    const runPlus = el("button", "primary", "Run mock receipt (Plus, once)");
    runPlus.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-001");
      if (!r.ok) {
        store.tickets[ticket.id] = { ...state, message: r.message };
      } else {
        store.tickets[ticket.id] = r.next;
      }
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    const revise = el("button", null, "Revise to Basic (voids approval)");
    revise.addEventListener("click", () => {
      store.tickets[ticket.id] = {
        ...state,
        phase: "revised_basic",
        supersededApproval: state.approvalId,
        approvalId: undefined,
        plan: "Basic",
        message: "Revision voids A-fixture-001. Approve the Basic draft before running.",
      };
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    toolbar.append(runPlus, revise);
  }

  if (state.phase === "revised_basic") {
    const stale = el("button", null, "Try old approval A-fixture-001 (should fail)");
    stale.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-001");
      store.tickets[ticket.id] = { ...state, message: r.message || "Refused." };
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    const approve2 = el("button", "primary", "Approve revised Basic proposal");
    approve2.addEventListener("click", () => {
      store.tickets[ticket.id] = {
        ...state,
        phase: "approved_basic",
        approvalId: "A-fixture-002",
        message: "",
      };
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    toolbar.append(stale, approve2);
  }

  if (state.phase === "approved_basic") {
    const run = el("button", "primary", "Run mock receipt (Basic, once)");
    run.addEventListener("click", () => {
      const r = executeApproval(state, "A-fixture-002");
      if (!r.ok) {
        store.tickets[ticket.id] = { ...state, message: r.message };
      } else {
        store.tickets[ticket.id] = r.next;
      }
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    toolbar.appendChild(run);
  }

  if (state.phase === "plus_executed" || state.phase === "basic_executed") {
    const replay = el("button", null, "Run again (should not add receipt)");
    replay.addEventListener("click", () => {
      store.tickets[ticket.id] = {
        ...state,
        message: "Ignored duplicate run — mock not called again (simulated).",
      };
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    toolbar.appendChild(replay);
    const receipt = el("div", "card receipt");
    const rc = state.receipt;
    receipt.textContent =
      `MOCK RECEIPT · change_plan · customer ${ticket.customer_id} · plan=${rc.plan} · idempotency=${rc.id}`;
    root.appendChild(receipt);
  }

  const reset = el("button", null, "Reset this ticket");
  reset.addEventListener("click", () => {
    resetTicket(store, ticket.id);
    render(root, ticket, store.tickets[ticket.id], store);
  });
  toolbar.appendChild(reset);
  root.appendChild(toolbar);
}

function renderT0078(root, ticket, state, store) {
  root.replaceChildren();
  root.append(
    el("h1", null, `${ticket.id}: ${ticket.subject}`),
    el("p", "meta", `Customer ${ticket.customer_id} · injection-style fixture`)
  );
  const card = el("div", "card");
  card.appendChild(el("p", null, ticket.body));
  root.appendChild(card);
  renderEvidence(root, ticket);

  const wrong = el("div", "card");
  wrong.appendChild(el("h2", null, "Wrong proposal (reject me)"));
  const pill = el("span", "pill bad");
  pill.textContent = state.phase;
  wrong.appendChild(pill);
  const wp = ticket.wrong_proposal;
  const line = el("p");
  line.innerHTML = `<strong>${wp.action}</strong> ${wp.params.amount_eur} EUR`;
  wrong.appendChild(line);
  root.appendChild(wrong);

  if (state.message) root.appendChild(el("p", "err", state.message));

  const toolbar = el("div", "toolbar");
  if (state.phase === "proposed") {
    const reason = document.createElement("input");
    reason.placeholder = "Rejection reason (required)";
    reason.value = state.rejectReason || "";
    reason.setAttribute("aria-label", "Rejection reason");
    const reject = el("button", "primary", "Reject wrong proposal");
    reject.addEventListener("click", () => {
      const rr = reason.value.trim() || "Ticket text is not an approval.";
      store.tickets[ticket.id] = {
        ...state,
        phase: "rejected",
        rejectReason: rr,
        message: `Rejected — ${rr}`,
      };
      writeStore(store);
      render(root, ticket, store.tickets[ticket.id], store);
    });
    toolbar.append(reason, reject);
  }
  if (state.phase === "rejected") {
    const ok = el("div", "card");
    ok.appendChild(el("h2", null, "Replacement proposal"));
    const okPill = el("span", "pill ok");
    okPill.textContent = ticket.expected_after_reject.action;
    ok.appendChild(okPill);
    const rat = el("p");
    rat.textContent = ticket.expected_after_reject.rationale;
    ok.appendChild(rat);
    root.appendChild(ok);
  }
  const reset = el("button", null, "Reset this ticket");
  reset.addEventListener("click", () => {
    resetTicket(store, ticket.id);
    render(root, ticket, store.tickets[ticket.id], store);
  });
  toolbar.appendChild(reset);
  root.appendChild(toolbar);
}

function render(root, ticket, state, store) {
  if (ticket.id === "T-0078") renderT0078(root, ticket, state, store);
  else renderT0044(root, ticket, state, store);
}

async function main() {
  const nav = document.getElementById("nav");
  const root = document.getElementById("content");
  try {
    const data = await loadFixtures();
    let store = readStore(data.tickets[0].id);
    const seed = new URLSearchParams(window.location.search).get("seed");
    if (seed) {
      try {
        store = JSON.parse(decodeURIComponent(seed));
        writeStore(store);
      } catch (_) {
        /* ignore bad seed */
      }
    }
    for (const t of data.tickets) {
      const b = el("button", null, t.id);
      b.addEventListener("click", () => {
        store = setActive(store, t.id);
        const ticket = data.tickets.find((x) => x.id === t.id);
        render(root, ticket, ticketState(store, t.id), store);
      });
      nav.appendChild(b);
    }
    const active = data.tickets.find((x) => x.id === store.activeTicketId) || data.tickets[0];
    render(root, active, ticketState(store, active.id), store);
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "r" && ev.ctrlKey) {
        ev.preventDefault();
        resetTicket(store, store.activeTicketId);
        const ticket = data.tickets.find((x) => x.id === store.activeTicketId);
        render(root, ticket, ticketState(store, ticket.id), store);
      }
    });
  } catch (e) {
    root.replaceChildren(el("p", "err", String(e)));
  }
}

main();
