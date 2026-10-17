/* The full-body mascot from the design canvas, drawn into any element, with its five moods and a
   squash-and-stretch transform for the promo:  fawaMascot(el, { mood, sx, sy, rot, dy }). */
(() => {
  const SVG = `<svg viewBox="0 0 120 120" style="width:100%;height:100%;display:block;overflow:visible" aria-hidden="true">
<ellipse class="shadow" cx="60" cy="117" rx="40" ry="3" fill="#000" opacity=".35"/>
<g class="body">
<path d="M84 104 C100 106 110 98 112 86 C113 80 108 78 106 84 C104 92 96 98 84 96 Z" fill="#B81F14"/>
<path d="M98 97 L101 91 L104 96 Z M105 92 L110 87 L110 93 Z" fill="#B81F14"/>
<path d="M34 96 C24 98 22 112 30 116 L48 116 C52 110 50 98 44 96 Z" fill="#C92616"/>
<path d="M86 96 C96 98 98 112 90 116 L72 116 C68 110 70 98 76 96 Z" fill="#C92616"/>
<circle cx="29" cy="115" r="3.5" fill="#C92616"/><circle cx="36" cy="116" r="3.5" fill="#C92616"/>
<circle cx="84" cy="116" r="3.5" fill="#C92616"/><circle cx="91" cy="115" r="3.5" fill="#C92616"/>
<path d="M60 34 C40 34 22 40 18 56 C15 68 26 76 30 86 C33 96 42 104 60 104 C78 104 87 96 90 86 C94 76 105 68 102 56 C98 40 80 34 60 34 Z" fill="#E2341F"/>
<path d="M60 52 C48 52 40 60 40 72 C40 88 48 100 60 100 C72 100 80 88 80 72 C80 60 72 52 60 52 Z" fill="#F5C33B"/>
<path d="M44 62 Q52 66 60 60 Q68 66 76 62 M60 64 L60 96 M50 72 Q55 74 60 72 Q65 74 70 72 M49 82 Q55 84 60 82 Q65 84 71 82 M51 91 Q56 93 60 91 Q64 93 69 91" stroke="#D99A1C" stroke-width="1.8" fill="none" stroke-linecap="round"/>
<circle cx="45" cy="66" r="1.2" fill="#E2341F"/><circle cx="73" cy="70" r="1" fill="#E2341F"/><circle cx="47" cy="90" r="1.4" fill="#E2341F"/>
<path d="M24 48 C10 52 6 70 10 86 C12 96 16 102 22 102 C28 102 30 94 30 86 C30 74 34 62 34 54 Z" fill="#E2341F"/>
<path d="M96 48 C110 52 114 70 110 86 C108 96 104 102 98 102 C92 102 90 94 90 86 C90 74 86 62 86 54 Z" fill="#E2341F"/>
<path d="M12 66 Q18 62 26 66 M94 66 Q102 62 108 66" stroke="#B81F14" stroke-width="2" fill="none" stroke-linecap="round"/>
<circle cx="18" cy="104" r="9" fill="#E2341F"/><circle cx="102" cy="104" r="9" fill="#E2341F"/>
<path d="M13 101 Q15 99 17 101 M18 100 Q20 98 22 100 M98 100 Q100 98 102 100 M103 101 Q105 99 107 101" stroke="#B81F14" stroke-width="1.5" fill="none" stroke-linecap="round"/>
<ellipse cx="28" cy="46" rx="7" ry="3" fill="#F25A3F" opacity=".8" transform="rotate(-20 28 46)"/>
<ellipse cx="92" cy="46" rx="7" ry="3" fill="#F25A3F" opacity=".8" transform="rotate(20 92 46)"/>
<path d="M44 18 L38 8 L50 14 Z M76 18 L82 8 L70 14 Z M56 14 L60 6 L64 14 Z" fill="#B81F14"/>
<path d="M40 34 C38 22 46 14 60 14 C74 14 82 22 80 34 C79 42 70 46 60 46 C50 46 41 42 40 34 Z" fill="#E2341F"/>
<ellipse cx="52" cy="19" rx="6" ry="2.5" fill="#F25A3F" opacity=".8"/>
<path d="M41 22 Q47 18 53 22 M68 21 Q74 18 80 22" stroke="#B81F14" stroke-width="2" fill="none" stroke-linecap="round"/>
<circle cx="56" cy="33" r="1.1" fill="#8E170E"/><circle cx="64" cy="33" r="1.1" fill="#8E170E"/>
<g class="googly"><circle cx="46" cy="27" r="6.5" fill="#FFF6E8"/><circle cx="75" cy="26" r="5" fill="#FFF6E8"/>
<circle class="pl" cx="47" cy="28" r="2.6" fill="#17100E"/><circle class="pr" cx="77" cy="24.5" r="2.1" fill="#17100E"/></g>
<g class="think"><path d="M40 27 Q46 23 52 27" stroke="#3A0D08" stroke-width="2.5" fill="none" stroke-linecap="round"/>
<circle cx="75" cy="26" r="5" fill="#FFF6E8"/><circle cx="76" cy="22.6" r="2.1" fill="#17100E"/></g>
<g class="x"><path d="M42 23 L50 31 M50 23 L42 31 M72 23 L78 29 M78 23 L72 29" stroke="#3A0D08" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="smile"><path d="M44 37 Q60 46 78 36" stroke="#3A0D08" stroke-width="2.5" fill="none" stroke-linecap="round"/>
<path d="M47.5 39 L49.5 44 L51.5 39.4 Z M69.5 39.5 L71 43 L72.8 39 Z" fill="#FFF6E8"/></g>
<g class="grin"><path d="M43 35 Q60 52 79 34 Q60 41 43 35 Z" fill="#3A0D08"/><ellipse cx="61" cy="43.5" rx="5" ry="2.2" fill="#E86A7A"/>
<path d="M46.5 36.2 L48.5 41 L50.5 36.9 Z M71.5 36.5 L73.5 40.5 L75.5 35.7 Z" fill="#FFF6E8"/></g>
<g class="wavy"><path d="M47 39 Q51 36 55 39 Q59 42 63 39 Q67 36 71 39" stroke="#3A0D08" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="gasp"><ellipse cx="60" cy="39" rx="6" ry="4" fill="#3A0D08"/><path d="M57.5 41 Q60 50 63 41 Z" fill="#E86A7A"/></g>
<g class="sweat"><path d="M85 13 Q89 20 85 23 Q81 20 85 13 Z" fill="#9FD3F0"/></g>
<g class="waves"><path d="M34 22 Q30 28 34 34 M29 18 Q23 28 29 38 M86 22 Q90 28 86 34 M91 18 Q97 28 91 38" stroke="#F5C33B" stroke-width="2.2" fill="none" stroke-linecap="round"/></g>
</g>
<g class="stars"><path d="M44 23 L45.5 26.5 L49 28 L45.5 29.5 L44 33 L42.5 29.5 L39 28 L42.5 26.5 Z M60 17 L61.5 20.5 L65 22 L61.5 23.5 L60 27 L58.5 23.5 L55 22 L58.5 20.5 Z M76 23 L77.5 26.5 L81 28 L77.5 29.5 L76 33 L74.5 29.5 L71 28 L74.5 26.5 Z" fill="#F5C33B"/></g>
</svg>`;
  const SHOWN = {
    googly: ["idle", "listening", "ready"], think: ["thinking"], x: ["oops"], smile: ["idle", "listening"],
    grin: ["ready"], wavy: ["thinking"], gasp: ["oops"], sweat: ["thinking", "oops"], waves: ["listening"],
    stars: ["oops"],
  };
  const SHAPE = { idle: [1, 1, -3], listening: [1, 1, 2], thinking: [1.08, 0.9, 0], ready: [0.96, 1.05, 0], oops: [1.22, 0.72, 0] };
  const PUPILS = { listening: ["translate(0,-2)", "translate(-1,-1)"], ready: ["translate(1,1)", "translate(-2,2)"] };

  window.fawaMascot = function (host, o = {}) {
    if (!host.querySelector("svg")) host.innerHTML = SVG;
    const svg = host.querySelector("svg");
    const mood = o.mood || host.dataset.mood || "idle";
    for (const [cls, moods] of Object.entries(SHOWN)) svg.querySelector("." + cls).style.display = moods.includes(mood) ? "" : "none";
    const [px, py] = PUPILS[mood] || ["", ""];
    svg.querySelector(".pl").setAttribute("transform", px);
    svg.querySelector(".pr").setAttribute("transform", py);
    const [bx, by, br] = SHAPE[mood];
    const sx = o.sx ?? bx, sy = o.sy ?? by, rot = o.rot ?? br, dy = o.dy ?? 0;
    // squash happens at the feet, the jump lifts the whole body
    svg.querySelector(".body").setAttribute("transform",
      `translate(0 ${dy}) translate(60 118) rotate(${rot}) scale(${sx} ${sy}) translate(-60 -118)`);
    const lift = Math.min(1, Math.abs(dy) / 60);
    const sh = svg.querySelector(".shadow");
    sh.setAttribute("rx", String(40 * sx * (1 - lift * 0.5)));
    sh.setAttribute("opacity", String(0.35 * (1 - lift * 0.6)));
  };
  for (const el of document.querySelectorAll("[data-mascot]")) window.fawaMascot(el, { mood: el.dataset.mascot });
})();
