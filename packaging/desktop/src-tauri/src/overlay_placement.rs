//! Native desktop-coordinate placement persistence and recovery for overlays.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::WebviewWindow;

use crate::commands::{string_arg, Args};
use crate::overlay::is_overlay_label;
use crate::overlay_coordinates::DesktopSpace;

const PLACEMENT_VERSION: u8 = if cfg!(target_os = "linux") { 2 } else { 1 };

#[derive(Clone, Copy, Deserialize, Serialize)]
struct Placement {
    version: u8,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl Placement {
    fn valid(self) -> bool {
        self.version == PLACEMENT_VERSION
            && [self.x, self.y, self.width, self.height]
                .iter()
                .all(|n| n.is_finite())
            && (48.0..=32768.0).contains(&self.width)
            && (48.0..=32768.0).contains(&self.height)
    }

    fn reachable(self, work: Self) -> bool {
        let width = (self.x + self.width).min(work.x + work.width) - self.x.max(work.x);
        let height = (self.y + self.height).min(work.y + work.height) - self.y.max(work.y);
        width >= 48.0 && height >= 48.0
    }

    fn centered(self, work: Self) -> Self {
        Self {
            x: work.x + ((work.width - self.width) / 2.0).max(0.0),
            y: work.y + ((work.height - self.height) / 2.0).max(0.0),
            ..self
        }
    }
}

/// Positions use Windows physical pixels or macOS/GTK logical desktop coordinates.
#[tauri::command]
pub fn overlay_placement(window: WebviewWindow, args: Args) -> Option<Value> {
    if !is_overlay_label(window.label()) {
        return None;
    }
    let scale = window.scale_factor().ok()?;
    let space = DesktopSpace::CURRENT;
    let divisor = space.divisor(scale);
    let position = window.outer_position().ok()?;
    let size = window.inner_size().ok()?;
    let current = Placement {
        version: PLACEMENT_VERSION,
        x: position.x as f64 / divisor,
        y: position.y as f64 / divisor,
        width: size.width as f64 / divisor,
        height: size.height as f64 / divisor,
    };
    let operation = string_arg(&args, 0).unwrap_or("read");
    if operation == "read" {
        return serde_json::to_value(current).ok();
    }
    let saved = match operation {
        "restore" => args
            .as_ref()
            .and_then(|args| args.get(1))
            .filter(|v| !v.is_null())
            .cloned()
            .or_else(|| crate::overlay_store::load(&window))
            .and_then(|v| serde_json::from_value::<Placement>(v).ok())
            .filter(|p| p.valid())
            .unwrap_or(current),
        "validate" => current,
        "reset" => current,
        _ => return None,
    };
    if !saved.valid() {
        return None;
    }
    let monitors = window.available_monitors().ok()?;
    let areas: Vec<_> = monitors
        .iter()
        .map(|monitor| {
            let work = monitor.work_area();
            let divisor = space.divisor(monitor.scale_factor());
            Placement {
                version: PLACEMENT_VERSION,
                x: work.position.x as f64 / divisor,
                y: work.position.y as f64 / divisor,
                width: work.size.width as f64 / divisor,
                height: work.size.height as f64 / divisor,
            }
        })
        .collect();
    let reachable = areas.iter().find(|work| saved.reachable(**work));
    let target = if operation != "reset" && reachable.is_some() {
        saved
    } else {
        let work = areas
            .iter()
            .find(|work| current.reachable(**work))
            .or(areas.first())?;
        saved.centered(*work)
    };
    if operation == "validate" && reachable.is_some() {
        return serde_json::to_value(current).ok();
    }
    window
        .set_position(space.position(target.x, target.y))
        .ok()?;
    window
        .set_size(space.size(target.width, target.height))
        .ok()?;
    serde_json::to_value(target).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixed_dpi_restore_uses_one_native_coordinate_space() {
        let secondary = Placement {
            version: PLACEMENT_VERSION,
            x: 1920.0,
            y: 0.0,
            width: 2560.0,
            height: 1440.0,
        };
        let visible = Placement {
            version: PLACEMENT_VERSION,
            x: 2200.0,
            y: 200.0,
            width: 600.0,
            height: 800.0,
        };
        assert!(visible.reachable(secondary));
        let removed = Placement {
            x: -4000.0,
            ..visible
        };
        assert!(!removed.reachable(secondary));
        let reset = removed.centered(secondary);
        assert_eq!((reset.x, reset.y), (2900.0, 320.0));
        assert!(reset.reachable(secondary));
        assert!(!Placement {
            version: 0,
            ..visible
        }
        .valid());
        #[cfg(target_os = "linux")]
        assert!(!Placement {
            version: 1,
            ..visible
        }
        .valid());
        assert!(!Placement {
            width: f64::NAN,
            ..visible
        }
        .valid());
    }
}
