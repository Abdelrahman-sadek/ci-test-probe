// AUC Library Assistant — accessible, bilingual chat client (no inline script; works under a strict CSP).
const STR = {
  en: {title: "AUC Library Assistant", skip: "Skip to question box", label: "Your question",
       placeholder: "Ask about borrowing, rare books, theses…", send: "Ask", voice: "Ask by voice",
       note: "Independent demo, not affiliated with AUC. Answers come only from cited sources. Ask in English, Arabic or Franco-Arabic.",
       other: "العربية", thinking: "Searching the library sources…", ready: "Answer ready.", sources: "Sources",
       retrieval: "Retrieval details", error: "Something went wrong. Please try again.", busy: "Too many questions, please wait a moment.",
       listening: "Listening…", log: "Conversation", helpful: "Helpful", unhelpful: "Not helpful",
       why: "What was wrong? (optional)", sendfb: "Send feedback", thanks: "Thank you for the feedback.",
       save: "Save", saved: "Saved.", handoff: "Send my question to a librarian", email: "Your email (optional)",
       consent: "Library staff may contact me at this email about my question", send2: "Send",
       ticket: "Sent. Reference", consult: "Book a consultation", contact: "Contact", request: "Request rare materials",
       renew: "Renew", renewed: "Renewal requested.", mine: "My saved items and past chats", savedh: "Saved",
       pasth: "Past conversations", signin: "Sign in with your AUC account to see this.", scan: "View scanned page",
       degraded: "The AI service is busy, so this answer shows the most relevant passage from library sources.",
       related: "You may also ask about:", referral: "Contact the", privacy: "Privacy: how your questions are handled",
       download: "Download my data", erase: "Delete my data", confirm: "Delete all your saved chats, searches and feedback?",
       erased: "Your data was deleted."},
  ar: {title: "مساعد مكتبة الجامعة الأمريكية", skip: "انتقل إلى مربع السؤال", label: "سؤالك",
       placeholder: "اسأل عن الاستعارة أو الكتب النادرة أو الرسائل العلمية…", send: "اسأل", voice: "اسأل بالصوت",
       note: "عرض تجريبي مستقل وغير تابع للجامعة. الإجابات من مصادر موثقة فقط. اسأل بالعربية أو الإنجليزية أو الفرانكو.",
       other: "English", thinking: "جارٍ البحث في مصادر المكتبة…", ready: "الإجابة جاهزة.", sources: "المصادر",
       retrieval: "تفاصيل البحث", error: "حدث خطأ، حاول مرة أخرى.", busy: "أسئلة كثيرة، انتظر قليلًا من فضلك.",
       listening: "جارٍ الاستماع…", log: "المحادثة", helpful: "مفيدة", unhelpful: "غير مفيدة",
       why: "ما المشكلة؟ (اختياري)", sendfb: "إرسال الملاحظة", thanks: "شكرًا على ملاحظتك.",
       save: "حفظ", saved: "تم الحفظ.", handoff: "أرسل سؤالي إلى أمين مكتبة", email: "بريدك الإلكتروني (اختياري)",
       consent: "أوافق على أن يتواصل معي موظفو المكتبة عبر هذا البريد بخصوص سؤالي", send2: "إرسال",
       ticket: "تم الإرسال. رقم الطلب", consult: "احجز استشارة", contact: "تواصل", request: "طلب مواد نادرة",
       renew: "تجديد", renewed: "تم طلب التجديد.", mine: "محفوظاتي ومحادثاتي السابقة", savedh: "المحفوظات",
       pasth: "المحادثات السابقة", signin: "سجّل الدخول بحساب الجامعة لعرض هذا.", scan: "عرض الصفحة الممسوحة",
       degraded: "خدمة الذكاء الاصطناعي مشغولة، لذلك تعرض هذه الإجابة أنسب فقرة من مصادر المكتبة.",
       related: "يمكنك أيضًا السؤال عن:", referral: "تواصل مع", privacy: "الخصوصية: كيف نتعامل مع أسئلتك",
       download: "تنزيل بياناتي", erase: "حذف بياناتي", confirm: "هل تريد حذف كل محادثاتك وعمليات البحث والملاحظات المحفوظة؟",
       erased: "تم حذف بياناتك."},
};
const $ = (id) => document.getElementById(id);
const log = $("log"), form = $("f"), q = $("q"), status = $("status");
let lang = "en", history = [], conversationId = null, me = {signed_in: false};
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

