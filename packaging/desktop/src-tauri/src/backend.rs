//! Starts the bundled Python backend and reports its progress to the window.
//!
//! The backend is the `kohakuterrarium.launcher.sidecar` module run by the
//! bundled interpreter. It prints JSON progress lines while it installs or
//! updates the release tree, then publishes `{"status": "ready", "port": N}` in a
//! state file once the server listens.

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, Url};

use crate::overlay;
use crate::popout::{self, PopoutRegistry};
use crate::smoke;

/// Must equal `utils/desktop_launch.py`; a Python test compares the two.
pub const LAUNCH_ENV: &str = "KT_DESKTOP_LAUNCH_TOKEN";
pub const QUERY_PARAM: &str = "kt_launch";

pub const REQUESTED_PORT: u16 = 8001;
const READY_TIMEOUT: Duration = Duration::from_secs(1800);
const STALL_TIMEOUT: Duration = Duration::from_secs(180);
const POLL: Duration = Duration::from_millis(100);

const RECOVER_WINDOW: Duration = Duration::from_secs(120);
const RECOVER_LIMIT: usize = 3;

/// Where the backend is in its life, as the page and the tray show it.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    #[default]
    Starting,
    Running,
    Restarting,
    Failed,
}

impl Phase {
    pub fn name(self) -> &'static str {
        match self {
            Phase::Starting => "starting",
            Phase::Running => "running",
            Phase::Restarting => "restarting",
            Phase::Failed => "failed",
        }
    }
}

/// Whether another automatic restart is allowed; records it when it is.
pub fn may_recover(crashes: &mut Vec<Instant>, now: Instant) -> bool {
    crashes.retain(|at| now.duration_since(*at) < RECOVER_WINDOW);
    if crashes.len() >= RECOVER_LIMIT {
        return false;
    }
    crashes.push(now);
    true
}

#[derive(Default)]
pub struct Backend {
    child: Mutex<Option<Child>>,
    state_path: Mutex<Option<PathBuf>>,
    token: Mutex<Option<String>>,
    phase: Mutex<Phase>,
    started_at: Mutex<Option<Instant>>,
    crashes: Mutex<Vec<Instant>>,
    restarts: AtomicU32,
    main_loads: AtomicU32,
    relaunching: AtomicBool,
    port: AtomicU16,
    shell_loaded: AtomicBool,
    page_loaded: AtomicBool,
    ipc_confirmed: AtomicBool,
    overlay_page_loaded: AtomicBool,
    overlay_ipc_confirmed: AtomicBool,
    popout_page_loaded: AtomicBool,
    popout_ipc_confirmed: AtomicBool,
    popouts_restored: AtomicBool,
    stopping: AtomicBool,
}

impl Backend {
    pub fn phase(&self) -> Phase {
        *self.phase.lock().unwrap()
    }

    fn set_phase(&self, phase: Phase) {
        *self.phase.lock().unwrap() = phase;
    }

    pub fn restarts(&self) -> u32 {
        self.restarts.load(Ordering::SeqCst)
    }

    pub fn is_relaunching(&self) -> bool {
        self.relaunching.load(Ordering::SeqCst)
    }

    pub fn is_stopping(&self) -> bool {
        self.stopping.load(Ordering::SeqCst)
    }

    /// Seconds since the current backend process became ready.
    pub fn uptime_secs(&self) -> Option<u64> {
        self.started_at
            .lock()
            .unwrap()
            .map(|at| at.elapsed().as_secs())
    }

    /// The operating-system id of the running backend process.
    pub fn pid(&self) -> Option<u32> {
        self.child.lock().unwrap().as_ref().map(Child::id)
    }

    pub fn token(&self) -> Option<String> {
        self.token.lock().unwrap().clone()
    }

