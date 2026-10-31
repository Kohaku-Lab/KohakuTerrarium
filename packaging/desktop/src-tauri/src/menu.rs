//! The application menu bar (macOS), whose Edit menu also keeps copy and paste working in the page.

use tauri::menu::{
    Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID,
};
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_opener::OpenerExt;

use crate::window;

/// Page event carrying the chosen menu action in `detail.action`.
pub const MENU_EVENT: &str = "kt:desktop-menu";
const ID_PREFIX: &str = "kt-menu:";
const DOCS_URL: &str = "https://github.com/Kohaku-Lab/KohakuTerrarium";

/// Actions the page knows how to perform: `(menu id, label, accelerator)`.
const PAGE_ACTIONS: &[(&str, &str, Option<&str>)] = &[
    ("settings", "Settings…", Some("CmdOrCtrl+,")),
    ("new-session", "New Session…", Some("CmdOrCtrl+N")),
    ("palette", "Command Palette…", Some("CmdOrCtrl+K")),
    ("popout", "Pop Out Session", None),
];

fn page_item(app: &AppHandle, action: &str) -> tauri::Result<MenuItem<Wry>> {
    let (id, label, accelerator) = PAGE_ACTIONS
        .iter()
        .find(|(id, _, _)| *id == action)
        .expect("a menu action is declared in PAGE_ACTIONS");
    MenuItem::with_id(app, format!("{ID_PREFIX}{id}"), *label, true, *accelerator)
}

/// The page action a menu item id names, if it is one of ours.
pub fn action_of(id: &str) -> Option<&str> {
    let action = id.strip_prefix(ID_PREFIX)?;
    PAGE_ACTIONS
        .iter()
        .any(|(known, _, _)| *known == action)
        .then_some(action)
}

/// Script that delivers `action` to the page.
pub fn script(action: &str) -> String {
    format!("window.dispatchEvent(new CustomEvent('{MENU_EVENT}', {{ detail: {{ action: '{action}' }} }}))")
}

/// Build the menu: app, File, Edit, View, Window and Help.
pub fn build(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let docs = MenuItem::with_id(
        app,
        format!("{ID_PREFIX}docs"),
        "Documentation",
        true,
        None::<&str>,
    )?;
    let app_menu = Submenu::with_items(
        app,
        "KohakuTerrarium",
        true,
        &[
            &PredefinedMenuItem::about(app, None, None)?,
            &PredefinedMenuItem::separator(app)?,
            &page_item(app, "settings")?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::services(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, None)?,
        ],
    )?;
    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &page_item(app, "new-session")?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let view = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &page_item(app, "palette")?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;
    let window_menu = Submenu::with_id_and_items(
        app,
        WINDOW_SUBMENU_ID,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &page_item(app, "popout")?,
        ],
    )?;
    let help = Submenu::with_id_and_items(app, HELP_SUBMENU_ID, "Help", true, &[&docs])?;
    Menu::with_items(app, &[&app_menu, &file, &edit, &view, &window_menu, &help])
}

/// Number of top-level menus [`build`] produces.
pub const TOP_LEVEL_MENUS: usize = 6;

/// Set the menu bar on macOS, where every app has one.
pub fn install(app: &AppHandle) -> tauri::Result<()> {
    if cfg!(target_os = "macos") {
        app.set_menu(build(app)?)?;
    }
    Ok(())
}

/// Handle a chosen menu item: open the docs, or pass a page action to the page.
pub fn on_event(app: &AppHandle, event: &MenuEvent) {
    let id = event.id().as_ref();
    if id == format!("{ID_PREFIX}docs") {
        let _ = app.opener().open_url(DOCS_URL, None::<&str>);
        return;
    }
    let Some(action) = action_of(id) else {
        return;
    };
    window::focus(app);
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.eval(script(action));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_our_prefixed_ids_are_page_actions() {
        assert_eq!(action_of("kt-menu:palette"), Some("palette"));
        assert_eq!(action_of("kt-menu:popout"), Some("popout"));
        assert_eq!(action_of("kt-menu:docs"), None);
        assert_eq!(action_of("kt-menu:nonsense"), None);
        assert_eq!(action_of("show"), None);
        assert_eq!(action_of("quit"), None);
    }

    #[test]
    fn every_declared_action_has_a_label_and_a_unique_name() {
        let mut names: Vec<_> = PAGE_ACTIONS.iter().map(|(id, _, _)| *id).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), PAGE_ACTIONS.len());
        assert!(PAGE_ACTIONS.iter().all(|(_, label, _)| !label.is_empty()));
    }

    #[test]
    fn the_script_names_the_event_and_the_action() {
        let text = script("settings");
        assert!(text.contains(MENU_EVENT));
        assert!(text.contains("action: 'settings'"));
    }
}
