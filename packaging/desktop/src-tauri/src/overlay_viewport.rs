//! Visible work areas for menus inside partly off-screen overlays.

use tauri::WebviewWindow;

use crate::commands::{number_arg, Args};
use crate::overlay::{is_overlay_label, Rect};
use crate::overlay_coordinates::DesktopSpace;

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
    let space = DesktopSpace::CURRENT;
    let point = space.point((origin.x as f64, origin.y as f64), (x, y), scale);
    let monitor = window
        .monitor_from_point(point.0, point.1)
        .ok()
        .flatten()
        .or_else(|| window.current_monitor().ok().flatten())?;
    let work = monitor.work_area();
    Some(visible_viewport(
        space.rect(
            Rect {
                x: origin.x as f64,
                y: origin.y as f64,
                w: size.width as f64,
                h: size.height as f64,
            },
            scale,
        ),
        space.rect(
            Rect {
                x: work.position.x as f64,
                y: work.position.y as f64,
                w: work.size.width as f64,
                h: work.size.height as f64,
            },
            monitor.scale_factor(),
        ),
        scale / space.divisor(scale),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gtk_two_hidpi_monitors_lookup_and_clip_in_the_same_space() {
        let space = DesktopSpace::Logical;
        let origin = (2200.0, 200.0);
        let point = space.point(origin, (100.0, 100.0), 2.0);
        assert_eq!(point, (1200.0, 200.0));
        let monitors = [
            Rect {
                x: 0.0,
                y: 0.0,
                w: 3840.0,
                h: 2160.0,
            },
            Rect {
                x: 3840.0,
                y: 0.0,
                w: 3840.0,
                h: 2160.0,
            },
        ];
        let work = monitors
            .into_iter()
            .map(|rect| space.rect(rect, 2.0))
            .find(|work| point.0 >= work.x && point.0 < work.x + work.w)
            .unwrap();
        let window = space.rect(
            Rect {
                x: origin.0,
                y: origin.1,
                w: 1200.0,
                h: 1600.0,
            },
            2.0,
        );
        assert_eq!(
            visible_viewport(window, work, 2.0 / space.divisor(2.0)),
            [0.0, 0.0, 600.0, 800.0]
        );
    }

    #[test]
    fn cocoa_and_gtk_mixed_density_monitors_share_desktop_points() {
        let space = DesktopSpace::Logical;
        let window = space.rect(
            Rect {
                x: -600.0,
                y: 100.0,
                w: 1200.0,
                h: 1600.0,
            },
            2.0,
        );
        let work = space.rect(
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
        assert_eq!(
            space.point((-600.0, 100.0), (100.0, 100.0), 2.0),
            (-200.0, 150.0)
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