    /// Ask the backend to exit, then force it if it does not; the app keeps running.
    fn stop_child(&self) {
        let Some(mut child) = self.child.lock().unwrap().take() else {
            return;
        };
        if let Some(path) = self.state_path.lock().unwrap().as_ref() {
            let _ = std::fs::write(path, r#"{"status":"shutdown"}"#);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            thread::sleep(POLL);
        }
        let _ = child.kill();
        let _ = child.wait();
    }

    /// Whether the caller is the first to ask to reopen the last run's session windows.
    pub fn claim_popout_restore(&self) -> bool {
        !self.popouts_restored.swap(true, Ordering::SeqCst)
    }

    pub fn mark_popout_page_loaded(&self) {
        self.popout_page_loaded.store(true, Ordering::SeqCst);
    }

    pub fn popout_page_loaded(&self) -> bool {
        self.popout_page_loaded.load(Ordering::SeqCst)
    }

    pub fn confirm_popout_ipc(&self) {
        self.popout_ipc_confirmed.store(true, Ordering::SeqCst);
    }

    pub fn popout_ipc_confirmed(&self) -> bool {
        self.popout_ipc_confirmed.load(Ordering::SeqCst)
    }

    pub fn mark_overlay_page_loaded(&self) {
        self.overlay_page_loaded.store(true, Ordering::SeqCst);
    }

    pub fn overlay_page_loaded(&self) -> bool {
        self.overlay_page_loaded.load(Ordering::SeqCst)
    }

    pub fn confirm_overlay_ipc(&self) {
        self.overlay_ipc_confirmed.store(true, Ordering::SeqCst);
    }

    pub fn overlay_ipc_confirmed(&self) -> bool {
        self.overlay_ipc_confirmed.load(Ordering::SeqCst)
    }

    pub fn confirm_ipc(&self) {
        self.ipc_confirmed.store(true, Ordering::SeqCst);
    }

    pub fn ipc_confirmed(&self) -> bool {
        self.ipc_confirmed.load(Ordering::SeqCst)
    }

    pub fn mark_page_loaded(&self) {
        self.page_loaded.store(true, Ordering::SeqCst);
        self.main_loads.fetch_add(1, Ordering::SeqCst);
    }

    /// How many times the main window has finished loading the app page.
    pub fn main_loads(&self) -> u32 {
        self.main_loads.load(Ordering::SeqCst)
    }

    pub fn page_loaded(&self) -> bool {
        self.page_loaded.load(Ordering::SeqCst)
    }

    pub fn port(&self) -> Option<u16> {
        match self.port.load(Ordering::SeqCst) {
            0 => None,
            port => Some(port),
        }
    }

    pub fn mark_shell_loaded(&self) {
        self.shell_loaded.store(true, Ordering::SeqCst);
    }

    fn wait_for_shell(&self, limit: Duration) {
        let deadline = Instant::now() + limit;
        while !self.shell_loaded.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(POLL);
        }
    }

    /// Ask the backend to exit, then force it if it does not.
    pub fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.stop_child();
    }
}

/// Files the shell ships next to the executable.
#[derive(Debug, PartialEq, Eq)]
pub struct Layout {
    pub python: PathBuf,
    pub bootstrap: PathBuf,
    pub bundled_release: PathBuf,
}

impl Layout {
    pub fn from_root(root: &Path) -> Layout {
        let python = if cfg!(windows) {
            root.join("python").join("python.exe")
        } else {
            root.join("python").join("bin").join("python3")
        };
        Layout {
            python,
            bootstrap: root.join("bootstrap"),
            bundled_release: root.join("bundled-release"),
        }
    }
}

pub fn launch_url(port: u16, token: &str) -> String {
    format!("http://127.0.0.1:{port}/?{QUERY_PARAM}={token}")
}

pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("operating system random source");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn arguments(port: u16, state_path: &Path, parent_pid: u32) -> Vec<String> {
    vec![
        "-m".into(),
        "kohakuterrarium.launcher.sidecar".into(),
        "--port".into(),
        port.to_string(),
        "--log-level".into(),
        "INFO".into(),
        "--state-path".into(),
        state_path.display().to_string(),
        "--parent-pid".into(),
        parent_pid.to_string(),
    ]
}

