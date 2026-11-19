#!/bin/sh
# The native core is built on the server from git, as the front end is: the library goes beside the code that imports it
# (core/_native.so), never into the repository. `--locked`: the versions of Cargo.lock, nothing newer by surprise.
#
# The core reads the base with the SQLite the interpreter itself uses (the system's libsqlite3.so.0), never a copy of its
# own: two copies of SQLite in one process do not see each other's locks of a base in WAL, and one of them can unmap the
# memory of the journal under the other (a crash, and by the SQLite documentation a corrupt base).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
python="${PYO3_PYTHON:-$root/.venv/bin/python}"
. "$here/system-sqlite.sh"
cd "$here"
PYO3_PYTHON="$python" cargo build --release --locked -p st-native
cp "$here/target/release/lib_native.so" "$root/core/_native.so.new"
mv "$root/core/_native.so.new" "$root/core/_native.so"
echo "native core built: $root/core/_native.so"
