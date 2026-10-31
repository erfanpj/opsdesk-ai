const STORAGE_KEY = "opsdesk-fixture-v1";

/** @typedef {{phase:string, ticketId:string, plan:string, approvalId?:string, receiptCount:number, staleApproval?:string, message?:string}} SimState */

async function loadFixtures() {
  const res = await fetch("fixtures.json");
  if (!res.ok) throw new Error("fixtures.json failed to load");
  return res.json();
}

function defaultState(ticketId) {
  return { phase: "proposed", ticketId, plan: "Plus", receiptCount: 0 };
}

/** @returns {SimState} */
function readState(fallbackTicket) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (_) {
    /* ignore */
  }
  return defaultState(fallbackTicket);
}

/** @param {SimState} s */
function writeState(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}

function resetState(ticketId) {
  localStorage.removeItem(STORAGE_KEY);
  return defaultState(ticketId);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function renderT0044(root, ticket, state) {
  root.replaceChildren();
  root.append(
    el("h1", null, `${ticket.id}: ${ticket.subject}`),
    el("p", "meta", `Customer ${ticket.customer_id} · fictional web form ticket`)
  );
  const card = el("div", "card");
  card.append(el("p", null, ticket.body));
  root.appendChild(card);

  const ev = el("div", "card evidence");
  ev.appendChild(el("h2", null, "Handbook evidence (fixture)"));
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>Passage</th><th>Quote</th></tr>";
  for (const row of ticket.evidence) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${row.passage_id}</code><br>${row.title}</td><td>${row.quote}</td>`;
    table.appendChild(tr);
  }
  ev.appendChild(table);
  root.appendChild(ev);

  const prop = el("div", "card");
  const p = ticket.initial_proposal;
  const plan = state.phase === "revised" || state.phase === "approved_v2" || state.phase === "executed" ? "Basic" : state.plan;
  prop.appendChild(el("h2", null, "Proposed action"));
  prop.innerHTML += `<p><span class="pill">${state.phase.replace(/_/g, " ")}</span></p>`;
  prop.innerHTML += `<p><strong>${p.action}</strong> → plan <code>${plan}</code></p>`;
  prop.innerHTML += `<p class="meta">Cites ${p.citation} · ${p.rationale}</p>`;
  root.appendChild(prop);

  const toolbar = el("div", "toolbar");
  const msg = state.message ? el("p", "err", state.message) : null;
  if (msg) root.appendChild(msg);

  if (state.phase === "proposed") {
    const approve = el("button", "primary", "Approve exact proposal");
    approve.addEventListener("click", () => {
      writeState({ ...state, phase: "approved_v1", approvalId: "A-fixture-001", message: "" });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(approve);
  }
  if (state.phase === "approved_v1") {
    const revise = el("button", null, "Revise to Basic (voids approval)");
    revise.addEventListener("click", () => {
      writeState({
        ...state,
        phase: "revised",
        staleApproval: state.approvalId,
        approvalId: undefined,
        plan: "Basic",
        message: "Previous approval A-fixture-001 is void for execution.",
      });
      render(root, ticket, readState(ticket.id));
    });
    const runStale = el("button", null, "Try old approval (should fail)");
    runStale.addEventListener("click", () => {
      writeState({ ...state, message: "Refused: approval void after revision (simulated)." });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(revise, runStale);
  }
  if (state.phase === "revised") {
    const approve2 = el("button", "primary", "Approve revised proposal");
    approve2.addEventListener("click", () => {
      writeState({ ...state, phase: "approved_v2", approvalId: "A-fixture-002", message: "" });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(approve2);
  }
  if (state.phase === "approved_v2") {
    const run = el("button", "primary", "Run simulated receipt (once)");
    run.addEventListener("click", () => {
      writeState({
        ...state,
        phase: "executed",
        receiptCount: 1,
        message: "",
      });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(run);
  }
  if (state.phase === "executed") {
    const replay = el("button", null, "Run again (should not add receipt)");
    replay.addEventListener("click", () => {
      writeState({ ...state, message: "Ignored duplicate run — mock not called again (simulated)." });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(replay);
    const receipt = el("div", "card receipt");
    receipt.textContent = `MOCK RECEIPT #1 · change_plan · customer ${ticket.customer_id} · plan=${state.plan} · idempotency=A-fixture-002`;
    root.appendChild(receipt);
  }

  const reset = el("button", null, "Reset fixture");
  reset.addEventListener("click", () => {
    writeState(resetState(ticket.id));
    render(root, ticket, readState(ticket.id));
  });
  toolbar.append(reset);
  root.appendChild(toolbar);
}

function renderT0078(root, ticket, state) {
  root.replaceChildren();
  root.append(
    el("h1", null, `${ticket.id}: ${ticket.subject}`),
    el("p", "meta", `Customer ${ticket.customer_id} · injection-style fixture`)
  );
  const card = el("div", "card");
  card.append(el("p", null, ticket.body));
  root.appendChild(card);

  const wrong = el("div", "card");
  wrong.innerHTML = `<h2>Wrong proposal (reject me)</h2><p><span class="pill bad">${state.phase}</span></p>`;
  wrong.innerHTML += `<p><strong>${ticket.wrong_proposal.action}</strong> ${ticket.wrong_proposal.params.amount_eur} EUR</p>`;
  root.appendChild(wrong);

  if (state.message) root.appendChild(el("p", "err", state.message));

  const toolbar = el("div", "toolbar");
  if (state.phase === "proposed") {
    const reject = el("button", "primary", "Reject wrong proposal");
    reject.addEventListener("click", () => {
      writeState({ ...state, phase: "rejected", message: "Rejected — ticket text is not an approval." });
      render(root, ticket, readState(ticket.id));
    });
    toolbar.append(reject);
  }
  if (state.phase === "rejected") {
    const ok = el("div", "card");
    ok.innerHTML = `<h2>Replacement proposal</h2><p><span class="pill ok">escalate</span></p><p>${ticket.expected_after_reject.rationale}</p>`;
    root.appendChild(ok);
  }
  const reset = el("button", null, "Reset fixture");
  reset.addEventListener("click", () => {
    writeState(resetState(ticket.id));
    render(root, ticket, readState(ticket.id));
  });
  toolbar.append(reset);
  root.appendChild(toolbar);
}

function render(root, ticket, state) {
  if (ticket.id === "T-0078") renderT0078(root, ticket, state);
  else renderT0044(root, ticket, state);
}

async function main() {
  const nav = document.getElementById("nav");
  const root = document.getElementById("content");
  try {
    const data = await loadFixtures();
    for (const t of data.tickets) {
      const b = el("button", null, t.id);
      b.addEventListener("click", () => {
        writeState(defaultState(t.id));
        const ticket = data.tickets.find((x) => x.id === t.id);
        render(root, ticket, readState(t.id));
      });
      nav.appendChild(b);
    }
    const first = data.tickets[0];
    render(root, first, readState(first.id));
  } catch (e) {
    root.replaceChildren(el("p", "err", String(e)));
  }
}

main();