pub fn environment(layout: &Layout, token: &str) -> Vec<(String, String)> {
    vec![
        ("PYTHONPATH".into(), layout.bootstrap.display().to_string()),
        (
            "KT_BOOTSTRAP_DIR".into(),
            layout.bootstrap.display().to_string(),
        ),
        ("PYTHONNOUSERSITE".into(), "1".into()),
        ("PYTHONUTF8".into(), "1".into()),
        ("KT_PYTHON".into(), layout.python.display().to_string()),
        (
            "KT_BUNDLED_RELEASE_DIR".into(),
            layout.bundled_release.display().to_string(),
        ),
        ("KT_STARTUP_SURFACE".into(), "desktop".into()),
        ("KT_DESKTOP_SHELL".into(), "tauri".into()),
        (LAUNCH_ENV.into(), token.into()),
    ]
}

#[derive(Debug, Deserialize, PartialEq)]
pub struct State {
    pub status: String,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub error: Option<String>,
}

pub fn read_state(path: &Path) -> Option<State> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// A progress line is a JSON object naming a phase or a percentage.
pub fn parse_progress(line: &str) -> Option<Value> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    let object = value.as_object()?;
    (object.contains_key("phase") || object.contains_key("percent")).then_some(value)
}

pub fn config_dir(app: &AppHandle) -> PathBuf {
    match std::env::var_os("KT_CONFIG_DIR") {
        Some(dir) => PathBuf::from(dir),
        None => app
            .path()
            .home_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(".kohakuterrarium"),
    }
}

/// A command that runs the bundled interpreter with the launcher on its path.
pub fn python_command(app: &AppHandle, args: &[&str]) -> Result<Command, String> {
    let layout = Layout::from_root(&resource_root(app)?);
    if !layout.python.is_file() {
        return Err(format!(
            "bundled Python not found at {}",
            layout.python.display()
        ));
    }
    let mut command = Command::new(&layout.python);
    command
        .args(args)
        .envs(environment(&layout, ""))
        .stdin(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    Ok(command)
}

fn resource_root(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(dir) = std::env::var_os("KT_DESKTOP_RESOURCES") {
        return Ok(PathBuf::from(dir));
    }
    app.path()
        .resource_dir()
        .map_err(|error| format!("resource directory unavailable: {error}"))
}

fn spawn(
    app: &AppHandle,
    layout: &Layout,
    state_path: &Path,
    token: &str,
    port: u16,
) -> Result<Child, String> {
    if !layout.python.is_file() {
        return Err(format!(
            "bundled Python not found at {}",
            layout.python.display()
        ));
    }
    let config = config_dir(app);
    std::fs::create_dir_all(&config).map_err(|error| error.to_string())?;
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(config.join("app.log"))
        .map_err(|error| format!("cannot open app.log: {error}"))?;

    let mut command = Command::new(&layout.python);
    command
        .args(arguments(port, state_path, std::process::id()))
        .envs(environment(layout, token))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .spawn()
        .map_err(|error| format!("cannot start the backend: {error}"))
}

fn forward_progress(app: AppHandle, child: &mut Child, activity: Arc<AtomicU64>, clock: Instant) {
    let Some(stdout) = child.stdout.take() else {
        return;
    };
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(progress) = parse_progress(&line) {
                activity.store(clock.elapsed().as_millis() as u64, Ordering::SeqCst);
                let _ = app.emit("kt-progress", progress);
            }
        }
    });
}

fn wait_ready(
    backend: &Backend,
    state_path: &Path,
    activity: &AtomicU64,
    clock: Instant,
) -> Result<u16, String> {
    let mut last_state = None;
    loop {
        if let Some(state) = read_state(state_path) {
            match state.status.as_str() {
                "ready" => return state.port.ok_or_else(|| "backend reported no port".into()),
                "error" => {
                    return Err(state
                        .error
                        .unwrap_or_else(|| "backend failed to start".into()))
                }
                _ => {}
            }
            if last_state.as_ref() != Some(&state.status) {
                activity.store(clock.elapsed().as_millis() as u64, Ordering::SeqCst);
                last_state = Some(state.status);
            }
        }
        if let Some(child) = backend.child.lock().unwrap().as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                return Err(format!("backend exited during startup ({status})"));
            }
        }
        let elapsed = clock.elapsed();
        let idle = elapsed.saturating_sub(Duration::from_millis(activity.load(Ordering::SeqCst)));
        if elapsed > READY_TIMEOUT || idle > STALL_TIMEOUT {
            return Err("backend did not start in time".into());
        }
        thread::sleep(POLL);
    }
}

