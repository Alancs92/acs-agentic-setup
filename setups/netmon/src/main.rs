use std::io::{self, Write};
use std::path::Path;
use std::process::ExitCode;

use netmon::{paths, query, report, sample, store::Store, usage};

const LAST_ERROR: &str = "last-error.txt";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("sample") => cmd_sample(&args[1..]),
        Some("report") => logged("report", cmd_report(&args[1..])),
        Some("query") => logged("query", cmd_query(&args[1..])),
        Some("--help" | "-h" | "help") => {
            print!("{}", usage());
            Ok(())
        }
        _ => {
            eprint!("{}", usage());
            return ExitCode::from(2);
        }
    };
    let code = match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("netmon: {e}");
            ExitCode::FAILURE
        }
    };
    if netmon::selfstats::enabled() {
        if let Some(l) = netmon::selfstats::line() {
            eprintln!("{l}");
        }
    }
    code
}

fn open_store(dir: &Path) -> Result<Store, String> {
    sample::ensure_layout(dir).map_err(|e| format!("data dir: {e}"))?;
    Store::open(&dir.join(sample::DB_FILE)).map_err(|e| format!("db: {e}"))
}

fn cmd_sample(args: &[String]) -> Result<(), String> {
    if args.first().map(String::as_str) == Some("--dry") {
        let n =
            sample::dry(|r| println!("{}\t{}\t{}\t{}", r.name, r.pid, r.bytes_in, r.bytes_out))?;
        eprintln!("{n} rows");
        return Ok(());
    }
    if !args.is_empty() {
        return Err(format!("unknown argument `{}`", args[0]));
    }
    let dir = paths::data_dir().map_err(|e| e.to_string())?;
    // Failures leave one small, overwritten file (bounded) for diagnosis.
    match sample::run(&dir, sample::now()) {
        Ok(o) => {
            let _ = std::fs::remove_file(dir.join(LAST_ERROR));
            println!(
                "{}: {} processes, {} apps with traffic",
                if o.baseline { "baseline" } else { "sampled" },
                o.processes,
                o.app_deltas
            );
            Ok(())
        }
        Err(e) => {
            let _ = paths::write_private(
                &dir.join(LAST_ERROR),
                format!("{} {e}\n", sample::now()).as_bytes(),
            );
            Err(e.to_string())
        }
    }
}

/// Run `report`/`query`; failures also go to the capped error log (launchd
/// would otherwise drop stderr). Logging never changes the outcome.
fn logged(sub: &str, r: Result<(), String>) -> Result<(), String> {
    if let Err(e) = &r {
        if let Ok(dir) = paths::data_dir() {
            netmon::errlog::append(&dir, sub, "error", e);
        }
    }
    r
}

fn cmd_report(args: &[String]) -> Result<(), String> {
    let dir = paths::data_dir().map_err(|e| e.to_string())?;
    let mut store = open_store(&dir)?;
    let date = match args {
        [] => store.local_day("+0 days").map_err(|e| e.to_string())?,
        [f, d] if f == "--date" => d.clone(),
        _ => return Err("usage: netmon report [--date YYYY-MM-DD]".into()),
    };
    if !report::valid_date(&date) {
        return Err(format!("`{date}` is not a YYYY-MM-DD date"));
    }
    let rules = sample::load_rules(&dir)?;
    // Keep the SQL-facing class cache (v_daily_class) fresh for other agents.
    store.sync_classes(&rules).map_err(|e| e.to_string())?;
    let r = report::build(&store, &rules, &date).map_err(|e| e.to_string())?;
    let p = report::write_report_file(&dir, &r).map_err(|e| format!("report file: {e}"))?;
    println!("{}", report::summary(&r));
    eprintln!("wrote {}", p.display());
    Ok(())
}

fn cmd_query(args: &[String]) -> Result<(), String> {
    let a = query::parse_args(args)?;
    let dir = paths::data_dir().map_err(|e| e.to_string())?;
    let mut store = open_store(&dir)?;
    if a.needs_classes() {
        let rules = sample::load_rules(&dir)?;
        store.sync_classes(&rules).map_err(|e| e.to_string())?;
    }
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    query::run(&store, &a, &mut out)?;
    out.flush().map_err(|e| e.to_string())
}
