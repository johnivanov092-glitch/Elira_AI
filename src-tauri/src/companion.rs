//! Desktop companion (John 2026-10-15, variant B): a small transparent window that stays on top,
//! with Elira standing at the screen edge. The page is the same frontend bundle; it tells the
//! companion view apart by its window label. Clicks pass through everywhere except the regions the
//! page reports (her figure, bubbles, input), so the window never blocks the programs below it.
use std::sync::Mutex;
use std::time::Duration;

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub const LABEL: &str = "companion";
const TITLE: &str = "Elira companion";
const WIDTH: f64 = 560.0;
const HEIGHT: f64 = 480.0;
const EXPLAIN_EVENT: &str = "companion://explain-screen";
const OPEN_SESSION_EVENT: &str = "companion://open-session";
const MAX_CAPTURE_WIDTH: u32 = 1600;

/// Rectangles in logical pixels of the companion window where it takes the mouse.
#[derive(Default)]
pub struct HitRegions(Mutex<Vec<Region>>);

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct Region {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl Region {
    fn contains(&self, x: f64, y: f64) -> bool {
        x >= self.x && x <= self.x + self.width && y >= self.y && y <= self.y + self.height
    }
}

fn explain_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyE)
}

/// Ctrl+Shift+E shows the companion and asks it to explain the screen.
pub fn plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, shortcut, event| {
            if event.state() == ShortcutState::Pressed && *shortcut == explain_shortcut() {
                if let Some(window) = app.get_webview_window(LABEL) {
                    let _ = window.show();
                }
                let _ = app.emit_to(LABEL, EXPLAIN_EVENT, ());
            }
        })
        .build()
}

pub fn setup(app: &tauri::App) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("index.html".into()))
        .title(TITLE)
        .inner_size(WIDTH, HEIGHT)
        .resizable(false)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .build()?;
    place_bottom_right(&window);
    let _ = window.set_ignore_cursor_events(true);
    let _ = window.show();
    tray(app)?;
    if let Err(error) = app.global_shortcut().register(explain_shortcut()) {
        eprintln!("[Elira] Ctrl+Shift+E is not available: {error}");
    }
    start_hit_test(app.handle().clone());
    Ok(())
}

