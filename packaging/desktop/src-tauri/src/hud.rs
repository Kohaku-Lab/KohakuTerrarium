//! The agents HUD overlay asks the shell to bring one running session to the front.

use tauri::{AppHandle, WebviewWindow};

use crate::commands::{string_arg, Args};
use crate::overlay::is_overlay_label;

const MAX_SCOPE: usize = 100;

/// Whether `scope` is a session id the HUD may open: letters, digits, `.`, `_`, `:` and `-`.
pub fn is_scope(scope: &str) -> bool {
    !scope.is_empty()
        && scope.len() <= MAX_SCOPE
        && scope
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b':' | b'-'))
}

/// The `kt://` link that shows the running session `scope`.
pub fn live_link(scope: &str) -> String {
    format!("kt://live/{scope}")
}

/// Bring the main window forward showing the running session named by the first argument. Only
/// overlay windows may ask.
#[tauri::command]
pub fn focus_session(window: WebviewWindow, app: AppHandle, args: Args) -> bool {
    let Some(scope) = string_arg(&args, 0).filter(|scope| is_scope(scope)) else {
        return false;
    };
    if !is_overlay_label(window.label()) {
        return false;
    }
    crate::deliver_deep_link(&app, live_link(scope));
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_plain_session_ids_are_accepted() {
        assert!(is_scope("graph_c997e8705cf0"));
        assert!(is_scope("a.b:c-d_1"));
        for bad in ["", "a b", "a/b", "a?x=1", "é", &"x".repeat(MAX_SCOPE + 1)] {
            assert!(!is_scope(bad), "{bad}");
        }
    }

    #[test]
    fn the_link_names_the_live_session() {
        assert_eq!(live_link("graph_1"), "kt://live/graph_1");
    }
}
