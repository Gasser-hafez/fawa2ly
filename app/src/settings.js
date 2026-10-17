/*
The settings window (2026-09-11). A second client of the same engine the overlay talks to: it
sends {"cmd": "settings"} and the engine saves the choice, applies what can change mid-call (the
answer profile, a new provider, a new background) and tells every window, so the overlay and this
page agree without talking to each other.

Laid out as a wide window with one section at a time (maintainer's request, 2026-09-11), and grown the
same day by three sections: Roles (pick one, or describe your own, typed or spoken, and the model
writes it up), a speed test under Model, and Usage.
*/

const WS_URL = `ws://127.0.0.1:${new URLSearchParams(location.search).get("port") || 8765}`;
const $ = (id) => document.getElementById(id);

let socket = null;
let providers = [];
let presets = [];
let presetModes = {};             // role -> the kind of call it is written for
let presetInfo = {};              // role -> {title, custom}
let current = {};
let live = false;
let activeVendor = "";
let flashTimer = null;

function option(value, label, selected) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  o.selected = selected;
  return o;
}

const roleTitle = (name) => (presetInfo[name] && presetInfo[name].title) || name;
const rolesFor = (mode) => presets.filter((p) => (presetModes[p] || "interview") === mode)
  .sort((a, b) => roleTitle(a).localeCompare(roleTitle(b)));

/* ---------- sections ---------- */

let section = "call";
try { section = localStorage.getItem("settings-section") || "call"; } catch (e) { /* no storage */ }
// The overlay's "Change in Settings" opens this window on Roles (2026-09-11).
const askedSection = new URLSearchParams(location.search).get("section");
if (askedSection) section = askedSection;

/* The ten sections sit in five groups (Fawa2ly identity, 2026-10-17). `section` holds the group;
   a section's own name still works everywhere one is asked for (?section=roles, settings-section),
   and opens its group scrolled to it. */
const GROUPS = {
  call: ["call", "listening"],
  you: ["roles", "documents"],
  calls: ["calls"],
  model: ["model", "keys", "usage"],
  app: ["display", "hotkeys"],
};
const groupOf = (name) => Object.keys(GROUPS).find((g) => g === name || GROUPS[g].includes(name)) || "call";

function showSection(name) {
  const group = groupOf(name);
  section = group;
  for (const s of document.querySelectorAll(".section")) s.hidden = !GROUPS[group].includes(s.dataset.section);
  for (const b of document.querySelectorAll("#nav button")) b.setAttribute("aria-current", String(b.dataset.section === group));
  try { localStorage.setItem("settings-section", group); } catch (e) { /* no storage */ }
  const target = name !== group && document.querySelector(`.section[data-section="${name}"]`);
  if (target) target.scrollIntoView(); else $("content").scrollTop = 0;
  if (group === "model") send("usage", {}, true);
  if (group === "calls") send("calls", {}, true);
  if (group === "you") {
    send("jobfit", {}, true);       // the comparison kept from last time (item 23)
  }
}

for (const b of document.querySelectorAll("#nav button")) b.addEventListener("click", () => showSection(b.dataset.section));

/* ---------- drawing the saved settings ---------- */

function render(s) {
  if (s) current = s;
  const c = current;
  $("style").value = c.style || "full";
  // Roadmap item 4: only the roles written for this kind of call, and the document named for it.
  $("mode").value = c.mode || "interview";
  const mode = $("mode").value;
  const role = rolesFor(mode).includes(c.preset) ? c.preset : "";
  $("role-name").textContent = role ? roleTitle(role) : "No role";
  $("role-name").classList.toggle("none", !role);
  renderRoles();
  const [heading, warning] = {
    meeting: ["Meeting material", "No meeting material loaded. The model cannot state project facts, figures or dates."],
  }[mode] || ["Your background", "No CV loaded. The model cannot use your experience, and every answer will be generic."];
  $("cv-heading").textContent = heading;
  $("cv-warning").textContent = warning;
  const chosen = live && activeVendor ? activeVendor : c.vendor;
  $("vendor").replaceChildren(...(providers.length
    ? providers.map((p) => option(p.vendor, `${p.label || p.vendor} - ${p.model}`, p.vendor === chosen))
    : [option("", "No model has a key yet: add one under API keys", true)]));
  $("model").value = c.model || "";
  $("speculate").checked = c.speculate !== false;
  $("lang").value = c.lang || "en";
  $("user-mic").checked = Boolean(c.user_mic);
  const offline = c.stt_vendor === "whisper";
  $("stt-vendor").value = offline ? "whisper" : "deepgram";
  $("whisper-model").value = c.whisper_model || "small";
  $("whisper-options").hidden = !offline;
  $("whisper-missing").hidden = !(offline && c.whisper_available === false);
  renderDevices();
  renderKeys({ DEEPGRAM_API_KEY: !!(c.keys || {}).DEEPGRAM_API_KEY });
  renderProviders(c.provider_catalog || []);
  $("animate").checked = c.animate !== false;
  $("pace-meter").checked = c.pace_meter !== false;
  $("text-size").value = c.text_size || "medium";
  $("theme").value = c.theme || "dark";
  document.documentElement.dataset.theme = $("theme").value;
  $("ui-lang").value = c.ui_lang === "ar" ? "ar" : "en";
  if (window.fawa2lyI18n) window.fawa2lyI18n.setLang($("ui-lang").value);
  // transcripts (roadmap item 6): off unless turned on, and deletable here
  $("save-transcripts").checked = Boolean(c.save_transcripts);
  $("live-feedback").checked = Boolean(c.live_feedback);
  $("negotiation").checked = Boolean(c.negotiation);
  const transcripts = c.transcripts || {};
  $("transcripts-state").textContent = transcripts.count
    ? `${transcripts.count} saved in ${transcripts.folder}` : "None saved.";
  $("transcripts-delete").hidden = !transcripts.count;
  // a call that just ended with its transcript saved joins the Calls list if that is on screen
  if (section === "calls" && lastTranscriptCount !== null && transcripts.count !== lastTranscriptCount) {
    send("calls", {}, true);
  }
  lastTranscriptCount = transcripts.count || 0;
  // Not while the slider is being dragged, and not until the value this page sent last comes
  // back: the echo of an earlier step can arrive after the thumb has moved on, and would pull it
  // back for a moment (seen in the browser: 70 % showing after the slider was set to 90).
  const incoming = c.opacity ?? 0.9;
  if (pendingOpacity !== null && incoming === pendingOpacity) pendingOpacity = null;
  if (!dragging && pendingOpacity === null) $("opacity").value = Math.round(incoming * 100);
  $("opacity-value").textContent = `${$("opacity").value}%`;
  renderHotkeys(c.hotkeys || {});
  // the documents stored for this kind of call (roadmap item 5)
  const docs = (c.documents || {})[mode] || [];
  const words = docs.reduce((sum, d) => sum + d.words, 0);
  $("cv-state").textContent = docs.length
    ? `${words.toLocaleString()} of ${(c.document_words_limit || 0).toLocaleString()} words. Answers will use ${docs.length === 1 ? "it" : "them"}.`
    : "No document loaded.";
  $("cv-warning").hidden = docs.length > 0;
  $("docs").replaceChildren(...docs.map((d) => documentRow(d, mode)));
  $("drop-cv").hidden = docs.length < 2;
  $("pick-cv").textContent = docs.length ? "Add more" : "Choose files";
}

