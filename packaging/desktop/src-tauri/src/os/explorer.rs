//! The "Open with KohakuTerrarium" entry in the Explorer context menu of folders.

use std::path::Path;

use serde_json::json;
use tauri::{AppHandle, Manager, State};

use crate::commands::Args;
use crate::settings::ShellSettings;

/// The per-user registry root that Explorer merges into its class definitions.
pub const CLASSES: &str = r"Software\Classes";
const VERB: &str = "KohakuTerrarium";
const LABEL: &str = "Open with KohakuTerrarium";
/// Where the entry goes, with the argument that names the folder: the folder itself, and the
/// empty space inside an open folder.
const TARGETS: &[(&str, &str)] = &[
    (r"Directory\shell", "%1"),
    (r"Directory\Background\shell", "%V"),
];

/// One context-menu entry: its key below the classes root, caption, icon and command line.
#[derive(Debug, PartialEq, Eq)]
pub struct Entry {
    pub key: String,
    pub label: &'static str,
    pub icon: String,
    pub command: String,
}

/// The entries that open a folder in the app at `exe`.
pub fn entries(exe: &Path) -> Vec<Entry> {
    let exe = exe.display().to_string();
    TARGETS
        .iter()
        .map(|(parent, argument)| Entry {
            key: format!(r"{parent}\{VERB}"),
            label: LABEL,
            icon: format!("\"{exe}\""),
            command: format!("\"{exe}\" \"{argument}\""),
        })
        .collect()
}

#[cfg(windows)]
mod registry {
    use std::io;
    use std::path::Path;

    use winreg::enums::{HKEY_CURRENT_USER, KEY_READ};
    use winreg::RegKey;

    use super::{entries, TARGETS, VERB};

    fn hkcu() -> RegKey {
        RegKey::predef(HKEY_CURRENT_USER)
    }

    pub fn install(classes: &str, exe: &Path) -> io::Result<()> {
        for entry in entries(exe) {
            let (key, _) = hkcu().create_subkey(format!(r"{classes}\{}", entry.key))?;
            key.set_value("", &entry.label)?;
            key.set_value("Icon", &entry.icon)?;
            let (command, _) = key.create_subkey("command")?;
            command.set_value("", &entry.command)?;
        }
        Ok(())
    }

    pub fn remove(classes: &str) -> io::Result<()> {
        for (parent, _) in TARGETS {
            match hkcu().delete_subkey_all(format!(r"{classes}\{parent}\{VERB}")) {
                Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
                _ => {}
            }
        }
        Ok(())
    }

    pub fn is_installed(classes: &str, exe: &Path) -> bool {
        entries(exe).iter().all(|entry| {
            hkcu()
                .open_subkey_with_flags(format!(r"{classes}\{}\command", entry.key), KEY_READ)
                .and_then(|key| key.get_value::<String, _>(""))
                .is_ok_and(|command| command == entry.command)
        })
    }
}

#[cfg(windows)]
pub use registry::{install, is_installed, remove};

#[cfg(not(windows))]
pub fn install(_classes: &str, _exe: &Path) -> std::io::Result<()> {
    Ok(())
}

#[cfg(not(windows))]
pub fn remove(_classes: &str) -> std::io::Result<()> {
    Ok(())
}

#[cfg(not(windows))]
pub fn is_installed(_classes: &str, _exe: &Path) -> bool {
    false
}

/// Re-write the entries for this install location when the user turned them on.
pub fn sync(app: &AppHandle) {
    let enabled = app.state::<ShellSettings>().get_bool("explorerVerbs");
    if let (true, Ok(exe)) = (enabled, std::env::current_exe()) {
        let _ = install(CLASSES, &exe);
    }
}

/// Whether the Explorer entries are installed for this copy of the app.
#[tauri::command]
pub fn get_explorer_verbs() -> bool {
    std::env::current_exe().is_ok_and(|exe| is_installed(CLASSES, &exe))
}

/// Add (`[true]`) or remove (`[false]`) the Explorer entries and remember the choice.
#[tauri::command]
pub fn set_explorer_verbs(settings: State<ShellSettings>, args: Args) -> bool {
    let Some(on) = args
        .as_ref()
        .and_then(|values| values.first())
        .and_then(serde_json::Value::as_bool)
    else {
        return false;
    };
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let changed = if on {
        install(CLASSES, &exe)
    } else {
        remove(CLASSES)
    };
    changed.is_ok() && settings.patch(&json!({ "explorerVerbs": on })).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_folder_and_the_space_inside_it_each_get_an_entry_with_the_right_argument() {
        let found = entries(Path::new(r"C:\Apps\Kohaku Terrarium\kohakuterrarium.exe"));
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].key, r"Directory\shell\KohakuTerrarium");
        assert_eq!(
            found[0].command,
            r#""C:\Apps\Kohaku Terrarium\kohakuterrarium.exe" "%1""#
        );
        assert_eq!(found[1].key, r"Directory\Background\shell\KohakuTerrarium");
        assert!(found[1].command.ends_with(r#""%V""#));
        assert!(found.iter().all(|e| e.label == "Open with KohakuTerrarium"));
        assert!(found[0].icon.starts_with("\"C:"));
    }

    #[cfg(windows)]
    #[test]
    fn the_entries_are_written_found_and_removed_in_the_registry() {
        use winreg::enums::HKEY_CURRENT_USER;
        use winreg::RegKey;

        let base = format!(r"Software\KohakuTerrariumTest\{}", std::process::id());
        let exe = Path::new(r"C:\Apps\kohakuterrarium.exe");
        assert!(!is_installed(&base, exe));

        install(&base, exe).unwrap();
        assert!(is_installed(&base, exe));
        assert!(!is_installed(
            &base,
            Path::new(r"C:\Elsewhere\kohakuterrarium.exe")
        ));
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let key = hkcu
            .open_subkey(format!(r"{base}\Directory\shell\KohakuTerrarium"))
            .unwrap();
        assert_eq!(
            key.get_value::<String, _>("").unwrap(),
            "Open with KohakuTerrarium"
        );

        install(&base, Path::new(r"C:\New\kohakuterrarium.exe")).unwrap();
        assert!(is_installed(
            &base,
            Path::new(r"C:\New\kohakuterrarium.exe")
        ));
        assert!(!is_installed(&base, exe));

        remove(&base).unwrap();
        assert!(!is_installed(
            &base,
            Path::new(r"C:\New\kohakuterrarium.exe")
        ));
        assert!(hkcu
            .open_subkey(format!(r"{base}\Directory\shell\KohakuTerrarium"))
            .is_err());
        remove(&base).unwrap();
        let _ = hkcu.delete_subkey_all(r"Software\KohakuTerrariumTest");
    }
}