/// Tell the shell page and every app page which phase the backend is in.
fn announce(app: &AppHandle, phase: Phase, message: &str) {
    let detail = serde_json::json!({ "state": phase.name(), "message": message });
    let _ = app.emit("kt-backend-state", &detail);
    let script = format!(
        "window.dispatchEvent(new CustomEvent('kt:desktop-backend-state', {{ detail: {detail} }}))"
    );
    for window in app.webview_windows().values() {
        let _ = window.eval(&script);
    }
    crate::runtime::refresh_tray_line(app);
}

fn fail(app: &AppHandle, message: &str) {
    if let Some(out) = smoke::output_path() {
        smoke::fail(app, &out, message);
        return;
    }
    app.state::<Backend>().set_phase(Phase::Failed);
    let _ = app.emit("kt-error", message);
    if let Some(window) = app.get_webview_window("main") {
        let encoded = serde_json::to_string(message).unwrap_or_else(|_| "\"error\"".into());
        let _ = window.eval(format!(
            "window.__ktShowError && window.__ktShowError({encoded})"
        ));
    }
    announce(app, Phase::Failed, message);
}

/// Start the backend process on `port` (or the next free one) and wait until it serves.
fn start_child(app: &AppHandle, token: &str, port: u16) -> Result<u16, String> {
    let backend = app.state::<Backend>();
    let layout = Layout::from_root(&resource_root(app)?);
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_nanos());
    let state_path = std::env::temp_dir().join(format!(
        "kt-desktop-{}-{}-{unique}.json",
        std::process::id(),
        &token[..8]
    ));
    let _ = std::fs::remove_file(&state_path);
    *backend.state_path.lock().unwrap() = Some(state_path.clone());

    let clock = Instant::now();
    let activity = Arc::new(AtomicU64::new(0));
    let mut child = spawn(app, &layout, &state_path, token, port)?;
    forward_progress(app.clone(), &mut child, activity.clone(), clock);
    *backend.child.lock().unwrap() = Some(child);

    let ready = wait_ready(&backend, &state_path, &activity, clock)?;
    backend.port.store(ready, Ordering::SeqCst);
    *backend.started_at.lock().unwrap() = Some(Instant::now());
    backend.set_phase(Phase::Running);
    Ok(ready)
}

fn launch(app: &AppHandle) -> Result<(), String> {
    let backend = app.state::<Backend>();
    let token = new_token();
    *backend.token.lock().unwrap() = Some(token.clone());
    let port = start_child(app, &token, REQUESTED_PORT)?;

    let window = app
        .get_webview_window("main")
        .ok_or("the main window is gone")?;
    let url = Url::parse(&launch_url(port, &token)).map_err(|error| error.to_string())?;
    window.navigate(url).map_err(|error| error.to_string())?;
    match smoke::output_path() {
        Some(out) => smoke::finish_when_loaded(app.clone(), port, out),
        None => watch(app.clone()),
    }
    Ok(())
}

/// Send every window to the restarted backend: the main window reloads first because only it
/// carries the launch token; session windows follow once it has the new session cookie.
fn reload_windows(app: &AppHandle, token: &str, port: u16) {
    let backend = app.state::<Backend>();
    let loads_before = backend.main_loads();
    if let (Some(main), Ok(url)) = (
        app.get_webview_window("main"),
        Url::parse(&launch_url(port, token)),
    ) {
        let _ = main.navigate(url);
    }
    let mut sessions = Vec::new();
    let registry = app.state::<PopoutRegistry>();
    for (label, window) in app.webview_windows() {
        if popout::is_popout_label(&label) {
            match registry
                .scope_of(&label)
                .and_then(|scope| popout::popout_url(port, &scope))
            {
                Some(url) => sessions.push((window, url)),
                None => {
                    let _ = window.close();
                }
            }
        } else if overlay::is_overlay_label(&label) {
            let _ = window.close();
        }
    }
    if sessions.is_empty() {
        return;
    }
    let app = app.clone();
    thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(60);
        let backend = app.state::<Backend>();
        while backend.main_loads() == loads_before && Instant::now() < deadline {
            thread::sleep(POLL);
        }
        for (window, url) in sessions {
            let _ = window.navigate(url);
        }
    });
}

