/*
Desktop-app manners for both windows (2026-09-11, maintainer's request: "look like a professional app").

A webview shows the browser's own context menu (Back, Reload, Inspect) on right-click, and answers
browser shortcuts: F5 or Ctrl+R reloads the page, which drops the call view in the middle of a
call; Ctrl+P prints; Ctrl and the wheel zoom the whole window. None of that belongs in an overlay.
Text boxes keep their menu, because pasting an API key or a role description is what they are for.
The window also cannot be maximized (tauri.conf.json, "maximizable": false), which covers the
double-click on the title bar.
*/

document.addEventListener("contextmenu", (e) => {
  const t = e.target;
  if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
  e.preventDefault();
});

document.addEventListener("keydown", (e) => {
  const key = (e.key || "").toLowerCase();
  const ctrl = e.ctrlKey || e.metaKey;
  const reload = key === "f5" || (ctrl && key === "r");
  const devtools = key === "f12" || (ctrl && e.shiftKey && ["i", "j", "c"].includes(key));
  const print = ctrl && !e.shiftKey && key === "p";
  const zoom = ctrl && ["+", "=", "-", "0"].includes(key);
  const find = ctrl && key === "f";
  if (reload || devtools || print || zoom || find) e.preventDefault();
}, true);

document.addEventListener("wheel", (e) => {
  if (e.ctrlKey) e.preventDefault();
}, { passive: false });
