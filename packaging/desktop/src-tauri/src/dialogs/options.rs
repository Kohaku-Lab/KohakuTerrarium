//! Dialog options as the page sends them, reduced to values the OS dialog can take.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::commands::Args;

const MAX_TITLE: usize = 120;
const MAX_PATH: usize = 1024;
const MAX_NAME: usize = 200;
const MAX_FILTERS: usize = 8;
const MAX_EXTENSIONS: usize = 16;

#[derive(Debug, Default, PartialEq, Eq)]
pub struct Options {
    pub title: Option<String>,
    pub directory: Option<PathBuf>,
    pub file_name: Option<String>,
    pub filters: Vec<(String, Vec<String>)>,
}

/// The nearest existing directory at or above `path`.
fn start_directory(path: &str) -> Option<PathBuf> {
    if path.is_empty() || path.chars().count() > MAX_PATH {
        return None;
    }
    Path::new(path)
        .ancestors()
        .find(|candidate| candidate.is_dir())
        .map(Path::to_path_buf)
}

/// A bare file name: separators and control characters removed, length capped.
fn bare_name(name: &str) -> Option<String> {
    let cleaned: String = name
        .chars()
        .filter(|c| {
            !matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') && !c.is_control()
        })
        .take(MAX_NAME)
        .collect();
    let cleaned = cleaned.trim().to_string();
    (!cleaned.is_empty()).then_some(cleaned)
}

fn filters_of(value: &Value) -> Vec<(String, Vec<String>)> {
    let mut filters = Vec::new();
    for entry in value.as_array().into_iter().flatten().take(MAX_FILTERS) {
        let name = entry
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let extensions: Vec<String> = entry
            .get("extensions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(|ext| ext.trim_start_matches('.').to_string())
            .filter(|ext| !ext.is_empty() && ext.chars().all(|c| c.is_ascii_alphanumeric()))
            .take(MAX_EXTENSIONS)
            .collect();
        if !name.is_empty() && !extensions.is_empty() {
            filters.push((name.chars().take(MAX_TITLE).collect(), extensions));
        }
    }
    filters
}

impl Options {
    /// Read `{ title, defaultPath, fileName, filters }` from the first command argument.
    pub fn from_args(args: &Args) -> Options {
        let Some(spec) = args.as_ref().and_then(|values| values.first()) else {
            return Options::default();
        };
        let text = |key: &str| spec.get(key).and_then(Value::as_str).map(str::trim);
        Options {
            title: text("title")
                .filter(|title| !title.is_empty())
                .map(|title| title.chars().take(MAX_TITLE).collect()),
            directory: text("defaultPath").and_then(start_directory),
            file_name: text("fileName").and_then(bare_name),
            filters: spec.get("filters").map(filters_of).unwrap_or_default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn args(value: Value) -> Args {
        Some(vec![value])
    }

    #[test]
    fn no_arguments_mean_default_options() {
        assert_eq!(Options::from_args(&None), Options::default());
        assert_eq!(Options::from_args(&Some(vec![])), Options::default());
        assert_eq!(Options::from_args(&args(json!("text"))), Options::default());
    }

    #[test]
    fn the_start_directory_is_the_nearest_existing_ancestor() {
        let base = std::env::temp_dir();
        let missing = base.join("kt-dialog-missing").join("deeper");
        let options = Options::from_args(&args(json!({ "defaultPath": missing })));
        assert_eq!(options.directory.as_deref(), Some(base.as_path()));

        let file = std::env::current_exe().unwrap();
        let options = Options::from_args(&args(json!({ "defaultPath": file })));
        assert_eq!(options.directory.as_deref(), file.parent());

        let none = Options::from_args(&args(json!({ "defaultPath": "" })));
        assert_eq!(none.directory, None);
        let huge = "a".repeat(MAX_PATH + 1);
        assert_eq!(
            Options::from_args(&args(json!({ "defaultPath": huge }))).directory,
            None
        );
    }

    #[test]
    fn titles_and_names_are_trimmed_and_cleaned() {
        let long = "t".repeat(500);
        let options = Options::from_args(&args(json!({
            "title": format!("  {long}  "),
            "fileName": "..\\evil/<export>:*.json",
        })));
        assert_eq!(options.title.unwrap().chars().count(), MAX_TITLE);
        assert_eq!(options.file_name.as_deref(), Some("..evilexport.json"));

        let blank = Options::from_args(&args(json!({ "title": "  ", "fileName": "///" })));
        assert_eq!(blank.title, None);
        assert_eq!(blank.file_name, None);
    }

    #[test]
    fn filters_keep_only_named_alphanumeric_extensions() {
        let options = Options::from_args(&args(json!({ "filters": [
            { "name": "Sessions", "extensions": [".kohakutr", "db", "bad ext", "*", ""] },
            { "name": "", "extensions": ["txt"] },
            { "name": "Empty", "extensions": [] },
            { "name": "No list" },
            "nonsense",
        ] })));
        assert_eq!(
            options.filters,
            vec![(
                "Sessions".to_string(),
                vec!["kohakutr".to_string(), "db".to_string()]
            )]
        );

        let many: Vec<Value> = (0..20)
            .map(|n| json!({ "name": format!("f{n}"), "extensions": ["a"] }))
            .collect();
        let capped = Options::from_args(&args(json!({ "filters": many })));
        assert_eq!(capped.filters.len(), MAX_FILTERS);
    }
}
