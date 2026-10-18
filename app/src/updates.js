/*
Updates (release 0.1.0), shared by both windows. src-tauri/src/update.rs checks GitHub for a newer
signed installer and installs it; this file wraps its commands and events into one state that each
window draws in its own way:

  fawa2lyUpdates.subscribe((s) => ...)   s.version, s.update {version, current, notes, date},
                                          s.phase idle | checking | latest | ready | downloading |
                                          installing | error, s.received, s.total,
                                          s.failed check | install, s.error
  fawa2lyUpdates.check()                  ask GitHub now
  fawa2lyUpdates.install()                download, verify, install, restart

In a browser tab there is no installed app to update: `available` is false and nothing happens.
*/
(() => {
  const t = window.__TAURI__;
  const available = Boolean(t && t.core && t.event && t.app);
  const state = { version: null, update: null, phase: "idle", received: 0, total: null, failed: "", error: "" };
  const listeners = [];
  const emit = () => listeners.forEach((f) => f(state));
  const busy = () => state.phase === "downloading" || state.phase === "installing";

  window.fawa2lyUpdates = {
    available,
    state,
    subscribe(f) {
      listeners.push(f);
      f(state);
    },
    async check() {
      if (!available || busy()) return state.update;
      state.phase = "checking";
      emit();
      try {
        state.update = await t.core.invoke("check_update");
        state.phase = state.update ? "ready" : "latest";
      } catch (e) {
        state.phase = "error";
        state.failed = "check";
        state.error = String(e);
      }
      emit();
      return state.update;
    },
    async install() {
      if (!available || !state.update || busy()) return;
      state.phase = "downloading";
      state.received = 0;
      state.total = null;
      emit();
      try {
        await t.core.invoke("install_update");    // on success the app closes and reopens
      } catch (e) {
        // The download is spent; a new check finds the update again.
        state.phase = "error";
        state.failed = "install";
        state.error = String(e);
        state.update = null;
        emit();
      }
    },
  };

  if (!available) return;
  t.app.getVersion().then((v) => { state.version = v; emit(); });
  t.core.invoke("update_status").then((u) => {
    if (u && !busy()) { state.update = u; state.phase = "ready"; emit(); }
  });
  t.event.listen("update-available", (e) => {
    state.update = e.payload;
    if (!busy()) state.phase = "ready";
    emit();
  });
  t.event.listen("update-progress", (e) => {
    [state.received, state.total] = e.payload;
    state.phase = "downloading";
    emit();
  });
  t.event.listen("update-installing", () => { state.phase = "installing"; emit(); });
})();
