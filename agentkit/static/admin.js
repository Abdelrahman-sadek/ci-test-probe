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
    $("stats").textContent = JSON.stringify(await api("/admin/api/stats"), null, 2);
  } catch (e) { say("Sign in or enter the admin key to see staff data (" + e.message + ")."); }
}
$("refresh").addEventListener("click", load);
load();