async function post(path, payload) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || t("error"));
  return data;
}
function button(label, cls) { const b = el("button", cls || "ghost", label); b.type = "button"; return b; }
let uid = 0;

function feedbackRow(d, question) {
  const row = el("div", "row actions");
  const up = button("👍", "ghost"), down = button("👎", "ghost");
  up.setAttribute("aria-label", t("helpful")); down.setAttribute("aria-label", t("unhelpful"));
  up.setAttribute("aria-pressed", "false"); down.setAttribute("aria-pressed", "false");
  const send = async (rating, reason) => {
    try { await post("/api/feedback", {answer_id: d.id, rating, reason: reason || "", question, mode: d.mode, lang: d.lang}); status.textContent = t("thanks"); }
    catch (e) { status.textContent = e.message; }
  };
  up.addEventListener("click", () => { up.setAttribute("aria-pressed", "true"); down.disabled = true; send(1); });
  down.addEventListener("click", () => {
    down.setAttribute("aria-pressed", "true"); up.disabled = true;
    const id = "why" + (++uid), lab = el("label", "sr-only", t("why")), inp = el("input"), go = button(t("sendfb"));
    lab.htmlFor = id; inp.id = id; inp.maxLength = 500; inp.placeholder = t("why");
    go.addEventListener("click", () => { send(-1, inp.value); go.disabled = true; });
    row.append(lab, inp, go); inp.focus();
  });
  row.append(up, down);
  if (me.signed_in) {
    const sv = button(t("save"));
    sv.addEventListener("click", async () => { try { await post("/api/saved", {question, answer: d.answer}); status.textContent = t("saved"); sv.disabled = true; } catch (e) { status.textContent = e.message; } });
    row.append(sv);
  }
  return row;
}

function handoffForm(question) {
  const wrap = el("div", "panel"), id = "em" + (++uid), cid = "cs" + (++uid);
  const lab = el("label", "", t("email")), inp = el("input"); lab.htmlFor = id; inp.id = id; inp.type = "email"; inp.autocomplete = "email";
  const crow = el("div", "row"), cb = el("input"), clab = el("label", "", t("consent"));
  cb.type = "checkbox"; cb.id = cid; clab.htmlFor = cid; crow.append(cb, clab);
  const go = button(t("send2"), "");
  go.addEventListener("click", async () => {
    try {
      const r = await post("/api/handoff", {question, history: history.slice(-6), email: inp.value, consent: cb.checked});
      wrap.textContent = `${t("ticket")} ${r.ticket} · ${r.routed_to}. ${r.message}`; status.textContent = wrap.textContent;
    } catch (e) { status.textContent = e.message; }
  });
  wrap.append(lab, inp, crow, go);
  return wrap;
}

function actionsRow(box, d, question) {
  for (const a of d.actions || []) {
    if (a.type === "handoff") {
      const b = button(t("handoff"));
      b.addEventListener("click", () => { b.replaceWith(handoffForm(question)); });
      box.append(b);
    } else if (a.type === "librarian") {
      const u = safeUrl(a.booking_url) || safeUrl(a.contact);
      if (u) { const link = el("a", "", (a.booking_url ? t("consult") : t("contact")) + " — " + a.subject); link.href = u; link.target = "_blank"; link.rel = "noopener noreferrer"; box.append(el("p", ""), link); }
    } else if (a.type === "request") {
      const link = el("a", "", t("request")); link.href = "/request"; box.append(el("p", ""), link);
    } else if (a.type === "renew") {
      const b = button(t("renew") + ": " + a.title);
      b.addEventListener("click", async () => { try { await post("/api/account/renew", {loan_id: a.loan_id}); status.textContent = t("renewed"); b.disabled = true; } catch (e) { status.textContent = e.message; } });
      box.append(b);
    } else if (a.type === "referral") {
      const u = safeUrl(a.url), txt = t("referral") + " " + a.office;
      if (u) { const link = el("a", "", txt); link.href = u; link.target = "_blank"; link.rel = "noopener noreferrer"; box.append(el("p", ""), link); }
      else box.append(el("p", "", txt));
    } else if (a.type === "signin") {
      box.append(el("p", "note", t("signin")));
    }
  }
}

