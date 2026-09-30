#!/usr/bin/env bash
# pyGigaSt environment setup for Linux / macOS (and Git Bash on Windows).
#
# Usage:
#   ./setup.sh                    use PYGIGAST_PYTHON, or python3 on PATH
#   ./setup.sh /path/to/python3   use this Python as the base
#
# Creates ./venv (virtualenv if available, otherwise venv) and installs requirements.txt.
set -euo pipefail
cd "$(dirname "$0")"

PY="${1:-${PYGIGAST_PYTHON:-python3}}"

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 13) else 1)' >/dev/null 2>&1; then
    echo "Python 3.13 or later was not found. Pass the path: ./setup.sh /path/to/python3" >&2
    exit 1
fi
echo "Base Python: $PY"

venv_python() {
    if [ -x venv/bin/python ]; then echo venv/bin/python; else echo venv/Scripts/python.exe; fi
}

if [ ! -x venv/bin/python ] && [ ! -f venv/Scripts/python.exe ]; then
    if "$PY" -m virtualenv --version >/dev/null 2>&1; then
        echo "Creating venv with virtualenv..."
        "$PY" -m virtualenv venv
    else
        echo "Creating venv with the venv module..."
        "$PY" -m venv venv
    fi
fi

VPY="$(venv_python)"
echo "Installing packages..."
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt
echo
echo "Done. Python for this project: $(pwd)/$VPY"