/* ---------- roles ---------- */

/* Sections and a search (2026-09-11, maintainer's request): with nearly 80 roles one list is not a way to find
   one, and the setup screen no longer lists them at all. The search matches every word typed
   against the title, the role line and the section name, so "sales software" finds the SaaS rep. */
const USER_CATEGORY = "Your roles";       // fawa2ly/context/builder.py
const categoryOf = (name) => (presetInfo[name] && presetInfo[name].category) || "General";

function renderRoles() {
  const mode = $("mode").value;
  $("roles-heading").textContent = { meeting: "Roles for meetings" }[mode] || "Roles for interviews";
  const all = rolesFor(mode);
  $("no-role").setAttribute("aria-pressed", String(!all.includes(current.preset)));
  const words = $("role-search").value.toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map();
  for (const name of all) {
    const info = presetInfo[name] || {};
    const text = `${roleTitle(name)} ${info.role || ""} ${categoryOf(name)} ${name}`.toLowerCase();
    if (!words.every((w) => text.includes(w))) continue;
    if (!groups.has(categoryOf(name))) groups.set(categoryOf(name), []);
    groups.get(categoryOf(name)).push(name);
  }
  // the user's own roles first, then General, then the sections in alphabetical order
  const rank = (cat) => (cat === USER_CATEGORY ? 0 : cat === "General" ? 1 : 2);
  const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  $("role-sections").replaceChildren(...order.map((cat) => {
    const box = document.createElement("div");
    box.className = "role-section";
    const head = document.createElement("h4");
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(groups.get(cat).length);
    head.append(cat, count);
    const list = document.createElement("ul");
    list.className = "role-list";
    list.replaceChildren(...groups.get(cat).map(roleItem));
    box.append(head, list);
    return box;
  }));
  $("role-empty").hidden = groups.size > 0;
}

function roleItem(name) {
    const li = document.createElement("li");
    li.setAttribute("aria-selected", String(name === current.preset));
    li.title = name;
    const title = document.createElement("span");
    title.className = "role-title";
    title.translate = false;              // a role's name, not the app's words (i18n.js)
    title.textContent = roleTitle(name);
    li.append(title);
    if (presetInfo[name] && presetInfo[name].custom) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "yours";
      const drop = document.createElement("button");
      drop.type = "button";
      drop.textContent = "✕";
      drop.title = `Delete ${roleTitle(name)}`;
      drop.addEventListener("click", (e) => {
        e.stopPropagation();
        send("role_delete", { name });
      });
      li.append(tag, drop);
    }
    li.addEventListener("click", () => save({ preset: name === current.preset ? "" : name }, nextCall()));
    return li;
}

let analyzing = false;
let dictating = false;

function setRoleStatus(text) {
  $("role-status").textContent = text;
}

$("role-analyze").addEventListener("click", () => {
  const description = $("role-description").value.trim();
  if (description.split(/\s+/).filter(Boolean).length < 5) {
    setRoleStatus("Say a little more about the role first: at least five words.");
    return;
  }
  if (dictating) send("dictate", { action: "stop" });
  if (send("role", { mode: $("mode").value, description })) {
    analyzing = true;
    $("role-analyze").disabled = true;
    $("role-analyze").classList.add("loading");
    setRoleStatus("The model is writing the role up");
  }
});

$("role-speak").addEventListener("click", () => {
  if (dictating) {
    send("dictate", { action: "stop" });
    return;
  }
  if (send("dictate", { action: "start" })) setRoleStatus("Starting the microphone");
});

function onDictation(e) {
  if (e.state === "listening") {
    dictating = true;
    $("role-speak").textContent = "Stop";
    $("role-speak").classList.add("loading");
    setRoleStatus("Listening: say what you do. Press Stop when you are done (it stops by itself after a minute).");
  } else if (e.state === "text") {
    $("role-description").value = e.text;
  } else {
    dictating = false;
    $("role-speak").textContent = "Speak";
    $("role-speak").classList.remove("loading");
    if (e.state === "done") {
      if (e.text) $("role-description").value = e.text;
      setRoleStatus(e.text ? "Check the text, then press Analyze and add." : "Nothing was heard.");
    } else {
      setRoleStatus(e.text || "The microphone stopped.");
    }
  }
}

function onRole(e) {
  analyzing = false;
  $("role-analyze").disabled = false;
  $("role-analyze").classList.remove("loading");
  setRoleStatus(`Added "${e.title}" and chose it for ${{ meeting: "meetings" }[e.mode] || "interviews"}.`);
  $("role-description").value = "";
  save({ mode: e.mode, preset: e.name }, "Role added and chosen");
}