function render(box, d, question) {
  box.textContent = "";
  const banner = $("banner");
  if (d.degraded) { banner.textContent = t("degraded"); banner.hidden = false; } else { banner.hidden = true; }
  box.append(el("div", "chip", d.agent + " · " + d.mode), el("div", "", d.answer));
  if (d.sources && d.sources.length) {
    const list = el("ol", "src"); list.setAttribute("aria-label", t("sources"));
    for (const s of d.sources) {
      const li = el("li", ""); li.value = s.n;
      const u = safeUrl(s.url), label = s.title + (s.section ? " › " + s.section : "");
      if (u) { const a = el("a", "", label); a.href = u; a.target = "_blank"; a.rel = "noopener noreferrer"; li.append(a); }
      else li.append(label);
      if (s.method && s.method.startsWith("ocr") && s.origin && s.origin.toLowerCase().endsWith(".pdf")) {
        const img = el("a", "", " · " + t("scan"));
        img.href = "/api/page-image?origin=" + encodeURIComponent(s.origin) + "&page=" + s.page;
        img.target = "_blank"; img.rel = "noopener noreferrer"; li.append(img);
      }
      list.append(li);
    }
    box.append(list);
  }
  if (d.retrieved && d.retrieved.length) {
    const det = el("details"); det.append(el("summary", "", t("retrieval") + " (" + d.retrieved.length + ")"));
    for (const h of d.retrieved) det.append(el("div", "", h.score + "  " + h.title + (h.section ? " › " + h.section : "")));
    box.append(det);
  }
  actionsRow(box, d, question);
  if (d.related && d.related.length) {
    const rel = el("div", "row actions"); rel.append(el("span", "note", t("related")));
    for (const topic of d.related) { const b = button(topic); b.addEventListener("click", () => ask(topic)); rel.append(b); }
    box.append(rel);
  }
  if (d.id) box.append(feedbackRow(d, question));
}

async function ask(question) {
  log.append(el("div", "msg me", question));
  const box = el("div", "msg", t("thinking")); box.setAttribute("aria-busy", "true"); log.append(box);
  box.scrollIntoView({block: "nearest"});
  const body = JSON.stringify({question, history: history.slice(-6), conversation_id: conversationId});
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
    render(box, final, question);
    if (final.conversation_id) conversationId = final.conversation_id;
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
async function loadMine(panel) {
  panel.textContent = "";
  try {
    const [saved, convs] = await Promise.all([fetch("/api/saved").then((r) => r.json()), fetch("/api/conversations").then((r) => r.json())]);
    panel.append(el("h2", "", t("savedh")));
    const ul = el("ul", ""); for (const s of saved) ul.append(el("li", "", s.question)); panel.append(ul);
    panel.append(el("h2", "", t("pasth")));
    const ul2 = el("ul", "");
    for (const c of convs) {
      const li = el("li", ""), b = button(c.title);
      b.addEventListener("click", async () => {
        const turns = await fetch("/api/conversations/" + encodeURIComponent(c.id)).then((r) => r.json());
        log.textContent = ""; history = turns; conversationId = c.id;
        for (const tr of turns) log.append(el("div", tr.role === "user" ? "msg me" : "msg", tr.content));
        q.focus();
      });
      li.append(b); ul2.append(li);
    }
    panel.append(ul2);
    const rights = el("div", "row"), dl = button(t("download")), er = button(t("erase"));
    dl.addEventListener("click", async () => {
      const data = await fetch("/api/me/data").then((r) => r.json());
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
      a.download = "my-library-assistant-data.json"; a.click();
    });
    er.addEventListener("click", async () => {
      if (!window.confirm(t("confirm"))) return;
      await fetch("/api/me/data", {method: "DELETE"}); status.textContent = t("erased"); loadMine(panel);
    });
    rights.append(dl, er); panel.append(rights);
  } catch (e) { panel.append(el("p", "", t("error"))); }
}

fetch("/api/me").then((r) => r.json()).then((m) => {
  me = m;
  if (m.signed_in && m.features && m.features.history) {
    const toggle = button(t("mine")), panel = el("section", "panel"); panel.hidden = true; panel.id = "mine";
    toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", "mine");
    toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; toggle.setAttribute("aria-expanded", String(!panel.hidden)); if (!panel.hidden) loadMine(panel); });
    document.getElementById("main").insertBefore(toggle, log); document.getElementById("main").insertBefore(panel, log);
  }
}).catch(() => {});
applyLang();
