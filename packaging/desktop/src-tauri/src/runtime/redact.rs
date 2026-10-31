//! Removes credentials from text that is about to leave the machine.

const MASK: &str = "<redacted>";
const SENSITIVE_WORDS: &[&str] = &[
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "credential",
    "cookie",
    "kt_launch",
];
const SECRET_PREFIXES: &[&str] = &[
    "sk-",
    "sk_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
    "AKIA",
    "AIza",
];
const MIN_PREFIXED: usize = 20;
const MIN_HEX: usize = 32;
const MIN_MIXED: usize = 40;

fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-'
}

/// Whether a key name says its value is a credential ("max_tokens" counts tokens, not credentials).
fn is_sensitive_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    if lower.ends_with("tokens") {
        return false;
    }
    SENSITIVE_WORDS.iter().any(|word| lower.contains(word))
}

/// Whether a bare word has the shape of a credential on its own.
fn is_secret_shaped(word: &str) -> bool {
    if word.len() >= MIN_PREFIXED && SECRET_PREFIXES.iter().any(|p| word.starts_with(p)) {
        return true;
    }
    if word.len() >= MIN_HEX && word.chars().all(|c| c.is_ascii_hexdigit()) {
        return true;
    }
    word.len() >= MIN_MIXED
        && word.chars().any(|c| c.is_ascii_digit())
        && word.chars().any(|c| c.is_ascii_uppercase())
        && word.chars().any(|c| c.is_ascii_lowercase())
}

/// End of the value that starts at `start` in `chars`: a quoted string, or up to a separator.
fn value_end(chars: &[char], start: usize) -> usize {
    let mut at = start;
    match chars.get(at) {
        Some(&quote) if quote == '"' || quote == '\'' => {
            at += 1;
            while at < chars.len() && chars[at] != quote {
                at += 1;
            }
            (at + 1).min(chars.len())
        }
        _ => {
            while at < chars.len() && !matches!(chars[at], ' ' | '\t' | ',' | '&' | ';' | '}' | ')')
            {
                at += 1;
            }
            at
        }
    }
}

/// Skip spaces from `at`.
fn skip_spaces(chars: &[char], mut at: usize) -> usize {
    while at < chars.len() && (chars[at] == ' ' || chars[at] == '\t') {
        at += 1;
    }
    at
}

/// Replace the value after every sensitive `name = value` or `name: value`.
fn mask_named_values(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::with_capacity(line.len());
    let mut at = 0;
    while at < chars.len() {
        if !is_word_char(chars[at]) {
            out.push(chars[at]);
            at += 1;
            continue;
        }
        let word_start = at;
        while at < chars.len() && is_word_char(chars[at]) {
            at += 1;
        }
        let word: String = chars[word_start..at].iter().collect();
        out.push_str(&word);
        if !is_sensitive_name(&word) {
            continue;
        }
        let mut probe = at;
        if matches!(chars.get(probe), Some('"') | Some('\'')) {
            probe += 1;
        }
        probe = skip_spaces(&chars, probe);
        if !matches!(chars.get(probe), Some(':') | Some('=')) {
            continue;
        }
        let separator_end = skip_spaces(&chars, probe + 1);
        let mut end = value_end(&chars, separator_end);
        let value: String = chars[separator_end..end].iter().collect();
        if matches!(
            value.to_ascii_lowercase().as_str(),
            "bearer" | "basic" | "token"
        ) {
            let next = skip_spaces(&chars, end);
            end = value_end(&chars, next);
        }
        if end == separator_end {
            continue;
        }
        out.extend(chars[at..separator_end].iter());
        out.push_str(MASK);
        at = end;
    }
    out
}

/// Move the finished `word` to `out`, masked when it looks like a credential.
fn flush_word(word: &mut String, out: &mut String) {
    if is_secret_shaped(word) {
        out.push_str(MASK);
    } else {
        out.push_str(word);
    }
    word.clear();
}

