//! `rules.yaml`: app classification (`work | personal | ask`).
//!
//! Deliberately a tiny, strict YAML *subset* (no crate, ~60 lines), because
//! the data is just three string lists:
//!
//! ```yaml
//! # comment lines and blank lines are ignored
//! work:            # top-level key: work | personal | ask | domains
//!   - Slack*       # list item, indented. Optional "double quotes".
//! personal:
//!   - Spotify*
//! domains:         # reserved for the later per-domain phase: the whole
//!   work:          # indented block is skipped (not validated) in v1
//!     - github.com
//! ```
//!
//! A trailing `*` is a prefix match; otherwise the name must be equal.
//! Matching ignores case. First match wins, in file order. Anything this
//! parser does not understand is an error with its line number.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    Work,
    Personal,
    Ask,
}

impl Class {
    pub fn as_str(self) -> &'static str {
        match self {
            Class::Work => "work",
            Class::Personal => "personal",
            Class::Ask => "ask",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Pattern {
    text: String, // lower-cased, without the trailing `*`
    prefix: bool,
    class: Class,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Rules {
    patterns: Vec<Pattern>,
}

/// Seed written on first run.
pub const DEFAULT_RULES: &str = include_str!("../rules.default.yaml");

#[derive(Clone, Copy, PartialEq)]
enum Section {
    None,
    Class(Class),
    Skipped,
}

fn strip_comment(s: &str) -> &str {
    // `#` starts a comment at the start of a line or after whitespace.
    let b = s.as_bytes();
    for i in 0..b.len() {
        if b[i] == b'#' && (i == 0 || b[i - 1] == b' ' || b[i - 1] == b'\t') {
            return &s[..i];
        }
    }
    s
}

impl Rules {
    pub fn parse(src: &str) -> Result<Rules, String> {
        let mut rules = Rules::default();
        let mut section = Section::None;
        for (idx, raw) in src.lines().enumerate() {
            let n = idx + 1;
            let line = strip_comment(raw).trim_end();
            if line.trim().is_empty() {
                continue;
            }
            let indented = line.starts_with(' ') || line.starts_with('\t');
            if !indented {
                let key = line.strip_suffix(':').ok_or_else(|| {
                    format!("rules line {n}: expected `work:`, `personal:`, `ask:` or `domains:`")
                })?;
                section = match key {
                    "work" => Section::Class(Class::Work),
                    "personal" => Section::Class(Class::Personal),
                    "ask" => Section::Class(Class::Ask),
                    "domains" => Section::Skipped,
                    other => return Err(format!("rules line {n}: unknown key `{other}`")),
                };
                continue;
            }
            match section {
                Section::Skipped => {}
                Section::None => return Err(format!("rules line {n}: list item before any key")),
                Section::Class(class) => {
                    let item = line
                        .trim()
                        .strip_prefix("- ")
                        .ok_or_else(|| format!("rules line {n}: expected `- <pattern>`"))?
                        .trim();
                    let item = match item.strip_prefix('"') {
                        Some(r) => r
                            .strip_suffix('"')
                            .ok_or_else(|| format!("rules line {n}: unterminated quote"))?,
                        None => item,
                    };
                    let (text, prefix) = match item.strip_suffix('*') {
                        Some(t) => (t, true),
                        None => (item, false),
                    };
                    if text.is_empty() {
                        return Err(format!("rules line {n}: empty pattern"));
                    }
                    if text.contains('*') {
                        return Err(format!("rules line {n}: `*` is only allowed at the end"));
                    }
                    rules.patterns.push(Pattern {
                        text: text.to_lowercase(),
                        prefix,
                        class,
                    });
                }
            }
        }
        Ok(rules)
    }

    pub fn classify(&self, proc_name: &str) -> Class {
        let name = proc_name.to_lowercase();
        for p in &self.patterns {
            let hit = if p.prefix {
                name.starts_with(&p.text)
            } else {
                name == p.text
            };
            if hit {
                return p.class;
            }
        }
        Class::Ask
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_and_prefix_and_case_insensitive() {
        let r = Rules::parse("work:\n  - Slack*\n  - git\npersonal:\n  - Spotify*\n").unwrap();
        assert_eq!(r.classify("Slack Helper"), Class::Work);
        assert_eq!(r.classify("slack"), Class::Work);
        assert_eq!(r.classify("git"), Class::Work);
        assert_eq!(r.classify("gitstatusd"), Class::Ask); // exact only
        assert_eq!(r.classify("Spotify Helper"), Class::Personal);
    }

    #[test]
    fn first_match_wins_in_file_order() {
        let r = Rules::parse("personal:\n  - Music*\nwork:\n  - M*\n").unwrap();
        assert_eq!(r.classify("Music"), Class::Personal);
        assert_eq!(r.classify("Mail"), Class::Work);
    }

    #[test]
    fn unmatched_is_ask() {
        let r = Rules::parse("work:\n  - a\n").unwrap();
        assert_eq!(r.classify("zzz"), Class::Ask);
        assert_eq!(Rules::default().classify("anything"), Class::Ask);
    }

    #[test]
    fn quotes_comments_blank_lines() {
        let r = Rules::parse("# top\n\nwork:   # c\n  - \"Google Chrome*\"  # browser\n").unwrap();
        assert_eq!(r.classify("Google Chrome H."), Class::Work);
    }

    #[test]
    fn domains_block_is_skipped_unvalidated() {
        let r = Rules::parse("work:\n  - a\ndomains:\n  work:\n    - github.com\n  ???\n").unwrap();
        assert_eq!(r.classify("a"), Class::Work);
    }

    #[test]
    fn invalid_files_error_with_line_numbers() {
        let cases = [
            ("work:\n  Slack\n", "line 2"),
            ("bogus:\n  - x\n", "line 1"),
            ("  - x\n", "line 1"),
            ("work\n", "line 1"),
            ("work:\n  - \"x\n", "line 2"),
            ("work:\n  - *\n", "line 2"),
            ("work:\n  - a*b\n", "line 2"),
            ("work:\n  - a\n  - \n", "line 3"),
        ];
        for (src, want) in cases {
            let e = Rules::parse(src).unwrap_err();
            assert!(e.contains(want), "{src:?} -> {e}");
        }
    }

    #[test]
    fn default_seed_parses_and_classifies_as_decided() {
        let r = Rules::parse(DEFAULT_RULES).unwrap();
        for w in [
            "Google Chrome H.",
            "Slack Helper",
            "zoom.us",
            "Microsoft Edge ",
            "2.1.327",
            "ssh",
            "git",
            "claude",
            "com.docker.back.",
        ] {
            assert_eq!(r.classify(w), Class::Work, "{w}");
        }
        for p in ["Spotify", "Steam Helper", "Music"] {
            assert_eq!(r.classify(p), Class::Personal, "{p}");
        }
        assert_eq!(r.classify("syncthing"), Class::Ask);
        assert_eq!(r.classify("mDNSResponder"), Class::Ask);
    }
}