fn place_bottom_right(window: &WebviewWindow) {
    let Ok(Some(monitor)) = window.primary_monitor() else { return };
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let x = area.position.x + area.size.width as i32 - ((WIDTH + 8.0) * scale) as i32;
    let y = area.position.y + area.size.height as i32 - (HEIGHT * scale) as i32;
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

fn tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open-main", "Открыть Elira", true, None::<&str>)?;
    let toggle = MenuItem::with_id(app, "companion-toggle", "Показать или спрятать Элиру", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &PredefinedMenuItem::separator(app)?, &toggle])?;
    let mut builder = TrayIconBuilder::with_id("elira")
        .tooltip("Elira")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open-main" => show_main(app),
            "companion-toggle" => toggle_companion(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_companion(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn toggle_companion(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            let _ = window.show();
        }
    }
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Clicks go through the window except over the regions the page reported.
fn start_hit_test(app: AppHandle) {
    std::thread::spawn(move || {
        let mut passing = true;
        loop {
            std::thread::sleep(Duration::from_millis(40));
            let Some(window) = app.get_webview_window(LABEL) else { break };
            if !window.is_visible().unwrap_or(false) {
                continue;
            }
            let (Ok(cursor), Ok(origin), Ok(scale)) = (window.cursor_position(), window.outer_position(), window.scale_factor()) else {
                continue;
            };
            let x = (cursor.x - f64::from(origin.x)) / scale;
            let y = (cursor.y - f64::from(origin.y)) / scale;
            let inside = app
                .state::<HitRegions>()
                .0
                .lock()
                .map(|regions| regions.iter().any(|region| region.contains(x, y)))
                .unwrap_or(false);
            if inside == passing {
                passing = !inside;
                let _ = window.set_ignore_cursor_events(passing);
            }
        }
    });
}

#[tauri::command]
pub fn companion_set_regions(regions: Vec<Region>, state: tauri::State<HitRegions>) {
    if let Ok(mut current) = state.0.lock() {
        *current = regions;
    }
}

#[tauri::command]
pub fn companion_open_main(app: AppHandle, session_id: Option<String>) {
    show_main(&app);
    if let Some(id) = session_id {
        let _ = app.emit_to("main", OPEN_SESSION_EVENT, id);
    }
}

#[derive(Serialize)]
pub struct Capture {
    image_base64: String,
    title: String,
    app_name: String,
    width: u32,
    height: u32,
    source: &'static str,
}

/// A snapshot of the window the user is looking at (or the primary screen), as PNG.
#[tauri::command]
pub async fn companion_capture() -> Result<Capture, String> {
    tauri::async_runtime::spawn_blocking(capture_target)
        .await
        .map_err(|e| e.to_string())?
}

#[derive(Debug)]
pub struct Candidate {
    pub index: usize,
    pub own: bool,
    pub title: String,
    pub focused: bool,
    pub minimized: bool,
    pub width: u32,
    pub height: u32,
}

/// The focused window, else the topmost real one (windows come in z-order); never the companion.
pub fn pick_window(candidates: &[Candidate]) -> Option<usize> {
    let real = |c: &&Candidate| {
        !c.own && !c.minimized && c.width >= 200 && c.height >= 120
            && !c.title.trim().is_empty() && c.title != "Program Manager"
    };
    candidates
        .iter()
        .filter(real)
        .find(|c| c.focused)
        .or_else(|| candidates.iter().find(real))
        .map(|c| c.index)
}

fn capture_target() -> Result<Capture, String> {
    let own_pid = std::process::id();
    let windows = xcap::Window::all().map_err(|e| format!("window list: {e}"))?;
    let candidates: Vec<Candidate> = windows
        .iter()
        .enumerate()
        .map(|(index, window)| {
            let title = window.title().unwrap_or_default();
            Candidate {
                index,
                own: window.pid().map(|pid| pid == own_pid).unwrap_or(false) && title == TITLE,
                title,
                focused: window.is_focused().unwrap_or(false),
                minimized: window.is_minimized().unwrap_or(true),
                width: window.width().unwrap_or(0),
                height: window.height().unwrap_or(0),
            }
        })
        .collect();
    if let Some(index) = pick_window(&candidates) {
        let window = &windows[index];
        let image = window.capture_image().map_err(|e| format!("window capture: {e}"))?;
        return encode(image, window.title().unwrap_or_default(), window.app_name().unwrap_or_default(), "window");
    }
    let monitor = xcap::Monitor::all()
        .map_err(|e| format!("monitor list: {e}"))?
        .into_iter()
        .find(|monitor| monitor.is_primary().unwrap_or(false))
        .ok_or("no primary monitor")?;
    let image = monitor.capture_image().map_err(|e| format!("screen capture: {e}"))?;
    encode(image, String::new(), String::new(), "screen")
}

fn encode(image: xcap::image::RgbaImage, title: String, app_name: String, source: &'static str) -> Result<Capture, String> {
    use xcap::image::{imageops::FilterType, DynamicImage, ImageFormat};
    let mut image = DynamicImage::ImageRgba8(image);
    if image.width() > MAX_CAPTURE_WIDTH {
        image = image.resize(MAX_CAPTURE_WIDTH, u32::MAX, FilterType::Triangle);
    }
    let mut png = Vec::new();
    image
        .write_to(&mut std::io::Cursor::new(&mut png), ImageFormat::Png)
        .map_err(|e| format!("png: {e}"))?;
    Ok(Capture {
        image_base64: base64::engine::general_purpose::STANDARD.encode(&png),
        title,
        app_name,
        width: image.width(),
        height: image.height(),
        source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(index: usize, title: &str, focused: bool) -> Candidate {
        Candidate { index, own: false, title: title.into(), focused, minimized: false, width: 1200, height: 800 }
    }

    #[test]
    fn focused_window_wins_over_z_order() {
        let list = [candidate(0, "Notepad", false), candidate(1, "docs.python.org", true)];
        assert_eq!(pick_window(&list), Some(1));
    }

    #[test]
    fn companion_tiny_untitled_and_desktop_windows_are_skipped() {
        let mut own = candidate(0, TITLE, true);
        own.own = true;
        let mut tiny = candidate(1, "Tooltip", false);
        tiny.width = 80;
        let list = [own, tiny, candidate(2, "", false), candidate(3, "Program Manager", false), candidate(4, "Excel", false)];
        assert_eq!(pick_window(&list), Some(4));
    }

    #[test]
    fn regions_take_the_mouse_only_inside() {
        let region = Region { x: 10.0, y: 20.0, width: 100.0, height: 50.0 };
        assert!(region.contains(10.0, 20.0) && region.contains(110.0, 70.0));
        assert!(!region.contains(9.0, 20.0) && !region.contains(50.0, 71.0));
    }
}
