//! Native file and folder dialogs for the app page.

mod options;

use tauri::WebviewWindow;
use tauri_plugin_dialog::{DialogExt, FileDialogBuilder, FilePath};

use crate::commands::Args;
use options::Options;

fn path_text(path: FilePath) -> Option<String> {
    path.into_path()
        .ok()
        .map(|path| path.to_string_lossy().into_owned())
}

fn builder(window: &WebviewWindow, args: &Args) -> FileDialogBuilder<tauri::Wry> {
    let options = Options::from_args(args);
    let mut dialog = window.dialog().file().set_parent(window);
    if let Some(title) = options.title {
        dialog = dialog.set_title(title);
    }
    if let Some(directory) = options.directory {
        dialog = dialog.set_directory(directory);
    }
    if let Some(name) = options.file_name {
        dialog = dialog.set_file_name(name);
    }
    for (label, extensions) in &options.filters {
        let extensions: Vec<&str> = extensions.iter().map(String::as_str).collect();
        dialog = dialog.add_filter(label, &extensions);
    }
    dialog
}

/// Ask for a folder: its path, or `null` when the user cancelled.
#[tauri::command]
pub async fn pick_folder(window: WebviewWindow, args: Args) -> Option<String> {
    builder(&window, &args)
        .blocking_pick_folder()
        .and_then(path_text)
}

/// Ask for files: their paths, empty when the user cancelled.
#[tauri::command]
pub async fn pick_files(window: WebviewWindow, args: Args) -> Vec<String> {
    builder(&window, &args)
        .blocking_pick_files()
        .into_iter()
        .flatten()
        .filter_map(path_text)
        .collect()
}

/// Ask where to save a file: its path, or `null` when the user cancelled.
#[tauri::command]
pub async fn save_file(window: WebviewWindow, args: Args) -> Option<String> {
    builder(&window, &args)
        .blocking_save_file()
        .and_then(path_text)
}
