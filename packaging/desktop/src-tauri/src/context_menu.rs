//! Context menus the page asks the operating system to draw.

use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::{AppHandle, Manager, WebviewWindow};

use crate::commands::Args;
use crate::window;

/// Prefix of the menu ids this module owns; the rest is the page's row id.
const ITEM_PREFIX: &str = "ctx:";
/// Page event carrying the chosen row in `detail.id`.
pub const CHOICE_EVENT: &str = "kt:desktop-context-choice";
const MAX_ROWS: usize = 40;
const MAX_LABEL: usize = 80;
const MAX_ID: usize = 200;

/// One row of a context menu.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    Separator,
    Item {
        id: String,
        label: String,
        enabled: bool,
    },
}

/// The rows of a page-supplied `{ items: [...] }`: invalid and repeated rows are dropped, and
/// separators never lead, trail or repeat.
pub fn rows_of(spec: &Value) -> Vec<Row> {
    let mut rows: Vec<Row> = Vec::new();
    for entry in spec
        .get("items")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if rows.len() >= MAX_ROWS {
            break;
        }
        if entry.get("separator").and_then(Value::as_bool) == Some(true) {
            if matches!(rows.last(), Some(Row::Item { .. })) {
                rows.push(Row::Separator);
            }
            continue;
        }
        let id = entry.get("id").and_then(Value::as_str).unwrap_or("");
        let label = entry
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let repeated = rows
            .iter()
            .any(|row| matches!(row, Row::Item { id: known, .. } if known == id));
        if id.is_empty() || id.chars().count() > MAX_ID || label.is_empty() || repeated {
            continue;
        }
        rows.push(Row::Item {
            id: id.to_string(),
            label: label.chars().take(MAX_LABEL).collect(),
            enabled: entry.get("enabled").and_then(Value::as_bool) != Some(false),
        });
    }
    while matches!(rows.last(), Some(Row::Separator)) {
        rows.pop();
    }
    rows
}

/// The page row id a native menu id stands for.
pub fn choice_of(menu_id: &str) -> Option<&str> {
    menu_id.strip_prefix(ITEM_PREFIX)
}

/// Script that tells the page which row was chosen.
pub fn choice_script(id: &str) -> String {
    let id = serde_json::to_string(id).unwrap_or_else(|_| "\"\"".to_string());
    format!("window.dispatchEvent(new CustomEvent('{CHOICE_EVENT}', {{ detail: {{ id: {id} }} }}))")
}

/// Tell the window that has focus (else the main one) which row was chosen.
pub fn deliver(app: &AppHandle, id: &str) {
    let target = app
        .webview_windows()
        .into_values()
        .find(|window| window.is_focused().unwrap_or(false))
        .or_else(|| app.get_webview_window("main"));
    if let Some(target) = target {
        let _ = target.eval(choice_script(id));
    }
}

fn build(window: &WebviewWindow, rows: &[Row]) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::new(window)?;
    for row in rows {
        match row {
            Row::Separator => menu.append(&PredefinedMenuItem::separator(window)?)?,
            Row::Item { id, label, enabled } => menu.append(&MenuItem::with_id(
                window,
                format!("{ITEM_PREFIX}{id}"),
                label,
                *enabled,
                None::<&str>,
            )?)?,
        }
    }
    Ok(menu)
}

/// Show a menu at the pointer: `{ items: [{ id, label, enabled? } | { separator: true }] }`. The
/// chosen row comes back as a page event; `false` when there was nothing to show.
#[tauri::command]
pub fn show_context_menu(window: WebviewWindow, args: Args) -> bool {
    let rows = args
        .as_ref()
        .and_then(|values| values.first())
        .map(rows_of)
        .unwrap_or_default();
    if rows.is_empty() {
        return false;
    }
    window::focus(window.app_handle());
    build(&window, &rows).is_ok_and(|menu| window.popup_menu(&menu).is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn item(id: &str, label: &str) -> Row {
        Row::Item {
            id: id.into(),
            label: label.into(),
            enabled: true,
        }
    }

    #[test]
    fn rows_keep_order_labels_and_enabled_state() {
        let rows = rows_of(&json!({ "items": [
            { "id": "a", "label": "  Refresh " },
            { "id": "b", "label": "Close", "enabled": false },
        ] }));
        assert_eq!(
            rows,
            vec![
                item("a", "Refresh"),
                Row::Item {
                    id: "b".into(),
                    label: "Close".into(),
                    enabled: false
                }
            ]
        );
    }

    #[test]
    fn separators_never_lead_trail_or_repeat() {
        let rows = rows_of(&json!({ "items": [
            { "separator": true },
            { "id": "a", "label": "A" },
            { "separator": true },
            { "separator": true },
            { "id": "b", "label": "B" },
            { "separator": true },
        ] }));
        assert_eq!(rows, vec![item("a", "A"), Row::Separator, item("b", "B")]);
    }

    #[test]
    fn invalid_repeated_and_surplus_rows_are_dropped() {
        let many: Vec<Value> = (0..80)
            .map(|n| json!({ "id": format!("i{n}"), "label": "x" }))
            .collect();
        assert_eq!(rows_of(&json!({ "items": many })).len(), MAX_ROWS);
        let rows = rows_of(&json!({ "items": [
            { "id": "a", "label": "A" },
            { "id": "a", "label": "again" },
            { "id": "", "label": "no id" },
            { "id": "c", "label": "   " },
            { "id": "d" },
            { "label": "no id key" },
            { "id": "e".repeat(MAX_ID + 1), "label": "long id" },
            { "id": "f", "label": "x".repeat(300) },
            "text",
        ] }));
        assert_eq!(rows.len(), 2);
        assert!(matches!(&rows[1], Row::Item { label, .. } if label.chars().count() == MAX_LABEL));
        assert!(rows_of(&json!({})).is_empty());
        assert!(rows_of(&json!({ "items": "x" })).is_empty());
        assert!(rows_of(&Value::Null).is_empty());
    }

    #[test]
    fn only_our_prefix_names_a_row_and_the_id_travels_as_json() {
        assert_eq!(choice_of("ctx:action:pkg/x"), Some("action:pkg/x"));
        assert_eq!(choice_of("show"), None);
        assert_eq!(choice_of("ext:ctx:x"), None);
        let script = choice_script("a\"b'\\");
        assert!(script.contains(CHOICE_EVENT));
        assert!(script.contains(r#"id: "a\"b'\\""#));
    }
}
