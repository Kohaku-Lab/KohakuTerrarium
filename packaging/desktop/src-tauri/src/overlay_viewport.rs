//! Visible work areas for menus inside partly off-screen overlays.

use tauri::WebviewWindow;

use crate::commands::{number_arg, Args};
use crate::overlay::{is_overlay_label, Rect};

/// Visible work area in window-relative CSS pixels, including off-screen origins and HiDPI.
fn visible_viewport(window: Rect, work: Rect, scale: f64) -> [f64; 4] {
    let left = window.x.max(work.x);
    let top = window.y.max(work.y);
    [
        (left - window.x) / scale,
        (top - window.y) / scale,
        ((window.x + window.w).min(work.x + work.w) - left).max(0.0) / scale,
        ((window.y + window.h).min(work.y + work.h) - top).max(0.0) / scale,
    ]
}

// macOS monitor lookup uses desktop points, while Tauri positions are scaled per monitor.
fn desktop_scale(scale: f64) -> f64 {
    if cfg!(target_os = "macos") {
        scale
    } else {
        1.0
    }
}

fn desktop_rect(rect: Rect, scale: f64) -> Rect {
    let divisor = desktop_scale(scale);
    Rect {
        x: rect.x / divisor,
        y: rect.y / divisor,
        w: rect.w / divisor,
        h: rect.h / divisor,
    }
}

/// Work area of the monitor at a window-relative point, clipped to this overlay.
#[tauri::command]
pub fn overlay_get_viewport(window: WebviewWindow, args: Args) -> Option<[f64; 4]> {
    if !is_overlay_label(window.label()) {
        return None;
    }
    let scale = window.scale_factor().ok()?;
    let origin = window.inner_position().ok()?;
    let size = window.inner_size().ok()?;
    let x = number_arg(&args, 0).unwrap_or(size.width as f64 / scale / 2.0);
    let y = number_arg(&args, 1).unwrap_or(size.height as f64 / scale / 2.0);
    let divisor = desktop_scale(scale);
    let monitor = window
        .monitor_from_point(
            (origin.x as f64 + x * scale) / divisor,
            (origin.y as f64 + y * scale) / divisor,
        )
        .ok()
        .flatten()
        .or_else(|| window.current_monitor().ok().flatten())?;
    let work = monitor.work_area();
    Some(visible_viewport(
        desktop_rect(
            Rect {
                x: origin.x as f64,
                y: origin.y as f64,
                w: size.width as f64,
                h: size.height as f64,
            },
            scale,
        ),
        desktop_rect(
            Rect {
                x: work.position.x as f64,
                y: work.position.y as f64,
                w: work.size.width as f64,
                h: work.size.height as f64,
            },
            monitor.scale_factor(),
        ),
        scale / divisor,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(target_os = "macos")]
    #[test]
    fn mixed_density_monitors_share_desktop_points() {
        let window = desktop_rect(
            Rect {
                x: -600.0,
                y: 100.0,
                w: 1200.0,
                h: 1600.0,
            },
            2.0,
        );
        let work = desktop_rect(
            Rect {
                x: -1920.0,
                y: 24.0,
                w: 1920.0,
                h: 1056.0,
            },
            1.0,
        );
        assert_eq!(
            visible_viewport(window, work, 1.0),
            [0.0, 0.0, 300.0, 800.0]
        );
    }

    #[test]
    fn menu_viewport_excludes_offscreen_space_and_system_bars() {
        let work = Rect {
            x: -2560.0,
            y: 48.0,
            w: 2560.0,
            h: 1392.0,
        };
        let window = Rect {
            x: -500.0,
            y: -40.0,
            w: 1000.0,
            h: 1600.0,
        };
        assert_eq!(
            visible_viewport(window, work, 2.0),
            [0.0, 44.0, 250.0, 696.0]
        );
        let window = Rect {
            x: -2800.0,
            y: 1200.0,
            w: 1000.0,
            h: 900.0,
        };
        assert_eq!(
            visible_viewport(window, work, 2.0),
            [120.0, 0.0, 380.0, 120.0]
        );
        let window = Rect {
            x: -2400.0,
            y: 100.0,
            w: 600.0,
            h: 800.0,
        };
        assert_eq!(
            visible_viewport(window, work, 1.0),
            [0.0, 0.0, 600.0, 800.0]
        );
    }
}