/* ---------- job description gap analysis (roadmap item 23) ----------

   One request before the interview, kept in the app folder, so reopening this window shows the
   comparison again for free. The description is also stored as an interview document, which is why
   "Remove it" takes both away: leaving the document behind would put a job description the user
   thought they had removed into the next interview's prompt. */

let jobfitting = false;

function setJobfitStatus(text) {
  $("jobfit-status").textContent = text;
}

function renderJobfitList(id, items, line) {
  const list = $(id);
  $(id + "-head").hidden = items.length === 0;
  list.hidden = items.length === 0;
  list.replaceChildren(...items.map((item) => {
    const li = modelText("li", line(item));       // the model's words, not the app's (i18n.js)
    return li;
  }));
}

function renderJobfit(fit) {
  $("jobfit-result").hidden = !fit;
  $("jobfit-clear").hidden = !fit;
  if (!fit) return;
  $("jobfit-summary").textContent = fit.summary || "";
  $("jobfit-summary").translate = false;
  $("jobfit-summary").dir = "auto";
  renderJobfitList("jobfit-matches", fit.matches || [], (m) => `${m.asks} — ${m.evidence}`);
  renderJobfitList("jobfit-missing", fit.missing || [], (x) => x);
  renderJobfitList("jobfit-prepare", fit.prepare || [], (x) => x);
  const words = fit.words ? `${fit.words} words` : "";
  $("jobfit-meta").textContent = [words, fit.model, startedLabel(fit.written, "")].filter(Boolean).join(" · ");
  // removed from the documents list on its own: the comparison stands, the next call will not see it
  $("jobfit-gone").hidden = fit.stored !== false;
}

$("jobfit-run").addEventListener("click", () => {
  const description = $("jobfit-description").value.trim();
  if (description.split(/\s+/).filter(Boolean).length < 25) {
    setJobfitStatus("Paste the job description itself: at least 25 words.");
    return;
  }
  if (send("jobfit", { description })) {
    jobfitting = true;
    $("jobfit-run").disabled = true;
    $("jobfit-run").classList.add("loading");
    setJobfitStatus("The model is comparing it with your CV");
  }
});

$("jobfit-clear").addEventListener("click", () => {
  if (send("jobfit", { clear: true })) setJobfitStatus("Removed.");
});

function onJobfit(e) {
  jobfitting = false;
  $("jobfit-run").disabled = false;
  $("jobfit-run").classList.remove("loading");
  renderJobfit(e.jobfit);
  if (e.jobfit) {
    $("jobfit-description").value = "";
    setJobfitStatus("Kept with your documents, and used in the next interview.");
  }
}

/* ---------- saved calls and their review (roadmap item 7) ----------
The saved calls, newest first, and the model's review of one. The engine keeps a review in the
call's own file, so a call reviewed once opens with it. "Review this call" in the overlay opens this
window as ?section=calls&call=<file>&review=1, or sends settings-call when it is already open. */

let calls = [];
let openCall = null;              // {file, summary, review, has_you} as the engine sent it
let reviewing = false;
let lastTranscriptCount = null;
const askedParams = new URLSearchParams(location.search);
let pendingCall = askedParams.get("call")
  ? { file: askedParams.get("call"), review: askedParams.get("review") === "1" } : null;
const VERDICT_LABEL = { landed: "Landed", partly: "Partly", missed: "Missed" };

function callLength(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

// the same line as the overlay's summary, so it reads (and translates) the same
function callFacts(s) {
  const parts = [callLength(s.duration_s), `${s.heard || 0} question${s.heard === 1 ? "" : "s"} heard`,
                 `${s.answered || 0} answered`];
  if (s.median_first_line_ms != null) parts.push(`first line in ${(s.median_first_line_ms / 1000).toFixed(1)} s, median`);
  if (s.you_wpm != null) {
    parts.push(`you: ${s.you_wpm} wpm`);
    if (s.you_fillers != null) parts.push(`${s.you_fillers} filler${s.you_fillers === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

// the model's words and what was said on the call are not the app's own text (i18n.js)
function modelText(tag, text, cls) {
  const node = document.createElement(tag);
  node.textContent = text;
  node.translate = false;
  node.dir = "auto";
  if (cls) node.className = cls;
  return node;
}

function startedLabel(started, fallback) {
  const d = started ? new Date(started) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString() : fallback;
}

function renderCalls() {
  $("calls-empty").hidden = calls.length > 0;
  $("calls-table").hidden = calls.length === 0;
  $("calls-rows").replaceChildren(...calls.map((c) => {
    const tr = document.createElement("tr");
    tr.setAttribute("aria-selected", String(Boolean(openCall && openCall.file === c.file)));
    const cells = [[startedLabel(c.started, c.file), "strong"], [callLength(c.duration_s)],
                   [`${c.answered} of ${c.heard} answered`],
                   [c.reviewed ? "Reviewed" : c.has_you ? "Not yet" : "None of your words"]];
    for (const [text, cls] of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    tr.addEventListener("click", () => send("call", { file: c.file }));
    return tr;
  }));
}

function renderCall() {
  const c = openCall;
  $("call-detail").hidden = !c;
  if (!c) return;
  const s = c.summary || {};
  $("call-heading").textContent = startedLabel(s.started, c.file);
  $("call-facts").textContent = s.heard == null ? "" : callFacts(s);
  const r = c.review;
  $("call-review").textContent = r ? "Review again" : "Review this call";
  $("call-review").disabled = reviewing || !c.has_you;
  $("call-review").classList.toggle("loading", reviewing);
  // notes (roadmap item 19): from the conversation alone, so they do not need the user's words
  const n = c.notes;
  $("call-notes").textContent = n ? "Write notes again" : "Write notes";
  $("call-notes").disabled = reviewing;
  $("call-notes-body").hidden = !n;
  if (n) {
    $("notes-summary").textContent = n.summary || "";
    const actions = (n.actions || []).map((a) => (a.owner ? `${a.task} (${a.owner})` : a.task));
    for (const [key, rows] of [["decisions", n.decisions || []], ["actions", actions], ["questions", n.questions || []]]) {
      $(`notes-${key}-wrap`).hidden = !rows.length;
      $(`notes-${key}`).replaceChildren(...rows.map((t) => modelText("li", t)));
    }
  }
  if (!c.has_you) $("call-status").textContent = "Nothing to review: your microphone was not transcribed on this call.";
  $("call-review-body").hidden = !r;
  if (!r) return;
  $("review-score").textContent = String(r.score);
  $("review-summary").textContent = r.summary || "";
  const asked = Object.fromEntries((s.questions || []).map((q) => [q.n, q.question]));
  $("review-questions").replaceChildren(...(r.questions || []).map((q) => {
    const li = document.createElement("li");
    const verdict = document.createElement("span");
    verdict.className = `verdict ${q.verdict}`;
    verdict.textContent = VERDICT_LABEL[q.verdict] || q.verdict;
    li.append(verdict, modelText("span", asked[q.n] || `#${q.n}`, "q"));
    if (q.missing) li.append(modelText("p", q.missing, "missing"));
    return li;
  }));
  $("review-strengths").replaceChildren(...(r.strengths || []).map((t) => modelText("li", t)));
  $("review-improve").replaceChildren(...(r.improve || []).map((t) => modelText("li", t)));
}

