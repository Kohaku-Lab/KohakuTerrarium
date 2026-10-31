//! Shell preferences that outlive a run, stored as JSON next to the window state.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde_json::{json, Value};

const SETTINGS_FILE: &str = "shell-settings.json";

#[derive(Default)]
pub struct ShellSettings {
    close_to_tray: AtomicBool,
    quitting: AtomicBool,
    summon_shortcut: Mutex<Option<String>>,
    path: Mutex<Option<PathBuf>>,
}

impl ShellSettings {
    pub fn load(&self, directory: PathBuf) {
        let path = directory.join(SETTINGS_FILE);
        let stored = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .unwrap_or(Value::Null);
        self.close_to_tray.store(
            stored["closeToTray"].as_bool().unwrap_or(false),
            Ordering::SeqCst,
        );
        *self.summon_shortcut.lock().unwrap() = stored["summonShortcut"]
            .as_str()
            .filter(|text| !text.is_empty())
            .map(str::to_string);
        *self.path.lock().unwrap() = Some(path);
    }

    fn save(&self) -> bool {
        let guard = self.path.lock().unwrap();
        let Some(path) = guard.as_ref() else {
            return false;
        };
        let text = json!({
            "closeToTray": self.close_to_tray(),
            "summonShortcut": self.summon_shortcut(),
        })
        .to_string();
        path.parent()
            .is_none_or(|parent| std::fs::create_dir_all(parent).is_ok())
            && std::fs::write(path, text).is_ok()
    }

    pub fn close_to_tray(&self) -> bool {
        self.close_to_tray.load(Ordering::SeqCst)
    }

    pub fn set_close_to_tray(&self, enabled: bool) -> bool {
        self.close_to_tray.store(enabled, Ordering::SeqCst);
        self.save()
    }

    pub fn quitting(&self) -> bool {
        self.quitting.load(Ordering::SeqCst)
    }

    pub fn begin_quit(&self) {
        self.quitting.store(true, Ordering::SeqCst);
    }

    /// Whether closing the main window should hide it instead of quitting.
    pub fn hides_on_close(&self) -> bool {
        self.close_to_tray() && !self.quitting()
    }

    /// The system-wide shortcut that raises the app, if the user chose one.
    pub fn summon_shortcut(&self) -> Option<String> {
        self.summon_shortcut.lock().unwrap().clone()
    }

    pub fn set_summon_shortcut(&self, shortcut: Option<String>) -> bool {
        *self.summon_shortcut.lock().unwrap() = shortcut;
        self.save()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kt-settings-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn close_to_tray_defaults_off_and_persists() {
        let dir = scratch("tray");
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert!(!settings.close_to_tray() && !settings.hides_on_close());

        assert!(settings.set_close_to_tray(true));
        assert!(settings.hides_on_close());
        let reloaded = ShellSettings::default();
        reloaded.load(dir.clone());
        assert!(reloaded.close_to_tray());

        settings.begin_quit();
        assert!(!settings.hides_on_close());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_summon_shortcut_persists_without_disturbing_other_settings() {
        let dir = scratch("summon");
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert_eq!(settings.summon_shortcut(), None);

        assert!(settings.set_close_to_tray(true));
        assert!(settings.set_summon_shortcut(Some("Ctrl+Shift+Space".into())));
        let reloaded = ShellSettings::default();
        reloaded.load(dir.clone());
        assert!(reloaded.close_to_tray());
        assert_eq!(
            reloaded.summon_shortcut().as_deref(),
            Some("Ctrl+Shift+Space")
        );

        assert!(reloaded.set_summon_shortcut(None));
        let cleared = ShellSettings::default();
        cleared.load(dir.clone());
        assert_eq!(cleared.summon_shortcut(), None);
        assert!(cleared.close_to_tray());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_corrupt_settings_file_means_the_defaults() {
        let dir = scratch("bad");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(SETTINGS_FILE), "{not json").unwrap();
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert!(!settings.close_to_tray());
        assert_eq!(settings.summon_shortcut(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn settings_cannot_be_saved_before_the_directory_is_known() {
        assert!(!ShellSettings::default().set_close_to_tray(true));
    }
}
