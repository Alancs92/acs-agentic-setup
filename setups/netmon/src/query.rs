//! `netmon query`: on-demand, streamed aggregation over the SQL views.
//!
//! `netmon query --from YYYY-MM-DD --to YYYY-MM-DD --group-by day|app|class|network[,..]
//!               [--format json|csv]`
//!
//! Rows are written to the output as SQLite produces them — nothing is
//! collected. Dimensions: `day`, `app` (nettop process name), `class`
//! (work|personal|ask from rules.yaml), `network` (home|elsewhere).
//! Values are bytes. `network`/`day`-only groupings are read from
//! `v_daily_network` (interface totals, authoritative, include unattributed);
//! groupings with `app` or `class` sum per-app deltas (exclude unattributed).

use std::io::Write;

use crate::json;
use crate::report::valid_date;
use crate::store::{csv_field, Store};

pub const QUERY_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dim {
    Day,
    App,
    Class,
    Network,
}

impl Dim {
    fn name(self) -> &'static str {
        match self {
            Dim::Day => "day",
            Dim::App => "app",
            Dim::Class => "class",
            Dim::Network => "network",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Json,
    Csv,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Args {
    pub from: String,
    pub to: String,
    pub group: Vec<Dim>,
    pub format: Format,
}

impl Args {
    pub fn needs_classes(&self) -> bool {
        self.group.contains(&Dim::Class)
    }
}

pub fn parse_args(args: &[String]) -> Result<Args, String> {
    let (mut from, mut to, mut group, mut format) = (None, None, None, Format::Json);
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let val = |it: &mut std::slice::Iter<String>| {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{flag} needs a value"))
        };
        match flag.as_str() {
            "--from" => from = Some(val(&mut it)?),
            "--to" => to = Some(val(&mut it)?),
            "--group-by" => group = Some(val(&mut it)?),
            "--format" => {
                format = match val(&mut it)?.as_str() {
                    "json" => Format::Json,
                    "csv" => Format::Csv,
                    o => return Err(format!("unknown --format `{o}` (json|csv)")),
                }
            }
            o => return Err(format!("unknown argument `{o}`")),
        }
    }
    let from = from.ok_or("--from YYYY-MM-DD is required")?;
    let to = to.ok_or("--to YYYY-MM-DD is required")?;
    for (n, d) in [("--from", &from), ("--to", &to)] {
        if !valid_date(d) {
            return Err(format!("{n} `{d}` is not a YYYY-MM-DD date"));
        }
    }
    if from > to {
        return Err("--from is after --to".into());
    }
    let group = group.ok_or("--group-by day|app|class|network[,..] is required")?;
    let mut dims = Vec::new();
    for g in group.split(',') {
        let d = match g {
            "day" => Dim::Day,
            "app" => Dim::App,
            "class" => Dim::Class,
            "network" => Dim::Network,
            o => return Err(format!("unknown group `{o}` (day|app|class|network)")),
        };
        if dims.contains(&d) {
            return Err(format!("group `{g}` given twice"));
        }
        dims.push(d);
    }
    Ok(Args {
        from,
        to,
        group: dims,
        format,
    })
}

fn sql(args: &Args) -> String {
    let has = |d| args.group.contains(&d);
    let src = if has(Dim::App) {
        "(SELECT v.day AS day, v.app AS app, COALESCE(c.class,'ask') AS class, v.home AS home,
                 v.bytes_in AS bytes_in, v.bytes_out AS bytes_out
            FROM v_daily_app v LEFT JOIN app_class c ON c.proc = v.app
           WHERE v.day BETWEEN ?1 AND ?2)"
    } else if has(Dim::Class) {
        "(SELECT day, class, home, bytes_in, bytes_out FROM v_daily_class WHERE day BETWEEN ?1 AND ?2)"
    } else {
        "(SELECT day, home, bytes_in, bytes_out FROM v_daily_network WHERE day BETWEEN ?1 AND ?2)"
    };
    let keys: Vec<&str> = args
        .group
        .iter()
        .map(|d| match d {
            Dim::Network => "(CASE home WHEN 1 THEN 'home' ELSE 'elsewhere' END)",
            Dim::Day => "day",
            Dim::App => "app",
            Dim::Class => "class",
        })
        .collect();
    let k = keys.join(", ");
    format!("SELECT {k}, SUM(bytes_in), SUM(bytes_out) FROM {src} GROUP BY {k} ORDER BY {k}")
}

