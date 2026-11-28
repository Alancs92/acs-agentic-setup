//! SQLite store: schema, one-transaction sample writes, retention/rollup,
//! 5-year CSV archive, class cache. Schema documented in `SCHEMA.md`.
//!
//! Journal choice: default rollback journal (`DELETE`), `synchronous=FULL`.
//! A sample is a tiny write every 2 minutes, so the extra fsync is free, there
//! are no `-wal`/`-shm` side files to leak or leave at a looser mode, and a
//! crash mid-write leaves the previous state intact.

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};

use crate::delta::{AppDelta, IfaceDelta, ProcEntry, State};
use crate::paths;
use crate::rules::Rules;

pub type Result<T> = rusqlite::Result<T>;

pub const SCHEMA_VERSION: i64 = 2;
/// Raw 2-minute deltas are kept this long, then rolled up per (day, app, home).
pub const RETENTION_DAYS: i64 = 35;
/// Daily rollups of years that ended more than this long ago are archived.
pub const ARCHIVE_YEARS: i64 = 5;

/// Migrations, applied in order to a DB whose `user_version` is below the
/// step's version (a fresh DB starts at 0 and runs them all). Each step is
/// idempotent (`IF NOT EXISTS`) and runs in one transaction with the version
/// bump. Never edit a released step: append a new one and raise `SCHEMA_VERSION`.
const MIGRATIONS: &[(i64, &str)] = &[(1, SCHEMA_V1), (2, MIGRATION_V2)];

