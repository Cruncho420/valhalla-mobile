#!/bin/bash
set -euo pipefail
exec python3 scripts/package_apple_native.py --repo "$(pwd)"
