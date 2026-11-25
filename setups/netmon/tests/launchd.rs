//! launchd plist template + installer. Nothing here installs or loads anything:
//! the plist is only rendered into a temp dir and the installer only dry-run.

use std::path::{Path, PathBuf};
use std::process::Command;

fn root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
}

fn tmp(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("netmon-launchd-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn render(out: &Path, binary: &str) {
    let s = Command::new("sh")
        .arg(root().join("scripts/install.sh"))
        .args(["--render", out.to_str().unwrap(), "--binary", binary])
        .status()
        .unwrap();
    assert!(s.success());
}

fn extract(plist: &Path, key: &str) -> String {
    let o = Command::new("plutil")
        .args(["-extract", key, "raw", "-o", "-"])
        .arg(plist)
        .output()
        .unwrap();
    assert!(
        o.status.success(),
        "plutil -extract {key}: {}",
        String::from_utf8_lossy(&o.stderr)
    );
    String::from_utf8(o.stdout).unwrap().trim().to_string()
}

#[test]
fn rendered_plist_lints_and_has_the_required_keys() {
    let d = tmp("render");
    let p = d.join("out.plist");
    render(&p, "/Users/x/.local/bin/netmon");

    let lint = Command::new("plutil")
        .args(["-lint"])
        .arg(&p)
        .output()
        .unwrap();
    assert!(
        lint.status.success(),
        "{}",
        String::from_utf8_lossy(&lint.stdout)
    );

    assert_eq!(extract(&p, "Label"), "dev.netmon.sample");
    assert_eq!(extract(&p, "StartInterval"), "120");
    assert_eq!(extract(&p, "ProcessType"), "Background");
    assert_eq!(extract(&p, "LowPriorityIO"), "true");
    let nice: i32 = extract(&p, "Nice").parse().unwrap();
    assert!(nice > 0);
    assert_eq!(
        extract(&p, "ProgramArguments.0"),
        "/Users/x/.local/bin/netmon"
    );
    assert_eq!(extract(&p, "ProgramArguments.1"), "sample");
    assert!(
        !std::fs::read_to_string(&p).unwrap().contains('@'),
        "unrendered placeholder left"
    );
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn render_rejects_relative_binary_paths() {
    let d = tmp("rel");
    let s = Command::new("sh")
        .arg(root().join("scripts/install.sh"))
        .args([
            "--render",
            d.join("o.plist").to_str().unwrap(),
            "--binary",
            "netmon",
        ])
        .output()
        .unwrap();
    assert!(!s.status.success());
    assert!(!d.join("o.plist").exists());
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn dry_run_installs_nothing() {
    let home = tmp("dry-home");
    let o = Command::new("sh")
        .arg(root().join("scripts/install.sh"))
        .args([
            "--dry-run",
            "--source",
            "/bin/sh",
            "--home-macs",
            "aa:bb:cc:dd:ee:ff",
        ])
        .env("HOME", &home)
        .env_remove("NETMON_DIR")
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let out = String::from_utf8(o.stdout).unwrap();
    assert!(out.contains("launchctl bootstrap"), "{out}");
    assert!(
        out.contains("LaunchAgents/dev.netmon.sample.plist"),
        "{out}"
    );
    // HOME is untouched: no LaunchAgents, no binary, no data dir.
    assert_eq!(std::fs::read_dir(&home).unwrap().count(), 0);
    let _ = std::fs::remove_dir_all(home);
}

#[test]
fn install_refuses_without_a_built_binary() {
    let home = tmp("nobin");
    let o = Command::new("sh")
        .arg(root().join("scripts/install.sh"))
        .args(["--dry-run", "--source", "/nonexistent/netmon"])
        .env("HOME", &home)
        .output()
        .unwrap();
    assert!(!o.status.success());
    let _ = std::fs::remove_dir_all(home);
}
