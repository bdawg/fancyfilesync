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
    "/Users/bnorris/DontBackup/PL_data",
    # "/another/local/folder",
]

# SSH destination of the remote machine, or an alias from ~/.ssh/config.
# Set to None (or "") to compare against a SECOND LOCAL directory set instead
# of a remote machine (REMOTE_DIRS are then treated as local paths, no SSH).
REMOTE_HOST = None #"bnorris@gateway.physics.usyd.edu.au"

# Directories to compare against (on the remote machine, or local if
# REMOTE_HOST is None). Remote access is read-only.
REMOTE_DIRS = [
    "/Volumes/ExtSSD/PL_slices",
    "/Volumes/ExtSSD/PL_labdata",
    "/Volumes/ExtSSD/PL_data"
    # "/import/morgana1/snert/barnaby/PL",
    # "/import/roci1/bnorris/PL",
]
# Hash algorithm: "sha256" (default), "sha1", or "md5". Must exist on the remote.
ALGO = "sha256"

# Glob patterns to skip on BOTH sides (matched on file/directory name). Handy
# for junk that pollutes the scan. Add more as needed, e.g. "*.tmp", ".git".
EXCLUDE = [
    ".DS_Store",
]

# Extra ssh options, each passed as `ssh -o OPT`. e.g. ["Port=2222"].
SSH_OPTIONS = []

# Where to also write the full JSON report (None to skip).
JSON_OUTPUT = "result.json"

# Where to also write a formatted Markdown report (None to skip).
MD_OUTPUT = "report.md"

# Cap entries shown per section in the text report (0 = no limit).
MAX_EXAMPLES = 0

# Colourise the report: "auto" (colour only on a terminal), "always", "never".
COLOR = "auto"

# Instead of the full report, print a short summary of the local files with NO
# match on the other side, grouped by the directory holding them (rather than
# listed file by file), flagging whether each directory is entirely unmatched
# (copy it wholesale) or also holds files that are already on the other side.
# Applies to the text and Markdown output; the JSON output is unaffected.
LOCAL_ONLY_DIRS = False

# List every remote file that has no local match. Usually noise on a big remote
# tree, so off by default (the full list is still in the JSON output).
SHOW_REMOTE_ONLY = False

# Also detect RENAMED duplicates: unmatched local files that are byte-for-byte
# copies of a remote file under a different name (matched by size then content).
MATCH_RENAMED = False

# Treat files sharing a filename AND size as duplicates WITHOUT hashing to
# confirm. Fast (no file contents read on either side) but UNVERIFIED: different
# files with the same name and size will be falsely reported as duplicates.
# Ignores MATCH_RENAMED. The report prints a warning when this is on.
ASSUME_NAME_SIZE = False

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
    if REMOTE_HOST:
        argv += ["--remote-host", REMOTE_HOST]
    for directory in REMOTE_DIRS:
        argv += ["--remote-dir", directory]
    argv += ["--algo", ALGO]
    for pattern in EXCLUDE:
        argv += ["--exclude", pattern]
    for opt in SSH_OPTIONS:
        argv += ["--ssh-option", opt]
    if JSON_OUTPUT:
        argv += ["--json", JSON_OUTPUT]
    if MD_OUTPUT:
        argv += ["--md", MD_OUTPUT]
    argv += ["--max-examples", str(MAX_EXAMPLES)]
    argv += ["--color", COLOR]
    if SHOW_REMOTE_ONLY:
        argv += ["--show-remote-only"]
    if LOCAL_ONLY_DIRS:
        argv += ["--local-only-dirs"]
    if MATCH_RENAMED:
        argv += ["--match-renamed"]
    if ASSUME_NAME_SIZE:
        argv += ["--assume-name-size"]
    if SHOW_PLAN:
        argv += ["--show-plan"]
    if QUIET:
        argv += ["--quiet"]
    return argv


if __name__ == "__main__":
    raise SystemExit(main(build_argv()))
