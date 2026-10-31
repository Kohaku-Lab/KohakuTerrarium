//! Keeps the computer from sleeping while agents work.

use std::sync::Mutex;

use keepawake::{Builder, KeepAwake};
use tauri::State;

use crate::commands::Args;

/// The sleep inhibitor, held while the page says work is running.
#[derive(Default)]
pub struct KeepAwakeState(Mutex<Option<KeepAwake>>);

impl KeepAwakeState {
    #[cfg(test)]
    fn is_held(&self) -> bool {
        self.0.lock().unwrap().is_some()
    }

    /// Hold or release the inhibitor; whether it now matches `on`.
    pub fn set(&self, on: bool) -> bool {
        let mut held = self.0.lock().unwrap();
        match (on, held.is_some()) {
            (true, false) => {
                *held = Builder::default()
                    .idle(true)
                    .sleep(true)
                    .reason("An agent is working")
                    .app_name("KohakuTerrarium")
                    .app_reverse_domain("com.kohaku.terrarium")
                    .create()
                    .ok();
            }
            (false, true) => *held = None,
            _ => {}
        }
        held.is_some() == on
    }
}

/// Hold (`[true]`) or release (`[false]`) the sleep inhibitor; `false` if it could not be set.
#[tauri::command]
pub fn set_keep_awake(state: State<KeepAwakeState>, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .and_then(serde_json::Value::as_bool)
        .is_some_and(|on| state.set(on))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_inhibitor_is_held_and_released_on_request() {
        let state = KeepAwakeState::default();
        assert!(!state.is_held());
        // A headless macOS runner may refuse the assertion; Windows must not.
        let acquired = state.set(true);
        assert!(acquired || !cfg!(windows));
        assert_eq!(state.is_held(), acquired);
        if acquired {
            assert!(state.set(true));
        }
        assert!(state.set(false));
        assert!(!state.is_held());
        assert!(state.set(false));
    }
}
