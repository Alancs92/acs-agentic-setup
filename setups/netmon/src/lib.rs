//! netmon: run-and-exit per-app network usage sampler. See `design.md`.

pub mod delta;
pub mod json;
pub mod netinfo;
pub mod nettop;
pub mod paths;
pub mod query;
pub mod report;
pub mod rules;
pub mod sample;
pub mod selfstats;
pub mod store;

#[cfg(test)]
pub(crate) mod testutil;

pub fn usage() -> &'static str {
    "netmon - per-app network usage sampler (macOS)\n\
     \n\
     usage:\n  \
     netmon sample [--dry]        take one sample (run by launchd every 2 min); --dry prints nettop rows only\n  \
     netmon report [--date YYYY-MM-DD]\n  \
                                  daily JSON (reports/<date>.json) + 3-line summary (default: today)\n  \
     netmon query --from YYYY-MM-DD --to YYYY-MM-DD --group-by day|app|class|network[,..] [--format json|csv]\n  \
                                  streamed aggregation over the SQL views\n  \
     netmon --help\n\
     \n\
     env: NETMON_DIR (data dir, default ~/Library/Application Support/netmon)\n"
}
