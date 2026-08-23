#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
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

/// Keep WebView2's browser profile with Elira's runtime data instead of filling
/// the Windows system drive. An explicit WebView2 override still wins.
fn configure_webview_data_dir() {
    if std::env::var_os("WEBVIEW2_USER_DATA_FOLDER").is_some() {
        return;
    }

    let data_dir = std::env::var_os("ELIRA_DATA_DIR")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| find_project_root().map(|root| root.join("data")));

    if let Some(data_dir) = data_dir {
        let webview_dir = data_dir.join("webview2");
        if std::fs::create_dir_all(&webview_dir).is_ok() {
            std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", webview_dir);
        }
    }
}

#[tauri::command]
fn start_backend(
    _app: tauri::AppHandle,
    state: tauri::State<BackendState>,
) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_ref() {
        return Ok(format!("Backend already running with pid {}", child.id()));
    }

    // Find project root by walking up from current_dir or exe path until we find "backend/"
    let project_dir = find_project_root()
        .ok_or_else(|| "Failed to find project root (no backend/ folder found)".to_string())?;

    eprintln!("[Elira] Project root: {}", project_dir.display());
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
        .arg("0.0.0.0")
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
        child
            .kill()
            .map_err(|e| format!("Failed to stop backend: {e}"))?;
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

#[cfg(target_os = "windows")]
fn powershell_literal(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[tauri::command]
#[cfg(target_os = "windows")]
fn run_elevated_command(
    request_id: String,
    program: String,
    args: Vec<String>,
    cwd: Option<String>,
) -> Result<serde_json::Value, String> {
    let program = program.trim().to_string();
    if program.is_empty() {
        return Err("Elevation request has no program".to_string());
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let temp_dir = std::env::temp_dir().join(format!(
        "elira-elevation-{}-{}",
        std::process::id(),
        nonce
    ));
    std::fs::create_dir_all(&temp_dir)
        .map_err(|e| format!("Failed to create elevation workspace: {e}"))?;
    let input_path = temp_dir.join("request.json");
    let helper_path = temp_dir.join("elevated-helper.ps1");
    let launcher_path = temp_dir.join("uac-launcher.ps1");
    let result_path = temp_dir.join("result.json");

    let request = serde_json::json!({
        "program": program,
        "args": args,
        "cwd": cwd,
    });
    std::fs::write(
        &input_path,
        serde_json::to_vec(&request).map_err(|e| e.to_string())?,
    )
    .map_err(|e| format!("Failed to write elevation request: {e}"))?;

    const ELEVATED_HELPER: &str = r#"param(
    [Parameter(Mandatory=$true)][string]$RequestPath,
    [Parameter(Mandatory=$true)][string]$ResultPath
)
$ErrorActionPreference = 'Stop'
try {
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $program = [string]$request.program
    if ([string]::IsNullOrWhiteSpace($program)) { throw 'program is empty' }
    if ($request.cwd) { Set-Location -LiteralPath ([string]$request.cwd) }
    $arguments = @($request.args | ForEach-Object { [string]$_ })
    $commandOutput = (& $program @arguments 2>&1 | Out-String)
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($commandOutput.Length -gt 60000) {
        $commandOutput = $commandOutput.Substring(0, 60000) + "`n[output truncated]"
    }
    $result = @{
        elevated = $true
        ok = ($exitCode -eq 0)
        exit_code = $exitCode
        output = $commandOutput
    }
} catch {
    $result = @{
        elevated = $true
        ok = $false
        exit_code = -1
        output = [string]$_.Exception.Message
    }
}
$json = $result | ConvertTo-Json -Compress -Depth 8
[System.IO.File]::WriteAllText($ResultPath, $json, [System.Text.UTF8Encoding]::new($false))
exit ([int]$result.exit_code)
"#;
    std::fs::write(&helper_path, ELEVATED_HELPER.as_bytes())
        .map_err(|e| format!("Failed to write elevation helper: {e}"))?;

    let quoted = |path: &std::path::Path| {
        powershell_literal(&format!("\"{}\"", path.display()))
    };
    let launcher = format!(
        "$ErrorActionPreference='Stop'\n$child=Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',{},{},{}) -Wait -PassThru\nexit $child.ExitCode\n",
        quoted(&helper_path),
        quoted(&input_path),
        quoted(&result_path),
    );
    std::fs::write(&launcher_path, launcher.as_bytes())
        .map_err(|e| format!("Failed to write UAC launcher: {e}"))?;

    let status = Command::new("powershell.exe")
        .arg("-NoProfile")
        .arg("-NonInteractive")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(&launcher_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("Failed to start UAC: {e}"))?;

    let result = (|| -> Result<serde_json::Value, String> {
        if !result_path.is_file() {
            return Err(format!(
                "UAC was cancelled or the elevated helper did not return a result (exit {:?})",
                status.code()
            ));
        }
        let mut value: serde_json::Value = serde_json::from_slice(
            &std::fs::read(&result_path)
                .map_err(|e| format!("Failed to read elevation result: {e}"))?,
        )
        .map_err(|e| format!("Invalid elevation result: {e}"))?;
        let object = value
            .as_object_mut()
            .ok_or_else(|| "Invalid elevation result object".to_string())?;
        object.insert(
            "native_bridge".to_string(),
            serde_json::Value::String("tauri-v1".to_string()),
        );
        object.insert(
            "request_id".to_string(),
            serde_json::Value::String(request_id),
        );
        Ok(value)
    })();
    let _ = std::fs::remove_dir_all(&temp_dir);
    result
}

#[tauri::command]
#[cfg(not(target_os = "windows"))]
fn run_elevated_command(
    _request_id: String,
    _program: String,
    _args: Vec<String>,
    _cwd: Option<String>,
) -> Result<serde_json::Value, String> {
    Err("Native elevation is available only on Windows".to_string())
}

fn main() {
    configure_webview_data_dir();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(BackendState {
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            backend_status,
            run_elevated_command
        ])
        .setup(|app| {
            // When ELIRA_EXTERNAL_BACKEND=1 the launcher script (run_tauri_dev.bat or
            // equivalent) already started the backend process.  Skip auto-start so we
            // don't race against the externally-managed process.
            let skip = std::env::var("ELIRA_EXTERNAL_BACKEND")
                .map(|v| v == "1")
                .unwrap_or(false);

            if skip {
                eprintln!("[Elira] External backend detected (ELIRA_EXTERNAL_BACKEND=1); skipping auto-start.");
            } else {
                let handle = app.handle();
                let state: tauri::State<BackendState> = handle.state();
                match start_backend(handle.clone(), state) {
                    Ok(msg) => eprintln!("[Elira] {}", msg),
                    Err(e) => {
                        eprintln!("[Elira] WARNING: Backend failed to start: {}", e);
                        eprintln!("[Elira] Check: 1) backend/.venv/ exists  2) pip install -r requirements.txt  3) port 8000 is free");
                    }
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Graceful shutdown: останавливаем backend при закрытии окна
            if let tauri::WindowEvent::Destroyed = event {
                let state: tauri::State<BackendState> = window.state();
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(mut child) = guard.take() {
                        eprintln!("[Elira] Stopping backend (pid {})...", child.id());
                        let _ = child.kill();
                        let _ = child.wait();
                        eprintln!("[Elira] Backend stopped.");
                    }
                };
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
