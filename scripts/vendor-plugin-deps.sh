#!/usr/bin/env bash
# Bundle pure-Python dependencies into plugin/py_modules (no compiled extensions, so the
# result works on whichever Python Decky Loader ships). Used by CI and for local testing.
set -euo pipefail
cd "$(dirname "$0")/../plugin"
TARGET="${1:-py_modules}"
python -m pip install --quiet --target "$TARGET" --no-deps --no-binary :all: -r requirements-vendor.txt
find "$TARGET" -name "*.so" -delete
find "$TARGET" -name "__pycache__" -type d -prune -exec rm -rf {} +
python - "$TARGET" <<'PY'
import sys, importlib
sys.path.insert(0, sys.argv[1])
import zeroconf, ifaddr  # noqa: F401
print(f"vendored zeroconf {zeroconf.__version__} + ifaddr into {sys.argv[1]}")
PY
