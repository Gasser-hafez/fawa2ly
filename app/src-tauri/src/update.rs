// Updates (release 0.1.0). The app asks GitHub for `latest.json`, written by the release workflow
// next to a signed installer, shortly after launch and then every few hours. A newer version is
// announced to both windows as `update-available`; nothing is downloaded until the user presses
// Update, because the install replaces the app and its engine and ends any call in progress.
//
// The installer is verified against the public key in tauri.conf.json before it runs, so only a
// build signed with the project's private key can update anyone. On Windows the updater exits the
// process with std::process::exit, which skips RunEvent::Exit, so the engine this app started is
// stopped here first: a running engine holds its files and the installer could not replace them.
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::{start_engine, stop_engine, EngineProcess};

const FIRST_CHECK: Duration = Duration::from_secs(20);
const EVERY: Duration = Duration::from_secs(6 * 60 * 60);

/// What the windows are told about an update: never the download itself.
#[derive(Clone, Serialize)]
pub struct UpdateInfo {
    version: String,
    current: String,
    notes: String,
    date: Option<String>,
}

/// The update found last, kept so Update installs exactly what was announced.
pub struct Pending(pub Mutex<Option<Update>>);

fn info(update: &Update) -> UpdateInfo {
    UpdateInfo {
        version: update.version.clone(),
        current: update.current_version.clone(),
        notes: update.body.clone().unwrap_or_default(),
        date: update.date.map(|d| d.date().to_string()),
    }
}

async fn find(app: &AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let found = update.as_ref().map(info);
    *app.state::<Pending>().0.lock().unwrap() = update;
    if let Some(found) = &found {
        let _ = app.emit("update-available", found);
    }
    Ok(found)
}

/// Asks GitHub now (the Check for updates button). `None` means this is the newest version.
#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    find(&app).await
}

/// What was found last, without asking again: a window opened after the announcement.
#[tauri::command]
pub fn update_status(app: AppHandle) -> Option<UpdateInfo> {
    app.state::<Pending>().0.lock().unwrap().as_ref().map(info)
}

/// Downloads the announced update, reporting `update-progress` [bytes, total], then installs it.
/// On Windows the app closes and the installer restarts it; elsewhere it restarts itself.
#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    let update = app.state::<Pending>().0.lock().unwrap().take();
    let Some(update) = update else {
        return Err("No update is waiting. Check for updates again.".into());
    };
    let mut received: usize = 0;
    let bytes = update
        .download(
            |chunk, total| {
                received += chunk;
                let _ = app.emit("update-progress", (received, total));
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    let _ = app.emit("update-installing", ());
    stop_engine(&app);
    if let Err(e) = update.install(bytes) {
        // Still running: bring the engine back so the app is usable, and say why.
        let engine = start_engine(&app);
        *app.state::<EngineProcess>().0.lock().unwrap() = engine;
        return Err(e.to_string());
    }
    app.restart();
}

/// The background check: once shortly after launch, then every few hours. A failure (offline, no
/// release yet) is only logged; the windows hear nothing until there is something to install.
pub fn watch(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK);
        loop {
            if let Err(e) = tauri::async_runtime::block_on(find(&app)) {
                eprintln!("update check failed: {e}");
            }
            std::thread::sleep(EVERY);
        }
    });
}
