//! Shell preferences that outlive a run, declared once in `SPECS` and stored as JSON.
//! The page reads and patches them through `get_shell_settings` / `patch_shell_settings`.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde_json::{json, Map, Value};
use tauri::State;

use crate::commands::Args;

const SETTINGS_FILE: &str = "shell-settings.json";
const MAX_TEXT: usize = 200;
const MAX_LIST: usize = 64;

#[derive(Clone, Copy)]
enum Kind {
    Bool,
    Choice(&'static [&'static str]),
    OptText,
    List,
    Table,
}

struct Spec {
    key: &'static str,
    kind: Kind,
    default: fn() -> Value,
}

const SPECS: &[Spec] = &[
    Spec {
        key: "closeToTray",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "summonShortcut",
        kind: Kind::OptText,
        default: || Value::Null,
    },
    Spec {
        key: "keepAwake",
        kind: Kind::Choice(&["never", "running", "running-on-ac"]),
        default: || json!("running"),
    },
    Spec {
        key: "startHidden",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "windowMaterial",
        kind: Kind::Choice(&["auto", "none", "mica", "acrylic"]),
        default: || json!("auto"),
    },
    Spec {
        key: "followAccent",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "explorerVerbs",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "updateChannel",
        kind: Kind::Choice(&["stable", "nightly"]),
        default: || json!("stable"),
    },
    Spec {
        key: "notifyActions",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "firstRunDismissed",
        kind: Kind::Bool,
        default: || json!(false),
    },
    Spec {
        key: "zoom",
        kind: Kind::Table,
        default: || json!({}),
    },
    Spec {
        key: "restorePopouts",
        kind: Kind::Bool,
        default: || json!(true),
    },
    Spec {
        key: "popouts",
        kind: Kind::List,
        default: || json!([]),
    },
];

fn spec_of(key: &str) -> Option<&'static Spec> {
    SPECS.iter().find(|spec| spec.key == key)
}

/// The stored form of `value` for a setting of `kind`, or `None` when it does not fit.
fn normalize(kind: Kind, value: &Value) -> Option<Value> {
    match kind {
        Kind::Bool => value.as_bool().map(Value::Bool),
        Kind::Choice(options) => value
            .as_str()
            .filter(|text| options.contains(text))
            .map(|text| json!(text)),
        Kind::OptText => match value {
            Value::Null => Some(Value::Null),
            Value::String(text) if text.is_empty() => Some(Value::Null),
            Value::String(text) if text.chars().count() <= MAX_TEXT => Some(value.clone()),
            _ => None,
        },
        Kind::List => value
            .as_array()
            .filter(|items| items.len() <= MAX_LIST)
            .map(|_| value.clone()),
        Kind::Table => value
            .as_object()
            .filter(|entries| entries.len() <= MAX_LIST)
            .map(|_| value.clone()),
    }
}

#[derive(Default)]
pub struct ShellSettings {
    values: Mutex<Map<String, Value>>,
    quitting: AtomicBool,
    path: Mutex<Option<PathBuf>>,
}

impl ShellSettings {
    pub fn load(&self, directory: PathBuf) {
        let path = directory.join(SETTINGS_FILE);
        let stored = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .unwrap_or(Value::Null);
        let mut values = Map::new();
        for spec in SPECS {
            if let Some(value) = stored
                .get(spec.key)
                .and_then(|value| normalize(spec.kind, value))
            {
                values.insert(spec.key.to_string(), value);
            }
        }
        *self.values.lock().unwrap() = values;
        *self.path.lock().unwrap() = Some(path);
    }

    fn save(&self) -> bool {
        let guard = self.path.lock().unwrap();
        let Some(path) = guard.as_ref() else {
            return false;
        };
        let text = Value::Object(self.values.lock().unwrap().clone()).to_string();
        path.parent()
            .is_none_or(|parent| std::fs::create_dir_all(parent).is_ok())
            && std::fs::write(path, text).is_ok()
    }

    /// The value of a declared setting: what was stored, else its default.
    pub fn get(&self, key: &str) -> Value {
        if let Some(value) = self.values.lock().unwrap().get(key) {
            return value.clone();
        }
        spec_of(key).map_or(Value::Null, |spec| (spec.default)())
    }

    pub fn get_bool(&self, key: &str) -> bool {
        self.get(key).as_bool().unwrap_or(false)
    }

    pub fn get_text(&self, key: &str) -> Option<String> {
        self.get(key).as_str().map(str::to_string)
    }

    /// Every declared setting with its current value.
    pub fn snapshot(&self) -> Value {
        let mut all = Map::new();
        for spec in SPECS {
            all.insert(spec.key.to_string(), self.get(spec.key));
        }
        Value::Object(all)
    }

