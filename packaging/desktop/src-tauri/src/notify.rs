//! System notifications with buttons: the click comes back to the page as an event.

use serde_json::Value;
use tauri::AppHandle;

use crate::commands::{string_arg, Args};

/// Page event carrying `detail.token` and `detail.id` (the pressed button, `null` for the body).
#[cfg_attr(not(windows), allow(dead_code))]
pub const ACTION_EVENT: &str = "kt:desktop-notification-action";
const MAX_ACTIONS: usize = 3;
const MAX_LABEL: usize = 40;
const MAX_ID: usize = 100;
const MAX_TOKEN: usize = 100;

/// One button of a notification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Action {
    pub id: String,
    pub label: String,
}

/// The buttons of a page-supplied `[{ id, label }]`: invalid and repeated entries are dropped and
/// no more than three are kept.
pub fn actions_of(spec: &Value) -> Vec<Action> {
    let mut actions: Vec<Action> = Vec::new();
    for entry in spec.as_array().into_iter().flatten() {
        if actions.len() >= MAX_ACTIONS {
            break;
        }
        let id = entry.get("id").and_then(Value::as_str).unwrap_or("");
        let label = entry
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let repeated = actions.iter().any(|action| action.id == id);
        if id.is_empty() || id.chars().count() > MAX_ID || label.is_empty() || repeated {
            continue;
        }
        actions.push(Action {
            id: id.to_string(),
            label: label.chars().take(MAX_LABEL).collect(),
        });
    }
    actions
}

/// Script that tells the page which button of the notification `token` was pressed.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn choice_script(token: &str, id: Option<&str>) -> String {
    let json = |text: &str| serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string());
    let id = id.map_or_else(|| "null".to_string(), json);
    format!(
        "window.dispatchEvent(new CustomEvent('{ACTION_EVENT}', {{ detail: {{ token: {}, id: {id} }} }}))",
        json(token)
    )
}

#[cfg(windows)]
fn show(app: &AppHandle, title: &str, body: &str, actions: &[Action], token: &str) -> bool {
    use tauri::Manager;
    use tauri_winrt_notification::Toast;

    let handle = app.clone();
    let reply = token.to_string();
    let mut toast = Toast::new(&app.config().identifier)
        .title(title)
        .text1(body)
        .on_activated(move |pressed| {
            crate::window::focus(&handle);
            if let Some(main) = handle.get_webview_window("main") {
                let _ = main.eval(choice_script(&reply, pressed.as_deref()));
            }
            Ok(())
        });
    for action in actions {
        toast = toast.add_button(&action.label, &action.id);
    }
    toast.show().is_ok()
}

#[cfg(not(windows))]
fn show(_app: &AppHandle, _title: &str, _body: &str, _actions: &[Action], _token: &str) -> bool {
    false
}

/// Show `[title, body, [{ id, label }], token]`; the pressed button comes back as a page event
/// carrying `token`. `false` when the system has no notification buttons or the arguments are bad.
#[tauri::command]
pub fn show_action_notification(app: AppHandle, args: Args) -> bool {
    let (Some(title), Some(body), Some(token)) = (
        string_arg(&args, 0),
        string_arg(&args, 1),
        string_arg(&args, 3),
    ) else {
        return false;
    };
    let actions = args
        .as_ref()
        .and_then(|values| values.get(2))
        .map(actions_of)
        .unwrap_or_default();
    if actions.is_empty() || token.is_empty() || token.chars().count() > MAX_TOKEN {
        return false;
    }
    show(&app, title, body, &actions, token)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn action(id: &str, label: &str) -> Action {
        Action {
            id: id.into(),
            label: label.into(),
        }
    }

    #[test]
    fn buttons_keep_order_and_trim_labels() {
        let spec = json!([{ "id": "a", "label": " Allow " }, { "id": "b", "label": "Deny" }]);
        assert_eq!(
            actions_of(&spec),
            vec![action("a", "Allow"), action("b", "Deny")]
        );
    }

    #[test]
    fn invalid_repeated_and_surplus_buttons_are_dropped() {
        let spec = json!([
            { "id": "a", "label": "A" },
            { "id": "a", "label": "again" },
            { "id": "", "label": "no id" },
            { "id": "c", "label": "  " },
            { "id": "d" },
            { "id": "e".repeat(MAX_ID + 1), "label": "long" },
            { "id": "f", "label": "x".repeat(300) },
            { "id": "g", "label": "G" },
            { "id": "h", "label": "H" },
            "text",
        ]);
        let actions = actions_of(&spec);
        assert_eq!(actions.len(), MAX_ACTIONS);
        assert_eq!(actions[0], action("a", "A"));
        assert_eq!(actions[1].label.chars().count(), MAX_LABEL);
        assert!(actions_of(&json!({})).is_empty());
        assert!(actions_of(&Value::Null).is_empty());
    }

    #[test]
    fn the_pressed_button_travels_as_json_and_the_body_as_null() {
        let script = choice_script("t\"1", Some("a'b"));
        assert!(script.contains(ACTION_EVENT));
        assert!(script.contains(r#"token: "t\"1""#));
        assert!(script.contains(r#"id: "a'b""#));
        assert!(choice_script("t", None).contains("id: null"));
    }
}
