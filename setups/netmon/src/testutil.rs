//! Test helper: a self-deleting temp dir (no `tempfile` crate).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};

static N: AtomicU32 = AtomicU32::new(0);

pub struct TempDir(PathBuf);

impl TempDir {
    pub fn new(tag: &str) -> Self {
        let p = std::env::temp_dir().join(format!(
            "netmon-test-{}-{}-{}",
            tag,
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&p).unwrap();
        TempDir(p)
    }
    pub fn path(&self) -> &Path {
        &self.0
    }
}

/// Write one fixture sample (iface_in given, vpn 1/2, unattributed as given).
pub fn put_sample(
    s: &mut crate::store::Store,
    ts: i64,
    home: bool,
    list: &[(&str, u64, u64)],
    iface_in: u64,
    unattr_in: u64,
) {
    use crate::delta::{AppDelta, IfaceDelta};
    use crate::store::{SampleRow, WriteArgs};
    use std::collections::HashMap;
    let apps: Vec<AppDelta> = list
        .iter()
        .map(|(n, i, o)| AppDelta {
            name: n.to_string(),
            bytes_in: *i,
            bytes_out: *o,
        })
        .collect();
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
            apps: &apps,
        }),
        ts,
        boot_time: Some(1),
        procs: &procs,
        ifaces: &ifaces,
    })
    .unwrap();
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
