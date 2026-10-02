// Staff page: upload documents (base64 JSON, no multipart dependency), watch jobs, approve changed sources.
const $ = (id) => document.getElementById(id);
const say = (msg) => { $("status").textContent = msg; };
function headers() { const h = {"Content-Type": "application/json"}; const k = $("key").value; if (k) h["X-API-Key"] = k; return h; }
async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: headers()});
  if (!r.ok) throw new Error(r.status + " " + ((await r.json().catch(() => ({}))).detail || r.statusText));
  return r.json();
}
function b64(file) {
  return new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result.split(",")[1]); fr.onerror = rej; fr.readAsDataURL(file); });
}
$("up").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = $("file").files[0]; if (!f) return;
  try {
    say("Uploading…");
    const {job} = await api("/admin/api/upload", {method: "POST", body: JSON.stringify({
      filename: f.name, content_b64: await b64(f), title: $("title").value, url: $("url").value, access: $("access").value})});
    for (let i = 0; i < 120; i++) {
      const j = await api("/admin/api/jobs/" + job);
      if (j.status === "done") { const r = j.report[0] || {}; say(r.error ? "Not indexed: " + r.error : `Indexed: ${r.chunks} passages (${r.status}).`); break; }
      if (j.status === "failed") { say("Failed: " + j.error); break; }
      await new Promise((r) => setTimeout(r, 1000));
    }
    load();
  } catch (e) { say("Error: " + e.message); }
});
async function load() {
  try {
    const pending = await api("/admin/api/review"), ul = $("review"); ul.textContent = "";
    for (const origin of Object.keys(pending)) {
      const li = document.createElement("li"); li.textContent = origin + " ";
      const b = document.createElement("button"); b.type = "button"; b.textContent = "Approve";
      b.setAttribute("aria-label", "Approve " + origin);
      b.addEventListener("click", async () => { await api("/admin/api/approve", {method: "POST", body: JSON.stringify({origin})}); load(); });
      li.append(b); ul.append(li);
    }
    if (!ul.children.length) ul.append(Object.assign(document.createElement("li"), {textContent: "Nothing to review."}));
  } catch (e) { say("Sign in or enter the admin key to see staff data (" + e.message + ")."); }
}
function li(text) { const x = document.createElement("li"); x.textContent = text; return x; }
async function loadExtra() {
  try {
    const ns = await api("/admin/api/notices"), ul = $("notices"); ul.textContent = "";
    for (const n of ns) {
      const x = li(`${n.title} (${n.valid_from || "now"} → ${n.valid_to || "no end"}) `);
      const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = "Remove";
      b.setAttribute("aria-label", "Remove notice " + n.title);
      b.addEventListener("click", async () => { await api("/admin/api/notices/" + n.id, {method: "DELETE"}); loadExtra(); });
      x.append(b); ul.append(x);
    }
    const tix = await api("/admin/api/tickets"), tl = $("tickets"); tl.textContent = "";
    for (const t of tix) tl.append(li(`${t.id} · ${t.kind} · ${t.status} · ${t.routed_to}: ${t.question}`));
    if (!tix.length) tl.append(li("None yet."));
    const fb = await api("/admin/api/feedback");
    $("fbsum").textContent = `${fb.up} helpful, ${fb.down} not helpful, ${fb.unanswered.length} unanswered. Run "agentkit feedback-report" to add them to the test set.`;
    const cl = $("cands"); cl.textContent = "";
    for (const c of await api("/admin/api/eval-candidates")) cl.append(li(`${c.question} (${c.seen_as || "thumbs down"})`));
    const cors = await api("/admin/api/corrections"), box = $("cors"); box.textContent = "";
    if (!cors.length) box.textContent = "No low-confidence pages.";
    cors.forEach((c, i) => {
      const lab = document.createElement("label"); lab.htmlFor = "cor" + i;
      lab.textContent = `${c.origin} — page ${c.page} (confidence ${c.confidence})`;
      const ta = document.createElement("textarea"); ta.id = "cor" + i; ta.rows = 6; ta.value = c.text; ta.dir = "auto";
      const b = document.createElement("button"); b.type = "button"; b.textContent = "Save correction and re-index";
      b.addEventListener("click", async () => { await api("/admin/api/corrections", {method: "POST", body: JSON.stringify({origin: c.origin, page: c.page, text: ta.value})}); say("Correction saved; re-indexing."); });
      box.append(lab, ta, b);
    });
  } catch (e) { /* not signed in yet */ }
}
$("nf").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/admin/api/notices", {method: "POST", body: JSON.stringify({title: $("n-title").value, body: $("n-body").value,
      valid_from: $("n-from").value, valid_to: $("n-to").value, lang: $("n-lang").value})});
    $("nf").reset(); say("Notice added."); loadExtra();
  } catch (e) { say("Error: " + e.message); }
});
$("refresh").addEventListener("click", () => { load(); loadExtra(); });
loadExtra();
load();