function askForReview(file, again, kind = "review") {
  if (!send("call", { file, [kind]: true, again })) return;
  reviewing = true;
  if (!openCall || openCall.file !== file) openCall = { file, summary: {}, review: null, has_you: true };
  renderCall();
  $("call-status").textContent = kind === "notes" ? "The model is writing the notes" : "The model is reading the call";
}

function openAskedCall() {
  if (!pendingCall) return;
  const { file, review } = pendingCall;
  pendingCall = null;
  showSection("calls");
  if (review) askForReview(file, false);
  else send("call", { file });
}

$("call-review").addEventListener("click", () => {
  if (openCall && !reviewing) askForReview(openCall.file, Boolean(openCall.review));
});
$("call-notes").addEventListener("click", () => {
  if (openCall && !reviewing) askForReview(openCall.file, Boolean(openCall.notes), "notes");
});
$("notes-copy").addEventListener("click", () => {
  if (!openCall || !openCall.notes_markdown || !navigator.clipboard) return;
  navigator.clipboard.writeText(openCall.notes_markdown).then(() => flash("Copied"), () => flash("Could not copy"));
});

/* ---------- speed test ---------- */

let speedRows = {};
let fastest = null;

function speedCell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

function drawSpeedRow(vendor, r) {
  const tr = speedRows[vendor];
  if (!tr) return;
  const ms = (v) => (v == null ? "-" : `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`);
  if (!r) {
    tr.replaceChildren(speedCell(vendor, "strong"), speedCell("-"), speedCell("-"), speedCell("-"), speedCell("waiting"));
    return;
  }
  const result = r.error ? r.error : r.lines ? `${r.lines} line${r.lines === 1 ? "" : "s"}` : "no usable lines";
  tr.replaceChildren(speedCell(vendor, "strong"), speedCell(r.model || "-"), speedCell(ms(r.ttft_ms), "strong"),
                     speedCell(ms(r.total_ms)), speedCell(result, r.error || !r.lines ? "err" : ""));
}

function onSpeedtest(e) {
  if (e.state === "started") {
    fastest = null;
    speedRows = {};
    $("speed-table").hidden = false;
    $("speed-use").hidden = true;
    $("speed-run").disabled = true;
    $("speed-run").classList.add("loading");
    $("speed-status").textContent = `Testing ${e.vendors.length} model${e.vendors.length === 1 ? "" : "s"}, one after another`;
    $("speed-rows").replaceChildren(...e.vendors.map((vendor) => {
      const tr = document.createElement("tr");
      speedRows[vendor] = tr;
      return tr;
    }));
    for (const vendor of e.vendors) drawSpeedRow(vendor, null);
  } else if (e.state === "result") {
    drawSpeedRow(e.result.vendor, e.result);
  } else if (e.state === "finished") {
    for (const r of e.results || []) drawSpeedRow(r.vendor, r);
    $("speed-run").disabled = false;
    $("speed-run").classList.remove("loading");
    fastest = e.fastest;
    for (const [vendor, tr] of Object.entries(speedRows)) tr.classList.toggle("best", vendor === fastest);
    if (e.error) {
      $("speed-status").textContent = `The test stopped: ${e.error}`;
    } else if (fastest) {
      const best = (e.results || []).find((r) => r.vendor === fastest);
      $("speed-status").textContent = `Fastest: ${fastest}, first line in ${(best.ttft_ms / 1000).toFixed(2)} s.`;
      $("speed-use").hidden = current.vendor === fastest;
      $("speed-use").textContent = `Use ${fastest}`;
    } else {
      $("speed-status").textContent = "No model answered with usable lines.";
    }
    if (section === "model") send("usage", {}, true);
  }
}

$("speed-run").addEventListener("click", () => send("speedtest"));
$("speed-use").addEventListener("click", () => {
  if (!fastest) return;
  save({ vendor: fastest, model: null }, `Now using ${fastest}`);
  if (live) send("provider", { vendor: fastest, model: null });
  $("speed-use").hidden = true;
});

/* ---------- usage ---------- */

const n = (v) => Number(v || 0).toLocaleString();

function onUsage(u) {
  for (const period of ["today", "month", "all"]) {
    const t = u[period] || {};
    $(`usage-${period}`).textContent = n((t.input_tokens || 0) + (t.output_tokens || 0));
    $(`usage-${period}-detail`).textContent =
      `${n(t.input_tokens)} in, ${n(t.output_tokens)} out, ${n(t.requests)} request${t.requests === 1 ? "" : "s"}`
      + (t.unreported ? `, ${n(t.unreported)} not reported` : "");
  }
  const models = Object.entries(u.models || {});
  $("usage-rows").replaceChildren(...(models.length ? models.map(([key, t]) => {
    const tr = document.createElement("tr");
    for (const [v, cls] of [[key, "strong"], [n(t.requests)], [n(t.input_tokens)], [n(t.output_tokens)], [n(t.unreported)]]) {
      const td = document.createElement("td");
      td.textContent = v;
      if (cls) td.className = cls;
      tr.append(td);
    }
    return tr;
  }) : [(() => {
    const tr = document.createElement("tr");
    tr.className = "empty-row";
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "Nothing counted yet.";
    tr.append(td);
    return tr;
  })()]));
  $("usage-since").textContent = u.since ? `Counted since ${u.since}.` : "";
}