    /// Apply `patch` (an object of setting keys) all-or-nothing and save it.
    pub fn patch(&self, patch: &Value) -> Result<Value, String> {
        let Some(entries) = patch.as_object() else {
            return Err("a settings patch is an object".into());
        };
        let mut accepted = Vec::new();
        for (key, value) in entries {
            let spec = spec_of(key).ok_or_else(|| format!("unknown setting {key}"))?;
            let value = normalize(spec.kind, value).ok_or_else(|| format!("invalid {key}"))?;
            accepted.push((key.clone(), value));
        }
        {
            let mut values = self.values.lock().unwrap();
            for (key, value) in accepted {
                values.insert(key, value);
            }
        }
        if self.save() {
            Ok(self.snapshot())
        } else {
            Err("settings could not be saved".into())
        }
    }

    fn set(&self, key: &str, value: Value) -> bool {
        self.patch(&json!({ key: value })).is_ok()
    }

    pub fn close_to_tray(&self) -> bool {
        self.get_bool("closeToTray")
    }

    pub fn set_close_to_tray(&self, enabled: bool) -> bool {
        self.set("closeToTray", json!(enabled))
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
        self.get_text("summonShortcut")
    }

    pub fn set_summon_shortcut(&self, shortcut: Option<String>) -> bool {
        self.set("summonShortcut", json!(shortcut))
    }
}

/// Every shell setting with its current value.
#[tauri::command]
pub fn get_shell_settings(settings: State<ShellSettings>) -> Value {
    settings.snapshot()
}

/// Apply a `{ key: value }` patch: `{ ok: true, settings }` or `{ ok: false, error }`.
#[tauri::command]
pub fn patch_shell_settings(settings: State<ShellSettings>, args: Args) -> Value {
    let patch = args
        .as_ref()
        .and_then(|values| values.first())
        .cloned()
        .unwrap_or(Value::Null);
    match settings.patch(&patch) {
        Ok(snapshot) => json!({ "ok": true, "settings": snapshot }),
        Err(error) => json!({ "ok": false, "error": error }),
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
        assert_eq!(settings.get("keepAwake"), json!("running"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn settings_cannot_be_saved_before_the_directory_is_known() {
        assert!(!ShellSettings::default().set_close_to_tray(true));
    }

    #[test]
    fn the_snapshot_lists_every_declared_setting_with_defaults() {
        let settings = ShellSettings::default();
        let snapshot = settings.snapshot();
        for spec in SPECS {
            assert!(snapshot.get(spec.key).is_some(), "{}", spec.key);
        }
        assert_eq!(snapshot["keepAwake"], "running");
        assert_eq!(snapshot["windowMaterial"], "auto");
        assert_eq!(snapshot["updateChannel"], "stable");
        assert_eq!(snapshot["zoom"], json!({}));
        assert_eq!(snapshot["popouts"], json!([]));
        assert_eq!(snapshot["notifyActions"], false);
    }

    #[test]
    fn a_patch_is_all_or_nothing() {
        let dir = scratch("patch");
        let settings = ShellSettings::default();
        settings.load(dir.clone());

        let applied = settings
            .patch(&json!({ "keepAwake": "never", "startHidden": true }))
            .unwrap();
        assert_eq!(applied["keepAwake"], "never");
        assert_eq!(applied["startHidden"], true);

        let bad = settings.patch(&json!({ "startHidden": false, "keepAwake": "sometimes" }));
        assert!(bad.is_err());
        assert_eq!(settings.get("startHidden"), json!(true));
        assert_eq!(settings.get("keepAwake"), json!("never"));

        assert!(settings.patch(&json!({ "madeUp": 1 })).is_err());
        assert!(settings.patch(&json!([1, 2])).is_err());
        assert!(settings.patch(&json!({ "closeToTray": "yes" })).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn stored_values_are_checked_again_on_load() {
        let dir = scratch("stale");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join(SETTINGS_FILE),
            r#"{"keepAwake":"bogus","closeToTray":true,"retired":1,"zoom":[1]}"#,
        )
        .unwrap();
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert_eq!(settings.get("keepAwake"), json!("running"));
        assert!(settings.close_to_tray());
        assert_eq!(settings.get("zoom"), json!({}));
        assert_eq!(settings.get("retired"), Value::Null);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn text_and_collection_limits_are_enforced() {
        let settings = ShellSettings::default();
        settings.load(scratch("limits"));
        let long = "x".repeat(MAX_TEXT + 1);
        assert!(settings.patch(&json!({ "summonShortcut": long })).is_err());
        assert!(settings.patch(&json!({ "summonShortcut": "" })).is_ok());
        assert_eq!(settings.summon_shortcut(), None);
        let many: Vec<u32> = (0..=MAX_LIST as u32).collect();
        assert!(settings.patch(&json!({ "popouts": many })).is_err());
        assert!(settings.patch(&json!({ "popouts": [] })).is_ok());
        assert!(settings.patch(&json!({ "zoom": { "main": 1.2 } })).is_ok());
    }
}
