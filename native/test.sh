#!/bin/sh
# The tests of the native core (cargo test of every crate but the binding), against the SQLite the interpreter uses.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
. "$here/system-sqlite.sh"
cd "$here"
cargo test --locked -p st-contracts -p st-composition "$@"