let resetArmed = null;
$("usage-refresh").addEventListener("click", () => send("usage"));
$("usage-reset").addEventListener("click", () => {
  // two clicks, not a dialog: a count cleared by one stray click cannot be brought back
  if (!resetArmed) {
    $("usage-reset").textContent = "Click again to reset";
    resetArmed = setTimeout(() => {
      resetArmed = null;
      $("usage-reset").textContent = "Reset the count";
    }, 3000);
    return;
  }
  clearTimeout(resetArmed);
  resetArmed = null;
  $("usage-reset").textContent = "Reset the count";
  send("usage_reset");
  flash("Usage count reset");
});

/* ---------- hotkeys (roadmap item 3) ----------
The engine checks a combination's shape and that no two actions share one, and refuses the change
with an error otherwise. Only the OS knows whether another application owns a combination, so the
overlay window, which registers them, reports that back as a "hotkeys-registered" event. */

const HOTKEY_LABELS = [
  ["toggle", "Show and hide"],
  ["click_through", "Click-through"],
  ["pause", "Pause and resume"],
  ["previous", "Previous question"],
  ["next", "Next question"],
  ["more", "More lines"],
  ["retry", "Retry"],
  ["say", "What should I say?"],
  ["recap", "Recap"],
  ["ask", "Questions to ask"],
  ["shorter", "Shorter"],
];
const MODIFIER_KEYS = new Set(["Control", "Alt", "Shift", "Meta", "AltGraph"]);
let hotkeyErrors = {};            // action -> why the window could not register it ("" = fine)
let recording = null;             // the action whose box has focus

function renderHotkeys(hotkeys) {
  const box = $("hotkeys");
  if (!box.children.length) {
    for (const [action, label] of HOTKEY_LABELS) {
      const row = document.createElement("label");
      row.className = "field";
      const name = document.createElement("span");
      name.textContent = label;
      const input = document.createElement("input");
      input.type = "text";
      input.readOnly = true;
      input.id = `hk-${action}`;
      input.addEventListener("focus", () => { recording = action; renderHotkeys(current.hotkeys || {}); });
      input.addEventListener("blur", () => { recording = null; renderHotkeys(current.hotkeys || {}); });
      input.addEventListener("keydown", (e) => recordHotkey(e, action, input));
      const why = document.createElement("span");
      why.className = "hk-why";
      why.id = `hk-why-${action}`;
      why.hidden = true;
      row.append(name, input, why);
      box.append(row);
    }
  }
  for (const [action] of HOTKEY_LABELS) {
    const input = $(`hk-${action}`);
    if (recording !== action) input.value = hotkeys[action] || "";
    input.placeholder = recording === action ? "Press the keys" : "Not set";
    const why = hotkeyErrors[action] || "";
    $(`hk-why-${action}`).textContent = why ? `Not active: ${why}` : "";
    $(`hk-why-${action}`).hidden = !why;
  }
}

// The names the Rust parser accepts: KeyboardEvent.code works as is ("Space", "F5",
// "BracketLeft"); letters, digits and arrows are shortened only so they read like a shortcut.
function keyName(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (code.startsWith("Arrow")) return code.slice(5);
  return code;
}

function recordHotkey(e, action, input) {
  if (e.key === "Tab") return;                     // let focus move on
  e.preventDefault();
  if (e.key === "Escape") {
    input.blur();
    return;
  }
  const mods = [];
  if (e.ctrlKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  if (e.metaKey) mods.push("Super");
  if ((e.key === "Backspace" || e.key === "Delete") && !mods.length) {
    save({ hotkeys: { [action]: "" } }, "Cleared");
    input.blur();
    return;
  }
  if (MODIFIER_KEYS.has(e.key)) {
    input.value = `${mods.join("+")}+`;            // held so far
    return;
  }
  if (!mods.length) {
    flash("Add Ctrl, Alt or Shift: a key on its own would stop typing in every application");
    return;
  }
  save({ hotkeys: { [action]: [...mods, keyName(e.code)].join("+") } });
  input.blur();
}

if (window.__TAURI__ && window.__TAURI__.event) {
  const events = window.__TAURI__.event;
  // the overlay asks a settings window that is already open to show a section ("Change in Settings")
  events.listen("settings-section", (e) => showSection(String(e.payload || "call")));
  events.listen("settings-call", (e) => {
    pendingCall = { file: String((e.payload || {}).file || ""), review: Boolean((e.payload || {}).review) };
    openAskedCall();
  });
  events.listen("hotkeys-registered", (e) => {
    hotkeyErrors = e.payload || {};
    renderHotkeys(current.hotkeys || {});
  }).then(() => events.emit("hotkeys-query"));
}

/* ---------- talking to the engine ---------- */

function send(cmd, extra, quiet) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ cmd, ...(extra || {}) }));
    return true;
  }
  if (!quiet) flash("Not connected to the engine");
  return false;
}

function flash(text) {
  $("saved").textContent = text;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { $("saved").textContent = ""; }, 4000);
}

function save(values, note) {
  if (send("settings", { values })) flash(note || "Saved");
}

const nextCall = () => (live ? "Saved. Applies from the next call." : "Saved");

$("style").addEventListener("change", () =>
  save({ style: $("style").value }, live ? "Saved. The next question uses it." : "Saved"));
