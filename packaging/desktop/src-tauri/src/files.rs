//! Files dragged from the desktop onto the main window, forwarded to the app page
//! with their real paths.

use serde_json::{json, Value};
use tauri::DragDropEvent;

/// Page event the app listens for.
pub const DROP_EVENT: &str = "kt:desktop-file-drop";

fn paths_of(paths: &[std::path::PathBuf]) -> Vec<String> {
    paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect()
}

/// The event detail for `event`: the phase, the dragged paths and the pointer in physical pixels.
pub fn detail(event: &DragDropEvent) -> Value {
    match event {
        DragDropEvent::Enter { paths, position } => {
            json!({ "phase": "enter", "paths": paths_of(paths), "x": position.x, "y": position.y })
        }
        DragDropEvent::Over { position } => {
            json!({ "phase": "over", "paths": [], "x": position.x, "y": position.y })
        }
        DragDropEvent::Drop { paths, position } => {
            json!({ "phase": "drop", "paths": paths_of(paths), "x": position.x, "y": position.y })
        }
        _ => json!({ "phase": "leave", "paths": [], "x": 0, "y": 0 }),
    }
}

/// Script that delivers `detail` to the page.
pub fn script(detail: &Value) -> String {
    format!("window.dispatchEvent(new CustomEvent('{DROP_EVENT}', {{ detail: {detail} }}))")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use tauri::PhysicalPosition;

    fn at(x: f64, y: f64) -> PhysicalPosition<f64> {
        PhysicalPosition::new(x, y)
    }

    #[test]
    fn a_drop_carries_the_real_paths_and_the_pointer() {
        let event = DragDropEvent::Drop {
            paths: vec![
                PathBuf::from("/home/a b/note.txt"),
                PathBuf::from("/x/y.png"),
            ],
            position: at(10.0, 20.5),
        };
        let value = detail(&event);
        assert_eq!(value["phase"], "drop");
        assert_eq!(value["paths"][0], "/home/a b/note.txt");
        assert_eq!(value["paths"][1], "/x/y.png");
        assert_eq!(value["x"], 10.0);
        assert_eq!(value["y"], 20.5);
    }

    #[test]
    fn hovering_phases_name_the_phase() {
        let enter = DragDropEvent::Enter {
            paths: vec![PathBuf::from("/a")],
            position: at(1.0, 2.0),
        };
        assert_eq!(detail(&enter)["phase"], "enter");
        assert_eq!(
            detail(&DragDropEvent::Over {
                position: at(3.0, 4.0)
            })["phase"],
            "over"
        );
        assert_eq!(detail(&DragDropEvent::Leave)["phase"], "leave");
    }

    #[test]
    fn the_script_cannot_be_broken_out_of_by_a_hostile_file_name() {
        let event = DragDropEvent::Drop {
            paths: vec![
                PathBuf::from("/x/'});alert(1);({'.txt"),
                PathBuf::from("/x/\"q\".txt"),
            ],
            position: at(0.0, 0.0),
        };
        let text = script(&detail(&event));
        assert!(text.starts_with("window.dispatchEvent(new CustomEvent('kt:desktop-file-drop'"));
        let start = text.find("detail: ").unwrap() + "detail: ".len();
        let end = text.rfind(" }))").unwrap();
        let parsed: Value = serde_json::from_str(&text[start..end]).unwrap();
        assert_eq!(parsed["paths"][0], "/x/'});alert(1);({'.txt");
        assert_eq!(parsed["paths"][1], "/x/\"q\".txt");
    }
}
