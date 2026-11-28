#!/bin/sh
# One command for everything that must hold before netmon is installed or merged:
#   fmt --check, clippy -D warnings, cargo test, and the RELEASE budget tests
#   (RSS < 5 MB, CPU < 50 ms, DB size, 1-year projection). Stops at the first failure.
#
#   scripts/check.sh
set -eu
cd "$(dirname "$0")/.."

step() { echo; echo "==> $*"; }

step "cargo fmt --check"
cargo fmt --check

step "cargo clippy --all-targets -- -D warnings"
cargo clippy --all-targets -- -D warnings

step "cargo test"
cargo test

step "release budgets (cargo test --release --test budget -- --ignored --nocapture)"
cargo test --release --test budget -- --ignored --nocapture --test-threads=1

echo
echo "check.sh: all checks passed"
