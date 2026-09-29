#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
    printf '%s\n' 'Bitte zuerst die manuelle Installation aus README.md ausführen.'
    exit 1
fi
exec .venv/bin/python bot.py
