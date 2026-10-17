/*
Puts the real overlay (app/src/index.html) into a chosen state with example content, for the README
screenshots and the promo. Load the overlay with a dead engine port (?port=1) so main.js never
receives settings that would undo this; the panel is cloned so main.js keeps redrawing a detached
copy instead of this one.

  fawaDemo(document, { state: "listening", question: "...", lines: ["..."], ... })
*/
window.fawaDemo = function (doc, s) {
  let p = doc.getElementById("panel");
  if (!p.dataset.demo) {
    const c = p.cloneNode(true);
    c.dataset.demo = "1";
    p.replaceWith(c);
    p = c;
    const still = doc.createElement("style");
    still.textContent = "*,*::before,*::after{animation:none!important;transition:none!important}";
    doc.head.appendChild(still);
  }
  const $ = (id) => p.querySelector("#" + id);
  const root = doc.documentElement;
  root.dataset.theme = s.theme || "dark";
  root.dataset.textSize = s.textSize || "medium";
  const i18n = doc.defaultView.fawa2lyI18n;
  if (i18n) i18n.setLang(s.lang || "en");

  const state = s.state || "listening";
  const live = state === "listening" || state === "paused";
  p.dataset.state = state;
  p.dataset.busy = s.busy ? "1" : "0";
  for (const id of ["setup", "notice", "current", "earlier", "no-cv", "click-through", "memory",
                    "provider-pick", "nav", "feedback", "web", "failure"]) {
    if ($(id)) $(id).hidden = true;                // memory and web are newer than some builds
  }

  $("state").textContent = s.stateText || (state === "listening" ? "Listening"
    : state === "paused" ? "Paused, not listening" : state === "stopped" ? "Call ended" : "Not in a call");
  $("pause").disabled = !live;
  $("pause").textContent = state === "paused" ? "Resume" : "Pause";
  $("end").hidden = !live;
  $("provider").textContent = s.provider || "";
  $("pace").hidden = !s.pace;
  if (s.pace) { $("pace").textContent = s.pace; $("pace").dataset.zone = "ok"; }
  $("call-actions").hidden = !s.actions;

  const cur = $("current");
  if (s.question) {
    cur.hidden = false;
    cur.dataset.phase = s.phase || (s.lines && s.lines.length ? "lines" : "waiting");
    cur.dataset.done = s.done ? "1" : "0";
    cur.dataset.style = s.style || "full";
    $("asked-label").textContent = s.onDemand ? "On demand" : "Asked";
    const q = $("question");
    q.textContent = s.question;
    q.translate = false;
    q.dir = "auto";
    const ol = $("lines");
    ol.replaceChildren(...(s.lines || []).map((text, k, all) => {
      const li = doc.createElement("li");
      li.textContent = text;
      li.dir = "auto";
      li.translate = false;
      if (!s.done && k === all.length - 1) li.className = "new";
      return li;
    }));
    $("more").hidden = !s.done;
    $("shorter").hidden = !s.done;
    if (s.pos) {
      $("nav").hidden = false;
      $("pos").textContent = s.pos;
      $("prev").disabled = false;
      $("next").disabled = true;
      $("latest").hidden = true;
    }
    if (s.feedback) {
      $("feedback").hidden = false;
      $("feedback").dataset.verdict = s.feedback.verdict;
      $("feedback-verdict").textContent = s.feedback.label;
      $("feedback-note").textContent = s.feedback.note;
    }
    if (cur.dataset.phase === "failed") {
      $("failure").hidden = false;
      $("failure-title").textContent = s.failTitle || "No lines for this one.";
      $("failure-text").textContent = s.failText || "Keep talking, earlier answers are still below.";
    }
  }

  if (s.notice) {
    $("notice").hidden = false;
    $("notice-title").textContent = s.notice.title || "";
    $("notice-text").textContent = s.notice.text || "";
    $("notice-note").hidden = !s.notice.note;
    $("notice-note").textContent = s.notice.note || "";
    $("notice-actions").hidden = !s.notice.actions;
    $("reconnect").hidden = true;
    $("new-call").hidden = !s.notice.actions;
    $("review-call").hidden = !s.notice.review;
  }

  if (s.earlier) {
    $("earlier").hidden = false;
    $("earlier-label").textContent = s.earlierLabel || "Earlier";
    $("earlier-list").replaceChildren(...s.earlier.map((e) => {
      const li = doc.createElement("li");
      const t = doc.createElement("span");
      t.className = "q-text";
      t.textContent = e.q;
      t.translate = false;
      t.dir = "auto";
      li.append(t);
      if (e.t) {
        const time = doc.createElement("span");
        time.className = "q-time";
        time.textContent = e.t;
        li.append(time);
      }
      return li;
    }));
  }

  const cap = $("caption-text");
  cap.textContent = s.caption || (live ? "listening" : "not listening");
  cap.classList.toggle("muted", !s.caption);
  cap.translate = !s.caption;
  cap.dir = "auto";
};

/* The example call used everywhere: a backend engineer (docs/sample_cv.txt) in an interview. */
window.fawaExamples = {
  live: {
    state: "listening", provider: "groq", pace: "138 wpm · 2 fillers", actions: true, pos: "3 / 3",
    question: "Tell me about a time something you shipped went wrong. What did you change?",
    lines: [
      "I once shipped a migration with no rollback path, and it locked a table for nine minutes.",
      "It was during business hours, so customers felt it right away.",
      "Since then every migration ships with a tested down path.",
      "And anything risky runs off peak, so a mistake stays small.",
    ],
    earlier: [{ q: "How did you make the order search endpoint faster?" },
              { q: "Why did you move from Django to FastAPI?" }],
    caption: "…and what did you change after that?",
  },
  thinking: {
    state: "listening", busy: true, provider: "groq", actions: true,
    question: "Tell me about a time something you shipped went wrong. What did you change?",
    lines: [], caption: "Tell me about a time something you shipped went wrong. What did you change?",
  },
  arabic: {
    state: "listening", lang: "ar", provider: "groq", actions: true, pos: "2 / 2",
    question: "إزاي خليت الـ endpoint بتاع البحث عن الطلبات أسرع؟",
    lines: [
      "البحث عن الطلبات كان بياخد ١٫٤ ثانية عند الـ p95.",
      "لقيت مشكلة N+1 في الاستعلامات، فجمعتها في استعلام واحد.",
      "وضفت covering index، فالوقت نزل لـ ١٨٠ ملي ثانية.",
    ],
    caption: "…وإيه اللي اتعلمته من ده؟",
  },
  ended: {
    state: "stopped", stateText: "Call ended, 5 questions",
    notice: { title: "Call ended.", actions: true, review: true,
              text: "24 min 10 s · 5 questions heard · 5 answered · you: 138 wpm · 4 fillers",
              note: "Transcript saved on this computer." },
    earlierLabel: "This call",
    earlier: [
      { q: "What's your biggest weakness as a developer right now?" },
      { q: "Tell me about a time something you shipped went wrong." },
      { q: "How did you make the order search endpoint faster?" },
      { q: "Why did you move from Django to FastAPI?" },
      { q: "Tell me a little bit about yourself." },
    ],
  },
};