/// Stop the backend, run `between` while it is stopped, start it again and reload every window.
fn relaunch(
    app: &AppHandle,
    between: impl FnOnce(&AppHandle) -> Result<(), String>,
) -> Result<(), String> {
    let backend = app.state::<Backend>();
    backend.stop_child();
    between(app)?;
    let token = backend.token().ok_or("the backend was never started")?;
    let port = start_child(app, &token, backend.port().unwrap_or(REQUESTED_PORT))?;
    reload_windows(app, &token, port);
    Ok(())
}

/// Stop the backend and start it again with the same token, then reload every window.
pub fn restart(app: &AppHandle) -> Result<(), String> {
    cycle(app, |_| Ok(()))
}

/// Reinstall the release shipped with the shell while the backend is stopped, then start it again.
pub fn repair(app: &AppHandle) -> Result<(), String> {
    cycle(app, crate::runtime::repair::run)
}

fn cycle(
    app: &AppHandle,
    between: impl FnOnce(&AppHandle) -> Result<(), String>,
) -> Result<(), String> {
    let backend = app.state::<Backend>();
    if backend.relaunching.swap(true, Ordering::SeqCst) {
        return Err("the backend is already restarting".into());
    }
    backend.set_phase(Phase::Restarting);
    announce(app, Phase::Restarting, "");
    let result = relaunch(app, between);
    backend.relaunching.store(false, Ordering::SeqCst);
    match &result {
        Ok(()) => {
            backend.restarts.fetch_add(1, Ordering::SeqCst);
            announce(app, Phase::Running, "");
        }
        Err(message) => fail(app, message),
    }
    result
}

fn watch(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(1));
        let backend = app.state::<Backend>();
        if backend.stopping.load(Ordering::SeqCst) {
            return;
        }
        if backend.is_relaunching() {
            continue;
        }
        let exited = match backend.child.lock().unwrap().as_mut() {
            Some(child) => child.try_wait().ok().flatten(),
            None => continue,
        };
        let Some(status) = exited else { continue };
        let allowed = may_recover(&mut backend.crashes.lock().unwrap(), Instant::now());
        if allowed && restart(&app).is_ok() {
            continue;
        }
        fail(
            &app,
            &format!("The backend stopped unexpectedly ({status}). Restart it from the tray or the settings; details are in app.log."),
        );
        return;
    });
}

