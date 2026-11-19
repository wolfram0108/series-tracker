# Sourced by build.sh and test.sh: the core links the SQLite the interpreter uses — the one `_sqlite3` of the Python of the
# service is linked with — through a link of its own name in the build folder (the system holds the library, not the
# name a linker looks for).
library="$("${PYO3_PYTHON:-$root/.venv/bin/python}" -c 'import _sqlite3; print(_sqlite3.__file__)' | xargs ldd \
    | awk '/libsqlite3\.so/ {print $3; exit}')"
[ -n "$library" ] || { echo "the SQLite of the interpreter is not found" >&2; exit 1; }
mkdir -p "$here/target/system-sqlite"
ln -sf "$library" "$here/target/system-sqlite/libsqlite3.so"
export SQLITE3_LIB_DIR="$here/target/system-sqlite"
export SQLITE3_STATIC=0
