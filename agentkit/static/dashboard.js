// Staff dashboard: grouped tabs, one JSON endpoint per tab (/admin/api/dashboard/<tab>), tiles, tables and
// single-hue SVG bar charts. Every chart has a table view and a tooltip per bar.
(() => {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const UI_ONLY = new Set(["review"]);  // existing staff panels, no dashboard endpoint
  let allowed = null;

  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) { if (k === "text") e.textContent = v; else e.setAttribute(k, v); }
    kids.forEach((k) => k != null && e.append(k));
    return e;
  };
  const fmt = (v) => v == null ? "—" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(v < 1 ? 3 : 2)) : String(v);
  const pct = (v) => v == null ? "—" : (v > 0 && v < 0.1 ? (v * 100).toFixed(1) : Math.round(v * 100)) + "%";
  const when = (ts) => new Date(ts * 1000).toLocaleString();

  function tiles(items) {
    const box = el("div", {class: "tiles"});
    for (const [k, v] of items) box.append(el("div", {class: "tile"}, el("div", {class: "k", text: k}), el("div", {class: "v", text: v})));
    return box;
  }
  function table(rows, cols, caption) {
    const t = el("table", {class: "tbl"});
    if (caption) t.append(el("caption", {class: "sr-only", text: caption}));
    const hr = el("tr"); cols.forEach(([, label]) => hr.append(el("th", {scope: "col", text: label}))); t.append(el("thead", {}, hr));
    const tb = el("tbody");
    for (const r of rows) { const tr = el("tr"); cols.forEach(([key, , f]) => tr.append(el("td", {text: f ? f(r[key], r) : fmt(r[key])}))); tb.append(tr); }
    if (!rows.length) { const tr = el("tr"); tr.append(el("td", {colspan: cols.length, text: "Nothing yet."})); tb.append(tr); }
    t.append(tb);
    return el("div", {class: "scroll"}, t);
  }
  // One measure, one hue: bars share var(--accent); value labels stay in text colour.
  function bars(title, points, valueFmt = fmt) {
    const fig = el("figure", {class: "chart"}); fig.append(el("h3", {text: title}));
    if (!points.length) { fig.append(el("p", {class: "note", text: "No data in this period."})); return fig; }
    const W = 640, H = 140, pad = 28, max = Math.max(...points.map((p) => p[1] || 0), 1e-9);  // nulls skipped below
    const bw = Math.min(36, Math.max(4, (W - pad * 2) / points.length - 8));  // thin marks
    const step = (W - pad * 2) / points.length;
    const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H + 24}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", title);
    const axis = document.createElementNS(ns, "line");
    Object.entries({x1: pad, x2: W - pad, y1: H, y2: H, class: "axis"}).forEach(([k, v]) => axis.setAttribute(k, v)); svg.append(axis);
    points.forEach(([label, v], i) => {
      if (v == null) return;  // no data that day: leave the slot empty rather than drawing a zero
      const h = Math.max(1, ((v || 0) / max) * (H - 20)), x = pad + i * step + (step - bw) / 2, y = H - h;
      const r = document.createElementNS(ns, "path");  // 4px rounded data-end, square at the baseline
      const rr = Math.min(4, bw / 2, h);
      r.setAttribute("d", `M${x},${H}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + bw - rr}Q${x + bw},${y} ${x + bw},${y + rr}V${H}Z`);
      r.setAttribute("class", "bar-mark"); r.setAttribute("tabindex", "0");
      const tip = document.createElementNS(ns, "title"); tip.textContent = `${label}: ${valueFmt(v)}`; r.append(tip);
      svg.append(r);
      const isDate = /^\d{4}-\d{2}-\d{2}$/.test(String(label));
      if (points.length <= (isDate ? 7 : 12) || i % Math.ceil(points.length / 7) === 0) {
        const t = document.createElementNS(ns, "text"); t.setAttribute("x", x + bw / 2); t.setAttribute("y", H + 16);
        const L = isDate ? String(label).slice(5) : String(label);
        t.setAttribute("text-anchor", "middle"); t.textContent = L.length > 14 ? L.slice(0, 13) + "…" : L; svg.append(t);
      }
    });
    fig.append(svg);
    const det = el("details", {}, el("summary", {text: "Table view"}),
      table(points.map(([l, v]) => ({l, v})), [["l", "Item"], ["v", "Value", valueFmt]], title));
    fig.append(det);
    return fig;
  }
  const entries = (obj) => Object.entries(obj || {});

  const render = {
    today(d) {
      const b = d.budget || {};
      return [tiles([["Questions today", fmt(d.questions)], ["Answered", pct(d.deflection)], ["Handed off", pct(d.handoff_rate)],
        ["Satisfaction", pct(d.satisfaction)], ["Open tickets", fmt(d.open_tickets)], ["Overdue tickets", fmt(d.overdue_tickets)],
        ["Model", d.maintenance ? "paused" : d.llm], ["Spend today", b.today != null ? "$" + fmt(b.today) : "—"],
        ["Budget used", pct(b.fraction)], ["Index passages", fmt(d.index.chunks)]]),
        bars("Questions by outcome", entries(d.modes)), bars("Questions by language", entries(d.languages))];
    },
    trends(d) {
      const s = d.series;
      return [bars("Questions per day", s.map((r) => [r.day, r.questions])),
        bars("Answered share per day", s.map((r) => [r.day, r.deflection]), pct),
        bars("Satisfaction per day", s.map((r) => [r.day, r.satisfaction]), pct),
        bars("Arabic and Franco-Arabic share", s.map((r) => [r.day, r.arabic_share]), pct)];
    },
    conversations(d, ctx) {
      const f = el("div", {class: "filters"});
      const sel = (id, label, opts) => {
        const s = el("select", {id: "f-" + id}); s.append(el("option", {value: "", text: "All"}));
        opts.forEach((o) => s.append(el("option", {value: o, text: o}))); s.value = ctx[id] || "";
        s.addEventListener("change", () => { ctx[id] = s.value; show("conversations"); });
        f.append(el("label", {for: "f-" + id, text: label}), s);
      };
      sel("mode", "Outcome", d.modes); sel("agent", "Agent", d.agents); sel("lang", "Language", ["en", "ar", "arabizi"]);
      return [f, table(d.rows, [["ts", "When", when], ["question", "Question (redacted)"], ["mode", "Outcome"], ["agent", "Agent"],
        ["lang", "Lang"], ["sources", "Sources", (v) => v.join("; ")], ["ms", "ms"],
        ["trace", "Steps", (v) => v.map((s) => s.step).join(" → ")]], "Recent questions")];
    },
    gaps(d) {
      const out = [tiles([["Signals", fmt(d.signals)], ["Gaps shown", fmt(d.clusters.length)], ["Hidden (fewer than " + (d.min_users || 3) + " people)", fmt(d.hidden_small)]])];
      for (const c of d.clusters) {
        const box = el("article", {class: "chart"});
        box.append(el("h3", {text: `${c.label || c.terms.join(" · ")} — ${c.people} people, ${c.size} questions`}));
        box.append(el("p", {}, el("span", {class: "badge", text: c.kind}), " Owner: " + c.owner +
          (c.nearest ? ` · nearest page: ${c.nearest.title} › ${c.nearest.section} (coverage ${pct(c.nearest.coverage)})` : "")));
        const ul = el("ul"); c.examples.forEach((q) => ul.append(el("li", {text: q, dir: "auto"}))); box.append(ul);
        const act = (label, path, done) => {
          const b = el("button", {type: "button", class: "ghost", text: label, "aria-label": `${label}: ${c.label}`});
          b.addEventListener("click", async () => { try { const r = await api(path, {method: "POST"}); say(done(r)); } catch (e) { say("Error: " + e.message); } });
          return b;
        };
        const draft = el("button", {type: "button", class: "ghost", text: "Draft notice", "aria-label": "Draft notice: " + c.label});
        draft.addEventListener("click", () => { activate(document.getElementById("t-review")); const t = document.getElementById("n-title"); t.value = c.label; document.getElementById("n-body").value = "Answer for: " + c.examples.join(" / "); t.focus(); });
        box.append(el("p", {class: "row"}, draft, " ", act("Create page task", `/admin/api/gaps/${c.id}/ticket`, (r) => "Task created: " + r.ticket),
          " ", act("Add to test candidates", `/admin/api/gaps/${c.id}/candidate`, (r) => `Added ${r.appended} questions to ${r.file}`)));
        out.push(box);
      }
      return out;
    },
    evaluations(d) {
      const rows = entries(d.reports).map(([name, r]) => ({name, ...r}));
      const out = [table(rows, [["name", "Set"], ["passed", "Passed"], ["total", "Total"], ["recall_at_k", "Recall@k"], ["mrr", "MRR"],
        ["live", "Live model", (v) => v ? "yes" : "offline"], ["updated", "Run", (v) => v ? when(v) : "—"]], "Evaluation reports")];
      for (const r of rows) if (r.failed && r.failed.length) out.push(el("h3", {text: `Failing in ${r.name}`}),
        table(r.failed, [["id", "Id"], ["question", "Question"], ["failed", "Failed checks", (v) => v.join(", ")]]));
      if (entries(d.critic_verdicts).length) out.push(bars("Critic verdicts", entries(d.critic_verdicts)));
      return out;
    },
    sources(d) {
      return [tiles([["Sources", fmt(d.rows.length)], ["Waiting for review", fmt(d.pending_review)],
        ["Stale", fmt(d.rows.filter((r) => r.stale).length)], ["With [VERIFY]", fmt(d.rows.filter((r) => r.verify).length)]]),
        table(d.rows, [["origin", "Source"], ["owner", "Owner"], ["status", "Status"], ["chunks", "Passages"], ["age_days", "Days since ingest"],
          ["stale", "Stale", (v) => v ? "yes" : ""], ["verify", "[VERIFY]", (v) => v ? "yes" : ""]], "Indexed sources"),
        el("h3", {text: "Notices"}), table(d.notices, [["title", "Title"], ["valid_from", "From"], ["valid_to", "Until"], ["lang", "Lang"]])];
    },
    ocr(d) {
      return [tiles([["Pages to correct", fmt(d.correction_queue.length)], ["Threshold", fmt(d.threshold)]]),
        bars("OCR passages by confidence", entries(d.confidence_histogram)), bars("Passages by extraction method", entries(d.methods))];
    },
    tickets(d) {
      const load = entries(d.workload).map(([q, w]) => ({q, ...w}));
      return [tiles([["Tickets", fmt(d.rows.length)], ["Overdue", fmt(d.rows.filter((r) => r.overdue).length)], ["SLA (hours)", fmt(d.sla_hours)]]),
        el("h3", {text: "Workload by subject queue"}),
        table(load, [["q", "Queue"], ["open", "Open"], ["closed", "Closed"], ["oldest_open_hours", "Oldest open (h)"]]),
        el("h3", {text: "Tickets"}), ticketTable(d.rows)];
    },
    librarians(d) {
      return [table(d.queues.map((q) => ({...q, open: (q.workload || {}).open || 0})),
        [["subject", "Subject queue"], ["contact", "Contact"], ["booking_url", "Booking"], ["open", "Open tickets"]], "Subject queues"),
        el("p", {text: d.rota && d.rota.by ? `Rota approved by ${d.rota.by} on ${d.rota.date}` : "No staff rota recorded in signoff.json yet."})];
    },
    costs(d) {
      const b = d.budget || {};
      const perAnswer = d.answered ? (d.by_model.reduce((s, r) => s + r.cost, 0) / d.answered) : null;
      return [tiles([["Today", "$" + fmt(b.today)], ["This month", "$" + fmt(b.month)], ["Daily cap", b.daily_cap ? "$" + b.daily_cap : "none"],
        ["Monthly cap", b.monthly_cap ? "$" + b.monthly_cap : "none"], ["Budget used", pct(b.fraction)],
        ["Month forecast", "$" + fmt(b.forecast_month)], ["Per answered question", perAnswer == null ? "—" : "$" + perAnswer.toFixed(4)],
        ["Cache hits", fmt(d.cache_hits)], ["Saved by cache (est.)", "$" + fmt(d.saved_usd_estimate)],
        ["Optional calls", b.brake ? "paused (soft brake)" : "on"]]),
        bars("Spend per day (USD)", d.daily.map((r) => [r.day, r.cost]), (v) => "$" + fmt(v)),
        bars("Spend by plugin (USD)", d.by_plugin.map((r) => [r.plugin, r.cost]), (v) => "$" + fmt(v)),
        bars("Spend by purpose (USD)", d.by_purpose.map((r) => [r.purpose, r.cost]), (v) => "$" + fmt(v)),
        el("h3", {text: "By agent"}), table(d.by_agent, [["agent", "Agent"], ["calls", "Calls"], ["input", "Input tokens"], ["output", "Output tokens"], ["cost", "USD"]]),
        el("h3", {text: "By model"}), table(d.by_model, [["model", "Model"], ["calls", "Calls"], ["input", "Input tokens"], ["output", "Output tokens"], ["cost", "USD"]])];
    },
    performance(d) {
      const rows = entries(d.steps).map(([step, v]) => ({step, ...v}));
      return [tiles([["Answers measured", fmt(d.total_ms.n)], ["p50 total (ms)", fmt(d.total_ms.p50)], ["p95 total (ms)", fmt(d.total_ms.p95)]]),
        bars("p95 by pipeline step (ms)", rows.map((r) => [r.step, r.p95])),
        table(rows, [["step", "Step"], ["n", "Count"], ["p50", "p50 ms"], ["p95", "p95 ms"], ["mean", "Mean ms"]], "Pipeline steps"),
        table(entries(d.counters).map(([k, v]) => ({k, v})), [["k", "Counter"], ["v", "Value"]], "Counters")];
    },
    system(d) {
      const p = d.preflight;
      const toggle = el("button", {type: "button", "aria-pressed": String(!!d.maintenance), text: d.maintenance ? "Resume answers" : "Pause answers (kill switch)"});
      toggle.addEventListener("click", async () => {
        if (!d.maintenance && !confirm("Pause all generated answers? Every question will go to a librarian.")) return;
        try { await api("/admin/api/maintenance", {method: "POST", body: JSON.stringify({on: !d.maintenance})}); show("system"); } catch (e) { say("Error: " + e.message); }
      });
      return [el("p", {class: "row"}, toggle),
        tiles([["Preflight", p.ok ? "ready" : `${p.blocking.length} blocking`], ["Index passages", fmt(d.index.chunks)], ["Snapshots", fmt(d.snapshots.length)]]),
        el("h3", {text: "Blocking"}), table(p.blocking.map((x) => ({x})), [["x", "Problem"]]),
        el("h3", {text: "Warnings"}), table(p.warnings.map((x) => ({x})), [["x", "Warning"]]),
        el("h3", {text: "Connectors"}), table(entries(d.connectors).map(([k, v]) => ({k, v})), [["k", "Connector"], ["v", "Configured", (v) => v ? "yes" : "no"]]),
        el("h3", {text: "Snapshots"}), table(d.snapshots.map((x) => ({x})), [["x", "Snapshot (restore with agentkit rollback)"]]),
        ...(d.local_models && d.local_models.length ? [el("h3", {text: "Local models"}), table(d.local_models,
          [["name", "Name"], ["model", "Model"], ["roles", "Roles", (v) => v.join(", ")], ["langs", "Languages", (v) => v.join(", ")],
           ["requests", "Requests"], ["failovers", "Failovers"]], "Local model servers")] : [])];
    },
    security(d) {
      return [tiles([["Rate-limited requests", fmt(d.rate_limited)], ["Quarantined passages", fmt(d.quarantined_chunks)],
        ["Data requests", fmt(d.data_requests.length)]]),
        bars("Questions blocked by guard", entries(d.blocked_by_guard)),
        el("h3", {text: "Staff actions"}), table(d.admin_actions, [["ts", "When", when], ["actor", "Who (pseudonym)"], ["action", "Action"], ["detail", "Detail"]]),
        el("h3", {text: "Data export and erasure"}), table(d.data_requests, [["ts", "When", when], ["action", "Request"], ["detail", "Detail"]])];
    },
  };

  function ticketTable(rows) {
    const wrap = table(rows, [["id", "Id"], ["kind", "Kind"], ["routed_to", "Queue"], ["status", "Status"], ["age_hours", "Age (h)"],
      ["overdue", "Overdue", (v) => v ? "yes" : ""], ["question", "Question"]], "Tickets");
    wrap.querySelectorAll("tbody tr").forEach((tr, i) => {
      const r = rows[i]; if (!r) return;
      const s = el("select", {"aria-label": "Set status of ticket " + r.id});
      ["queued", "sent", "answered", "closed", "escalated"].forEach((o) => s.append(el("option", {value: o, text: o})));
      s.value = r.status;
      s.addEventListener("change", async () => { try { await api("/admin/api/tickets/" + r.id, {method: "POST", body: JSON.stringify({status: s.value})}); say("Ticket updated."); } catch (e) { say("Error: " + e.message); } });
      tr.children[3].textContent = ""; tr.children[3].append(s);
    });
    return wrap;
  }

  const ctx = {};
  async function show(name) {
    const box = document.getElementById("d-" + name);
    if (!box || UI_ONLY.has(name)) return;
    box.setAttribute("aria-busy", "true");
    try {
      const q = new URLSearchParams({days: document.getElementById("days").value, ...Object.fromEntries(Object.entries(ctx).filter(([, v]) => v))});
      const res = await api(`/admin/api/dashboard/${name}?${q}`);
      box.replaceChildren(...render[name](res.data, ctx));
    } catch (e) {
      box.replaceChildren(el("p", {class: "note", text: "Not available: " + e.message}));
    } finally { box.removeAttribute("aria-busy"); }
  }
  function activate(tab, focus = true) {
    tabs.forEach((t) => { const on = t === tab; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on; });
    if (focus) tab.focus();
    try { localStorage.setItem("agentkit-tab", tab.dataset.tab); } catch (e) { /* storage blocked */ }
    show(tab.dataset.tab);
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => activate(t));
    t.addEventListener("keydown", (ev) => {
      const vis = tabs.filter((x) => !x.hidden), j = vis.indexOf(t);
      const rtl = document.dir === "rtl";
      const next = {ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1, ArrowDown: 1, ArrowUp: -1}[ev.key];
      if (next) { ev.preventDefault(); activate(vis[(j + next + vis.length) % vis.length]); }
      if (ev.key === "Home") { ev.preventDefault(); activate(vis[0]); }
      if (ev.key === "End") { ev.preventDefault(); activate(vis[vis.length - 1]); }
    });
  });
  document.getElementById("days").addEventListener("change", () => { const t = tabs.find((x) => x.getAttribute("aria-selected") === "true"); if (t) show(t.dataset.tab); });

  async function init() {
    try {
      const r = await api("/admin/api/role");
      allowed = new Set([...r.tabs, ...(r.role === "staff" || r.role === "admin" ? ["review"] : [])]);
      document.getElementById("role").textContent = r.role ? "Signed in as " + r.role : "Not signed in";
    } catch (e) { allowed = new Set(); }
    tabs.forEach((t) => { t.hidden = allowed.size > 0 && !allowed.has(t.dataset.tab); });
    document.querySelectorAll(".tg").forEach((g) => { g.hidden = [...g.querySelectorAll('[role="tab"]')].every((t) => t.hidden); });
    let saved = null; try { saved = localStorage.getItem("agentkit-tab"); } catch (e) { /* storage blocked */ }
    const first = tabs.find((t) => t.dataset.tab === saved && !t.hidden) || tabs.find((t) => !t.hidden) || tabs[0];
    activate(first, false);
  }
  document.getElementById("key").addEventListener("change", () => { init(); load(); loadExtra(); say(""); });
  init();
})();