$("change-role").addEventListener("click", () => showSection("roles"));
$("no-role").addEventListener("click", () => save({ preset: "" }, nextCall()));
$("role-search").addEventListener("input", renderRoles);
$("mode").addEventListener("change", () => {
  const mode = $("mode").value;
  // a role written for the other kind of call is let go of here too; the engine does the same
  const keep = current.preset && (presetModes[current.preset] || "interview") === mode ? current.preset : "";
  save({ mode, preset: keep }, nextCall());
});
$("vendor").addEventListener("change", () => {
  const vendor = $("vendor").value;
  save({ vendor, model: null }, live ? "Switching the model for the next question" : "Saved");
  if (live) send("provider", { vendor, model: null });
});
$("model").addEventListener("change", () => {
  const model = $("model").value.trim() || null;
  save({ model }, live ? "Switching the model for the next question" : "Saved");
  if (live) send("provider", { vendor: $("vendor").value, model });
});
$("speculate").addEventListener("change", () => save({ speculate: $("speculate").checked }, nextCall()));
$("lang").addEventListener("change", () => save({ lang: $("lang").value }, nextCall()));
$("user-mic").addEventListener("change", () => save({ user_mic: $("user-mic").checked }, nextCall()));
$("stt-vendor").addEventListener("change", () => save({ stt_vendor: $("stt-vendor").value }, nextCall()));
$("whisper-model").addEventListener("change", () => save({ whisper_model: $("whisper-model").value }, nextCall()));
$("animate").addEventListener("change", () => save({ animate: $("animate").checked }));
$("pace-meter").addEventListener("change", () => save({ pace_meter: $("pace-meter").checked }));
$("text-size").addEventListener("change", () => save({ text_size: $("text-size").value }));
$("theme").addEventListener("change", () => {
  // This window changes at once; the overlay changes when the engine's broadcast reaches it.
  document.documentElement.dataset.theme = $("theme").value;
  save({ theme: $("theme").value });
});
$("ui-lang").addEventListener("change", () => {
  // like the theme: this window switches at once, the overlay when the broadcast reaches it
  if (window.fawa2lyI18n) window.fawa2lyI18n.setLang($("ui-lang").value);
  save({ ui_lang: $("ui-lang").value });
});
$("save-transcripts").addEventListener("change", () =>
  save({ save_transcripts: $("save-transcripts").checked }, nextCall()));
$("live-feedback").addEventListener("change", () =>
  save({ live_feedback: $("live-feedback").checked }, nextCall()));
$("negotiation").addEventListener("change", () =>
  save({ negotiation: $("negotiation").checked }, nextCall()));
let deleteArmed = null;
$("transcripts-delete").addEventListener("click", () => {
  // two clicks, as for the usage count: a deleted transcript cannot be brought back
  if (!deleteArmed) {
    $("transcripts-delete").textContent = "Click again to delete them";
    deleteArmed = setTimeout(() => {
      deleteArmed = null;
      $("transcripts-delete").textContent = "Delete saved transcripts";
    }, 3000);
    return;
  }
  clearTimeout(deleteArmed);
  deleteArmed = null;
  $("transcripts-delete").textContent = "Delete saved transcripts";
  if (send("transcripts_delete")) flash("Transcripts deleted");
});

// Transparency applies while the slider moves, so the user can find the value against the video
// behind the overlay; the send is debounced so a drag is not one settings file write per step.
let dragging = false;
let opacityTimer = null;
let pendingOpacity = null;        // sent, and its broadcast not back yet
const sendOpacity = () => {
  const opacity = Number($("opacity").value) / 100;
  if (send("settings", { values: { opacity } })) {
    pendingOpacity = opacity;       // only when it went out, or the slider would wait forever
    flash("Saved");
  }
};
$("opacity").addEventListener("input", () => {
  dragging = true;
  $("opacity-value").textContent = `${$("opacity").value}%`;
  clearTimeout(opacityTimer);
  opacityTimer = setTimeout(sendOpacity, 60);
});
$("opacity").addEventListener("change", () => {
  clearTimeout(opacityTimer);
  dragging = false;
  sendOpacity();
});

/* ---------- devices and API keys (M3 step 5) ---------- */

let devices = null;               // {speakers, mics} as the engine lists them, asked for on connect

function deviceOptions(select, names, chosen, fallback) {
  const present = names || [];
  const list = chosen && !present.includes(chosen) ? [...present, chosen] : present;
  select.replaceChildren(option("", fallback, !chosen),
    ...list.map((d) => option(d, present.includes(d) || !names ? d : `${d} (not connected)`, d === chosen)));
}

function renderDevices() {
  deviceOptions($("speaker-device"), devices && devices.speakers, current.speaker_device || "",
                "Windows default speakers");
  deviceOptions($("mic-device"), devices && devices.mics, current.mic_device || "",
                "Windows default microphone");
}

function keyLabel(name) {
  if (name === "DEEPGRAM_API_KEY") return "Deepgram, to listen";
  return `${name.replace(/_API_KEY$/, "").toLowerCase()} model`;
}

// Rows are built once and only their status changes, so a key being pasted is not wiped by a
// broadcast arriving mid-paste. The engine says whether a key is set; it never sends one back.
function renderKeys(keys) {
  const box = $("keys");
  for (const name of Object.keys(keys)) {
    let row = document.getElementById(`key-${name}`);
    if (!row) {
      row = document.createElement("label");
      row.className = "field";
      row.id = `key-${name}`;
      const title = document.createElement("span");
      const input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.dataset.key = name;
      row.append(title, input);
      box.append(row);
    }
    const [title, input] = row.children;
    title.textContent = `${keyLabel(name)}: ${keys[name] ? "set" : "not set"}`;
    input.placeholder = keys[name] ? "Paste a new key to replace it" : "Paste the key";
  }
}

$("speaker-device").addEventListener("change", () => save({ speaker_device: $("speaker-device").value }, nextCall()));
$("mic-device").addEventListener("change", () => save({ mic_device: $("mic-device").value }, nextCall()));
$("save-keys").addEventListener("click", () => {
  const inputs = [...$("keys").querySelectorAll("input[data-key]")];
  const values = Object.fromEntries(inputs.filter((i) => i.value.trim()).map((i) => [i.dataset.key, i.value.trim()]));
  if (!Object.keys(values).length) {
    flash("Paste a key first");
    return;
  }
  if (send("keys", { values })) {
    for (const input of inputs) input.value = "";          // a key does not stay on screen
    flash("Keys saved on this computer. They apply from the next call.");
  }
});

