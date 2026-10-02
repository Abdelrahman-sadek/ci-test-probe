// AUC Library Assistant — accessible, bilingual chat client (no inline script; works under a strict CSP).
const STR = {
  en: {title: "AUC Library Assistant", skip: "Skip to question box", label: "Your question",
       placeholder: "Ask about borrowing, rare books, theses…", send: "Ask", voice: "Ask by voice",
       note: "Independent demo, not affiliated with AUC. Answers come only from cited sources. Ask in English, Arabic or Franco-Arabic.",
       other: "العربية", thinking: "Searching the library sources…", ready: "Answer ready.", sources: "Sources",
       retrieval: "Retrieval details", error: "Something went wrong. Please try again.", busy: "Too many questions, please wait a moment.",
       listening: "Listening…", log: "Conversation"},
  ar: {title: "مساعد مكتبة الجامعة الأمريكية", skip: "انتقل إلى مربع السؤال", label: "سؤالك",
       placeholder: "اسأل عن الاستعارة أو الكتب النادرة أو الرسائل العلمية…", send: "اسأل", voice: "اسأل بالصوت",
       note: "عرض تجريبي مستقل وغير تابع للجامعة. الإجابات من مصادر موثقة فقط. اسأل بالعربية أو الإنجليزية أو الفرانكو.",
       other: "English", thinking: "جارٍ البحث في مصادر المكتبة…", ready: "الإجابة جاهزة.", sources: "المصادر",
       retrieval: "تفاصيل البحث", error: "حدث خطأ، حاول مرة أخرى.", busy: "أسئلة كثيرة، انتظر قليلًا من فضلك.",
       listening: "جارٍ الاستماع…", log: "المحادثة"},
};
const $ = (id) => document.getElementById(id);
const log = $("log"), form = $("f"), q = $("q"), status = $("status");
let lang = "en", history = [];
try { lang = localStorage.getItem("agentkit-lang") || (navigator.language || "").startsWith("ar") && "ar" || "en"; } catch (e) {}
if (location.pathname === "/embed") document.body.classList.add("embed");

function t(k) { return STR[lang][k]; }
function applyLang() {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  document.title = t("title");
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nLabel)); });
  log.setAttribute("aria-label", t("log"));
  const b = $("lang"); b.textContent = t("other"); b.lang = lang === "ar" ? "en" : "ar";
  if (recognizer) recognizer.lang = lang === "ar" ? "ar-EG" : "en-US";
}
$("lang").addEventListener("click", () => {
  lang = lang === "ar" ? "en" : "ar";
  try { localStorage.setItem("agentkit-lang", lang); } catch (e) {}
  applyLang(); q.focus();
});

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;  // model output is always text, never HTML (OWASP LLM05)
  e.dir = "auto";
  return e;
}
function safeUrl(u) { try { const x = new URL(u); return ["http:", "https:"].includes(x.protocol) ? x.href : null; } catch (e) { return null; } }

function render(box, d) {
  box.textContent = "";
  box.append(el("div", "chip", d.agent + " · " + d.mode), el("div", "", d.answer));
  if (d.sources && d.sources.length) {
    const list = el("ol", "src"); list.setAttribute("aria-label", t("sources"));
    for (const s of d.sources) {
      const li = el("li", ""); li.value = s.n;
      const u = safeUrl(s.url), label = s.title + (s.section ? " › " + s.section : "");
      if (u) { const a = el("a", "", label); a.href = u; a.target = "_blank"; a.rel = "noopener noreferrer"; li.append(a); }
      else li.append(label);
      list.append(li);
    }
    box.append(list);
  }
  if (d.retrieved && d.retrieved.length) {
    const det = el("details"); det.append(el("summary", "", t("retrieval") + " (" + d.retrieved.length + ")"));
    for (const h of d.retrieved) det.append(el("div", "", h.score + "  " + h.title + (h.section ? " › " + h.section : "")));
    box.append(det);
  }
}

async function ask(question) {
  log.append(el("div", "msg me", question));
  const box = el("div", "msg", t("thinking")); box.setAttribute("aria-busy", "true"); log.append(box);
  box.scrollIntoView({block: "nearest"});
  const body = JSON.stringify({question, history: history.slice(-6)});
  let final = null;
  try {
    const r = await fetch("/api/ask/stream", {method: "POST", headers: {"Content-Type": "application/json"}, body});
    if (r.status === 429) throw new Error(t("busy"));
    if (!r.ok || !r.body) throw new Error(t("error"));
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", text = "";
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const evt = buf.slice(0, i); buf = buf.slice(i + 2);
        const isDone = evt.startsWith("event: done");
        const data = JSON.parse(evt.slice(evt.indexOf("data: ") + 6));
        if (isDone) final = data; else { text += data.delta; box.textContent = text; }
      }
    }
    if (!final) throw new Error(t("error"));
    render(box, final);
    history.push({role: "user", content: question}, {role: "assistant", content: final.answer});
    status.textContent = t("ready");
  } catch (e) {
    box.textContent = e.message || t("error"); status.textContent = box.textContent;
  } finally {
    box.setAttribute("aria-busy", "false"); q.focus();  // screen readers read the finished answer once
  }
}

form.addEventListener("submit", (ev) => { ev.preventDefault(); const v = q.value.trim(); if (!v) return; q.value = ""; ask(v); });

// Voice input (Web Speech API) — shown only where the browser supports it.
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
if (SR) {
  recognizer = new SR(); recognizer.interimResults = false;
  const mic = $("mic"); mic.hidden = false;
  mic.addEventListener("click", () => { mic.setAttribute("aria-pressed", "true"); status.textContent = t("listening"); recognizer.start(); });
  recognizer.addEventListener("result", (e) => { q.value = e.results[0][0].transcript; form.requestSubmit(); });
  recognizer.addEventListener("end", () => mic.setAttribute("aria-pressed", "false"));
}
applyLang();
