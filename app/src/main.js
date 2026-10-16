/*
The overlay, on the design canvas of 2026-09-10.

It talks to the Python engine over the WebSocket from step 2 and renders its events:

  hello          the providers this machine can use, for the model picker
  status         the dot, the state line, the Pause control and the active model
  status: idle   no call is running: draw the setup screen from the presets, the providers and
                 the last session's choices, all of which the engine owns rather than this page
  interim        the live caption at the bottom, replaced as it grows
  question       a question was heard and the model has been asked. Its number is the one `line`
                 and `assist` use; the same number again means it was asked again (Retry, or a
                 question that turned out longer), and its card is replaced in place.
  line           one line to say, appended the moment the model finishes writing it
  assist         the finished record for a question: the authoritative list, and the errors

Every question of the call is kept, with its lines, so the user can step back to one that was
answered while they were still talking and forward again (the arrows, or Left/Right). A question
that arrives while an older one is on screen does not yank the view away; "Latest" lights up.

The model can be changed before a call (setup screen) and during one (the picker in the status
strip). rehearsal6 is why: the pipeline closed turns in 1.8 s at p50 while the free Gemini tier
took 43 s to a first token, and the only fix available mid-interview is a different provider.

No framework and no build step: this file is loaded directly by the Tauri window, and by any
browser pointed at it, which is how it is tested without compiling anything.
*/

const WS_URL = `ws://127.0.0.1:${new URLSearchParams(location.search).get("port") || 8765}`;
const RECONNECT_MS = 1000;
const MAX_UPLOAD_BYTES = 10 * 2 ** 20;   // fawa2ly/limits.py MAX_UPLOAD_BYTES: a larger file is refused here

const el = {};
for (const id of ["panel", "dot", "state", "provider", "providerPick", "pause", "close", "notice",
                  "noticeTitle", "noticeText", "noticeNote", "reviewCall", "noticeActions", "reconnect", "current", "question",
                  "lines", "skeleton", "writing", "failure", "failureTitle", "failureText", "retry",
                  "earlier", "earlierLabel", "earlierList", "captionText", "setup", "roleName", "changeRole", "lang",
                  "vendor", "speculate", "userMic", "cvState", "pickCv", "dropCv", "cvFile", "start",
                  "setupError", "end", "newCall", "nav", "prev", "pos", "next", "latest", "noCv",
                  "cvWarning", "style", "more", "openSettings", "hide", "clickThrough", "mode",
                  "cvLabel", "docs", "pace", "feedback", "feedbackVerdict", "feedbackNote", "askedLabel",
                  "callActions", "actSay", "actRecap", "actAsk", "shorter",
                  "styleBadge"]) {
  el[id] = document.getElementById(id.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase()));
}

let socket = null;
let live = false;                 // the engine is running: listening or paused
let paused = false;
let providers = [];               // [{vendor, model, local}] this machine can use
let activeProvider = "";          // "vendor/model", as the engine reports it
const history = [];               // every question of this call, oldest first
let view = null;                  // index into history, or null to follow the newest
let unseen = false;               // a newer question arrived while an older one was on screen
let logPath = "";
let summary = null;               // the ended call's summary, from the engine (roadmap item 6)
let stopError = "";               // why the engine ended the call when the user did not: status "stopped" error
let pace = null;                  // the user's latest pace, from the engine (roadmap item 8)
let lastIdle = null;              // the setup screen's contents, kept while the call summary is up
let cvWords = null;               // background the running call has; 0 means none at all
let callStyle = "full";           // the profile chosen for the call; "dynamic" = the model picks each
// What the badge says once Dynamic has placed an answer. The card already looks different in each
// shape; the badge names it, so a user who is handed cues knows it was a choice and not a fault.
const STYLE_LABEL = { full: "Lines", keywords: "Keywords", direction: "Direction" };
const prefs = { animate: true, textSize: "medium", paceMeter: true };
const TYPE_CPS = 140;             // characters per second when text is typed in
const REDUCED_MOTION = Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

/* ---------- the questions of this call ---------- */

function itemFor(n, question) {
  let item = history.find((q) => q.n === n);
  if (!item) {
    item = { n, question: question || "", lines: [], phase: "waiting", done: false, error: null,
             feedback: null, style: "",     // style: the shape Dynamic chose for this one (item 27)
             action: "" };                  // the on-demand action that opened it, if any (item 17)
    history.push(item);
    if (view !== null) unseen = true;
  }
  return item;
}

function shownIndex() {
  return view === null ? history.length - 1 : view;
}

function shown() {
  return history[shownIndex()] || null;
}

function goTo(index) {
  if (index < 0 || index >= history.length) return;
  // after the call, "nothing opened" means the summary, so even the last question is opened by index
  const ended = el.panel.dataset.state === "stopped";
  view = !ended && index >= history.length - 1 ? null : index;
  if (view === null) unseen = false;
  renderQuestion();
}

/* ---------- painting ---------- */

function setPanel(state) {
  el.panel.dataset.state = state;
  const item = shown();
  el.panel.dataset.busy = item && view === null && item.phase === "waiting" ? "1" : "0";
}

