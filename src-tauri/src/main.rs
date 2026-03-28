#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

struct BackendState {
    child: Mutex<Option<Child>>,
}

/// Walk up from current working directory (and exe dir as fallback)
/// until we find a directory that contains "backend/"
fn find_project_root() -> Option<std::path::PathBuf> {
    // Try 1: from current working directory
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(root) = walk_up_for_backend(&cwd) {
            return Some(root);
        }
    }
    // Try 2: from executable location
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = walk_up_for_backend(&dir.to_path_buf()) {
                return Some(root);
            }
        }
    }
    None
}

fn walk_up_for_backend(start: &std::path::PathBuf) -> Option<std::path::PathBuf> {
    let mut dir = start.clone();
    for _ in 0..10 {
        if dir.join("backend").is_dir() {
            return Some(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    None
}

#[tauri::command]
fn start_backend(_app: tauri::AppHandle, state: tauri::State<BackendState>) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_ref() {
        return Ok(format!("Backend already running with pid {}", child.id()));
    }

    // Find project root by walking up from current_dir or exe path until we find "backend/"
    let project_dir = find_project_root()
        .ok_or_else(|| "Failed to find project root (no backend/ folder found)".to_string())?;

    eprintln!("[Jarvis] Project root: {}", project_dir.display());
    let backend_dir = project_dir.join("backend");

    let python_candidates = vec![
        backend_dir.join(".venv").join("Scripts").join("python.exe"),
        backend_dir.join(".venv").join("bin").join("python"),
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
        // Wait for the process to fully exit to avoid zombies
        let _ = child.wait();
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
            match start_backend(handle.clone(), state) {
                Ok(msg) => eprintln!("[Jarvis] {}", msg),
                Err(e) => {
                    eprintln!("[Jarvis] WARNING: Backend failed to start: {}", e);
                    eprintln!("[Jarvis] Проверь: 1) backend/.venv/ существует  2) pip install -r requirements.txt  3) порт 8000 свободен");
                }
            }
            Ok(())
        })
        .on_window_event(|event| {
            // Graceful shutdown: останавливаем backend при закрытии окна
            if let tauri::WindowEvent::Destroyed = event.event() {
                let state: tauri::State<BackendState> = event.window().state();
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(mut child) = guard.take() {
                        eprintln!("[Jarvis] Stopping backend (pid {})...", child.id());
                        let _ = child.kill();
                        let _ = child.wait();
                        eprintln!("[Jarvis] Backend stopped.");
                    }
                };
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
