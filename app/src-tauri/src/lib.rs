// The window is the whole app: everything it shows comes from the Python engine over the
// WebSocket in fawa2ly/server.py, so there is almost nothing to expose and no state to keep on
// this side. Keep it that way - logic that lands here cannot be tested by the Python suite and
// cannot run in a browser, which is where the UI is developed.
//
// The exceptions are global hotkeys, which have to be here because they must work while another
// application has focus: the overlay sits on top of a call, and hiding it has to be possible
// without clicking it first. Which combination does what is a user setting
// (roadmap item 3), stored by the engine and handed over by the overlay through `set_hotkeys`.
// Two actions are carried out here, because the page cannot do them for itself: show/hide (a
// hidden page cannot show its own window) and click-through (a page that ignores the mouse cannot
// be clicked to stop). Every other action is sent to the overlay as a `hotkey` event.
//
// And starting the engine (M3 step 5): the installed app ships the packaged engine as a resource
// and starts it here, because a user has no terminal to start it from.
use std::collections::HashMap;
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, RunEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

// Registered at launch, before the engine has said anything, so the window can always be shown
// and hidden. The overlay replaces them with the user's choices once it connects.
const DEFAULT_TOGGLE: &str = "CmdOrCtrl+Shift+P";
const DEFAULT_CLICK_THROUGH: &str = "CmdOrCtrl+Shift+L";

// Where the overlay looks for the engine (fawa2ly/server.py PORT, app/src/main.js WS_URL).
const ENGINE_ADDR: &str = "127.0.0.1:8765";

// Off at every launch: a window that starts out ignoring the mouse looks broken.
static PASSING_THROUGH: AtomicBool = AtomicBool::new(false);

/// A combination this process holds.
#[derive(Clone)]
struct Held {
    action: String,
    shortcut: Shortcut,
    /// As the user wrote it ("Ctrl+Shift+P"), for messages.
    combo: String,
}

struct Hotkeys {
    /// Registered shortcut id -> what it does. Read by the key handler on every press.
    table: Mutex<HashMap<u32, Held>>,
    /// Held for a whole `apply_hotkeys`, so two calls cannot interleave. Separate from `table`
    /// because registering waits for the main thread, which is where the key handler runs and
    /// reads `table`: holding `table` across a registration could deadlock the two.
    applying: Mutex<()>,
}

/// The engine this app started, stopped when the app exits.
struct EngineProcess(Mutex<Option<Child>>);

/// Make the registered hotkeys match `wanted` (action -> combination; empty means unassigned).
/// Returns action -> "" for each one in place, or why it is not: a combination the parser
/// rejects, one used twice, or one another application already owns.
///
/// Only the difference is applied. The first version unregistered everything and registered it
/// again, through the plugin's `unregister_all`, and in the real window Ctrl+Shift+P ended up
/// held by nobody while the call had reported it registered, so show/hide was dead. That path
/// also has a trap of its own: `unregister_all` forgets every shortcut before unregistering them
/// one by one, and stops at the first failure, so the rest stay held by this process with nothing
/// left that can fire or release them. Combinations that did not change are never touched.
///
/// New combinations are taken before old ones are let go, so a show/hide the OS refuses (another
/// application owns it) leaves the previous one working instead of none. The overlay calls this
/// again while anything failed, because what another application holds it can let go of later:
/// on 2026-09-11 something on the maintainer's machine held Ctrl+Shift+L at launch and released it a
/// minute after, and click-through stayed dead until the app was restarted.
fn apply_hotkeys(app: &AppHandle, wanted: HashMap<String, String>) -> HashMap<String, String> {
    let mut result: HashMap<String, String> = HashMap::new();
    let Some(state) = app.try_state::<Hotkeys>() else {
        return result;
    };
    let _applying = state.applying.lock().unwrap();
    let shortcuts = app.global_shortcut();

    // What is wanted, by id. The toggle first, so if two actions collide the other one is refused.
    let mut entries: Vec<(String, String)> = wanted.into_iter().collect();
    entries.sort_by_key(|(action, _)| action != "toggle");
    let mut desired: HashMap<u32, Held> = HashMap::new();
    for (action, combo) in entries {
        if combo.trim().is_empty() {
            continue;
        }
        match combo.parse::<Shortcut>() {
            Err(e) => {
                result.insert(action, format!("not a hotkey: {e}"));
            }
            Ok(s) if desired.contains_key(&s.id()) => {
                result.insert(action, "already used by another action".to_string());
            }
            Ok(s) => {
                desired.insert(s.id(), Held { action, shortcut: s, combo });
            }
        }
    }

    let held = state.table.lock().unwrap().clone();
    let mut next: HashMap<u32, Held> = HashMap::new();

    // Keep what is unchanged (possibly under a different action), take what is new.
    for (id, want) in desired {
        if held.contains_key(&id) {
            result.insert(want.action.clone(), String::new());
            next.insert(id, want);
            continue;
        }
        match shortcuts.register(want.shortcut) {
            Ok(()) => {
                result.insert(want.action.clone(), String::new());
                next.insert(id, want);
            }
            Err(e) => {
                result.insert(want.action.clone(), e.to_string());
            }
        }
    }

    // Let go of what is no longer wanted, one at a time - except a show/hide whose replacement
    // could not be taken: without one, the Hide button would hide the overlay for good.
    let toggle_taken = next.values().any(|h| h.action == "toggle");
    for (id, old) in held {
        if next.contains_key(&id) {
            continue;
        }
        if old.action == "toggle" && !toggle_taken {
            let why = result.remove("toggle").unwrap_or_default();
            result.insert("toggle".to_string(), format!("{why}; kept {} until it can be taken", old.combo));
            next.insert(id, old);
            continue;
        }
        let _ = shortcuts.unregister(old.shortcut);
    }

    // No show/hide at all (the first call, or the old one was moved to another action).
    if !next.values().any(|h| h.action == "toggle") {
        if let Ok(s) = DEFAULT_TOGGLE.parse::<Shortcut>() {
            if !next.contains_key(&s.id()) && shortcuts.register(s).is_ok() {
                next.insert(s.id(), Held { action: "toggle".to_string(), shortcut: s, combo: "Ctrl+Shift+P".to_string() });
                let why = result.remove("toggle").unwrap_or_default();
                result.insert("toggle".to_string(), format!("{why}; kept Ctrl+Shift+P until it can be taken"));
            }
        }
    }

    *state.table.lock().unwrap() = next;
    result
}

