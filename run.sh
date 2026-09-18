#!/usr/bin/env bash
# The long-running process. systemd keeps this alive as app@race-os.
set -euo pipefail
exec python3 -m http.server 8080 --bind 0.0.0.0 --directory public