function setCaption(text, muted) {
  const span = el.captionText;
  span.textContent = text;
  span.classList.toggle("muted", Boolean(muted));
  // The newest words are the ones to read: a caption wider than the strip is scrolled to its end
  // and fades at the start; a short one is left alone (QA 2026-10-17).
  const clipped = span.scrollWidth > span.clientWidth + 1;
  span.classList.toggle("clip", clipped);
  if (clipped) span.scrollLeft = getComputedStyle(span).direction === "rtl" ? -span.scrollWidth : span.scrollWidth;
}

function notice(title, text, action, note) {
  el.noticeTitle.textContent = title;
  el.noticeText.textContent = text;
  el.noticeNote.textContent = note || "";
  el.noticeNote.hidden = !note;
  el.reviewCall.hidden = true;
  el.reconnect.hidden = action !== "reconnect";
  el.newCall.hidden = action !== "new-call";
  el.noticeActions.hidden = !action;
  el.notice.hidden = false;
}

// The call's summary (roadmap item 6): one line of numbers, and whether the transcript was kept.
function summaryLine(s) {
  const minutes = Math.floor(s.duration_s / 60);
  const parts = [`${minutes ? `${minutes} min ` : ""}${s.duration_s % 60} s`,
                 `${s.heard} question${s.heard === 1 ? "" : "s"} heard`, `${s.answered} answered`];
  if (s.median_first_line_ms != null) {
    parts.push(`first line in ${(s.median_first_line_ms / 1000).toFixed(1)} s, median`);
  }
  if (s.you_wpm != null) {                        // the user's own pace, when their words were heard
    parts.push(`you: ${s.you_wpm} wpm`);
    if (s.you_fillers != null) parts.push(`${s.you_fillers} filler${s.you_fillers === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function transcriptLine(s) {
  if (s.transcript) return `Transcript saved in ${s.transcript}.`;
  if (s.transcript_error) return `Transcript not saved: ${s.transcript_error}`;
  return "Transcript not saved. To keep calls, turn it on in Settings, under Call and answers.";
}

// The ended call: its summary and its questions. A question opened from the list shows its card,
// and "Call summary" comes back here. renderQuestion lands here too while nothing is opened: the
// last line of an answer can still be appearing when the call stops, and its redraw used to put
// that card back over the summary (browser check of roadmap item 6, 2026-09-11).
function showEnded() {
  el.current.hidden = true;
  el.nav.hidden = true;
  const detail = summary ? summaryLine(summary)
    : logPath ? `Everything said and suggested is in ${logPath}.` : "This session is finished.";
  // When the engine ended the call itself it says why: a missing key, a device that would not open, a
  // dropped speech connection. That used to be thrown away, and "Call ended / This session is
  // finished" was all anyone saw of a failed start (audit F1).
  notice(stopError ? (summary ? "The call stopped." : "The call could not start.") : "Call ended.",
         stopError || detail,
         "new-call",
         summary ? (stopError ? `${detail}. ${transcriptLine(summary)}` : transcriptLine(summary)) : "");
  // a saved call with the user's own words can be reviewed (roadmap item 7)
  el.reviewCall.hidden = !(summary && summary.transcript && summary.has_you);
  renderEarlier();
}

function renderEarlier() {
  const ended = el.panel.dataset.state === "stopped" && view === null;
  const before = history.slice(0, Math.max(shownIndex(), 0)).reverse();
  const items = ended ? history.slice().reverse() : before.slice(0, 2);
  el.earlier.hidden = items.length === 0;
  el.earlierLabel.textContent = ended ? "This call" : "Earlier";
  el.earlierList.replaceChildren(...items.map((q) => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.className = "q-text";
    text.textContent = q.question;
    text.translate = false;                       // what was asked, not the app's words (i18n.js)
    text.dir = "auto";
    li.append(text);
    // An answer usually ends when the next question arrives, so its feedback lands while the next
    // card is on screen: the Earlier list is where it is seen without going back (roadmap item 9).
    if (q.feedback) {
      const chip = document.createElement("span");
      chip.className = `q-verdict ${q.feedback.verdict}`;
      chip.textContent = FEEDBACK_LABEL[q.feedback.verdict] || q.feedback.verdict;
      chip.title = q.feedback.note;
      li.append(chip);
    }
    // after the call, each question says how long its first line took (roadmap item 6)
    const timed = ended && summary ? summary.questions.find((x) => x.n === q.n) : null;
    if (timed) {
      const time = document.createElement("span");
      time.className = "q-time";
      time.textContent = timed.first_line_ms != null ? `${(timed.first_line_ms / 1000).toFixed(1)} s` : "-";
      li.append(time);
    }
    li.title = "Show this question";
    li.addEventListener("click", () => goTo(history.indexOf(q)));
    return li;
  }));
}

/* Text typed in, left to right, the way the maintainer asked for it: new text reads as arriving
   rather than appearing. Only text that is new is typed; a card revisited with the arrows, or a
   line already shown, is drawn at once. */
function setText(node, text) {
  node._typing = null;
  node.textContent = text;
  node.dataset.full = text;
}

function typeInto(node, text, onDone) {
  const token = {};
  node._typing = token;
  node.dataset.full = text;
  node.textContent = "";
  const start = performance.now();
  const finish = () => {
    if (node._typing !== token) return;
    setText(node, text);
    if (onDone) onDone();
  };
  const step = (now) => {
    if (node._typing !== token) return;
    const count = Math.min(text.length, Math.floor(((now - start) / 1000) * TYPE_CPS));
    node.textContent = text.slice(0, count);
    if (count < text.length) requestAnimationFrame(step);
    else finish();
  };
  requestAnimationFrame(step);
  // Animation frames stop while the window is hidden or throttled, and the full text must land
  // anyway: an overlay hidden with the Hide button and shown again mid-call would otherwise sit
  // on half a question until something else redrew it.
  setTimeout(finish, (text.length / TYPE_CPS) * 1000 + 400);
}

function renderQuestionText(item, newest) {
  if (el.question.dataset.full === item.question) return;
  if (prefs.animate && newest && !item.typedQuestion) typeInto(el.question, item.question);
  else setText(el.question, item.question);
  item.typedQuestion = true;
}

function renderLines(item, newest) {
  const list = el.lines;
  const children = [...list.children];
  const same = list.dataset.n === String(item.n)
    && children.length <= item.lines.length
    && children.every((li, k) => li.dataset.full === item.lines[k]);
  if (!same) {
    list.replaceChildren();
    list.dataset.n = String(item.n);
  }
  // One line at a time (maintainer, 2026-09-11): a line the user has not seen starts only once the one
  // before it is fully on screen, typed in, or with typing off after a short beat. Before, a fast
  // model's lines landed within a few hundred milliseconds of each other and typed in together.
  for (let k = list.children.length; k < item.lines.length; k++) {
    const text = item.lines[k];
    const li = document.createElement("li");
    li.dir = "auto";                              // an Arabic line reads, and aligns, right to left
    if (!newest || k < (item.typed || 0)) {
      setText(li, text);
      list.appendChild(li);
      continue;
    }
    if (item.revealing) break;
    item.revealing = true;
    item.typed = k + 1;
    list.appendChild(li);
    const next = () => {
      item.revealing = false;
      if (shown() === item) renderQuestion();
    };
    if (prefs.animate) typeInto(li, text, next);
    else {
      setText(li, text);
      setTimeout(next, LINE_GAP_MS);
    }
    break;
  }
  const last = list.children.length - 1;
  const pending = item.revealing || list.children.length < item.lines.length;
  [...list.children].forEach((li, k) => {
    li.classList.toggle("new", newest && k === last && item.phase === "lines" && (!item.done || pending));
  });
}

const LINE_GAP_MS = 250;

function applyPrefs(s) {
  prefs.animate = s.animate !== false && !REDUCED_MOTION;
  prefs.textSize = s.text_size || "medium";
  document.documentElement.dataset.textSize = prefs.textSize;
  document.documentElement.dataset.theme = s.theme || "dark";
  if (window.fawa2lyI18n) window.fawa2lyI18n.setLang(s.ui_lang);
  const opacity = Number(s.opacity);
  if (Number.isFinite(opacity)) document.documentElement.style.setProperty("--panel-alpha", String(opacity));
  applyHotkeys(s.hotkeys);
  prefs.paceMeter = s.pace_meter !== false;
  renderPace();
}

/* Speaking pace (roadmap item 8): words per minute over the user's last 40 words and filler words so
   far, from the engine's pace events, shown while a call is on and the meter is turned on. The
   zones are a starting point for English, not a measured standard: under PACE_SLOW reads slow, over
   PACE_FAST fast. A call in another language shows the number alone, since its words are not the
   same length and its fillers are not counted. */
const PACE_SLOW = 110;
const PACE_FAST = 170;

function renderPace() {
  const on = Boolean(prefs.paceMeter && live && pace && pace.wpm != null);
  el.pace.hidden = !on;
  if (!on) return;
  el.pace.textContent = pace.fillers == null
    ? `${pace.wpm} wpm`
    : `${pace.wpm} wpm · ${pace.fillers} filler${pace.fillers === 1 ? "" : "s"}`;
  el.pace.dataset.zone = !pace.english ? "" : pace.wpm < PACE_SLOW ? "slow" : pace.wpm > PACE_FAST ? "fast" : "ok";
}

function renderCvBadge() {
  // rehearsal7 ran a whole call with no background and nothing on screen said so
  el.noCv.hidden = !(live && cvWords === 0);
  el.noCv.textContent = (DOCUMENT_TEXT[callMode] || DOCUMENT_TEXT.interview).badge;
}

function renderQuestion() {
  if (el.panel.dataset.state === "stopped" && view === null) {
    showEnded();
    return;
  }
  const item = shown();
  if (!item) {
    el.current.hidden = true;
    return;
  }
  const i = shownIndex();
  const newest = i === history.length - 1;
  const ended = el.panel.dataset.state === "stopped";
  el.notice.hidden = true;
  el.current.hidden = false;
  el.current.dataset.phase = item.phase;
  el.current.dataset.done = item.done ? "1" : "0";
  // On Dynamic each card has its own shape, so it is set per card rather than per call: moving
  // back to an earlier question has to bring that question's arrows (or lack of them) with it.
  el.current.dataset.style = item.style || callStyle;
  // Nobody asked for an on-demand card, so it is not labelled "Asked" (roadmap item 17), and its
  // title is the app's own words rather than what someone said, so it is left translatable.
  el.askedLabel.textContent = item.action ? "On demand" : "Asked";
  el.question.translate = !item.action ? false : true;
  el.styleBadge.hidden = !(callStyle === "dynamic" && item.style);
  el.styleBadge.textContent = STYLE_LABEL[item.style] || "";
  renderQuestionText(item, newest);
  renderLines(item, newest);
  // live feedback on the user's answer to this question (roadmap item 9)
  el.feedback.hidden = !item.feedback;
  if (item.feedback) {
    el.feedback.dataset.verdict = item.feedback.verdict;
    el.feedbackVerdict.textContent = FEEDBACK_LABEL[item.feedback.verdict] || item.feedback.verdict;
    el.feedbackNote.textContent = item.feedback.note;
  }
  if (item.phase === "failed") {
    el.failureTitle.textContent = item.error ? "That one did not come back." : "No lines for this one.";
    el.failureText.textContent = item.error
      ? String(item.error).slice(0, 140)
      : "Keep talking, earlier answers are still below.";
    el.retry.hidden = !live;
  }
  const answered = live && item.done && item.phase === "lines" && !item.moreLoading;
  el.more.hidden = !answered;
  el.shorter.hidden = !answered;
  el.nav.hidden = history.length < 2 && !ended;
  el.pos.textContent = `${i + 1} / ${history.length}`;
  el.prev.disabled = i <= 0;
  el.next.disabled = newest;
  // after the call this button leads back to the summary, from any question
  el.latest.hidden = newest && !ended;
  el.latest.textContent = ended ? "Call summary" : "Latest";
  el.latest.classList.toggle("unseen", unseen);
  el.panel.dataset.busy = newest && item.phase === "waiting" ? "1" : "0";
  renderEarlier();
}

function setActiveProvider(label) {
  activeProvider = label;
  el.provider.textContent = label;
}

function renderProviderPick() {
  const inCall = live && el.panel.dataset.state !== "stopped";
  const canSwitch = inCall && providers.length > 1;
  el.providerPick.hidden = !canSwitch;
  el.provider.hidden = canSwitch;
  if (!canSwitch) return;
  const activeVendor = activeProvider.split("/")[0];
  el.providerPick.replaceChildren(...providers.map((p) => {
    const o = document.createElement("option");
    o.value = p.vendor;
    o.textContent = p.vendor === activeVendor && activeProvider ? activeProvider : `${p.vendor}/${p.model}`;
    o.selected = p.vendor === activeVendor;
    return o;
  }));
}

/* The call's mode (roadmap item 4): an interview reads a CV, a meeting reads an agenda or project
   notes, and each offers only the roles written for it. */
const DOCUMENT_TEXT = {
  interview: {
    label: "Your background",
    none: "No document. Suggestions will be general.",
    warn: "No CV loaded. The model cannot use your experience, and every answer will be generic.",
    startWithout: "Start without a CV",
    badge: "No CV",
  },
  meeting: {                      // roadmap item 18: an agenda or project notes
    label: "Meeting material",
    none: "No document. Suggestions will not state project facts or figures.",
    warn: "No meeting material loaded. The model cannot state project facts, figures or dates.",
    startWithout: "Start without meeting material",
    badge: "No material",
  },
};
let callMode = "interview";       // the running call's mode, as the engine reports it

// The setup screen names the role and leaves choosing it to Settings (maintainer's request, 2026-09-11):
// with nearly 80 roles, sectioned and searchable there, a dropdown here would be a worse copy of that list.
// A saved role written for the other kind of call is no role for this one, and is never sent.
function roleFor(event, mode) {
  const name = ((event && event.settings) || {}).preset || "";
  const modes = (event && event.preset_modes) || {};
  return name && ((event && event.presets) || []).includes(name) && (modes[name] || "interview") === mode ? name : "";
}

function renderRole(event, mode) {
  const name = roleFor(event, mode);
  const info = (event && event.preset_info) || {};
  el.roleName.textContent = name ? (info[name] && info[name].title) || name : "No role";
  el.roleName.classList.toggle("none", !name);
}

// The documents stored for one kind of call (roadmap item 5): a row each, with its words and a
// remove button, and the total against the budget the engine enforces.
function renderDocumentText(mode, s) {
  const t = DOCUMENT_TEXT[mode] || DOCUMENT_TEXT.interview;
  const docs = (s.documents || {})[mode] || [];
  const words = docs.reduce((sum, d) => sum + d.words, 0);
  el.cvLabel.textContent = t.label;
  el.cvWarning.textContent = t.warn;
  el.cvWarning.hidden = docs.length > 0;
  setStarting(false);             // the setup screen is back: a start that failed is over
  el.start.textContent = docs.length ? "Start listening" : t.startWithout;
  el.cvState.textContent = docs.length
    ? `${words.toLocaleString()} of ${(s.document_words_limit || 0).toLocaleString()} words. Suggestions will use ${docs.length === 1 ? "it" : "them"}.`
    : t.none;
  el.docs.replaceChildren(...docs.map((d) => {
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
  }));
  el.dropCv.hidden = docs.length < 2;
  el.pickCv.textContent = docs.length ? "Add more" : "Choose files";
}

function renderSetup(event) {
  const s = event.settings || {};
  const mode = s.mode || "interview";
  for (const option of el.mode.options) option.selected = option.value === mode;
  renderRole(event, mode);
  for (const option of el.lang.options) option.selected = option.value === (s.lang || "en");
  const choices = event.providers || providers;
  if (choices.length) {
    el.vendor.replaceChildren(...choices.map((p) => {
      const o = document.createElement("option");
      o.value = p.vendor;
      o.textContent = `${p.vendor} - ${p.model}`;
      o.selected = p.vendor === (s.vendor || "");
      return o;
    }));
  } else {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "No model has a key yet: add one under API keys";
    el.vendor.replaceChildren(o);
  }
  el.speculate.checked = s.speculate !== false;
  for (const option of el.style.options) option.selected = option.value === (s.style || "full");
  el.userMic.checked = Boolean(s.user_mic);
  renderDocumentText(mode, s);
}

function showSetup(event) {
  lastIdle = event || lastIdle;
  history.length = 0;
  summary = null;
  stopError = "";
  pace = null;
  view = null;
  unseen = false;
  setPanel("idle");
  el.state.textContent = "Not in a call";
  el.pause.disabled = true;
  el.end.hidden = true;
  el.notice.hidden = true;
  el.current.hidden = true;
  el.earlier.hidden = true;
  el.setup.hidden = false;
  if (lastIdle) renderSetup(lastIdle);
  renderProviderPick();
  setCaption("not listening", true);
}

/* ---------- events from the engine ---------- */

function onStatus(event) {
  if (event.state === "device") return;
  if (event.providers) providers = event.providers;
  if (event.settings) applyPrefs(event.settings);
  if (event.cv_words !== undefined) cvWords = event.cv_words;
  if (event.mode) callMode = event.mode;   // the label follows it, per card, in renderQuestion
  if (event.style) {
    callStyle = event.style;                                    // direction lines show as arrows
    el.current.dataset.style = event.style;
  }
  if (event.state !== "idle") setStarting(false);
  if (event.state === "idle") {
    live = false;
    paused = false;
    // A call that just ended leaves its summary up: the server is ready for the next one, but
    // the last thing the user saw should not be replaced by a form they did not ask for.
    lastIdle = event;
    if (el.panel.dataset.state !== "stopped") showSetup(event);
    return;
  }
  el.setup.hidden = true;
  el.end.hidden = false;
  if (event.provider) setActiveProvider(event.provider);
  if (event.log) logPath = event.log;

  if (event.state === "listening") {
    live = true;
    paused = false;
    setPanel("listening");
    el.state.textContent = "Listening";
    el.pause.textContent = "Pause";
    el.pause.disabled = false;
    if (history.length) {
      renderQuestion();
    } else {
      setCaption("listening", true);
      notice("", "Nothing asked yet. Lines appear here the moment a question is heard.", null);
    }
  } else if (event.state === "paused") {
    live = true;
    paused = true;
    setPanel("paused");
    el.state.textContent = "Paused, not listening";
    el.pause.textContent = "Resume";
    el.pause.disabled = false;
    setCaption("captions off while paused", true);
  } else if (event.state === "stopped") {
    live = false;
    view = null;
    stopError = event.error || "";
    if (onStopped) onStopped();                    // the window was closed on this call: now it can go
    setPanel("stopped");
    el.state.textContent = stopError ? "Call stopped"
      : `Call ended, ${history.length} question${history.length === 1 ? "" : "s"}`;
    el.end.hidden = true;
    setCaption("not listening", true);
    showEnded();
  }
  renderProviderPick();
  renderCvBadge();
  renderPace();
  el.callActions.hidden = !live;                    // actions on demand, while a call is on (item 17)
}

// Live feedback (roadmap item 9): the verdict on the user's answer to question n, from the engine.
const FEEDBACK_LABEL = { on_point: "On point", missed_point: "Missed the point", ran_long: "Ran long",
                         drifted: "Drifted" };

function onFeedback(event) {
  const item = history.find((q) => q.n === event.n);
  if (!item) return;
  item.feedback = { verdict: event.verdict, note: event.note || "" };
  renderQuestion();
}

// Dynamic (roadmap item 27): the shape for one answer, sent before its first line.
function onStyle(event) {
  const item = itemFor(event.n, "");
  item.style = event.style;
  if (item === shown()) renderQuestion();
}

function onQuestion(event) {
  const item = itemFor(event.n, event.text);
  item.action = event.action || "";
  // A number seen before was asked again (Retry, or the question turned out longer): same card.
  item.question = event.text;
  item.lines = [];
  item.phase = "waiting";
  item.done = false;
  item.error = null;
  item.typed = 0;
  item.revealing = false;         // a re-asked question does not wait on the old answer's reveal
  item.moreLoading = false;
  renderQuestion();
}

function onLine(event) {
  const item = itemFor(event.n);
  item.lines.push(event.text);
  item.phase = "lines";
  renderQuestion();
}

function onAssist(a) {
  const item = itemFor(a.n, a.question);
  if (a.more) {
    // lines from the More button have already streamed onto the card, which keeps its answer
    item.done = true;
    item.moreLoading = false;
    renderQuestion();
    return;
  }
  item.question = a.question || item.question;
  item.done = true;
  // The streamed lines and the record come from the same filter, so this only fills in what a
  // dropped connection missed.
  if (a.bullets.length) {
    item.lines = a.bullets;
    item.phase = "lines";
    item.error = null;
  } else {
    item.phase = "failed";
    item.error = a.error || null;
  }
  renderQuestion();
}

function handle(event) {
  switch (event.type) {
    case "hello":
      if (event.providers) providers = event.providers;
      return;
    case "settings":
      applyPrefs(event.settings || {});
      if (event.providers) providers = event.providers;
      if (lastIdle) {
        lastIdle = { ...lastIdle, settings: event.settings,
                     providers: event.providers || lastIdle.providers,
                     presets: event.presets || lastIdle.presets,
                     preset_modes: event.preset_modes || lastIdle.preset_modes,
                     preset_info: event.preset_info || lastIdle.preset_info };
        if (!el.setup.hidden) renderSetup(lastIdle);
      }
      renderProviderPick();
      return;
    case "style":    return onStyle(event);
    case "status":   return onStatus(event);
    case "question": return onQuestion(event);
    case "line":     return onLine(event);
    case "assist":   return onAssist(event.assist);
    case "summary":  summary = event.summary; return;   // sent just before "stopped"
    case "pace":     pace = event; renderPace(); return;
    case "feedback": return onFeedback(event);
    case "interim":  return setCaption(event.text, false);
    case "notice":   // the checks before a call: the model and the listening service (2026-10-16)
      if (el.start.disabled) el.start.textContent = event.text;
      return;
    case "error":
      if (!el.setup.hidden) {
        setStarting(false);
        el.setupError.textContent = event.text;
        el.setupError.hidden = false;
      } else {
        // during a call: a failed model switch, say. Say it where the eye already goes, and put
        // the picker back on the model that is actually running.
        setCaption(event.text, true);
        renderProviderPick();
      }
      return;
    default:         return;   // log, piece, utterance, pong: nothing to draw
  }
}

/* ---------- commands to the engine ---------- */

function send(cmd, extra) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ cmd, ...(extra || {}) }));
    return true;
  }
  return false;
}

el.pause.addEventListener("click", () => send(paused ? "resume" : "pause"));
el.retry.addEventListener("click", () => {
  const item = shown();
  if (item && send("retry", { n: item.n })) {
    item.lines = [];
    item.phase = "waiting";
    item.done = false;
    item.error = null;
    renderQuestion();
  }
});
el.reconnect.addEventListener("click", () => connect());
el.newCall.addEventListener("click", () => showSetup(lastIdle));
el.end.addEventListener("click", () => send("stop"));

el.more.addEventListener("click", () => {
  const item = shown();
  if (item && send("more", { n: item.n })) {
    item.moreLoading = true;
    item.done = false;          // "writing the next line" shows while the extra lines stream in
    renderQuestion();
  }
});
// Actions on demand (roadmap item 17): the engine opens a card for say, recap and ask, and redoes the
// card on screen for Shorter, so its question event is what redraws the overlay.
el.actSay.addEventListener("click", () => send("action", { kind: "say" }));
el.actRecap.addEventListener("click", () => send("action", { kind: "recap" }));
el.actAsk.addEventListener("click", () => send("action", { kind: "ask" }));
el.shorter.addEventListener("click", () => {
  const item = shown();
  if (item) send("action", { kind: "shorter", n: item.n });
});
// Minimize, not hide (QA 2026-10-17): a hidden window has no taskbar button, so a user who forgot
// the show/hide hotkey - or had changed it - could not get the overlay back. Minimized, it is one
// click away in the taskbar, and the hotkey still restores it (lib.rs).
el.hide.addEventListener("click", () => {
  const tauri = window.__TAURI__;
  if (tauri && tauri.window) tauri.window.getCurrentWindow().minimize();
});
// Click-through is toggled by a global hotkey on the Rust side (a window that ignores the mouse
// cannot be clicked to turn it off); this only shows that it is on, so a click that lands on the
// window underneath is not mistaken for a broken overlay.
if (window.__TAURI__ && window.__TAURI__.event) {
  window.__TAURI__.event.listen("click-through", (e) => { el.clickThrough.hidden = !e.payload; });
}

/* ---------- updates (release 0.1.0) ----------
   updates.js knows what is waiting; this draws it. A new version is offered on the setup screen,
   between calls, never over one: during a call only the dot on the settings button shows it. */
const upd = {};
for (const id of ["update-card", "update-title", "update-note", "update-bar", "update-now", "update-later",
                  "app-version"]) upd[id] = document.getElementById(id);
let laterFor = null;              // what Later was pressed for, this session: a version, or "failed"

function renderUpdate(s) {
  const u = s.update;
  const working = s.phase === "downloading" || s.phase === "installing";
  const failed = s.phase === "error" && s.failed === "install";
  el.openSettings.classList.toggle("has-update", Boolean(u));
  upd["app-version"].hidden = !s.version;
  if (s.version) upd["app-version"].textContent = `Version ${s.version}`;
  const show = working || (failed && laterFor !== "failed") || (u && laterFor !== u.version);
  upd["update-card"].hidden = !show;
  if (!show) return;
  const pct = s.phase === "installing" ? 100 : s.total ? Math.round((s.received / s.total) * 100) : null;
  upd["update-title"].textContent = failed ? "The update did not install" : `Fawa2ly ${u.version} is ready`;
  upd["update-note"].textContent = failed ? s.error
    : s.phase === "installing" ? "Installing. Fawa2ly closes and opens again by itself."
    : s.phase === "downloading" ? (pct === null ? "Downloading" : `Downloading, ${pct}%`)
    : `You have ${u.current}. Updating takes a minute and reopens the app.`;
  upd["update-bar"].hidden = !working;
  upd["update-bar"].firstElementChild.style.width = `${pct || 0}%`;
  upd["update-now"].hidden = working || failed;
  upd["update-later"].hidden = working;
}
window.fawa2lyUpdates.subscribe(renderUpdate);
upd["update-now"].addEventListener("click", () => window.fawa2lyUpdates.install());
upd["update-later"].addEventListener("click", () => {
  const s = window.fawa2lyUpdates.state;
  laterFor = s.update ? s.update.version : "failed";
  renderUpdate(s);
});

/* ---------- global hotkeys (roadmap item 3) ---------- */

// The engine stores the combinations; the window registers them. Registration is redone only when
// they change, since every status event carries the settings again.
let appliedHotkeys = "";
let hotkeyResult = {};            // action -> "" registered, or why not; for the settings window
let hotkeyTimer = null;
let hotkeyRetry = null;
const HOTKEY_SETTLE_MS = 250;
// A combination another application holds can be let go of later (Ctrl+Shift+L, 2026-09-11), so
// anything that failed is tried again on this interval until it is taken. Registration applies
// only the difference, so a retry touches nothing that already works.
const HOTKEY_RETRY_MS = 15000;

function applyHotkeys(hotkeys) {
  const tauri = window.__TAURI__;
  if (!tauri || !tauri.core || !hotkeys) return;
  // A retry scheduled for an older set of hotkeys must not fire after a newer one has arrived.
  clearTimeout(hotkeyRetry);
  // On connect the engine replays its history, and that can hold every settings change since it
  // started: seven in one run on 2026-09-11, each applied in turn, which briefly took combinations
  // like Ctrl+Alt+F9 from other applications. Only the last of a burst is registered.
  clearTimeout(hotkeyTimer);
  hotkeyTimer = setTimeout(() => {
    const wanted = JSON.stringify(hotkeys);
    if (wanted === appliedHotkeys) return;
    appliedHotkeys = wanted;
    clearTimeout(hotkeyRetry);
    tauri.core.invoke("set_hotkeys", { hotkeys })
      .then((result) => {
        hotkeyResult = result || {};
        tauri.event.emit("hotkeys-registered", hotkeyResult);
        if (Object.values(hotkeyResult).some(Boolean)) {
          hotkeyRetry = setTimeout(() => {
            appliedHotkeys = "";          // the same wish, asked for again
            applyHotkeys(hotkeys);
          }, HOTKEY_RETRY_MS);
        }
      })
      .catch((err) => console.error("set_hotkeys", err));
  }, HOTKEY_SETTLE_MS);
}

// A button that is disabled or not on screen means the action does not apply right now, and a
// hotkey should do exactly what a click would.
function press(button) {
  if (button && !button.disabled && button.offsetParent !== null) button.click();
}

if (window.__TAURI__ && window.__TAURI__.event) {
  const events = window.__TAURI__.event;
  events.listen("hotkey", (e) => {
    const inCall = el.setup.hidden && history.length > 0;
    switch (e.payload) {
      case "pause":    return press(el.pause);
      case "previous": if (inCall) goTo(shownIndex() - 1); return;
      case "next":     if (inCall) goTo(shownIndex() + 1); return;
      case "more":     return press(el.more);
      case "retry":    return press(el.retry);
      // the on-demand actions (roadmap item 17): the case they exist for is a user who cannot
      // reach for the mouse, so each has a key of its own
      case "say":      return press(el.actSay);
      case "recap":    return press(el.actRecap);
      case "ask":      return press(el.actAsk);
      case "shorter":  return press(el.shorter);
      default:         return;
    }
  });
  // The settings window can open after registration happened; it asks, and gets the last result.
  events.listen("hotkeys-query", () => events.emit("hotkeys-registered", hotkeyResult));
}
// The gear opens the settings window where it was left; "Change in Settings" on the setup screen
// opens it on Roles, and asks a window that is already open to go there.
async function openSettings(sectionName, extra) {
  const params = new URLSearchParams(location.search);
  if (sectionName) params.set("section", sectionName);
  for (const [key, value] of Object.entries(extra || {})) params.set(key, value);
  const query = params.toString();
  const url = `settings.html${query ? `?${query}` : ""}`;
  const tauri = window.__TAURI__;
  const WebviewWindow = tauri && tauri.webviewWindow && tauri.webviewWindow.WebviewWindow;
  if (!WebviewWindow) {
    window.open(url, "fawa2ly-settings", "width=1040,height=720");
    return;
  }
  try {
    const existing = WebviewWindow.getByLabel ? await WebviewWindow.getByLabel("settings") : null;
    if (existing) {
      if (sectionName && tauri.event) await tauri.event.emit("settings-section", sectionName);
      if (extra && extra.call && tauri.event) {
        await tauri.event.emit("settings-call", { file: extra.call, review: extra.review === "1" });
      }
      await existing.setFocus();
      return;
    }
  } catch (e) {
    console.error("settings window lookup", e);
  }
  // Frameless, like the overlay: settings.html draws its own title bar in the app's look.
  new WebviewWindow("settings", { url, title: "Fawa2ly settings", width: 1040, height: 720,
                                  minWidth: 820, minHeight: 560, resizable: true, focus: true,
                                  decorations: false });
}
el.openSettings.addEventListener("click", () => openSettings());
el.changeRole.addEventListener("click", () => openSettings("roles"));
el.reviewCall.addEventListener("click", () => {
  if (summary && summary.transcript) {
    openSettings("calls", { call: summary.transcript.split(/[\\/]/).pop(), review: "1" });
  }
});

el.prev.addEventListener("click", () => goTo(shownIndex() - 1));
el.next.addEventListener("click", () => goTo(shownIndex() + 1));
el.latest.addEventListener("click", () => {
  if (el.panel.dataset.state === "stopped") {
    view = null;                  // after the call: back to the summary
    renderQuestion();
  } else {
    goTo(history.length - 1);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
  if (!el.setup.hidden || history.length < 2) return;
  if (e.key === "ArrowLeft") {
    goTo(shownIndex() - 1);
    e.preventDefault();
  } else if (e.key === "ArrowRight") {
    goTo(shownIndex() + 1);
    e.preventDefault();
  }
});

el.providerPick.addEventListener("change", () => {
  const vendor = el.providerPick.value;
  if (send("provider", { vendor, model: null })) setCaption(`switching to ${vendor}`, true);
});

// Starting takes seconds: the engine opens the audio device, connects to Deepgram and prepares the
// model before it reports listening. The button says so until the call starts or fails (maintainer,
// 2026-09-11: pressing Start seemed to do nothing).
function setStarting(on) {
  el.start.classList.toggle("loading", on);
  el.start.disabled = on;
  if (on) el.start.textContent = "Starting the call";
}

el.start.addEventListener("click", () => {
  el.setupError.hidden = true;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  setStarting(true);
  send("start", {
    settings: {
      mode: el.mode.value,
      preset: roleFor(lastIdle, el.mode.value),
      lang: el.lang.value,
      vendor: el.vendor.value || undefined,
      speculate: el.speculate.checked,
      style: el.style.value,
      user_mic: el.userMic.checked,
      suggest: true,
    },
  });
});

// Changing the kind of call offers its roles and names its documents at once, and is saved at once:
// documents belong to a kind of call, and adding or removing one redraws this screen from the
// saved settings. Unsaved, the choice flipped back to Interview the moment a meeting document was
// removed, taking the meeting list off screen with it.
el.mode.addEventListener("change", () => {
  send("settings", { values: { mode: el.mode.value } });
  if (!lastIdle) return;
  renderRole(lastIdle, el.mode.value);
  renderDocumentText(el.mode.value, lastIdle.settings || {});
});

el.pickCv.addEventListener("click", () => el.cvFile.click());
el.dropCv.addEventListener("click", () => send("cv", { mode: el.mode.value }));
el.cvFile.addEventListener("change", async () => {
  // The picker hides the path on purpose, so each file itself goes to the engine, which parses it
  // with the same reader the CLI uses and keeps the text in the app folder, for the kind of call
  // chosen above. One message per file, in order: the engine checks the word budget as each one
  // arrives, so a file that does not fit is refused on its own and the ones before it stay.
  const files = [...(el.cvFile.files || [])];
  el.cvFile.value = "";
  for (const file of files) {
    if (file.size > MAX_UPLOAD_BYTES) {           // past the socket's frame limit: say so, do not send
      el.setupError.textContent = `${file.name} is larger than ${MAX_UPLOAD_BYTES / 2 ** 20} MB: use a smaller file`;
      el.setupError.hidden = false;
      continue;
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    send("cv", { mode: el.mode.value, name: file.name, data: btoa(binary) });
  }
});
// Closing the window ends the app and its engine, and a call that never reaches its end loses the
// summary and the transcript the user chose to keep: the engine writes both when the call ends. So a
// call still running is ended first, for as long as the engine needs: a little over its own wait
// for the answers still being written (engine.py SUGGEST_DRAIN_S). Audit F4. Alt+F4 and the taskbar's
// "Close window" do not come through here (see the fix plan, owner decision).
const CLOSE_WAIT_MS = 12000;
let onStopped = null;             // set while the window waits for the call it was closed on to end

el.close.addEventListener("click", async () => {
  if (live && send("stop")) {
    el.close.disabled = true;
    el.state.textContent = "Ending the call";
    await new Promise((resolve) => { onStopped = resolve; setTimeout(resolve, CLOSE_WAIT_MS); });
    onStopped = null;
    el.close.disabled = false;
  }
  // In the Tauri window this closes it; in a browser tab it is a no-op unless the tab was
  // opened by script, which is fine: the browser is only ever used for development.
  const tauri = window.__TAURI__;
  if (tauri && tauri.window) tauri.window.getCurrentWindow().close();
  else window.close();
});

/* ---------- connection ---------- */

function offline() {
  live = false;
  paused = false;
  setPanel("offline");
  el.state.textContent = "Engine offline";
  el.pause.disabled = true;
  el.current.hidden = true;
  el.nav.hidden = true;
  el.setup.hidden = true;
  el.end.hidden = true;
  renderProviderPick();
  notice("Fawa2ly is not listening.",
         "The connection dropped, so nothing on this call is being heard or suggested.",
         "reconnect");
  setCaption("captions stopped", true);
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  setPanel("connecting");
  el.state.textContent = "Connecting to engine";
  socket = new WebSocket(WS_URL);
  socket.addEventListener("message", (m) => {
    try {
      handle(JSON.parse(m.data));
    } catch (e) {
      console.error("bad message", e);
    }
  });
  socket.addEventListener("close", () => {
    offline();
    // The engine outlives the window: reconnecting replays the recent past, so reopening the
    // overlay mid-call brings back the question being answered instead of a blank panel.
    setTimeout(connect, RECONNECT_MS);
  });
  socket.addEventListener("error", () => socket.close());
}

connect();
