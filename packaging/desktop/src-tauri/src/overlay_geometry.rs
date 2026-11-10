//! Native overlay bounds and bottom-anchored resizing.

use crate::commands::{number_arg, Args};
use crate::overlay::is_overlay_label;
use tauri::{LogicalPosition, LogicalSize, WebviewWindow};
const MIN_SIZE: f64 = 48.0;
const MAX_SIZE: f64 = 4096.0;

fn resized_position(bounds: [f64; 4], size: (f64, f64)) -> (f64, f64) {
    (
        bounds[0] + (bounds[2] - size.0) / 2.0,
        bounds[1] + bounds[3] - size.1,
    )
}

/// Resize without trusting WebView screenX/screenY (unreliable on WKWebView).
#[tauri::command]
pub fn overlay_resize(window: WebviewWindow, args: Args) -> bool {
    if !is_overlay_label(window.label()) {
        return false;
    }
    let (Some(w), Some(h)) = (number_arg(&args, 0), number_arg(&args, 1)) else {
        return false;
    };
    let Some(bounds) = overlay_get_bounds(window.clone()) else {
        return false;
    };
    let size = (w.clamp(MIN_SIZE, MAX_SIZE), h.clamp(MIN_SIZE, MAX_SIZE));
    let (x, y) = resized_position(bounds, size);
    window.set_size(LogicalSize::new(size.0, size.1)).is_ok()
        && window.set_position(LogicalPosition::new(x, y)).is_ok()
}

/// Read the calling overlay's native placement, in logical screen pixels.
#[tauri::command]
pub fn overlay_get_bounds(window: WebviewWindow) -> Option<[f64; 4]> {
    if !is_overlay_label(window.label()) {
        return None;
    }
    let scale = window.scale_factor().ok()?;
    let position = window.outer_position().ok()?;
    let size = window.inner_size().ok()?;
    Some([
        position.x as f64 / scale,
        position.y as f64 / scale,
        size.width as f64 / scale,
        size.height as f64 / scale,
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn resizing_keeps_the_bottom_center_in_place() {
        assert_eq!(
            resized_position([1200.0, 40.0, 500.0, 900.0], (1000.0, 700.0)),
            (950.0, 240.0)
        );
        assert_eq!(
            resized_position([-250.0, 40.0, 1000.0, 700.0], (500.0, 900.0)),
            (0.0, -160.0)
        );
    }
}