/// Replace words that look like credentials wherever they appear.
fn mask_shaped_words(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut word = String::new();
    for c in line.chars() {
        if is_word_char(c) {
            word.push(c);
        } else {
            flush_word(&mut word, &mut out);
            out.push(c);
        }
    }
    flush_word(&mut word, &mut out);
    out
}

/// `text` with credentials replaced by a mask, line by line.
pub fn redact(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for (index, line) in text.split('\n').enumerate() {
        if index > 0 {
            out.push('\n');
        }
        out.push_str(&mask_shaped_words(&mask_named_values(line)));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const ANTHROPIC: &str = "sk-ant-api03-ZmFrZWtleWZha2VrZXlmYWtla2V5";
    const HEX: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    fn clean(text: &str) -> String {
        let out = redact(text);
        assert!(!out.contains(ANTHROPIC), "{out}");
        assert!(!out.contains(HEX), "{out}");
        out
    }

    #[test]
    fn json_values_under_sensitive_names_are_masked_whatever_their_shape() {
        let out = clean(&format!(
            r#"{{"api_key": "{ANTHROPIC}", "password": "hunter 2 foo", "name": "ok"}}"#
        ));
        assert!(out.contains(r#""api_key": <redacted>"#), "{out}");
        assert!(out.contains(r#""password": <redacted>"#), "{out}");
        assert!(!out.contains("hunter"), "{out}");
        assert!(out.contains(r#""name": "ok""#));
    }

    #[test]
    fn environment_and_header_forms_are_masked() {
        let out = clean(&format!(
            "OPENAI_API_KEY={ANTHROPIC}\nAuthorization: Bearer abc.def.ghi\nCookie: kt_session=zzzTOPSECRETzzz\nx-api-key: 'short'"
        ));
        assert!(!out.contains("abc.def.ghi"), "{out}");
        assert!(!out.contains("TOPSECRET"), "{out}");
        assert!(!out.contains("short"), "{out}");
        assert!(out.contains("OPENAI_API_KEY=<redacted>"), "{out}");
        assert!(out.contains("Authorization: <redacted>"), "{out}");
    }

    #[test]
    fn the_launch_token_in_an_address_is_masked() {
        let out = clean(&format!("GET /?kt_launch={HEX}&x=1 HTTP/1.1"));
        assert!(out.contains("kt_launch=<redacted>&x=1"), "{out}");
    }

    #[test]
    fn credential_shaped_words_are_masked_even_without_a_name() {
        let out = clean(&format!(
            "the provider rejected {ANTHROPIC} again, also {HEX}"
        ));
        assert!(
            out.contains("the provider rejected <redacted> again"),
            "{out}"
        );
        let mixed = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5hJ7k";
        assert!(!redact(&format!("v={mixed}")).contains(mixed));
        assert!(!redact(&format!("see ghp_{}", "a1B2".repeat(6))).contains("a1B2a1B2"));
    }

    #[test]
    fn counts_of_tokens_and_ordinary_text_are_left_alone() {
        let text = r#"{"max_tokens": 4096, "prompt_tokens": 12} backend started on port 8001"#;
        assert_eq!(redact(text), text);
        let path =
            r"C:\Users\apoll\Desktop\code\Project\KohakuTerrarium\packaging\desktop\src-tauri";
        assert_eq!(redact(path), path);
        assert_eq!(redact("2.2.1.dev11+f64b44e0"), "2.2.1.dev11+f64b44e0");
    }

    #[test]
    fn several_secrets_on_one_line_and_across_lines_all_go() {
        let out = clean(&format!(
            "token={ANTHROPIC} secret: {HEX}\nline two key = \"{ANTHROPIC}\"\n"
        ));
        assert_eq!(out.matches(MASK).count(), 3, "{out}");
        assert!(out.ends_with('\n'));
        assert_eq!(redact(""), "");
    }

    #[test]
    fn a_sensitive_name_without_a_value_changes_nothing() {
        for text in ["the token expired", "key:", "password =", "press any key"] {
            assert_eq!(redact(text), text);
        }
    }
}