/// Stream the result to `out`. JSON is one object:
/// `{"schema_version":1,"from":..,"to":..,"group_by":[..],"rows":[{..},..]}`.
pub fn run<W: Write>(store: &Store, args: &Args, out: &mut W) -> Result<(), String> {
    let e = |e: std::io::Error| e.to_string();
    let n = args.group.len();
    let gaps = store
        .gaps(&args.from, &args.to)
        .map_err(|e| e.to_string())?;
    let mut q = store
        .conn()
        .prepare(&sql(args))
        .map_err(|e| e.to_string())?;
    let mut rows = q.query([&args.from, &args.to]).map_err(|e| e.to_string())?;
    match args.format {
        Format::Json => {
            let mut head = format!("{{\"schema_version\":{QUERY_SCHEMA_VERSION},\"from\":");
            json::push_str(&mut head, &args.from);
            head.push_str(",\"to\":");
            json::push_str(&mut head, &args.to);
            head.push_str(",\"group_by\":[");
            for (i, d) in args.group.iter().enumerate() {
                if i > 0 {
                    head.push(',');
                }
                json::push_str(&mut head, d.name());
            }
            head.push_str("],\"gaps\":");
            gaps.push_json(&mut head);
            head.push_str(",\"rows\":[");
            out.write_all(head.as_bytes()).map_err(e)?;
        }
        Format::Csv => {
            let cols: Vec<&str> = args.group.iter().map(|d| d.name()).collect();
            writeln!(out, "{},bytes_in,bytes_out,bytes", cols.join(",")).map_err(e)?;
        }
    }
    let mut line = String::with_capacity(160);
    let mut first = true;
    while let Some(r) = rows.next().map_err(|e| e.to_string())? {
        line.clear();
        let bi: i64 = r.get(n).map_err(|e| e.to_string())?;
        let bo: i64 = r.get(n + 1).map_err(|e| e.to_string())?;
        match args.format {
            Format::Json => {
                if !first {
                    line.push(',');
                }
                line.push('{');
                for (i, d) in args.group.iter().enumerate() {
                    let v: String = r.get(i).map_err(|e| e.to_string())?;
                    json::push_str(&mut line, d.name());
                    line.push(':');
                    json::push_str(&mut line, &v);
                    line.push(',');
                }
                line.push_str(&format!(
                    "\"bytes_in\":{bi},\"bytes_out\":{bo},\"bytes\":{}}}",
                    bi.saturating_add(bo)
                ));
            }
            Format::Csv => {
                for i in 0..n {
                    let v: String = r.get(i).map_err(|e| e.to_string())?;
                    line.push_str(&csv_field(&v));
                    line.push(',');
                }
                line.push_str(&format!("{bi},{bo},{}\n", bi.saturating_add(bo)));
            }
        }
        first = false;
        out.write_all(line.as_bytes()).map_err(e)?;
    }
    if args.format == Format::Json {
        out.write_all(b"]}\n").map_err(e)?;
    } else if gaps.failed_samples > 0 {
        // CSV has no room for metadata; keep stdout clean and say it on stderr.
        eprintln!(
            "netmon: {} failed samples in range ({})",
            gaps.failed_samples,
            gaps.kinds_text()
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::Rules;
    use crate::testutil::{put_sample, TempDir};

    const T0: i64 = 1_700_000_000;
    const DAY: i64 = 86_400;

    fn day_of(s: &Store, ts: i64) -> String {
        s.conn()
            .query_row("SELECT date(?1,'unixepoch','localtime')", [ts], |r| {
                r.get(0)
            })
            .unwrap()
    }

    fn fixture() -> (TempDir, Store) {
        let t = TempDir::new("query");
        let mut s = Store::open(&t.path().join("netmon.db")).unwrap();
        put_sample(
            &mut s,
            T0,
            true,
            &[("Slack", 100, 10), ("Spotify", 40, 0)],
            200,
            50,
        );
        put_sample(&mut s, T0 + 60, false, &[("Slack", 10, 1)], 20, 9);
        put_sample(
            &mut s,
            T0 + DAY,
            true,
            &[("Slack", 7, 0), ("zzz", 3, 3)],
            20,
            10,
        );
        s.sync_classes(&Rules::parse("work:\n  - Slack*\npersonal:\n  - Spotify*\n").unwrap())
            .unwrap();
        (t, s)
    }

    fn args(s: &Store, group: &[Dim], fmt: Format) -> Args {
        Args {
            from: day_of(s, T0),
            to: day_of(s, T0 + DAY),
            group: group.to_vec(),
            format: fmt,
        }
    }

    fn run_str(s: &Store, a: &Args) -> String {
        let mut out = Vec::new();
        run(s, a, &mut out).unwrap();
        String::from_utf8(out).unwrap()
    }

    #[test]
    fn parse_ok_and_errors() {
        let v = |x: &[&str]| x.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let a = parse_args(&v(&[
            "--from",
            "2026-01-01",
            "--to",
            "2026-01-31",
            "--group-by",
            "day,app",
            "--format",
            "csv",
        ]))
        .unwrap();
        assert_eq!(a.group, [Dim::Day, Dim::App]);
        assert_eq!(a.format, Format::Csv);
        for bad in [
            v(&[]),
            v(&["--from", "2026-01-01", "--to", "2026-01-31"]),
            v(&[
                "--from",
                "2026-02-01",
                "--to",
                "2026-01-31",
                "--group-by",
                "day",
            ]),
            v(&["--from", "x", "--to", "2026-01-31", "--group-by", "day"]),
            v(&[
                "--from",
                "2026-01-01",
                "--to",
                "2026-01-31",
                "--group-by",
                "dya",
            ]),
            v(&[
                "--from",
                "2026-01-01",
                "--to",
                "2026-01-31",
                "--group-by",
                "day,day",
            ]),
            v(&[
                "--from",
                "2026-01-01",
                "--to",
                "2026-01-31",
                "--group-by",
                "day",
                "--format",
                "xml",
            ]),
            v(&[
                "--from",
                "2026-01-01",
                "--to",
                "2026-01-31",
                "--group-by",
                "day",
                "--bogus",
            ]),
            v(&["--from"]),
        ] {
            assert!(parse_args(&bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn group_by_day_uses_authoritative_interface_totals() {
        let (_t, s) = fixture();
        let a = args(&s, &[Dim::Day], Format::Csv);
        let out = run_str(&s, &a);
        let lines: Vec<&str> = out.lines().collect();
        assert_eq!(lines[0], "day,bytes_in,bytes_out,bytes");
        assert_eq!(lines[1], format!("{},220,0,220", day_of(&s, T0)));
        assert_eq!(lines[2], format!("{},20,0,20", day_of(&s, T0 + DAY)));
    }

    #[test]
    fn group_by_network() {
        let (_t, s) = fixture();
        let out = run_str(&s, &args(&s, &[Dim::Network], Format::Csv));
        assert_eq!(
            out,
            "network,bytes_in,bytes_out,bytes\nelsewhere,20,0,20\nhome,220,0,220\n"
        );
    }

    #[test]
    fn group_by_class_and_app_and_combinations() {
        let (_t, s) = fixture();
        let out = run_str(&s, &args(&s, &[Dim::Class], Format::Csv));
        assert_eq!(
            out,
            "class,bytes_in,bytes_out,bytes\nask,3,3,6\npersonal,40,0,40\nwork,117,11,128\n"
        );
        let out = run_str(&s, &args(&s, &[Dim::App, Dim::Class], Format::Csv));
        assert!(out.contains("Slack,work,117,11,128\n"), "{out}");
        assert!(out.contains("zzz,ask,3,3,6\n"));
        let out = run_str(
            &s,
            &args(&s, &[Dim::Day, Dim::Network, Dim::Class], Format::Csv),
        );
        assert_eq!(out.lines().count(), 1 + 5); // d1/home/{personal,work}, d1/elsewhere/work, d2/home/{ask,work}
    }

    #[test]
    fn date_range_is_inclusive_and_filters() {
        let (_t, s) = fixture();
        let mut a = args(&s, &[Dim::App], Format::Csv);
        a.to = a.from.clone();
        let out = run_str(&s, &a);
        assert!(!out.contains("zzz"));
        assert!(out.contains("Slack,110,11,121"));
    }

    #[test]
    fn json_output_is_versioned_and_streamed_with_commas() {
        let (_t, s) = fixture();
        let out = run_str(&s, &args(&s, &[Dim::Class], Format::Json));
        assert!(out.starts_with("{\"schema_version\":1,\"from\":\""));
        assert!(out.contains("\"group_by\":[\"class\"],\"gaps\":{\"failed_samples\":0,\"by_kind\":{},\"longest_interval_s\":120},\"rows\":[{\"class\":\"ask\",\"bytes_in\":3,\"bytes_out\":3,\"bytes\":6},{\"class\":\"personal\""));
        assert!(out.ends_with("]}\n"));
    }

    #[test]
    fn json_surfaces_failures_in_the_range() {
        let (_t, mut s) = fixture();
        s.record_failure(T0 + 10, "timeout", "t").unwrap();
        s.record_failure(T0 + DAY + 10, "exit", "e").unwrap();
        let out = run_str(&s, &args(&s, &[Dim::Day], Format::Json));
        assert!(out.contains(
            "\"gaps\":{\"failed_samples\":2,\"by_kind\":{\"exit\":1,\"timeout\":1},\"longest_interval_s\":120}"
        ), "{out}");
        // a one-day range only sees that day's failure
        let mut a = args(&s, &[Dim::Day], Format::Json);
        a.to = a.from.clone();
        assert!(run_str(&s, &a).contains("\"failed_samples\":1,\"by_kind\":{\"timeout\":1}"));
        // CSV stdout is untouched
        let csv = run_str(&s, &args(&s, &[Dim::Day], Format::Csv));
        assert!(csv.starts_with("day,bytes_in,bytes_out,bytes\n") && !csv.contains("gaps"));
    }

    #[test]
    fn empty_range_is_valid_json_and_header_only_csv() {
        let (_t, s) = fixture();
        let mut a = args(&s, &[Dim::Day], Format::Json);
        a.from = "1999-01-01".into();
        a.to = "1999-01-02".into();
        assert!(run_str(&s, &a).ends_with("\"rows\":[]}\n"));
        a.format = Format::Csv;
        assert_eq!(run_str(&s, &a), "day,bytes_in,bytes_out,bytes\n");
    }

    #[test]
    fn queries_span_raw_and_rolled_up_days() {
        let (_t, mut s) = fixture();
        let a = args(&s, &[Dim::App], Format::Csv);
        let before = run_str(&s, &a);
        s.rollup_before("2999-01-01").unwrap();
        assert_eq!(run_str(&s, &a), before);
    }

    #[test]
    fn hostile_names_are_escaped_in_both_formats() {
        let (_t, mut s) = fixture();
        put_sample(&mut s, T0, true, &[("a\"b,c\nd", 1, 1)], 2, 0);
        let a = args(&s, &[Dim::App], Format::Json);
        assert!(run_str(&s, &a).contains(r#""app":"a\"b,c\nd""#));
        let a = args(&s, &[Dim::App], Format::Csv);
        assert!(run_str(&s, &a).contains("\"a\"\"b,c\nd\",1,1,2"));
    }
}
