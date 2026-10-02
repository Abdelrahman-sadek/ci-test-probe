// Embeddable widget: <script src="https://<assistant-host>/widget.js" defer></script>
// The host page must be listed in AGENTKIT_EMBED_ORIGINS (CORS + CSP frame-ancestors).
(function () {
  const base = new URL(document.currentScript.src).origin;
  const ar = (document.documentElement.lang || "").startsWith("ar");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = ar ? "اسأل المكتبة" : "Ask the Library";
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", "agentkit-frame");
  Object.assign(btn.style, {position: "fixed", insetInlineEnd: "1rem", bottom: "1rem", zIndex: 2147483646,
    minHeight: "44px", padding: "0 1.1rem", borderRadius: "22px", border: "0", background: "#8a1c2b",
    color: "#fff", font: "600 1rem system-ui,Tahoma,sans-serif", cursor: "pointer"});
  const frame = document.createElement("iframe");
  frame.id = "agentkit-frame";
  frame.title = ar ? "مساعد مكتبة الجامعة الأمريكية" : "AUC Library Assistant";
  frame.src = base + "/embed";
  frame.hidden = true;
  frame.setAttribute("allow", "microphone");
  Object.assign(frame.style, {position: "fixed", insetInlineEnd: "1rem", bottom: "4.5rem", zIndex: 2147483646,
    width: "min(26rem, calc(100vw - 2rem))", height: "min(36rem, calc(100vh - 6rem))", border: "1px solid #c9c9c4",
    borderRadius: "12px", background: "#fff", boxShadow: "0 8px 30px rgba(0,0,0,.2)"});
  btn.addEventListener("click", () => {
    frame.hidden = !frame.hidden;
    btn.setAttribute("aria-expanded", String(!frame.hidden));
    if (!frame.hidden) frame.focus();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !frame.hidden) { frame.hidden = true; btn.setAttribute("aria-expanded", "false"); btn.focus(); } });
  document.body.append(btn, frame);
})();
