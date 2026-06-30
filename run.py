#!/usr/bin/env python3
"""Convenience launcher for fancyfilesync.

Fill in the options below, then just run:

    python run.py

It calls the same code as the `python -m fancyfilesync` command line, so
anything configurable on the CLI is configurable here.
"""

from fancyfilesync.cli import main

# ===========================================================================
# CONFIGURE ME
# ===========================================================================

# Local directories to scan (fast). Add as many as you like.
LOCAL_DIRS = [
    "/Users/bnorris/DontBackup/syncdatatest",
    # "/another/local/folder",
]

# SSH destination of the remote machine, or an alias from ~/.ssh/config.
REMOTE_HOST = "bnorris@gateway.physics.usyd.edu.au"

# Directories on the remote machine to scan (slow; read-only).
REMOTE_DIRS = [
    "/import/morgana1/snert/barnaby/PL",
    # "/another/remote/path",
]

# Hash algorithm: "sha256" (default), "sha1", or "md5". Must exist on the remote.
ALGO = "sha256"

# Extra ssh options, each passed as `ssh -o OPT`. e.g. ["Port=2222"].
SSH_OPTIONS = []

# Where to also write the full JSON report (None to skip).
JSON_OUTPUT = "result.json"

# Cap entries shown per section in the text report (0 = no limit).
MAX_EXAMPLES = 0

# Colourise the report: "auto" (colour only on a terminal), "always", "never".
COLOR = "auto"

# List every remote file that has no local match. Usually noise on a big remote
# tree, so off by default (the full list is still in the JSON output).
SHOW_REMOTE_ONLY = False

# Print the exact read-only remote commands and exit WITHOUT connecting.
# Set True for a dry-run audit of what would run remotely.
SHOW_PLAN = False

# Suppress progress messages on stderr.
QUIET = False

# ===========================================================================
# (no need to edit below here)
# ===========================================================================


def build_argv():
    argv = []
    for directory in LOCAL_DIRS:
        argv += ["--local", directory]
    argv += ["--remote-host", REMOTE_HOST]
    for directory in REMOTE_DIRS:
        argv += ["--remote-dir", directory]
    argv += ["--algo", ALGO]
    for opt in SSH_OPTIONS:
        argv += ["--ssh-option", opt]
    if JSON_OUTPUT:
        argv += ["--json", JSON_OUTPUT]
    argv += ["--max-examples", str(MAX_EXAMPLES)]
    argv += ["--color", COLOR]
    if SHOW_REMOTE_ONLY:
        argv += ["--show-remote-only"]
    if SHOW_PLAN:
        argv += ["--show-plan"]
    if QUIET:
        argv += ["--quiet"]
    return argv


if __name__ == "__main__":
    raise SystemExit(main(build_argv()))
