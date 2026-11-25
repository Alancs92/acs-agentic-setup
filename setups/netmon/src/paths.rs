//! Data directory location and owner-only permissions (dir 0700, files 0600).

use std::fs::{self, DirBuilder, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

/// `NETMON_DIR` if set (tests, manual runs), else
/// `~/Library/Application Support/netmon/`.
pub fn data_dir() -> io::Result<PathBuf> {
    if let Some(d) = std::env::var_os("NETMON_DIR") {
        return Ok(PathBuf::from(d));
    }
    let home = std::env::var_os("HOME")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "HOME is not set"))?;
    Ok(PathBuf::from(home)
        .join("Library")
        .join("Application Support")
        .join("netmon"))
}

/// Create `dir` (and parents) with mode 0700 and tighten it if it already
/// exists with a looser mode.
pub fn ensure_dir(dir: &Path) -> io::Result<()> {
    DirBuilder::new().recursive(true).mode(0o700).create(dir)?;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o700))
}

/// Set a file to 0600.
pub fn tighten_file(path: &Path) -> io::Result<()> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
}

/// Create/truncate `path` with mode 0600 and write `contents`.
pub fn write_private(path: &Path, contents: &[u8]) -> io::Result<()> {
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(contents)?;
    // `mode` only applies on creation; make sure an older, looser file is fixed.
    tighten_file(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;

    fn mode(p: &Path) -> u32 {
        fs::metadata(p).unwrap().permissions().mode() & 0o777
    }

    #[test]
    fn creates_dir_0700() {
        let t = TempDir::new("paths-create");
        let d = t.path().join("a").join("netmon");
        ensure_dir(&d).unwrap();
        assert_eq!(mode(&d), 0o700);
    }

    #[test]
    fn tightens_existing_0755_dir() {
        let t = TempDir::new("paths-tighten");
        let d = t.path().join("netmon");
        fs::create_dir(&d).unwrap();
        fs::set_permissions(&d, fs::Permissions::from_mode(0o755)).unwrap();
        ensure_dir(&d).unwrap();
        assert_eq!(mode(&d), 0o700);
    }

    #[test]
    fn private_files_are_0600_even_if_preexisting_0644() {
        let t = TempDir::new("paths-file");
        let f = t.path().join("config");
        write_private(&f, b"x").unwrap();
        assert_eq!(mode(&f), 0o600);
        fs::set_permissions(&f, fs::Permissions::from_mode(0o644)).unwrap();
        write_private(&f, b"y").unwrap();
        assert_eq!(mode(&f), 0o600);
        assert_eq!(fs::read(&f).unwrap(), b"y");
    }

    #[test]
    fn netmon_dir_env_overrides_default() {
        // Only this test touches NETMON_DIR inside the unit-test binary.
        std::env::set_var("NETMON_DIR", "/tmp/netmon-env-test");
        assert_eq!(data_dir().unwrap(), PathBuf::from("/tmp/netmon-env-test"));
        std::env::remove_var("NETMON_DIR");
        assert!(data_dir()
            .unwrap()
            .ends_with("Library/Application Support/netmon"));
    }
}
