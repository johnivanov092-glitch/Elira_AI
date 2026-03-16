#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

struct BackendState {
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn start_backend(app: tauri::AppHandle, state: tauri::State<BackendState>) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_ref() {
        return Ok(format!("Backend already running with pid {}", child.id()));
    }

    let project_dir = app
        .path_resolver()
        .resolve_resource(".")
        .ok_or_else(|| "Failed to resolve project directory".to_string())?;

    let backend_dir = project_dir.join("backend");

    let python_candidates = vec![
        project_dir.join(".venv").join("Scripts").join("python.exe"),
        project_dir.join(".venv").join("bin").join("python"),
    ];

    let mut chosen_python = None;
    for candidate in python_candidates {
        if candidate.exists() {
            chosen_python = Some(candidate);
            break;
        }
    }

    let mut cmd = if let Some(python) = chosen_python {
        Command::new(python)
    } else {
        Command::new("python")
    };

    let child = cmd
        .current_dir(&backend_dir)
        .arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg("8000")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to start backend: {e}"))?;

    let pid = child.id();
    *guard = Some(child);
    Ok(format!("Backend started with pid {}", pid))
}

#[tauri::command]
fn stop_backend(state: tauri::State<BackendState>) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = guard.take() {
        child.kill().map_err(|e| format!("Failed to stop backend: {e}"))?;
        return Ok("Backend stopped".to_string());
    }
    Ok("Backend is not running".to_string())
}

#[tauri::command]
fn backend_status(state: tauri::State<BackendState>) -> Result<serde_json::Value, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    let mut running = false;
    let mut pid = None;

    if let Some(child) = guard.as_mut() {
        match child.try_wait() {
            Ok(Some(_status)) => {
                *guard = None;
            }
            Ok(None) => {
                running = true;
                pid = Some(child.id());
            }
            Err(_e) => {
                *guard = None;
            }
        }
    }

    Ok(serde_json::json!({
        "running": running,
        "pid": pid,
        "mode": "tauri-managed"
    }))
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            backend_status
        ])
        .setup(|app| {
            let handle = app.handle();
            let state: tauri::State<BackendState> = handle.state();
            let _ = start_backend(handle, state);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
