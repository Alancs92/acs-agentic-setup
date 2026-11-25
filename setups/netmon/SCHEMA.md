# netmon data schema (schema_version 1)

For agents that read netmon's data directly with `sqlite3`. Prefer the **views**
(`v_*`): they hide the raw/rolled-up split and stay stable across versions;
the tables are an implementation detail.

- **Location:** `~/Library/Application Support/netmon/netmon.db` (`NETMON_DIR` overrides the directory). Dir `0700`, files `0600`.
- **Open it read-only:** `sqlite3 -readonly "$HOME/Library/Application Support/netmon/netmon.db"`.
- **Units:** every `bytes_*` / `*_in` / `*_out` column is **bytes** (not bits, not GB). `in` = received (download), `out` = sent (upload). GB in reports is decimal (1e9 bytes).
- **Time:** `ts` is UTC epoch seconds. `day` is the **local** calendar date `YYYY-MM-DD` the sample falls in (computed by SQLite with `'localtime'` when written), so day buckets follow the Mac's timezone at the time of the sample.
- **Version:** `PRAGMA user_version` and `meta.schema_version` both hold `1`. netmon refuses to open a DB with a newer version.

## Views (the supported read API)

| View | Columns | Meaning |
|---|---|---|
| `v_daily_app` | `day, app, home, bytes_in, bytes_out` | Per-app usage per day and network. `app` is nettop's (truncated) process name. `home` is `1` (home gateway) or `0` (elsewhere/offline). Spans raw rows **and** rolled-up days. **Excludes** unattributed bytes. |
| `v_daily_class` | `day, class, home, bytes_in, bytes_out` | `v_daily_app` grouped by `class` (`work`/`personal`/`ask`) via the `app_class` cache. |
| `v_daily_network` | `day, home, bytes_in, bytes_out, vpn_in, vpn_out, unattr_in, unattr_out, samples, seconds` | **Authoritative device totals** from the physical interfaces (`en*`), per day and network. `vpn_*` is the `utun*` tunnel hint (already inside the physical totals, never added). `unattr_*` = interface delta not attributed to any app (clamped >= 0). `samples`/`seconds` = how many samples / seconds of coverage. |

`app_class` is a cache refreshed from `rules.yaml` by `netmon report` and `netmon query`;
if you read `v_daily_class` directly after editing the rules, run `netmon report` first.

```sql
-- GB today, home vs elsewhere (authoritative)
SELECT home, round((bytes_in+bytes_out)/1e9, 2) AS gb FROM v_daily_network WHERE day = date('now','localtime');
-- top apps this week
SELECT app, round(SUM(bytes_in+bytes_out)/1e9, 2) AS gb FROM v_daily_app
 WHERE day >= date('now','localtime','-6 days') GROUP BY app ORDER BY gb DESC LIMIT 10;
```

## Tables

| Table | Role | Key columns |
|---|---|---|
| `meta(key, value)` | `schema_version`, `boot_time` (UTC epoch), `last_ts` (last sample), `last_maint_day` | PK `key` |
| `samples` | one row per 2-minute sample (raw, kept 35 days) | `id, ts, day, interval_s, iface_in/out, vpn_in/out, unattr_in/out, gateway_mac (NULL offline), home` |
| `app_deltas` | per-app bytes in a sample, **only apps with traffic** (raw, 35 days) | PK `(sample_id, proc)`, `bytes_in, bytes_out` |
| `daily_rollup` | samples older than 35 days summed per `(day, proc, home)`; kept up to 5 years | PK `(day, proc, home)` |
| `daily_totals` | same, for the interface totals / coverage | PK `(day, home)`, `iface_in/out, vpn_in/out, unattr_in/out, samples, seconds` |
| `proc_state`, `iface_state` | previous cumulative counters (delta baseline). **Do not read: internal.** | |
| `app_class` | `proc -> class` cache | PK `proc` |

`interval_s` is the time since the previous sample (about 120; larger after sleep, because launchd does
not fire while asleep: that sample covers the whole gap, correctly).

## Lifecycle and size

- Raw rows (`samples`, `app_deltas`) are rolled into `daily_rollup`/`daily_totals` after **35 days**; totals before and after are identical.
- Rollup years that ended more than **5 years** ago are exported to `archive/<year>-apps.csv` and `archive/<year>-totals.csv` (plain CSV with a header row, columns identical to the two rollup tables), verified by re-reading the files, then deleted from the DB. Whole years only, so archive files are write-once.
- Housekeeping runs inside `netmon sample` at most once per local day.
- Budget: raw 35 days ~34 MB, +~3.7 MB per year of rollups in the worst case (every one of 150 apps active on every day, both networks): **~38 MB after one year**, enforced by `tests/budget.rs` (limit 100 MB/year).

## Reading without sqlite

`netmon query --from YYYY-MM-DD --to YYYY-MM-DD --group-by day|app|class|network[,..] --format json|csv`
streams rows. JSON shape (`schema_version` 1):

```json
{"schema_version":1,"from":"2026-11-01","to":"2026-11-25","group_by":["day","class"],
 "rows":[{"day":"2026-11-25","class":"work","bytes_in":123,"bytes_out":45,"bytes":168}]}
```

`network` values are `home` / `elsewhere`. Groupings of only `day`/`network` read the authoritative
interface totals (include unattributed); any grouping with `app` or `class` sums per-app deltas
(exclude unattributed), so those can add up to less than the day total.
`netmon report --date D` writes `reports/D.json` (also `schema_version` 1: totals, home/elsewhere, `vpn_hint`, `unattributed`, per-class, per-app).
