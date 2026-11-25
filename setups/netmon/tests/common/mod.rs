//! Shared integration-test harness: a temp data dir plus fake `nettop`,
//! `netstat`, `route`, `arp` and `sysctl` scripts driven by fixture files.

#![allow(dead_code)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

pub struct Fake {
    pub root: PathBuf,
    pub fixtures: PathBuf,
    pub data: PathBuf,
    bin: PathBuf,
}

impl Fake {
    pub fn new(tag: &str) -> Fake {
        let root = std::env::temp_dir().join(format!("netmon-it-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let (fixtures, data, bin) = (root.join("fixtures"), root.join("data"), root.join("bin"));
        for d in [&fixtures, &bin] {
            fs::create_dir_all(d).unwrap();
        }
        let f = Fake {
            root,
            fixtures,
            data,
            bin,
        };
        f.script(
            "nettop",
            "if [ -f \"$NETMON_FAKE/hang\" ]; then exec sleep 30; fi\n\
                            if [ -f \"$NETMON_FAKE/fail\" ]; then exit 1; fi\n\
                            exec cat \"$NETMON_FAKE/nettop.txt\"",
        );
        for n in ["netstat", "route", "arp"] {
            f.script(n, &format!("exec cat \"$NETMON_FAKE/{n}.txt\""));
        }
        f.script("sysctl", "exec cat \"$NETMON_FAKE/boot.txt\"");
        f.set("route.txt", "    gateway: 192.168.68.1\n  interface: en0\n");
        f.set(
            "arp.txt",
            "? (192.168.68.1) at a:b:c:d:e:f on en0 ifscope [ethernet]\n",
        );
        f.set(
            "boot.txt",
            "{ sec = 1795002037, usec = 1 } Wed Nov 18 18:40:37 2026\n",
        );
        f
    }

    fn script(&self, name: &str, body: &str) {
        let p = self.bin.join(name);
        fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
    }

    pub fn set(&self, name: &str, contents: &str) {
        fs::write(self.fixtures.join(name), contents).unwrap();
    }

    pub fn flag(&self, name: &str, on: bool) {
        let p = self.fixtures.join(name);
        if on {
            fs::write(p, "").unwrap();
        } else {
            let _ = fs::remove_file(p);
        }
    }

    /// `rows`: (name.pid, in, out).
    pub fn nettop(&self, rows: &[(&str, u64, u64)]) {
        let mut s = String::from(",bytes_in,bytes_out,\n");
        for (n, i, o) in rows {
            s.push_str(&format!("{n},{i},{o},\n"));
        }
        self.set("nettop.txt", &s);
    }

    /// Interface counters: en0 (physical) and utun4 (tunnel).
    pub fn netstat(&self, en0: (u64, u64), utun4: (u64, u64)) {
        self.set(
            "netstat.txt",
            &format!(
                "Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll\n\
                 lo0 16384 <Link#1> 1 0 999999 1 0 999999 0\n\
                 en0 1500 <Link#14> b6:ab:a5:21:44:40 1 0 {} 1 0 {} 0\n\
                 utun4 1280 <Link#22> 1 0 {} 1 0 {} 0\n",
                en0.0, en0.1, utun4.0, utun4.1
            ),
        );
    }

    pub fn write_config(&self, body: &str) {
        fs::create_dir_all(&self.data).unwrap();
        fs::write(self.data.join("config"), body).unwrap();
    }

    pub fn cmd(&self, bin: &Path) -> Command {
        let mut c = Command::new(bin);
        c.env("NETMON_DIR", &self.data)
            .env("NETMON_FAKE", &self.fixtures)
            .env("NETMON_NETTOP", self.bin.join("nettop"))
            .env("NETMON_NETSTAT", self.bin.join("netstat"))
            .env("NETMON_ROUTE", self.bin.join("route"))
            .env("NETMON_ARP", self.bin.join("arp"))
            .env("NETMON_SYSCTL", self.bin.join("sysctl"));
        c
    }

    pub fn db(&self) -> rusqlite::Connection {
        rusqlite::Connection::open(self.data.join("netmon.db")).unwrap()
    }
}

impl Drop for Fake {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

pub fn netmon() -> &'static Path {
    Path::new(env!("CARGO_BIN_EXE_netmon"))
}