/// Start the backend on a background thread once the loading page is visible.
pub fn start(app: AppHandle) {
    thread::spawn(move || {
        app.state::<Backend>()
            .wait_for_shell(Duration::from_secs(5));
        if let Err(message) = launch(&app) {
            fail(&app, &message);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn automatic_restarts_stop_after_three_crashes_in_two_minutes() {
        let start = Instant::now();
        let mut crashes = Vec::new();
        let at = |secs: u64| start + Duration::from_secs(secs);
        assert!(may_recover(&mut crashes, at(0)));
        assert!(may_recover(&mut crashes, at(10)));
        assert!(may_recover(&mut crashes, at(20)));
        assert!(!may_recover(&mut crashes, at(30)));
        assert_eq!(crashes.len(), 3);
        assert!(may_recover(&mut crashes, at(125)));
        assert!(may_recover(&mut crashes, at(135)));
        assert!(!may_recover(&mut crashes, at(139)));
        assert!(may_recover(&mut crashes, at(140)));
    }

    #[test]
    fn phases_have_distinct_names_and_a_new_backend_is_starting() {
        let names: Vec<_> = [
            Phase::Starting,
            Phase::Running,
            Phase::Restarting,
            Phase::Failed,
        ]
        .map(Phase::name)
        .to_vec();
        assert_eq!(names, ["starting", "running", "restarting", "failed"]);
        let backend = Backend::default();
        assert_eq!(backend.phase(), Phase::Starting);
        assert_eq!(backend.restarts(), 0);
        assert!(backend.pid().is_none() && backend.token().is_none());
        assert!(!backend.is_relaunching());
    }

    #[test]
    fn layout_points_at_the_interpreter_for_the_platform() {
        let layout = Layout::from_root(Path::new("/r"));
        let expected = if cfg!(windows) {
            Path::new("/r").join("python").join("python.exe")
        } else {
            Path::new("/r").join("python").join("bin").join("python3")
        };
        assert_eq!(layout.python, expected);
        assert_eq!(layout.bootstrap, Path::new("/r").join("bootstrap"));
        assert_eq!(
            layout.bundled_release,
            Path::new("/r").join("bundled-release")
        );
    }

    #[test]
    fn arguments_name_the_sidecar_module_and_the_state_file() {
        let args = arguments(8001, Path::new("/tmp/state.json"), 42);
        assert_eq!(&args[..2], ["-m", "kohakuterrarium.launcher.sidecar"]);
        let state = args.iter().position(|a| a == "--state-path").unwrap();
        assert_eq!(
            args[state + 1],
            Path::new("/tmp/state.json").display().to_string()
        );
        let parent = args.iter().position(|a| a == "--parent-pid").unwrap();
        assert_eq!(args[parent + 1], "42");
    }

    #[test]
    fn the_token_travels_only_in_the_environment() {
        let layout = Layout::from_root(Path::new("/r"));
        let env = environment(&layout, "secret-token");
        assert!(env
            .iter()
            .any(|(k, v)| k == LAUNCH_ENV && v == "secret-token"));
        let args = arguments(8001, Path::new("/s"), 1).join(" ");
        assert!(!args.contains("secret-token"));
    }

    #[test]
    fn child_processes_get_an_isolated_interpreter_environment() {
        let env = environment(&Layout::from_root(Path::new("/r")), "t");
        let get = |name: &str| env.iter().find(|(k, _)| k == name).map(|(_, v)| v.clone());
        assert_eq!(get("PYTHONNOUSERSITE").as_deref(), Some("1"));
        assert_eq!(get("PYTHONUTF8").as_deref(), Some("1"));
        assert_eq!(
            get("PYTHONPATH").as_deref(),
            Some(
                Path::new("/r")
                    .join("bootstrap")
                    .display()
                    .to_string()
                    .as_str()
            )
        );
    }

    #[test]
    fn tokens_are_long_and_distinct() {
        let (a, b) = (new_token(), new_token());
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn launch_url_carries_the_token_in_the_query() {
        assert_eq!(
            launch_url(8002, "abc"),
            "http://127.0.0.1:8002/?kt_launch=abc"
        );
    }

    #[test]
    fn state_files_parse_ready_and_error_shapes() {
        let dir = std::env::temp_dir().join(format!("kt-state-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ready = dir.join("ready.json");
        std::fs::write(&ready, r#"{"status":"ready","port":8002}"#).unwrap();
        assert_eq!(
            read_state(&ready),
            Some(State {
                status: "ready".into(),
                port: Some(8002),
                error: None
            })
        );
        let broken = dir.join("broken.json");
        std::fs::write(&broken, "{not json").unwrap();
        assert_eq!(read_state(&broken), None);
        assert_eq!(read_state(&dir.join("absent.json")), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn progress_lines_need_a_phase_or_percent() {
        assert!(parse_progress(r#"{"phase":"Downloading","percent":4.5}"#).is_some());
        assert!(parse_progress(r#"{"percent":10}"#).is_some());
        assert!(parse_progress(r#"{"other":1}"#).is_none());
        assert!(parse_progress("plain log line").is_none());
        assert!(parse_progress("[1,2]").is_none());
    }
}