/// v2: skipped-sample / gap tracking.
const MIGRATION_V2: &str = "
CREATE TABLE IF NOT EXISTS sample_failures(
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,            -- UTC epoch seconds of the failed attempt
  day TEXT NOT NULL,              -- local calendar date YYYY-MM-DD
  kind TEXT NOT NULL,             -- timeout|exit|spawn|io|netinfo|db
  detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sample_failures_day ON sample_failures(day);
";

const SCHEMA_V1: &str = "
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS samples(
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,            -- UTC epoch seconds
  day TEXT NOT NULL,              -- local calendar date YYYY-MM-DD
  interval_s INTEGER NOT NULL,
  iface_in INTEGER NOT NULL, iface_out INTEGER NOT NULL,
  vpn_in INTEGER NOT NULL, vpn_out INTEGER NOT NULL,
  unattr_in INTEGER NOT NULL, unattr_out INTEGER NOT NULL,
  gateway_mac TEXT,
  home INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS samples_day ON samples(day);
CREATE TABLE IF NOT EXISTS app_deltas(
  sample_id INTEGER NOT NULL, proc TEXT NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  PRIMARY KEY(sample_id, proc)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS proc_state(
  pid INTEGER PRIMARY KEY, proc TEXT NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS iface_state(
  name TEXT PRIMARY KEY, bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily_rollup(
  day TEXT NOT NULL, proc TEXT NOT NULL, home INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  PRIMARY KEY(day, proc, home)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily_totals(
  day TEXT NOT NULL, home INTEGER NOT NULL,
  iface_in INTEGER NOT NULL, iface_out INTEGER NOT NULL,
  vpn_in INTEGER NOT NULL, vpn_out INTEGER NOT NULL,
  unattr_in INTEGER NOT NULL, unattr_out INTEGER NOT NULL,
  samples INTEGER NOT NULL, seconds INTEGER NOT NULL,
  PRIMARY KEY(day, home)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS app_class(
  proc TEXT PRIMARY KEY, class TEXT NOT NULL) WITHOUT ROWID;

CREATE VIEW IF NOT EXISTS v_daily_app AS
  SELECT day, proc AS app, home, SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out
  FROM (
    SELECT s.day AS day, a.proc AS proc, s.home AS home, a.bytes_in AS bytes_in, a.bytes_out AS bytes_out
      FROM app_deltas a JOIN samples s ON s.id = a.sample_id
    UNION ALL
    SELECT day, proc, home, bytes_in, bytes_out FROM daily_rollup)
  GROUP BY day, proc, home;
CREATE VIEW IF NOT EXISTS v_daily_class AS
  SELECT v.day AS day, COALESCE(c.class, 'ask') AS class, v.home AS home,
         SUM(v.bytes_in) AS bytes_in, SUM(v.bytes_out) AS bytes_out
  FROM v_daily_app v LEFT JOIN app_class c ON c.proc = v.app
  GROUP BY v.day, COALESCE(c.class, 'ask'), v.home;
CREATE VIEW IF NOT EXISTS v_daily_network AS
  SELECT day, home, SUM(iface_in) AS bytes_in, SUM(iface_out) AS bytes_out,
         SUM(vpn_in) AS vpn_in, SUM(vpn_out) AS vpn_out,
         SUM(unattr_in) AS unattr_in, SUM(unattr_out) AS unattr_out,
         SUM(samples) AS samples, SUM(seconds) AS seconds
  FROM (
    SELECT day, home, iface_in, iface_out, vpn_in, vpn_out, unattr_in, unattr_out,
           1 AS samples, interval_s AS seconds FROM samples
    UNION ALL
    SELECT day, home, iface_in, iface_out, vpn_in, vpn_out, unattr_in, unattr_out,
           samples, seconds FROM daily_totals)
  GROUP BY day, home;
";

fn i(x: u64) -> i64 {
    i64::try_from(x).unwrap_or(i64::MAX)
}

pub struct Store {
    conn: Connection,
}

/// Sampling gaps over a date range (see `Store::gaps`).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Gaps {
    pub failed_samples: u64,
    /// `(kind, count)`, sorted by kind.
    pub by_kind: Vec<(String, u64)>,
    pub longest_interval_s: Option<i64>,
}

impl Gaps {
    /// `"timeout 2, exit 1"`.
    pub fn kinds_text(&self) -> String {
        let v: Vec<String> = self
            .by_kind
            .iter()
            .map(|(k, n)| format!("{k} {n}"))
            .collect();
        v.join(", ")
    }

    /// `{"failed_samples":N,"by_kind":{..},"longest_interval_s":X|null}`.
    pub fn push_json(&self, o: &mut String) {
        o.push_str(&format!(
            "{{\"failed_samples\":{},\"by_kind\":{{",
            self.failed_samples
        ));
        for (n, (k, c)) in self.by_kind.iter().enumerate() {
            if n > 0 {
                o.push(',');
            }
            crate::json::push_str(o, k);
            o.push_str(&format!(":{c}"));
        }
        match self.longest_interval_s {
            Some(s) => o.push_str(&format!("}},\"longest_interval_s\":{s}}}")),
            None => o.push_str("},\"longest_interval_s\":null}"),
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Meta {
    pub boot_time: Option<i64>,
    pub last_ts: Option<i64>,
}

pub struct SampleRow<'a> {
    pub ts: i64,
    pub interval_s: i64,
    pub iface: IfaceDelta,
    pub unattr_in: u64,
    pub unattr_out: u64,
    pub gateway_mac: Option<&'a str>,
    pub home: bool,
    pub apps: &'a [AppDelta],
}

pub struct WriteArgs<'a> {
    /// `None` on the baseline (first-ever) run: state is saved, no row written.
    pub sample: Option<SampleRow<'a>>,
    pub ts: i64,
    pub boot_time: Option<i64>,
    pub procs: &'a HashMap<u32, ProcEntry>,
    pub ifaces: &'a HashMap<String, (u64, u64)>,
}

impl Store {
    pub fn open(path: &Path) -> Result<Store> {
        let conn = Connection::open(path)?;
        let _ = paths::tighten_file(path);
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        // Tiny page cache (16 KiB): the working set of a sample run is a few
        // pages, and in the daily maintenance run it is the dominant RSS cost
        // (measured: -256 -> 4.6 MB peak, -16 -> 4.4 MB; steady-state unchanged).
        conn.pragma_update(None, "cache_size", -16)?;
        let ver: i64 = conn.pragma_query_value(None, "user_version", |r| r.get(0))?;
        if ver > SCHEMA_VERSION {
            return Err(rusqlite::Error::InvalidParameterName(format!(
                "database schema version {ver} is newer than this netmon ({SCHEMA_VERSION})"
            )));
        }
        for (v, sql) in MIGRATIONS.iter().filter(|(v, _)| *v > ver) {
            // DDL is transactional in SQLite: a crash mid-migration leaves the
            // previous version intact and the step simply runs again.
            let tx = conn.unchecked_transaction()?;
            tx.execute_batch(sql)?;
            tx.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [v],
            )?;
            tx.pragma_update(None, "user_version", v)?;
            tx.commit()?;
        }
        Ok(Store { conn })
    }

    /// Record a skipped sample in its own tiny transaction (the failed sample
    /// itself wrote nothing). `detail` is truncated to keep rows small.
    pub fn record_failure(&mut self, ts: i64, kind: &str, detail: &str) -> Result<()> {
        let detail: String = detail.chars().take(200).collect();
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT INTO sample_failures(ts,day,kind,detail)
             VALUES(?1, date(?1,'unixepoch','localtime'), ?2, ?3)",
            params![ts, kind, detail],
        )?;
        tx.commit()
    }

    /// Failures and the longest sampling interval for local days
    /// `from..=to`. `longest_interval_s` comes from raw samples only (it is
    /// `None` for days already rolled up, or with no sample).
    pub fn gaps(&self, from: &str, to: &str) -> Result<Gaps> {
        let mut g = Gaps::default();
        let mut q = self.conn.prepare(
            "SELECT kind, COUNT(*) FROM sample_failures
              WHERE day BETWEEN ?1 AND ?2 GROUP BY kind ORDER BY kind",
        )?;
        let mut rows = q.query([from, to])?;
        while let Some(r) = rows.next()? {
            let n = r.get::<_, i64>(1)?.max(0) as u64;
            g.failed_samples += n;
            g.by_kind.push((r.get(0)?, n));
        }
        g.longest_interval_s = self.conn.query_row(
            "SELECT MAX(interval_s) FROM samples WHERE day BETWEEN ?1 AND ?2",
            [from, to],
            |r| r.get(0),
        )?;
        Ok(g)
    }

    pub fn conn(&self) -> &Connection {
        &self.conn
    }

    fn meta_i64(&self, key: &str) -> Result<Option<i64>> {
        Ok(self
            .conn
            .query_row("SELECT value FROM meta WHERE key=?1", [key], |r| {
                r.get::<_, Option<i64>>(0)
            })
            .optional()?
            .flatten())
    }

    fn meta_text(&self, key: &str) -> Result<Option<String>> {
        Ok(self
            .conn
            .query_row("SELECT value FROM meta WHERE key=?1", [key], |r| {
                r.get::<_, Option<String>>(0)
            })
            .optional()?
            .flatten())
    }

    pub fn load_state(&self) -> Result<(State, Meta)> {
        let meta = Meta {
            boot_time: self.meta_i64("boot_time")?,
            last_ts: self.meta_i64("last_ts")?,
        };
        let mut st = State {
            initialized: meta.last_ts.is_some(),
            ..Default::default()
        };
        let mut q = self
            .conn
            .prepare("SELECT pid, proc, bytes_in, bytes_out FROM proc_state")?;
        let mut rows = q.query([])?;
        while let Some(r) = rows.next()? {
            st.procs.insert(
                r.get::<_, u32>(0)?,
                ProcEntry {
                    name: r.get(1)?,
                    bytes_in: r.get::<_, i64>(2)? as u64,
                    bytes_out: r.get::<_, i64>(3)? as u64,
                },
            );
        }
        let mut q = self
            .conn
            .prepare("SELECT name, bytes_in, bytes_out FROM iface_state")?;
        let mut rows = q.query([])?;
        while let Some(r) = rows.next()? {
            st.ifaces.insert(
                r.get(0)?,
                (r.get::<_, i64>(1)? as u64, r.get::<_, i64>(2)? as u64),
            );
        }
        Ok((st, meta))
    }

    /// Everything a sample changes, in ONE transaction.
    pub fn write(&mut self, a: &WriteArgs) -> Result<()> {
        self.write_inner(a, None)
    }

    pub(crate) fn write_inner(
        &mut self,
        a: &WriteArgs,
        fail_after_apps: Option<usize>,
    ) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(s) = &a.sample {
            tx.execute(
                "INSERT INTO samples(ts,day,interval_s,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,gateway_mac,home)
                 VALUES(?1, date(?1,'unixepoch','localtime'), ?2,?3,?4,?5,?6,?7,?8,?9,?10)",
                params![
                    s.ts, s.interval_s, i(s.iface.phys_in), i(s.iface.phys_out), i(s.iface.vpn_in),
                    i(s.iface.vpn_out), i(s.unattr_in), i(s.unattr_out), s.gateway_mac, s.home as i64
                ],
            )?;
            let id = tx.last_insert_rowid();
            let mut ins = tx.prepare(
                "INSERT INTO app_deltas(sample_id,proc,bytes_in,bytes_out) VALUES(?1,?2,?3,?4)",
            )?;
            for (n, d) in s.apps.iter().enumerate() {
                if fail_after_apps == Some(n) {
                    return Err(rusqlite::Error::InvalidQuery); // injected, drops tx -> rollback
                }
                ins.execute(params![id, d.name, i(d.bytes_in), i(d.bytes_out)])?;
            }
        }
        tx.execute("DELETE FROM proc_state", [])?;
        {
            let mut ins = tx.prepare(
                "INSERT INTO proc_state(pid,proc,bytes_in,bytes_out) VALUES(?1,?2,?3,?4)",
            )?;
            for (pid, p) in a.procs {
                ins.execute(params![pid, p.name, i(p.bytes_in), i(p.bytes_out)])?;
            }
        }
        tx.execute("DELETE FROM iface_state", [])?;
        {
            let mut ins =
                tx.prepare("INSERT INTO iface_state(name,bytes_in,bytes_out) VALUES(?1,?2,?3)")?;
            for (n, (bi, bo)) in a.ifaces {
                ins.execute(params![n, i(*bi), i(*bo)])?;
            }
        }
        let up = "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value";
        if let Some(b) = a.boot_time {
            tx.execute(up, params!["boot_time", b])?;
        }
        tx.execute(up, params!["last_ts", a.ts])?;
        tx.commit()
    }

    // ---------------------------------------------------------------- dates

    /// Local calendar date for `now` shifted by an SQLite modifier like `-35 days`.
    pub fn local_day(&self, modifier: &str) -> Result<String> {
        self.conn
            .query_row("SELECT date('now','localtime',?1)", [modifier], |r| {
                r.get(0)
            })
    }

    // ------------------------------------------------------------ retention

    /// Sum raw rows of days `< cutoff_day` into the rollup tables, then delete
    /// them. Idempotent; totals are preserved. Returns raw samples rolled up.
    pub fn rollup_before(&mut self, cutoff_day: &str) -> Result<usize> {
        // One transaction for the whole backlog (all-or-nothing; separate
        // transactions cost 5x wall time in fsyncs and saved no memory), but
        // one DAY at a time inside it, so each statement sorts and collects
        // delete keys for a single day's rows rather than the whole backlog.
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let days: Vec<String> = {
            let mut q =
                tx.prepare("SELECT DISTINCT day FROM samples WHERE day < ?1 ORDER BY day")?;
            let v = q
                .query_map([cutoff_day], |r| r.get(0))?
                .collect::<Result<_>>()?;
            v
        };
        let mut n = 0;
        for day in &days {
            tx.execute(
                "INSERT INTO daily_rollup(day,proc,home,bytes_in,bytes_out)
                 SELECT s.day, a.proc, s.home, SUM(a.bytes_in), SUM(a.bytes_out)
                   FROM app_deltas a JOIN samples s ON s.id=a.sample_id
                  WHERE s.day = ?1 GROUP BY a.proc, s.home
                 ON CONFLICT(day,proc,home) DO UPDATE SET
                   bytes_in=bytes_in+excluded.bytes_in, bytes_out=bytes_out+excluded.bytes_out",
                [day],
            )?;
            tx.execute(
                "INSERT INTO daily_totals(day,home,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,samples,seconds)
                 SELECT day, home, SUM(iface_in), SUM(iface_out), SUM(vpn_in), SUM(vpn_out),
                        SUM(unattr_in), SUM(unattr_out), COUNT(*), SUM(interval_s)
                   FROM samples WHERE day = ?1 GROUP BY home
                 ON CONFLICT(day,home) DO UPDATE SET
                   iface_in=iface_in+excluded.iface_in, iface_out=iface_out+excluded.iface_out,
                   vpn_in=vpn_in+excluded.vpn_in, vpn_out=vpn_out+excluded.vpn_out,
                   unattr_in=unattr_in+excluded.unattr_in, unattr_out=unattr_out+excluded.unattr_out,
                   samples=samples+excluded.samples, seconds=seconds+excluded.seconds",
                [day],
            )?;
            // One indexed prefix delete per sample: no ephemeral key set.
            let ids: Vec<i64> = {
                let mut q = tx.prepare("SELECT id FROM samples WHERE day = ?1")?;
                let v = q.query_map([day], |r| r.get(0))?.collect::<Result<_>>()?;
                v
            };
            let mut del = tx.prepare("DELETE FROM app_deltas WHERE sample_id = ?1")?;
            for id in ids {
                del.execute([id])?;
            }
            drop(del);
            n += tx.execute("DELETE FROM samples WHERE day = ?1", [day])?;
        }
        // Failure rows have no rollup: they follow the raw retention.
        tx.execute("DELETE FROM sample_failures WHERE day < ?1", [cutoff_day])?;
        tx.commit()?;
        Ok(n)
    }

    // -------------------------------------------------------------- archive

    /// Export every year `< before_year` of the daily rollups to
    /// `dir/<year>-apps.csv` and `dir/<year>-totals.csv`, verify the row
    /// counts by re-reading the written files, then delete those rows — all
    /// inside one transaction. Years are archived whole, so the files are
    /// write-once. Returns the years archived.
    pub fn archive_before_year(&mut self, dir: &Path, before_year: &str) -> Result<Vec<String>> {
        let years: Vec<String> = {
            let mut q = self.conn.prepare(
                "SELECT y FROM (SELECT substr(day,1,4) AS y FROM daily_rollup
                                UNION SELECT substr(day,1,4) FROM daily_totals)
                 WHERE y < ?1 ORDER BY y",
            )?;
            let v = q
                .query_map([before_year], |r| r.get(0))?
                .collect::<Result<_>>()?;
            v
        };
        if years.is_empty() {
            return Ok(years);
        }
        paths::ensure_dir(dir).map_err(io_err)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        for y in &years {
            export_csv(
                &tx,
                dir,
                &format!("{y}-apps.csv"),
                "day,proc,home,bytes_in,bytes_out",
                "SELECT day,proc,home,bytes_in,bytes_out FROM daily_rollup WHERE substr(day,1,4)=?1 ORDER BY day,proc,home",
                "SELECT COUNT(*) FROM daily_rollup WHERE substr(day,1,4)=?1",
                y,
                5,
            )?;
            export_csv(
                &tx,
                dir,
                &format!("{y}-totals.csv"),
                "day,home,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,samples,seconds",
                "SELECT day,home,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,samples,seconds FROM daily_totals WHERE substr(day,1,4)=?1 ORDER BY day,home",
                "SELECT COUNT(*) FROM daily_totals WHERE substr(day,1,4)=?1",
                y,
                10,
            )?;
            tx.execute("DELETE FROM daily_rollup WHERE substr(day,1,4)=?1", [y])?;
            tx.execute("DELETE FROM daily_totals WHERE substr(day,1,4)=?1", [y])?;
        }
        tx.commit()?;
        Ok(years)
    }

    // ---------------------------------------------------------- class cache

    /// Refresh `app_class` (read by the `v_daily_class` view) from the rules.
    pub fn sync_classes(&mut self, rules: &Rules) -> Result<()> {
        let procs: Vec<String> = {
            let mut q = self
                .conn
                .prepare("SELECT proc FROM app_deltas UNION SELECT proc FROM daily_rollup")?;
            let v = q.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
            v
        };
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute("DELETE FROM app_class", [])?;
        {
            let mut ins = tx.prepare("INSERT INTO app_class(proc,class) VALUES(?1,?2)")?;
            for p in &procs {
                ins.execute(params![p, rules.classify(p).as_str()])?;
            }
        }
        tx.commit()
    }

    /// Daily housekeeping: at most once per local day. Cheap when skipped
    /// (one meta read). Returns true if it ran.
    /// (The class cache is NOT refreshed here: that scans every raw row, so it
    /// runs on demand in `report`/`query` instead of in the sample path.)
    pub fn maintain(&mut self, archive_dir: &Path) -> Result<bool> {
        let today = self.local_day("+0 days")?;
        if self.meta_text("last_maint_day")?.as_deref() == Some(today.as_str()) {
            return Ok(false);
        }
        let cutoff = self.local_day(&format!("-{RETENTION_DAYS} days"))?;
        self.rollup_before(&cutoff)?;
        let archive_cutoff = self.local_day(&format!("-{ARCHIVE_YEARS} years"))?;
        self.archive_before_year(archive_dir, &archive_cutoff[..4])?;
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES('last_maint_day',?1)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [&today],
        )?;
        Ok(true)
    }
}

fn io_err(e: std::io::Error) -> rusqlite::Error {
    rusqlite::Error::ToSqlConversionFailure(Box::new(e))
}

pub(crate) fn csv_field(s: &str) -> String {
    if s.contains([',', '"', '\n', '\r']) {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

/// Stream `select` into `dir/name` (via a temp file + rename, mode 0600),
/// checking that the number of rows written equals `count_sql` and that the
/// file re-reads with that many data lines.
#[allow(clippy::too_many_arguments)]
fn export_csv(
    tx: &rusqlite::Transaction,
    dir: &Path,
    name: &str,
    header: &str,
    select: &str,
    count_sql: &str,
    year: &str,
    cols: usize,
) -> Result<()> {
    let expected: i64 = tx.query_row(count_sql, [year], |r| r.get(0))?;
    let tmp = dir.join(format!("{name}.tmp"));
    let fin = dir.join(name);
    let written = (|| -> Result<i64> {
        let f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(io_err)?;
        let mut w = BufWriter::new(f);
        writeln!(w, "{header}").map_err(io_err)?;
        let mut q = tx.prepare(select)?;
        let mut rows = q.query([year])?;
        let mut n = 0i64;
        while let Some(r) = rows.next()? {
            let mut line = String::new();
            for c in 0..cols {
                if c > 0 {
                    line.push(',');
                }
                match r.get_ref(c)? {
                    rusqlite::types::ValueRef::Integer(v) => line.push_str(&v.to_string()),
                    rusqlite::types::ValueRef::Text(t) => {
                        line.push_str(&csv_field(&String::from_utf8_lossy(t)))
                    }
                    _ => {}
                }
            }
            writeln!(w, "{line}").map_err(io_err)?;
            n += 1;
        }
        w.flush().map_err(io_err)?;
        w.get_ref().sync_all().map_err(io_err)?;
        Ok(n)
    })();
    let verify = |n: i64| -> Result<()> {
        // Re-read the file: lines minus header must equal what the DB said.
        let f = File::open(&tmp).map_err(io_err)?;
        let lines = BufReader::new(f).lines().count() as i64 - 1;
        if n == expected && lines == expected {
            Ok(())
        } else {
            Err(rusqlite::Error::InvalidParameterName(format!(
                "archive verification failed for {name}: db={expected} written={n} file={lines}"
            )))
        }
    };
    match written.and_then(verify) {
        Ok(()) => fs::rename(&tmp, &fin).map_err(io_err),
        Err(e) => {
            let _ = fs::remove_file(&tmp);
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;
    use std::os::unix::fs::PermissionsExt;

    fn open(t: &TempDir) -> Store {
        Store::open(&t.path().join("netmon.db")).unwrap()
    }

    fn apps(v: &[(&str, u64, u64)]) -> Vec<AppDelta> {
        v.iter()
            .map(|(n, i, o)| AppDelta {
                name: n.to_string(),
                bytes_in: *i,
                bytes_out: *o,
            })
            .collect()
    }

    fn put(
        s: &mut Store,
        ts: i64,
        home: bool,
        list: &[(&str, u64, u64)],
        iface_in: u64,
        unattr_in: u64,
    ) {
        let a = apps(list);
        let procs = HashMap::new();
        let ifaces = HashMap::new();
        s.write(&WriteArgs {
            sample: Some(SampleRow {
                ts,
                interval_s: 120,
                iface: IfaceDelta {
                    phys_in: iface_in,
                    phys_out: 0,
                    vpn_in: 1,
                    vpn_out: 2,
                },
                unattr_in,
                unattr_out: 0,
                gateway_mac: if home {
                    Some("aa:bb:cc:dd:ee:ff")
                } else {
                    None
                },
                home,
                apps: &a,
            }),
            ts,
            boot_time: Some(1),
            procs: &procs,
            ifaces: &ifaces,
        })
        .unwrap();
    }

    fn count(s: &Store, table: &str) -> i64 {
        s.conn
            .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
            .unwrap()
    }

    fn day_total(s: &Store, day: &str) -> (i64, i64) {
        s.conn
            .query_row(
                "SELECT COALESCE(SUM(bytes_in),0), COALESCE(SUM(bytes_out),0) FROM v_daily_app WHERE day=?1",
                [day],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap()
    }

    #[test]
    fn schema_is_idempotent_and_versioned() {
        let t = TempDir::new("store-schema");
        drop(open(&t));
        let s = open(&t); // reopen: no error, no duplicate objects
        assert_eq!(s.meta_i64("schema_version").unwrap(), Some(SCHEMA_VERSION));
        let v: i64 = s
            .conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, SCHEMA_VERSION);
        let views: i64 = s
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='view'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(views, 3);
    }

    #[test]
    fn newer_schema_is_refused() {
        let t = TempDir::new("store-newer");
        let p = t.path().join("netmon.db");
        drop(Store::open(&p).unwrap());
        Connection::open(&p)
            .unwrap()
            .pragma_update(None, "user_version", 99)
            .unwrap();
        assert!(Store::open(&p).is_err());
    }

    #[test]
    fn v1_database_migrates_forward_keeping_its_data() {
        let t = TempDir::new("store-migrate");
        let p = t.path().join("netmon.db");
        {
            // A genuine v1 file: only the v1 schema, user_version 1.
            let c = Connection::open(&p).unwrap();
            c.execute_batch(SCHEMA_V1).unwrap();
            c.execute("INSERT INTO meta(key,value) VALUES('schema_version',1)", [])
                .unwrap();
            c.execute(
                "INSERT INTO samples(ts,day,interval_s,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,home)
                 VALUES(1,'2026-01-01',120,5,6,0,0,0,0,1)",
                [],
            )
            .unwrap();
            c.pragma_update(None, "user_version", 1).unwrap();
        }
        let mut s = Store::open(&p).unwrap();
        let v: i64 = s
            .conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, 2);
        assert_eq!(s.meta_i64("schema_version").unwrap(), Some(2));
        s.record_failure(100, "timeout", "x").unwrap(); // new table exists
        let n: i64 = s
            .conn
            .query_row("SELECT iface_in FROM samples", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 5, "existing rows survive");
    }

    #[test]
    fn failures_are_recorded_summarised_and_follow_retention() {
        let t = TempDir::new("store-failures");
        let mut s = open(&t);
        let day = |s: &Store, ts: i64| -> String {
            s.conn
                .query_row("SELECT date(?1,'unixepoch','localtime')", [ts], |r| {
                    r.get(0)
                })
                .unwrap()
        };
        const T: i64 = 1_700_000_000;
        put(&mut s, T, true, &[("a", 1, 1)], 2, 0);
        put(&mut s, T + 120, true, &[("a", 1, 1)], 2, 0);
        s.record_failure(T + 200, "timeout", "nettop: timed out")
            .unwrap();
        s.record_failure(T + 300, "timeout", &"d".repeat(1000))
            .unwrap();
        s.record_failure(T + 400, "exit", "status 3").unwrap();
        let d = day(&s, T);
        let g = s.gaps(&d, &d).unwrap();
        assert_eq!(g.failed_samples, 3);
        assert_eq!(g.by_kind, vec![("exit".into(), 1), ("timeout".into(), 2)]);
        assert_eq!(g.longest_interval_s, Some(120));
        assert_eq!(g.kinds_text(), "exit 1, timeout 2");
        let len: i64 = s
            .conn
            .query_row("SELECT MAX(length(detail)) FROM sample_failures", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(len, 200, "detail is capped");
        let none = s.gaps("1999-01-01", "1999-01-02").unwrap();
        assert_eq!(none, Gaps::default());
        let mut j = String::new();
        g.push_json(&mut j);
        assert_eq!(
            j,
            "{\"failed_samples\":3,\"by_kind\":{\"exit\":1,\"timeout\":2},\"longest_interval_s\":120}"
        );
        // same 35-day retention as raw rows
        s.rollup_before("2999-01-01").unwrap();
        assert_eq!(s.gaps(&d, &d).unwrap().failed_samples, 0);
    }

    #[test]
    fn db_file_is_0600() {
        let t = TempDir::new("store-mode");
        drop(open(&t));
        let m = fs::metadata(t.path().join("netmon.db"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(m, 0o600);
    }

    #[test]
    fn state_round_trips_and_initialized_flag() {
        let t = TempDir::new("store-state");
        let mut s = open(&t);
        let (st, meta) = s.load_state().unwrap();
        assert!(!st.initialized);
        assert_eq!(meta, Meta::default());

        let mut procs = HashMap::new();
        procs.insert(
            7u32,
            ProcEntry {
                name: "Safari".into(),
                bytes_in: 5,
                bytes_out: 6,
            },
        );
        let mut ifaces = HashMap::new();
        ifaces.insert("en0".to_string(), (10u64, 20u64));
        s.write(&WriteArgs {
            sample: None,
            ts: 1000,
            boot_time: Some(42),
            procs: &procs,
            ifaces: &ifaces,
        })
        .unwrap();

        let (st, meta) = s.load_state().unwrap();
        assert!(st.initialized);
        assert_eq!(st.procs, procs);
        assert_eq!(st.ifaces, ifaces);
        assert_eq!(
            meta,
            Meta {
                boot_time: Some(42),
                last_ts: Some(1000)
            }
        );
        assert_eq!(count(&s, "samples"), 0); // baseline writes no sample row
    }

    #[test]
    fn write_is_atomic_when_it_fails_midway() {
        let t = TempDir::new("store-atomic");
        let mut s = open(&t);
        let a = apps(&[("a", 1, 1), ("b", 2, 2), ("c", 3, 3)]);
        let procs = HashMap::new();
        let ifaces = HashMap::new();
        let args = WriteArgs {
            sample: Some(SampleRow {
                ts: 1_700_000_000,
                interval_s: 120,
                iface: IfaceDelta::default(),
                unattr_in: 0,
                unattr_out: 0,
                gateway_mac: None,
                home: false,
                apps: &a,
            }),
            ts: 1_700_000_000,
            boot_time: Some(1),
            procs: &procs,
            ifaces: &ifaces,
        };
        assert!(s.write_inner(&args, Some(2)).is_err());
        assert_eq!(count(&s, "samples"), 0);
        assert_eq!(count(&s, "app_deltas"), 0);
        assert!(!s.load_state().unwrap().0.initialized);
        s.write(&args).unwrap();
        assert_eq!(count(&s, "app_deltas"), 3);
    }

    #[test]
    fn day_column_is_the_local_date_of_ts() {
        let t = TempDir::new("store-day");
        let mut s = open(&t);
        put(&mut s, 1_700_000_000, false, &[("a", 1, 1)], 1, 0);
        let (day, local): (String, String) = s
            .conn
            .query_row(
                "SELECT day, date(ts,'unixepoch','localtime') FROM samples",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(day, local);
        assert_eq!(day.len(), 10);
    }

    mod retention {
        use super::*;

        const OLD: i64 = 1_600_000_000; // 2020-09-13 UTC
        const OLD2: i64 = OLD + 86_400 * 3;
        const NEW: i64 = 1_700_000_000; // 2023-11-14 UTC

        fn day_of(s: &Store, ts: i64) -> String {
            s.conn
                .query_row("SELECT date(?1,'unixepoch','localtime')", [ts], |r| {
                    r.get(0)
                })
                .unwrap()
        }

        fn seed(s: &mut Store) {
            put(s, OLD, true, &[("a", 10, 1), ("b", 5, 5)], 20, 5);
            put(s, OLD + 120, true, &[("a", 7, 2)], 10, 3);
            put(s, OLD + 240, false, &[("a", 1, 1)], 2, 0);
            put(s, OLD2, false, &[("c", 9, 9)], 9, 0);
            put(s, NEW, true, &[("a", 100, 100)], 100, 0);
        }

        #[test]
        fn old_rows_are_summed_into_rollups_and_deleted() {
            let t = TempDir::new("ret-sum");
            let mut s = open(&t);
            seed(&mut s);
            let cutoff = day_of(&s, NEW);
            let old_day = day_of(&s, OLD);
            let before = day_total(&s, &old_day);
            assert_eq!(s.rollup_before(&cutoff).unwrap(), 4);
            assert_eq!(count(&s, "samples"), 1); // recent untouched
            assert_eq!(count(&s, "app_deltas"), 1);
            let ru: (i64, i64) = s
                .conn
                .query_row("SELECT bytes_in, bytes_out FROM daily_rollup WHERE day=?1 AND proc='a' AND home=1", [&old_day], |r| Ok((r.get(0)?, r.get(1)?)))
                .unwrap();
            assert_eq!(ru, (17, 3));
            assert_eq!(day_total(&s, &old_day), before); // totals preserved
            let tot: (i64, i64, i64) = s
                .conn
                .query_row(
                    "SELECT iface_in, unattr_in, samples FROM daily_totals WHERE day=?1 AND home=1",
                    [&old_day],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .unwrap();
            assert_eq!(tot, (30, 8, 2));
        }

        #[test]
        fn chunked_rollup_over_a_big_backlog_preserves_totals() {
            let t = TempDir::new("ret-chunks");
            let mut s = open(&t);
            // 250 samples (> 2 chunks) over one old day, alternating networks
            for n in 0..250i64 {
                put(
                    &mut s,
                    OLD + n * 60,
                    n % 2 == 0,
                    &[("a", 3, 1), ("b", 2, 2)],
                    7,
                    1,
                );
            }
            let all = |s: &Store| -> (i64, i64) {
                s.conn
                    .query_row(
                        "SELECT SUM(bytes_in), SUM(bytes_out) FROM v_daily_app",
                        [],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )
                    .unwrap()
            };
            let before = all(&s);
            assert_eq!(before, (250 * 5, 250 * 3));
            assert_eq!(s.rollup_before("2999-01-01").unwrap(), 250);
            assert_eq!(count(&s, "samples"), 0);
            assert_eq!(count(&s, "app_deltas"), 0);
            assert_eq!(all(&s), before);
            let samples: i64 = s
                .conn
                .query_row("SELECT SUM(samples) FROM daily_totals", [], |r| r.get(0))
                .unwrap();
            assert_eq!(samples, 250);
        }

        #[test]
        fn rollup_is_idempotent() {
            let t = TempDir::new("ret-idem");
            let mut s = open(&t);
            seed(&mut s);
            let cutoff = day_of(&s, NEW);
            s.rollup_before(&cutoff).unwrap();
            let snapshot = |s: &Store| {
                (
                    count(s, "daily_rollup"),
                    day_total(s, &day_of(s, OLD)),
                    count(s, "daily_totals"),
                )
            };
            let a = snapshot(&s);
            assert_eq!(s.rollup_before(&cutoff).unwrap(), 0);
            assert_eq!(snapshot(&s), a);
        }

        #[test]
        fn late_rows_for_an_already_rolled_day_merge() {
            let t = TempDir::new("ret-merge");
            let mut s = open(&t);
            seed(&mut s);
            let cutoff = day_of(&s, NEW);
            s.rollup_before(&cutoff).unwrap();
            let old_day = day_of(&s, OLD);
            let before = day_total(&s, &old_day);
            put(&mut s, OLD + 360, true, &[("a", 1000, 0)], 1000, 0);
            s.rollup_before(&cutoff).unwrap();
            let after = day_total(&s, &old_day);
            assert_eq!(after.0, before.0 + 1000);
        }

        #[test]
        fn maintain_runs_once_per_day() {
            let t = TempDir::new("ret-maint");
            let mut s = open(&t);
            seed(&mut s);
            let dir = t.path().join("archive");
            assert!(s.maintain(&dir).unwrap());
            assert!(!s.maintain(&dir).unwrap());
            assert_eq!(count(&s, "samples"), 0); // all fixture rows are > 35 days old
            assert_eq!(count(&s, "app_class"), 0); // class cache is on-demand only
        }
    }

    mod archive {
        use super::*;

        fn insert_rollup(s: &Store, day: &str, proc_: &str, home: i64, i: i64, o: i64) {
            s.conn
                .execute(
                    "INSERT INTO daily_rollup VALUES(?1,?2,?3,?4,?5)",
                    params![day, proc_, home, i, o],
                )
                .unwrap();
        }
        fn insert_totals(s: &Store, day: &str, home: i64) {
            s.conn
                .execute(
                    "INSERT INTO daily_totals VALUES(?1,?2,10,20,1,2,3,4,5,600)",
                    params![day, home],
                )
                .unwrap();
        }

        #[test]
        fn whole_old_years_are_exported_verified_and_deleted() {
            let t = TempDir::new("arch");
            let mut s = open(&t);
            insert_rollup(&s, "2019-03-01", "a", 1, 5, 6);
            insert_rollup(&s, "2019-12-31", "we\"ird,name", 0, 7, 8);
            insert_rollup(&s, "2020-01-01", "keep-if-limit-2020", 0, 1, 1);
            insert_totals(&s, "2019-03-01", 1);
            insert_totals(&s, "2020-01-01", 1);
            let dir = t.path().join("archive");
            let years = s.archive_before_year(&dir, "2020").unwrap();
            assert_eq!(years, ["2019"]);
            let apps = fs::read_to_string(dir.join("2019-apps.csv")).unwrap();
            assert_eq!(
                apps,
                "day,proc,home,bytes_in,bytes_out\n2019-03-01,a,1,5,6\n2019-12-31,\"we\"\"ird,name\",0,7,8\n"
            );
            let tot = fs::read_to_string(dir.join("2019-totals.csv")).unwrap();
            assert_eq!(tot.lines().count(), 2);
            assert_eq!(count(&s, "daily_rollup"), 1); // 2020 kept
            assert_eq!(count(&s, "daily_totals"), 1);
            let mode = fs::metadata(dir.join("2019-apps.csv"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600);
            assert_eq!(
                fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
                0o700
            );
            assert!(!dir.join("2019-apps.csv.tmp").exists());
        }

        #[test]
        fn archive_is_idempotent() {
            let t = TempDir::new("arch-idem");
            let mut s = open(&t);
            insert_rollup(&s, "2019-03-01", "a", 1, 5, 6);
            let dir = t.path().join("archive");
            s.archive_before_year(&dir, "2020").unwrap();
            let first = fs::read(dir.join("2019-apps.csv")).unwrap();
            assert!(s.archive_before_year(&dir, "2020").unwrap().is_empty());
            assert_eq!(fs::read(dir.join("2019-apps.csv")).unwrap(), first);
        }

        #[test]
        fn nothing_to_archive_creates_nothing() {
            let t = TempDir::new("arch-none");
            let mut s = open(&t);
            insert_rollup(&s, "2023-03-01", "a", 1, 5, 6);
            let dir = t.path().join("archive");
            assert!(s.archive_before_year(&dir, "2020").unwrap().is_empty());
            assert!(!dir.exists());
            assert_eq!(count(&s, "daily_rollup"), 1);
        }

        #[test]
        fn failed_export_keeps_the_rows() {
            let t = TempDir::new("arch-fail");
            let mut s = open(&t);
            insert_rollup(&s, "2019-03-01", "a", 1, 5, 6);
            // the archive "dir" is a file, so it cannot be created
            let dir = t.path().join("blocker");
            fs::write(&dir, b"x").unwrap();
            assert!(s.archive_before_year(&dir, "2020").is_err());
            assert_eq!(count(&s, "daily_rollup"), 1);
        }
    }

    #[test]
    fn views_union_raw_and_rollup_and_classify() {
        let t = TempDir::new("views");
        let mut s = open(&t);
        put(
            &mut s,
            1_700_000_000,
            true,
            &[("Slack", 100, 10), ("Spotify", 50, 5), ("mystery", 1, 1)],
            200,
            49,
        );
        s.sync_classes(&Rules::parse("work:\n  - Slack*\npersonal:\n  - Spotify*\n").unwrap())
            .unwrap();
        let mut q = s
            .conn
            .prepare("SELECT class, SUM(bytes_in) FROM v_daily_class GROUP BY class ORDER BY class")
            .unwrap();
        let got: Vec<(String, i64)> = q
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(
            got,
            [
                ("ask".into(), 1),
                ("personal".into(), 50),
                ("work".into(), 100)
            ]
        );
        let net: (i64, i64, i64) = s
            .conn
            .query_row(
                "SELECT bytes_in, vpn_in, unattr_in FROM v_daily_network",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(net, (200, 1, 49));
    }
}