/* ---------- providers (2026-10-16) ---------- */
// One <details> card per provider, built once and then only updated, like the key rows above, so a
// broadcast arriving while something is being typed does not wipe it. A field the user has edited
// is marked dirty and left alone until it is saved.

function provField(label, type, name, placeholder) {
  const row = document.createElement("label");
  row.className = "field";
  const span = document.createElement("span");
  span.textContent = label;
  const input = document.createElement("input");
  input.type = type;
  input.spellcheck = false;
  input.autocomplete = "off";
  input.dataset.name = name;
  if (placeholder) input.placeholder = placeholder;
  input.addEventListener("input", () => { input.dataset.dirty = "1"; });
  row.append(span, input);
  return row;
}

function provButton(text, primary, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  if (primary) b.className = "primary";
  b.addEventListener("click", onClick);
  return b;
}

function provState(p) {
  if (!p.enabled) return ["", "Off"];
  if (!p.key_optional && !p.key_set) return ["", "Needs a key"];
  if (!p.model) return ["partial", "Pick a model"];
  return ["ready", "Ready"];
}

function setResult(card, text, kind) {
  const r = card.querySelector(".prov-result");
  r.textContent = text;
  r.className = `prov-result ${kind || ""}`;
}

function buildProvider(p) {
  const card = document.createElement("details");
  card.className = "prov";
  card.id = `prov-${p.id}`;
  const head = document.createElement("summary");
  const dot = document.createElement("span");
  dot.className = "prov-dot";
  const name = document.createElement("span");
  name.className = "prov-name";
  name.setAttribute("translate", "no");
  const state = document.createElement("span");
  state.className = "prov-state";
  head.append(dot, name, state);

  const body = document.createElement("div");
  body.className = "prov-body";
  const use = document.createElement("label");
  use.className = "check";
  const useBox = document.createElement("input");
  useBox.type = "checkbox";
  useBox.dataset.name = "enabled";
  useBox.addEventListener("change", () => send("provider_save", { vendor: p.id, values: { enabled: useBox.checked } }));
  const useText = document.createElement("span");
  useText.textContent = "Offer this provider in the model list";
  use.append(useBox, useText);
  body.append(use);

  if (p.key_env) {
    const keyRow = document.createElement("div");
    keyRow.className = "row2";
    const keyField = provField("Key", "password", "key", "");
    keyRow.append(keyField);
    body.append(keyRow);
  }
  const where = document.createElement("div");
  where.className = "row2";
  where.append(provField("Address", "text", "base_url", ""), provField("Temperature", "text", "temperature", ""));
  body.append(where);

  const modelRow = document.createElement("div");
  modelRow.className = "row2";
  const modelField = provField("Model id", "text", "model", "");
  const list = document.createElement("datalist");
  list.id = `models-${p.id}`;
  modelField.querySelector("input").setAttribute("list", list.id);
  modelField.append(list);
  const modelPick = document.createElement("label");
  modelPick.className = "field";
  const pickSpan = document.createElement("span");
  pickSpan.textContent = "Models from the provider";
  const pick = document.createElement("select");
  pick.dataset.name = "pick";
  pick.append(option("", "Test the connection to list them", true));
  pick.addEventListener("change", () => {
    if (!pick.value) return;
    const m = card.querySelector('input[data-name="model"]');
    m.value = pick.value;
    m.dataset.dirty = "1";
  });
  modelPick.append(pickSpan, pick);
  modelRow.append(modelField, modelPick);
  body.append(modelRow);

  const actions = document.createElement("div");
  actions.className = "actions";
  actions.append(
    provButton("Save", true, () => saveProvider(p.id)),
    provButton("Test connection", false, () => {
      setResult(card, "Checking...");
      send("provider_check", { vendor: p.id });
    }),
  );
  if (p.key_env) {
    actions.append(provButton("Remove key", false, () => {
      if (send("keys", { values: { [p.key_env]: "" } })) flash("Key removed");
    }));
  }
  if (p.kind === "custom") {
    actions.append(provButton("Remove endpoint", false, () => {
      if (confirm(`Remove ${p.label} and its key?`)) send("provider_remove", { vendor: p.id });
    }));
  }
  body.append(actions);
  const result = document.createElement("p");
  result.className = "prov-result";
  result.setAttribute("aria-live", "polite");
  body.append(result);

  card.append(head, body);
  return card;
}

function updateProvider(card, p) {
  const [dotClass, stateText] = provState(p);
  card.querySelector(".prov-dot").className = `prov-dot ${dotClass}`;
  card.querySelector(".prov-name").textContent = p.label;
  card.querySelector(".prov-state").textContent = stateText;
  card.querySelector('input[data-name="enabled"]').checked = p.enabled;
  const set = (name, value, placeholder) => {
    const input = card.querySelector(`input[data-name="${name}"]`);
    if (!input) return;
    if (placeholder !== undefined) input.placeholder = placeholder;
    if (input.dataset.dirty || document.activeElement === input) return;
    input.value = value;
  };
  set("base_url", p.base_url === p.default_base_url ? "" : p.base_url, p.default_base_url || p.base_url);
  set("model", p.model === p.default_model ? "" : p.model,
      p.default_model ? `Default: ${p.default_model}` : "Pick or type a model id");
  set("temperature", p.temperature == null ? "" : String(p.temperature), "Provider default");
  set("key", "", p.key_set ? "Set. Paste a new key to replace it" : p.key_optional ? "Optional" : "Paste the key");
}

function renderProviders(catalog) {
  const boxes = { local: $("prov-local"), cloud: $("prov-cloud"), custom: $("prov-custom") };
  const seen = new Set();
  for (const p of catalog) {
    seen.add(`prov-${p.id}`);
    let card = document.getElementById(`prov-${p.id}`);
    if (!card) {
      card = buildProvider(p);
      boxes[p.kind].append(card);
    }
    updateProvider(card, p);
  }
  for (const box of Object.values(boxes)) {
    for (const card of [...box.children]) if (!seen.has(card.id)) card.remove();
  }
}

