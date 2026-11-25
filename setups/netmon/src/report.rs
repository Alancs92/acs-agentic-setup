//! `netmon report`: one day's usage as versioned JSON plus a 3-line summary.

use std::path::Path;

use crate::json;
use crate::paths;
use crate::rules::{Class, Rules};
use crate::store::Store;

pub const REPORT_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Bytes {
    pub inn: u64,
    pub out: u64,
}

impl Bytes {
    pub fn total(&self) -> u64 {
        self.inn.saturating_add(self.out)
    }
    fn add(&mut self, o: Bytes) {
        self.inn = self.inn.saturating_add(o.inn);
        self.out = self.out.saturating_add(o.out);
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppUse {
    pub name: String,
    pub class: Class,
    pub bytes: Bytes,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub date: String,
    pub samples: u64,
    pub seconds: u64,
    /// Authoritative device total (physical interfaces).
    pub total: Bytes,
    pub home: Bytes,
    pub elsewhere: Bytes,
    pub vpn: Bytes,
    pub unattributed: Bytes,
    /// work, personal, ask (sums of per-app bytes).
    pub classes: [Bytes; 3],
    /// Sorted by total bytes, largest first.
    pub apps: Vec<AppUse>,
}

fn class_idx(c: Class) -> usize {
    match c {
        Class::Work => 0,
        Class::Personal => 1,
        Class::Ask => 2,
    }
}
const CLASSES: [Class; 3] = [Class::Work, Class::Personal, Class::Ask];

/// `YYYY-MM-DD` with plausible month/day.
pub fn valid_date(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return false;
    }
    let num = |r: std::ops::Range<usize>| s[r].parse::<u32>().ok();
    if !s.is_char_boundary(4)
        || !b
            .iter()
            .enumerate()
            .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
    {
        return false;
    }
    matches!((num(5..7), num(8..10)), (Some(m), Some(d)) if (1..=12).contains(&m) && (1..=31).contains(&d))
}

fn u(v: i64) -> u64 {
    v.max(0) as u64
}

pub fn build(store: &Store, rules: &Rules, date: &str) -> rusqlite::Result<Report> {
    let mut r = Report {
        date: date.to_string(),
        samples: 0,
        seconds: 0,
        total: Bytes::default(),
        home: Bytes::default(),
        elsewhere: Bytes::default(),
        vpn: Bytes::default(),
        unattributed: Bytes::default(),
        classes: [Bytes::default(); 3],
        apps: Vec::new(),
    };
    let c = store.conn();
    let mut q = c.prepare(
        "SELECT home, bytes_in, bytes_out, vpn_in, vpn_out, unattr_in, unattr_out, samples, seconds
           FROM v_daily_network WHERE day=?1",
    )?;
    let mut rows = q.query([date])?;
    while let Some(row) = rows.next()? {
        let b = Bytes {
            inn: u(row.get(1)?),
            out: u(row.get(2)?),
        };
        r.total.add(b);
        if row.get::<_, i64>(0)? == 1 {
            r.home.add(b);
        } else {
            r.elsewhere.add(b);
        }
        r.vpn.add(Bytes {
            inn: u(row.get(3)?),
            out: u(row.get(4)?),
        });
        r.unattributed.add(Bytes {
            inn: u(row.get(5)?),
            out: u(row.get(6)?),
        });
        r.samples += u(row.get(7)?);
        r.seconds += u(row.get(8)?);
    }
    let mut q = c.prepare(
        "SELECT app, SUM(bytes_in), SUM(bytes_out) FROM v_daily_app WHERE day=?1
          GROUP BY app ORDER BY SUM(bytes_in)+SUM(bytes_out) DESC, app",
    )?;
    let mut rows = q.query([date])?;
    while let Some(row) = rows.next()? {
        let name: String = row.get(0)?;
        let class = rules.classify(&name);
        let bytes = Bytes {
            inn: u(row.get(1)?),
            out: u(row.get(2)?),
        };
        r.classes[class_idx(class)].add(bytes);
        r.apps.push(AppUse { name, class, bytes });
    }
    Ok(r)
}

fn gb(b: u64) -> f64 {
    b as f64 / 1e9
}

fn push_bytes(o: &mut String, b: Bytes) {
    o.push_str(&format!(
        "{{\"bytes_in\":{},\"bytes_out\":{},\"bytes\":{},\"gb\":{:.3}}}",
        b.inn,
        b.out,
        b.total(),
        gb(b.total())
    ));
}

/// Versioned JSON (`schema_version`), bytes are exact, `gb` is decimal (1e9).
pub fn to_json(r: &Report) -> String {
    let mut o = String::with_capacity(512 + r.apps.len() * 96);
    o.push_str(&format!(
        "{{\"schema_version\":{REPORT_SCHEMA_VERSION},\"date\":"
    ));
    json::push_str(&mut o, &r.date);
    o.push_str(&format!(
        ",\"samples\":{},\"seconds_covered\":{},\"total\":",
        r.samples, r.seconds
    ));
    push_bytes(&mut o, r.total);
    o.push_str(",\"network\":{\"home\":");
    push_bytes(&mut o, r.home);
    o.push_str(",\"elsewhere\":");
    push_bytes(&mut o, r.elsewhere);
    o.push_str("},\"vpn_hint\":");
    push_bytes(&mut o, r.vpn);
    o.push_str(",\"unattributed\":");
    push_bytes(&mut o, r.unattributed);
    o.push_str(",\"classes\":{");
    for (n, c) in CLASSES.iter().enumerate() {
        if n > 0 {
            o.push(',');
        }
        o.push_str(&format!("\"{}\":", c.as_str()));
        push_bytes(&mut o, r.classes[n]);
    }
    o.push_str("},\"apps\":[");
    for (n, a) in r.apps.iter().enumerate() {
        if n > 0 {
            o.push(',');
        }
        o.push_str("{\"app\":");
        json::push_str(&mut o, &a.name);
        o.push_str(&format!(
            ",\"class\":\"{}\",\"bytes_in\":{},\"bytes_out\":{},\"bytes\":{}}}",
            a.class.as_str(),
            a.bytes.inn,
            a.bytes.out,
            a.bytes.total()
        ));
    }
    o.push_str("]}\n");
    o
}

/// Exactly three lines: total, home vs elsewhere, top apps by class.
/// (Unattributed bytes are called out on line 1 and in the JSON.)
pub fn summary(r: &Report) -> String {
    let l1 = format!(
        "{}: {:.2} GB total (in {:.2} / out {:.2}), {:.2} GB unattributed, {} samples",
        r.date,
        gb(r.total.total()),
        gb(r.total.inn),
        gb(r.total.out),
        gb(r.unattributed.total()),
        r.samples
    );
    let l2 = format!(
        "home {:.2} GB | elsewhere {:.2} GB | vpn hint {:.2} GB",
        gb(r.home.total()),
        gb(r.elsewhere.total()),
        gb(r.vpn.total())
    );
    let mut parts = Vec::with_capacity(3);
    for (n, c) in CLASSES.iter().enumerate() {
        let top: Vec<String> = r
            .apps
            .iter()
            .filter(|a| a.class == *c)
            .take(3)
            .map(|a| format!("{} {:.2}", a.name.trim(), gb(a.bytes.total())))
            .collect();
        let mut s = format!("{} {:.2} GB", c.as_str(), gb(r.classes[n].total()));
        if !top.is_empty() {
            s.push_str(&format!(" ({})", top.join(", ")));
        }
        parts.push(s);
    }
    format!("{l1}\n{l2}\n{}", parts.join(" | "))
}

/// Write `dir/reports/<date>.json` (dir 0700, file 0600).
pub fn write_report_file(data_dir: &Path, r: &Report) -> std::io::Result<std::path::PathBuf> {
    let dir = data_dir.join("reports");
    paths::ensure_dir(&dir)?;
    let p = dir.join(format!("{}.json", r.date));
    paths::write_private(&p, to_json(r).as_bytes())?;
    Ok(p)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{put_sample, TempDir};

    const T0: i64 = 1_700_000_000;

    fn setup() -> (TempDir, Store, String) {
        let t = TempDir::new("report");
        let mut s = Store::open(&t.path().join("netmon.db")).unwrap();
        // home sample: apps 600+300, iface 1000 in, 50 unattributed
        put_sample(
            &mut s,
            T0,
            true,
            &[("Slack Helper", 600, 100), ("Spotify", 300, 0)],
            1000,
            50,
        );
        // elsewhere sample: browser 200, unknown 100
        put_sample(
            &mut s,
            T0 + 120,
            false,
            &[("Google Chrome H.", 200, 20), ("zzz", 100, 10)],
            330,
            30,
        );
        let day = s
            .conn()
            .query_row("SELECT date(?1,'unixepoch','localtime')", [T0], |r| {
                r.get::<_, String>(0)
            })
            .unwrap();
        (t, s, day)
    }

    fn rules() -> Rules {
        Rules::parse("work:\n  - Slack*\n  - Google Chrome*\npersonal:\n  - Spotify*\n").unwrap()
    }

    #[test]
    fn totals_home_split_classes_and_unattributed() {
        let (_t, s, day) = setup();
        let r = build(&s, &rules(), &day).unwrap();
        assert_eq!(r.total.inn, 1330);
        assert_eq!(r.home.inn, 1000);
        assert_eq!(r.elsewhere.inn, 330);
        assert_eq!(r.unattributed.inn, 80);
        assert_eq!(r.vpn.inn, 2); // 1 per fixture sample
        assert_eq!(r.samples, 2);
        assert_eq!(r.seconds, 240);
        assert_eq!(r.classes[0], Bytes { inn: 800, out: 120 }); // work
        assert_eq!(r.classes[1], Bytes { inn: 300, out: 0 }); // personal
        assert_eq!(r.classes[2], Bytes { inn: 100, out: 10 }); // ask
        assert_eq!(r.apps[0].name, "Slack Helper"); // largest first
        assert_eq!(r.apps[0].class, Class::Work);
    }

    #[test]
    fn rolled_up_day_gives_the_same_totals_as_raw() {
        let (_t, mut s, day) = setup();
        let raw = build(&s, &rules(), &day).unwrap();
        s.rollup_before("2999-01-01").unwrap();
        assert_eq!(build(&s, &rules(), &day).unwrap(), raw);
    }

    #[test]
    fn empty_day_is_a_valid_zero_report() {
        let (_t, s, _) = setup();
        let r = build(&s, &rules(), "1999-01-01").unwrap();
        assert_eq!(r.total, Bytes::default());
        assert!(r.apps.is_empty());
        let j = to_json(&r);
        assert!(j.starts_with("{\"schema_version\":1,"));
        assert!(j.contains("\"apps\":[]"));
        assert_eq!(summary(&r).lines().count(), 3);
    }

    #[test]
    fn json_has_schema_version_and_expected_fields() {
        let (_t, s, day) = setup();
        let j = to_json(&build(&s, &rules(), &day).unwrap());
        assert!(j.starts_with("{\"schema_version\":1,\"date\":"));
        for k in [
            "\"total\":",
            "\"network\":{\"home\":",
            "\"elsewhere\":",
            "\"vpn_hint\":",
            "\"unattributed\":",
            "\"classes\":{\"work\":",
            "\"personal\":",
            "\"ask\":",
            "\"apps\":[{\"app\":\"Slack Helper\"",
        ] {
            assert!(j.contains(k), "missing {k} in {j}");
        }
        assert!(j.ends_with("]}\n"));
    }

    #[test]
    fn json_escapes_hostile_app_names() {
        let (_t, mut s, _) = setup();
        put_sample(
            &mut s,
            T0 + 240,
            true,
            &[("we\"ird\\na\nme\u{1}é", 5, 5)],
            10,
            0,
        );
        let day = s
            .conn()
            .query_row("SELECT date(?1,'unixepoch','localtime')", [T0], |r| {
                r.get::<_, String>(0)
            })
            .unwrap();
        let j = to_json(&build(&s, &rules(), &day).unwrap());
        assert!(j.contains(r#""app":"we\"ird\\na\nme\u0001é""#), "{j}");
        assert!(!j.trim_end().contains('\n'));
    }

    #[test]
    fn summary_is_three_lines() {
        let (_t, s, day) = setup();
        let sm = summary(&build(&s, &rules(), &day).unwrap());
        let lines: Vec<_> = sm.lines().collect();
        assert_eq!(lines.len(), 3, "{sm}");
        assert!(lines[0].contains("GB total") && lines[0].contains("unattributed"));
        assert!(lines[1].starts_with("home ") && lines[1].contains("elsewhere"));
        assert!(
            lines[2].starts_with("work ")
                && lines[2].contains("personal")
                && lines[2].contains("Slack Helper")
        );
    }

    #[test]
    fn date_validation() {
        for ok in ["2026-11-25", "1999-01-01"] {
            assert!(valid_date(ok));
        }
        for bad in [
            "",
            "2026-13-01",
            "2026-11-32",
            "2026-1-01",
            "20261125",
            "2026/11/25",
            "2026-11-2a",
            "../../etc/pa",
            "2026-00-10",
        ] {
            assert!(!valid_date(bad), "{bad}");
        }
    }

    #[test]
    fn report_file_is_written_0600_in_0700_dir() {
        use std::os::unix::fs::PermissionsExt;
        let (t, s, day) = setup();
        let r = build(&s, &rules(), &day).unwrap();
        let p = write_report_file(t.path(), &r).unwrap();
        assert_eq!(
            std::fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            std::fs::metadata(p.parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert!(std::fs::read_to_string(&p)
            .unwrap()
            .contains("schema_version"));
    }
}
