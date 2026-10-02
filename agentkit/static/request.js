// Special-collections request intake (reading-room visits and reproductions).
const $ = (id) => document.getElementById(id);
$("rq").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {request_type: document.querySelector("input[name=type]:checked").value, collection: $("collection").value,
    items: $("items").value, visit_date: $("visit").value, purpose: $("purpose").value, affiliation: $("aff").value,
    name: $("name").value, email: $("email").value, consent: $("consent").checked};
  try {
    const r = await fetch("/api/requests/special-collections", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const d = await r.json();
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Please check the form fields.");
    $("status").textContent = `Request sent. Reference ${d.ticket}. ${d.message}`;
    $("rq").reset();
  } catch (e) { $("status").textContent = "Not sent: " + e.message; }
});
