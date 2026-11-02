//! Source-backend configuration and authenticated development-page navigation.

use std::path::PathBuf;
use std::process::Command;
use std::sync::OnceLock;

use serde::Deserialize;
use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System};
use tauri::{Manager, Url, WebviewWindow};

use crate::backend::Backend;

#[cfg(all(feature = "source-dev", not(debug_assertions)))]
compile_error!("source-dev is only supported by development builds");

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub python: PathBuf,
    pub repository: PathBuf,
    pub data: PathBuf,
    pub frontend: String,
    pub port: u16,
    pub owner_pid: u32,
}

static CONFIG: OnceLock<Option<Config>> = OnceLock::new();

impl Config {
    pub fn validate(&self) -> Result<(), String> {
        let url = Url::parse(&self.frontend).map_err(|e| e.to_string())?;
        if self.owner_pid == 0
            || url.scheme() != "http"
            || url.host_str() != Some("127.0.0.1")
            || url.port().is_none()
            || url.port() == Some(self.port)
            || self.port < 1024
            || url.port().is_some_and(|port| port < 1024)
            || url.path() != "/"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err("source frontend must be a loopback HTTP origin on a separate port".into());
        }
        if !self.python.is_absolute()
            || !self.python.is_file()
            || !self.repository.is_absolute()
            || !self.repository.join("src/kohakuterrarium").is_dir()
            || !self.data.is_absolute()
        {
            return Err(
                "source mode requires an absolute interpreter, checkout and data directory".into(),
            );
        }
        Ok(())
    }
}

pub fn initialize() -> Result<(), String> {
    let config = if cfg!(feature = "source-dev") {
        let raw = std::env::var("KT_DESKTOP_DEV_CONFIG")
            .map_err(|_| "run scripts/dev_desktop.py to configure source mode".to_string())?;
        let config: Config = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
        config.validate()?;
        Some(config)
    } else {
        None
    };
    CONFIG
        .set(config)
        .map_err(|_| "source configuration was already initialized".into())
}

pub fn config() -> Option<&'static Config> {
    CONFIG.get().and_then(Option::as_ref)
}

pub fn active() -> bool {
    config().is_some()
}

fn owner_start(system: &mut System, pid: Pid) -> Option<u64> {
    system.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[pid]),
        true,
        ProcessRefreshKind::nothing(),
    );
    system.process(pid).map(|process| process.start_time())
}

/// The CLI can exit on a failed Vite hook without reaping its running app.
/// Follow the source runner's lifetime and use the ordinary backend shutdown.
pub fn watch_owner(app: tauri::AppHandle) -> Result<(), String> {
    let Some(config) = config() else {
        return Ok(());
    };
    let mut system = System::new().map_err(|error| error.to_string())?;
    let pid = Pid::from_u32(config.owner_pid);
    let started = owner_start(&mut system, pid).ok_or("development session owner is gone")?;
    std::thread::spawn(move || {
        while !app.state::<Backend>().is_stopping() {
            std::thread::sleep(std::time::Duration::from_secs(1));
            if owner_start(&mut system, pid) != Some(started) {
                app.state::<Backend>().stop();
                app.exit(1);
                break;
            }
        }
    });
    Ok(())
}

pub fn origin(port: u16) -> String {
    config()
        .map(|c| c.frontend.clone())
        .unwrap_or_else(|| format!("http://127.0.0.1:{port}/"))
}

pub fn entry(port: u16, token: &str, generation: u64) -> Result<Url, String> {
    if !active() {
        return Url::parse(&crate::backend::bootstrap_url(port)).map_err(|e| e.to_string());
    }
    let mut url = Url::parse(&origin(port)).map_err(|e| e.to_string())?;
    url.set_path("/__kt_desktop_launch");
    url.query_pairs_mut().append_pair("kt_launch", token);
    url.query_pairs_mut()
        .append_pair("kt_dev_generation", &generation.to_string());
    Ok(url)
}

pub fn internal(url: &Url) -> bool {
    config().is_some_and(|c| {
        Url::parse(&c.frontend).is_ok_and(|base| url.origin() == base.origin())
            && url.username().is_empty()
            && url.password().is_none()
    })
}

pub fn command(config: &Config, port: u16, state: &std::path::Path, token: &str) -> Command {
    let mut command = Command::new(&config.python);
    command
        .current_dir(&config.repository)
        .args([
            "-m",
            "kohakuterrarium.serving.desktop.server",
            "--source",
            "--port",
            &port.to_string(),
            "--state-path",
        ])
        .arg(state)
        .args(["--parent-pid", &std::process::id().to_string()])
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env_remove("KT_LAUNCHER_EXEC")
        .env_remove("KT_BOOTSTRAP_DIR")
        .env_remove("KT_BUNDLED_RELEASE_DIR")
        .env("PYTHONUTF8", "1")
        .env("PYTHONNOUSERSITE", "1")
        .env("KT_PYTHON", &config.python)
        .env("KT_CONFIG_DIR", &config.data)
        .env("KT_SECRET_STORE", "file")
        .env("KT_DESKTOP_SHELL", "tauri")
        .env("KT_DESKTOP_LAUNCH_TOKEN", token);
    command
}

