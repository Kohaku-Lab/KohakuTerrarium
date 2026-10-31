//! What the page reports about sessions needing the user, and how the shell shows it
//! on the tray icon, its tooltip and the taskbar or dock entry.

use serde_json::Value;
#[cfg(windows)]
use tauri::image::Image;
use tauri::window::{ProgressBarState, ProgressBarStatus};
use tauri::{AppHandle, Manager};

use crate::commands::Args;
use crate::tray;

const WAITING_COLOR: [u8; 3] = [0xdc, 0x26, 0x26];
const FAILED_COLOR: [u8; 3] = [0xf5, 0x9e, 0x0b];
const FINISHED_COLOR: [u8; 3] = [0x25, 0x63, 0xeb];
const MAX_COUNT: f64 = 9999.0;

/// Sessions per state, as counted by the page.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    pub waiting: u32,
    pub failed: u32,
    pub running: u32,
    pub finished: u32,
}

/// The taskbar progress state: what needs the user outranks a failure, which outranks plain work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Progress {
    Idle,
    Running,
    Waiting,
    Failed,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Presentation {
    pub dot: Option<[u8; 3]>,
    pub progress: Progress,
    pub tooltip: String,
    pub badge_total: u32,
}

pub fn derive(counts: &Counts) -> Presentation {
    let dot = if counts.waiting > 0 {
        Some(WAITING_COLOR)
    } else if counts.failed > 0 {
        Some(FAILED_COLOR)
    } else if counts.finished > 0 {
        Some(FINISHED_COLOR)
    } else {
        None
    };
    let progress = if counts.waiting > 0 {
        Progress::Waiting
    } else if counts.failed > 0 {
        Progress::Failed
    } else if counts.running > 0 {
        Progress::Running
    } else {
        Progress::Idle
    };
    let parts: Vec<String> = [
        (counts.waiting, "waiting for you"),
        (counts.failed, "failed"),
        (counts.running, "running"),
        (counts.finished, "finished"),
    ]
    .into_iter()
    .filter(|(count, _)| *count > 0)
    .map(|(count, label)| format!("{count} {label}"))
    .collect();
    let tooltip = if parts.is_empty() {
        "KohakuTerrarium".to_string()
    } else {
        format!("KohakuTerrarium - {}", parts.join(", "))
    };
    Presentation {
        dot,
        progress,
        tooltip,
        badge_total: counts.waiting + counts.failed + counts.finished,
    }
}

/// Counts from the page's `{ waiting, failed, running, finished }`; absent keys are zero.
pub fn counts_from(value: &Value) -> Option<Counts> {
    let object = value.as_object()?;
    let count = |key: &str| {
        object
            .get(key)
            .and_then(Value::as_f64)
            .map_or(0, |n| n.clamp(0.0, MAX_COUNT) as u32)
    };
    Some(Counts {
        waiting: count("waiting"),
        failed: count("failed"),
        running: count("running"),
        finished: count("finished"),
    })
}

fn progress_bar(progress: Progress) -> ProgressBarState {
    let (status, value) = match progress {
        Progress::Idle => (ProgressBarStatus::None, None),
        Progress::Running => (ProgressBarStatus::Indeterminate, None),
        Progress::Waiting => (ProgressBarStatus::Paused, Some(100)),
        Progress::Failed => (ProgressBarStatus::Error, Some(100)),
    };
    ProgressBarState {
        status: Some(status),
        progress: value,
    }
}

pub fn apply(app: &AppHandle, counts: &Counts) -> bool {
    let shown = derive(counts);
    let indicated = tray::set_indicator(app, shown.dot, &shown.tooltip);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_progress_bar(progress_bar(shown.progress));
        #[cfg(target_os = "macos")]
        {
            let total = i64::from(shown.badge_total);
            let _ = window.set_badge_count((total > 0).then_some(total));
        }
        #[cfg(windows)]
        {
            let dot = shown.dot.map(|color| {
                let mut rgba = vec![0u8; 16 * 16 * 4];
                tray::draw_badge(&mut rgba, 16, 16, color);
                Image::new_owned(rgba, 16, 16)
            });
            let _ = window.set_overlay_icon(dot);
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        let _ = window;
    }
    indicated
}