#[tauri::command]
fn set_hotkeys(app: AppHandle, hotkeys: HashMap<String, String>) -> HashMap<String, String> {
    apply_hotkeys(&app, hotkeys)
}

/// Start the packaged engine, with no window, unless one is already listening (started from a
/// terminal during development) or this build carries none (a plain `cargo build`, which has no
/// installer resources). It runs from its own folder, logs each call to the app's sessions folder
/// (only while the user has transcript saving on), and writes its console to
/// %APPDATA%\Fawa2ly\engine.log, the first thing to ask for when nothing connects.
fn start_engine(app: &AppHandle) -> Option<Child> {
    let addr: SocketAddr = ENGINE_ADDR.parse().ok()?;
    if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
        return None;
    }
    let exe = app.path().resource_dir().ok()?.join("engine").join("fawa2ly-engine.exe");
    if !exe.is_file() {
        return None;
    }
    let log = std::env::var_os("APPDATA").and_then(|base| {
        let dir = std::path::Path::new(&base).join("Fawa2ly");
        std::fs::create_dir_all(&dir).ok()?;
        std::fs::File::create(dir.join("engine.log")).ok()
    });
    let (stdout, stderr) = match log {
        Some(file) => match file.try_clone() {
            Ok(copy) => (Stdio::from(copy), Stdio::from(file)),
            Err(_) => (Stdio::null(), Stdio::from(file)),
        },
        None => (Stdio::null(), Stdio::null()),
    };
    let mut cmd = Command::new(&exe);
    cmd.args(["--idle", "--sessions"])
        .current_dir(exe.parent()?)
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    match cmd.spawn() {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("could not start the engine {}: {e}", exe.display());
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    // Only on the press: without this every action runs twice per keystroke.
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let action = app.try_state::<Hotkeys>().and_then(|state| {
                        state.table.lock().unwrap().get(&shortcut.id()).map(|h| h.action.clone())
                    });
                    let (Some(action), Some(window)) = (action, app.get_webview_window("main")) else {
                        return;
                    };
                    match action.as_str() {
                        "toggle" => {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        "click_through" => {
                            let on = !PASSING_THROUGH.load(Ordering::SeqCst);
                            if window.set_ignore_cursor_events(on).is_ok() {
                                PASSING_THROUGH.store(on, Ordering::SeqCst);
                                // The overlay shows a badge, so the user knows why clicks go through.
                                let _ = window.emit("click-through", on);
                            }
                        }
                        other => {
                            let _ = window.emit("hotkey", other);
                        }
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![set_hotkeys])
        .setup(|app| {
            app.manage(Hotkeys {
                table: Mutex::new(HashMap::new()),
                applying: Mutex::new(()),
            });
            let defaults = HashMap::from([
                ("toggle".to_string(), DEFAULT_TOGGLE.to_string()),
                ("click_through".to_string(), DEFAULT_CLICK_THROUGH.to_string()),
            ]);
            for (action, why) in apply_hotkeys(app.handle(), defaults) {
                if !why.is_empty() {
                    eprintln!("hotkey {action} unavailable: {why}");
                }
            }
            let engine = start_engine(app.handle());
            app.manage(EngineProcess(Mutex::new(engine)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        // The engine this app started goes with it; one started from a terminal is not ours to stop.
        if let RunEvent::Exit = event {
            if let Some(state) = app.try_state::<EngineProcess>() {
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    });
}
