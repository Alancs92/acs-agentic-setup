//! Minimal JSON string escaping (RFC 8259) — netmon only ever *writes* JSON.

use std::fmt::Write;

/// Append `s` as a quoted JSON string. Escapes `"` `\` and all control
/// characters (< 0x20); everything else, including non-ASCII, is emitted as
/// valid UTF-8 (process names such as `Café` stay readable).
pub fn push_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || c == '\u{7f}' => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

pub fn quoted(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    push_str(&mut o, s);
    o
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_and_empty() {
        assert_eq!(quoted("Slack"), "\"Slack\"");
        assert_eq!(quoted(""), "\"\"");
    }

    #[test]
    fn quotes_and_backslashes() {
        assert_eq!(quoted(r#"a"b\c"#), r#""a\"b\\c""#);
    }

    #[test]
    fn control_characters() {
        assert_eq!(quoted("a\nb\tc\r"), r#""a\nb\tc\r""#);
        assert_eq!(
            quoted("\u{01}\u{1f}\u{08}\u{0c}\u{7f}"),
            r#""\u0001\u001f\b\f\u007f""#
        );
    }

    #[test]
    fn non_ascii_is_passed_through() {
        assert_eq!(quoted("Café 日本 🚀"), "\"Café 日本 🚀\"");
    }

    #[test]
    fn output_round_trips_through_a_strict_reader() {
        // Cheap structural check without a JSON crate: no raw control chars,
        // and every quote inside is escaped.
        let q = quoted("x\"\\\n\u{2}y");
        let inner = &q[1..q.len() - 1];
        assert!(!inner.chars().any(|c| (c as u32) < 0x20));
        let mut prev_backslash = false;
        for c in inner.chars() {
            if c == '"' {
                assert!(prev_backslash);
            }
            prev_backslash = c == '\\' && !prev_backslash;
        }
    }
}