/// Show the page's `{ waiting, failed, running, finished }` counts on the tray icon and taskbar entry.
#[tauri::command]
pub fn set_attention_state(app: AppHandle, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .and_then(counts_from)
        .is_some_and(|counts| apply(&app, &counts))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn counts(waiting: u32, failed: u32, running: u32, finished: u32) -> Counts {
        Counts {
            waiting,
            failed,
            running,
            finished,
        }
    }

    #[test]
    fn nothing_to_show_means_no_dot_no_progress_and_a_plain_tooltip() {
        let shown = derive(&Counts::default());
        assert_eq!(shown.dot, None);
        assert_eq!(shown.progress, Progress::Idle);
        assert_eq!(shown.tooltip, "KohakuTerrarium");
        assert_eq!(shown.badge_total, 0);
    }

    #[test]
    fn waiting_outranks_failed_outranks_finished_for_the_dot() {
        assert_eq!(derive(&counts(0, 0, 0, 3)).dot, Some(FINISHED_COLOR));
        assert_eq!(derive(&counts(0, 1, 0, 3)).dot, Some(FAILED_COLOR));
        assert_eq!(derive(&counts(1, 1, 0, 3)).dot, Some(WAITING_COLOR));
        assert_eq!(derive(&counts(2, 0, 0, 0)).dot, Some(WAITING_COLOR));
    }

    #[test]
    fn running_work_alone_shows_progress_but_no_dot() {
        let shown = derive(&counts(0, 0, 2, 0));
        assert_eq!(shown.dot, None);
        assert_eq!(shown.progress, Progress::Running);
        assert_eq!(shown.badge_total, 0);
    }

    #[test]
    fn the_taskbar_state_follows_the_most_urgent_session() {
        assert_eq!(derive(&counts(0, 0, 5, 1)).progress, Progress::Running);
        assert_eq!(derive(&counts(0, 1, 5, 1)).progress, Progress::Failed);
        assert_eq!(derive(&counts(1, 1, 5, 1)).progress, Progress::Waiting);
        assert_eq!(derive(&counts(0, 0, 0, 4)).progress, Progress::Idle);
    }

    #[test]
    fn the_tooltip_lists_each_state_in_urgency_order() {
        let shown = derive(&counts(2, 1, 3, 4));
        assert_eq!(
            shown.tooltip,
            "KohakuTerrarium - 2 waiting for you, 1 failed, 3 running, 4 finished"
        );
        assert_eq!(
            derive(&counts(0, 0, 0, 4)).tooltip,
            "KohakuTerrarium - 4 finished"
        );
        assert_eq!(shown.badge_total, 7);
    }

    #[test]
    fn page_counts_are_clamped_and_missing_keys_are_zero() {
        let parsed = counts_from(&json!({ "waiting": 2, "running": 1e9, "failed": -4 })).unwrap();
        assert_eq!(parsed, counts(2, 0, 9999, 0));
        assert_eq!(counts_from(&json!({})), Some(Counts::default()));
        assert_eq!(counts_from(&json!([1, 2])), None);
        assert_eq!(counts_from(&Value::Null), None);
        let junk = counts_from(&json!({ "waiting": "many", "finished": null })).unwrap();
        assert_eq!(junk, Counts::default());
    }

    #[test]
    fn each_progress_state_maps_to_a_distinct_taskbar_status() {
        let status = |p| progress_bar(p).status.unwrap();
        assert!(matches!(status(Progress::Idle), ProgressBarStatus::None));
        assert!(matches!(
            status(Progress::Running),
            ProgressBarStatus::Indeterminate
        ));
        assert!(matches!(
            status(Progress::Waiting),
            ProgressBarStatus::Paused
        ));
        assert!(matches!(status(Progress::Failed), ProgressBarStatus::Error));
        assert_eq!(progress_bar(Progress::Failed).progress, Some(100));
        assert_eq!(progress_bar(Progress::Running).progress, None);
    }
}