pub const READY_SCRIPT: &str = r#"(() => {
  if (window.top !== window) return;
  const generation = Number(new URL(location.href).searchParams.get('kt_dev_generation'));
  if (!generation) return;
  const authenticationProbe = '/api/settings/ui-prefs';
  window.addEventListener('DOMContentLoaded', async () => {
    try {
      const response = await fetch(authenticationProbe, {credentials:'same-origin', cache:'no-store'});
      if (!response.ok) throw new Error('Desktop authentication failed: ' + response.status);
      await window.__TAURI_INTERNALS__.invoke('source_page_ready', {generation});
      const url = new URL(location.href); url.searchParams.delete('kt_dev_generation');
      history.replaceState(history.state, '', url);
    } catch (error) { console.error(error); }
  }, {once:true});
})();"#;

#[tauri::command]
pub fn source_page_ready(window: WebviewWindow, generation: u64) -> bool {
    if !active() || window.label() != "main" {
        return false;
    }
    let Ok(url) = window.url() else {
        return false;
    };
    if !internal(&url) || url.path() != "/" {
        return false;
    }
    let app = window.app_handle();
    let backend = app.state::<Backend>();
    if !backend.confirm_source_generation(generation) {
        return false;
    }
    backend.mark_page_loaded();
    if crate::smoke::output_path().is_none() && backend.claim_popout_restore() {
        let app = app.clone();
        std::thread::spawn(move || crate::popout::restore(&app));
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packaged_entry_bootstraps_initial_and_restarted_windows() {
        assert!(!active());
        for (port, generation) in [(8001, 1), (8001, 2), (8002, 3)] {
            let url = entry(port, "launch-secret", generation).unwrap();
            assert_eq!(url.as_str(), format!("http://127.0.0.1:{port}/healthz"));
            assert!(url.query().is_none());
        }
    }

    fn configuration() -> Config {
        Config {
            python: std::env::current_exe().unwrap(),
            repository: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../..")
                .canonicalize()
                .unwrap(),
            data: std::env::temp_dir().join("kt-development-test"),
            frontend: "http://127.0.0.1:15173/".into(),
            port: 18001,
            owner_pid: std::process::id(),
        }
    }

    #[test]
    fn rejects_nonlocal_ambiguous_and_missing_configuration() {
        let mut config = configuration();
        assert!(config.validate().is_ok());
        for origin in [
            "http://localhost:15173/",
            "https://127.0.0.1:15173/",
            "http://127.0.0.1:18001/",
            "http://user@127.0.0.1:15173/",
            "http://127.0.0.1:15173/path",
            "http://127.0.0.1:15173/?key=value",
        ] {
            config.frontend = origin.into();
            assert!(config.validate().is_err(), "{origin}");
        }
        config = configuration();
        config.python = PathBuf::from("relative-python");
        assert!(config.validate().is_err());
        assert!(serde_json::from_str::<Config>(r#"{"python":"python","extra":true}"#).is_err());
    }

    #[test]
    fn command_uses_editable_module_and_never_a_release_or_pythonpath() {
        let config = configuration();
        let command = command(
            &config,
            config.port,
            &config.data.join("state.json"),
            "test-secret",
        );
        let args: Vec<_> = command
            .get_args()
            .map(|arg| arg.to_string_lossy())
            .collect();
        assert_eq!(
            &args[..3],
            &["-m", "kohakuterrarium.serving.desktop.server", "--source"]
        );
        assert!(!args.iter().any(|arg| arg.contains("test-secret")));
        assert_eq!(command.get_current_dir(), Some(config.repository.as_path()));
        let env: std::collections::HashMap<_, _> = command.get_envs().collect();
        assert_eq!(env.get(std::ffi::OsStr::new("PYTHONPATH")), Some(&None));
        assert_eq!(
            env.get(std::ffi::OsStr::new("KT_SECRET_STORE")),
            Some(&Some(std::ffi::OsStr::new("file")))
        );
    }

    #[test]
    fn session_owner_must_exist_and_keep_the_same_identity() {
        let mut system = System::new().unwrap();
        let pid = Pid::from_u32(std::process::id());
        let identity = owner_start(&mut system, pid);
        assert!(identity.is_some());
        assert_eq!(owner_start(&mut system, pid), identity);
        assert_eq!(owner_start(&mut system, Pid::from_u32(u32::MAX)), None);
        let mut config = configuration();
        config.owner_pid = 0;
        assert!(config.validate().is_err());
    }
}