function saveProvider(id) {
  const card = $(`prov-${id}`);
  const values = {};
  for (const name of ["base_url", "model", "temperature"]) {
    const input = card.querySelector(`input[data-name="${name}"]`);
    if (input && input.dataset.dirty) values[name] = input.value.trim();
  }
  const keyInput = card.querySelector('input[data-name="key"]');
  const key = keyInput ? keyInput.value.trim() : "";
  const p = (current.provider_catalog || []).find((x) => x.id === id);
  if (key && p && p.key_env) send("keys", { values: { [p.key_env]: key } });
  if (Object.keys(values).length && !send("provider_save", { vendor: id, values })) return;
  if (!key && !Object.keys(values).length) {
    flash("Nothing changed");
    return;
  }
  for (const input of card.querySelectorAll("input[data-dirty]")) delete input.dataset.dirty;
  if (keyInput) keyInput.value = "";                      // a key does not stay on screen
  flash(nextCall());
}

function onProviderCheck(e) {
  const card = $(`prov-${e.vendor}`);
  if (!card) return;
  if (!e.ok) {
    setResult(card, `Could not connect: ${e.error}`, "err");
    return;
  }
  const models = e.models || [];
  const list = $(`models-${e.vendor}`);
  list.replaceChildren(...models.map((m) => option(m, m, false)));
  const pick = card.querySelector('select[data-name="pick"]');
  pick.replaceChildren(option("", models.length ? `${models.length} models: choose one` : "The provider listed no models", true),
    ...models.map((m) => option(m, m, false)));
  setResult(card, `Connected in ${e.ms} ms.`, "ok");
}

function onScanLocal(e) {
  const found = (e.results || []).filter((r) => r.ok);
  for (const r of e.results || []) onProviderCheck(r.ok ? r : { ...r, error: "nothing is listening at that address" });
  $("scan-status").textContent = found.length
    ? `Found: ${found.map((r) => labelOf(r.vendor)).join(", ")}. Turned on.`
    : "No local model server answered. Start one (for example Ollama or LM Studio), then scan again.";
  $("scan-local").disabled = false;
}

const labelOf = (id) => ((current.provider_catalog || []).find((p) => p.id === id) || { label: id }).label;

$("scan-local").addEventListener("click", () => {
  if (!send("scan_local")) return;
  $("scan-local").disabled = true;
  $("scan-status").textContent = "Scanning...";
});

$("add-endpoint-btn").addEventListener("click", () => {
  const values = { label: $("new-label").value.trim(), base_url: $("new-url").value.trim(),
                   model: $("new-model").value.trim(), key: $("new-key").value.trim() };
  if (!values.label || !values.base_url) {
    flash("Give the endpoint a name and an address");
    return;
  }
  if (send("provider_add", { values })) {
    for (const id of ["new-label", "new-url", "new-model", "new-key"]) $(id).value = "";
    $("add-endpoint").open = false;
    flash("Endpoint added. Open it to test the connection.");
  }
});

/* ---------- documents (roadmap item 5) ---------- */

function documentRow(d, mode) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.className = "doc-name";
  name.textContent = d.name;
  name.title = d.name;
  const count = document.createElement("span");
  count.className = "doc-words";
  count.textContent = `${d.words.toLocaleString()} words`;
  const drop = document.createElement("button");
  drop.type = "button";
  drop.textContent = "✕";
  drop.title = `Remove ${d.name}`;
  drop.addEventListener("click", () => send("cv", { mode, remove: d.name }));
  li.append(name, count, drop);
  return li;
}

$("pick-cv").addEventListener("click", () => $("cv-file").click());
$("drop-cv").addEventListener("click", () => send("cv", { mode: $("mode").value }));
$("cv-file").addEventListener("change", async () => {
  // One message per file, in order: the engine checks the word budget as each one arrives, so a
  // file that does not fit is refused on its own and the ones before it stay.
  const files = [...($("cv-file").files || [])];
  $("cv-file").value = "";
  for (const file of files) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    if (send("cv", { mode: $("mode").value, name: file.name, data: btoa(binary) })) flash(`Reading ${file.name}`);
  }
});

/* ---------- events ---------- */

function handle(e) {
  if (e.providers) providers = e.providers;
  if (e.presets) presets = e.presets;
  if (e.preset_modes) presetModes = e.preset_modes;
  if (e.preset_info) presetInfo = e.preset_info;
  switch (e.type) {
    case "hello":
      $("conn").textContent = "Connected";
      send("devices");                  // once per connection: listing opens PortAudio, which takes a moment
      if (section === "model") send("usage", {}, true);
      send("calls", {}, true);
      openAskedCall();
      return;
    case "devices":
      devices = { speakers: e.speakers || [], mics: e.mics || [] };
      renderDevices();
      return;
    case "settings":
      render(e.settings);
      return;
    case "calls":
      calls = e.calls || [];
      renderCalls();
      return;
    case "speedtest":
      onSpeedtest(e);
      return;
    case "role":
      onRole(e);
      return;
    case "jobfit":
      onJobfit(e);
      return;
    case "dictation":
      onDictation(e);
      return;
    case "usage":
      onUsage(e);
      return;
    case "provider_check":
      onProviderCheck(e);
      return;
    case "scan_local":
      onScanLocal(e);
      return;
    case "error":
      if (analyzing) {
        analyzing = false;
        $("role-analyze").disabled = false;
        $("role-analyze").classList.remove("loading");
        setRoleStatus(e.text);
      }
      if (jobfitting) {
        jobfitting = false;
        $("jobfit-run").disabled = false;
        $("jobfit-run").classList.remove("loading");
        setJobfitStatus(e.text);
      }
      flash(e.text);
      return;
    default:
      return;
  }
}

function connect() {
  socket = new WebSocket(WS_URL);
  socket.addEventListener("message", (m) => {
    try {
      handle(JSON.parse(m.data));
    } catch (err) {
      console.error("bad message", err);
    }
  });
  socket.addEventListener("close", () => {
    $("conn").textContent = "Engine offline, retrying";
    setTimeout(connect, 1000);
  });
  socket.addEventListener("error", () => socket.close());
}

showSection(section);
connect();
