#!/usr/bin/env python3
"""Resolve the pinned Impeccable engine to a verified local binary.

Provenance lives in engine.lock.json (committed). The binary itself is not
committed. Fails closed: missing, empty, or mismatched integrity aborts
without writing anything.

Primary route is the platform npm package, whose immutable version and
dist.integrity sha512 are a stronger provenance root than a mutable GitHub
release asset. See design.md, Evidence 3.
"""
import argparse
import base64
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEFAULT_LOCK = Path(__file__).with_name("engine.lock.json")
PKG_PREFIX = "@impeccable/cli-"


def platform_target() -> str:
    """The <os>-<arch> string npm and the upstream shim both use."""
    os_name = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(
        platform.machine(), platform.machine()
    )
    return f"{os_name}-{arch}"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tarball(data: bytes, integrity: str) -> bool:
    """Check bytes against an npm Subresource Integrity string (sha512-<b64>)."""
    if not integrity or "-" not in integrity:
        return False
    algo, _, b64 = integrity.partition("-")
    if algo != "sha512":
        return False
    try:
        expected = base64.b64decode(b64, validate=True)
    except Exception:
        return False
    return hashlib.sha512(data).digest() == expected


def verify_binary(path, expected_sha256: str) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    return sha256_file(p) == expected_sha256


def load_lock(path=DEFAULT_LOCK) -> dict:
    return json.loads(Path(path).read_text())


def npm_integrity(pkg: str, version: str, runner=subprocess.run) -> str:
    """Ask the registry for the published dist.integrity of an exact version."""
    out = runner(
        ["npm", "view", f"{pkg}@{version}", "dist.integrity", "--json"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return json.loads(out) if out.startswith('"') else out


def resolve(lock: dict, dest_dir, *, runner=subprocess.run) -> Path:
    """Fetch, verify and install the locked engine. Returns the binary path."""
    target = lock.get("platform") or platform_target()
    pkg = f"{PKG_PREFIX}{target}"
    version = lock["engine_version"]
    dest = Path(dest_dir) / version / "impeccable"

    if dest.is_file() and verify_binary(dest, lock.get("binary_sha256", "")):
        return dest

    integrity = npm_integrity(pkg, version, runner=runner)
    with tempfile.TemporaryDirectory() as tmp:
        runner(["npm", "pack", f"{pkg}@{version}", "--silent"],
               cwd=tmp, capture_output=True, text=True, check=True)
        tarballs = list(Path(tmp).glob("*.tgz"))
        if len(tarballs) != 1:
            raise SystemExit(f"expected one tarball, got {len(tarballs)}")
        data = tarballs[0].read_bytes()
        if not verify_tarball(data, integrity):
            raise SystemExit(
                f"integrity mismatch for {pkg}@{version}; "
                "refusing the unverified download"
            )
        with tarfile.open(tarballs[0]) as tf:
            member = tf.extractfile("package/bin/impeccable")
            if member is None:
                raise SystemExit("tarball has no package/bin/impeccable")
            payload = member.read()

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".part")
    tmp_path.write_bytes(payload)
    tmp_path.chmod(0o755)
    tmp_path.replace(dest)

    expected = lock.get("binary_sha256")
    actual = sha256_file(dest)
    if expected and expected != actual:
        dest.unlink()
        raise SystemExit(f"binary sha256 mismatch: locked {expected}, got {actual}")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", default=DEFAULT_LOCK, type=Path)
    ap.add_argument("--dest", default=Path.home() / ".impeccable" / "bin", type=Path)
    ap.add_argument("--print-export", action="store_true",
                    help="print the IMPECCABLE_BIN export line and exit")
    ap.add_argument("--print-sha256", action="store_true",
                    help="print the resolved binary's sha256 (for filling the lock)")
    args = ap.parse_args(argv)

    try:
        lock = load_lock(args.lock)
    except (OSError, json.JSONDecodeError) as err:
        print(f"error: cannot read lock {args.lock}: {err}", file=sys.stderr)
        return 1

    try:
        binary = resolve(lock, args.dest)
    except SystemExit as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as err:
        print(f"error: npm failed: {err.stderr or err}", file=sys.stderr)
        return 1

    if args.print_sha256:
        print(sha256_file(binary))
    elif args.print_export:
        print(f'export IMPECCABLE_BIN="{binary}"')
    else:
        print(f"engine {lock['engine_version']} verified at {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
