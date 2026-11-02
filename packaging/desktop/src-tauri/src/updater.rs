//! The shell's own updater: the latest stable release by default, the nightly shell manifest when
//! the nightly channel is chosen.

use semver::Version;
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::{Updater, UpdaterExt};

use crate::settings::ShellSettings;

/// Fixed address of the newest nightly's shell manifest.
pub const NIGHTLY_ENDPOINT: &str =
    "https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/manifests-nightly/shell.json";

/// The version this build was made from, set at build time. Installers carry only the numeric
/// `X.Y.Z`, so two nightlies share an installer version but differ here (`2.2.1-dev.5`).
pub fn build_version() -> Option<Version> {
    option_env!("KT_SHELL_BUILD").and_then(|text| Version::parse(text).ok())
}

/// Whether `remote` is a later build than `own`.
pub fn is_newer_build(own: &Version, remote: &Version) -> bool {
    remote > own
}

/// Whether the nightly shell manifest applies: the channel asks for it and this build knows what
/// it is (a local build has no identity to compare).
pub fn follows_nightly(channel: Option<&str>, own: Option<&Version>) -> bool {
    channel == Some("nightly") && own.is_some()
}

/// The updater for the chosen channel.
pub fn for_app(app: &AppHandle) -> tauri_plugin_updater::Result<Updater> {
    let channel = app.state::<ShellSettings>().get_text("updateChannel");
    let own = build_version();
    if !follows_nightly(channel.as_deref(), own.as_ref()) {
        return app.updater();
    }
    let own = own.expect("checked by follows_nightly");
    let endpoint = NIGHTLY_ENDPOINT
        .parse()
        .map_err(tauri_plugin_updater::Error::UrlParse)?;
    app.updater_builder()
        .endpoints(vec![endpoint])?
        .version_comparator(move |_installed, remote| is_newer_build(&own, &remote.version))
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(text: &str) -> Version {
        Version::parse(text).unwrap()
    }

    #[test]
    fn a_later_nightly_is_newer_and_an_older_or_equal_one_is_not() {
        let own = v("2.2.1-dev.5");
        assert!(is_newer_build(&own, &v("2.2.1-dev.6")));
        assert!(is_newer_build(&own, &v("2.2.1")));
        assert!(is_newer_build(&own, &v("2.3.0-dev.1")));
        assert!(!is_newer_build(&own, &v("2.2.1-dev.5")));
        assert!(!is_newer_build(&own, &v("2.2.1-dev.4")));
        assert!(!is_newer_build(&own, &v("2.2.0")));
    }

    #[test]
    fn the_nightly_manifest_applies_only_on_that_channel_and_to_a_known_build() {
        let own = v("2.2.1-dev.5");
        assert!(follows_nightly(Some("nightly"), Some(&own)));
        assert!(!follows_nightly(Some("stable"), Some(&own)));
        assert!(!follows_nightly(None, Some(&own)));
        assert!(!follows_nightly(Some("nightly"), None));
    }

    #[test]
    fn the_nightly_endpoint_is_a_valid_address() {
        assert!(NIGHTLY_ENDPOINT.parse::<url::Url>().is_ok());
    }
}
