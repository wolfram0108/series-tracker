#!/bin/bash
# Builds core.native (the count of the data of a series, this crate) and puts it where the service imports it. Run after
# every pull that changes native/, before the restart: `native/build.sh` (as the owner of the checkout).
set -euo pipefail
cd "$(dirname "$0")"
cargo build --release --quiet
install -m 755 target/release/libnative.so ../core/native.abi3.so
echo "core/native.abi3.so built"
