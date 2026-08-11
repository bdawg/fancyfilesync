# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only duplicate *finder* (not a syncer): it reports which local files also
exist on a remote machine, matching on filename + content rather than path or
mtime. Pure standard library, Python 3.8+. `pytest` is the only dev dependency.

## Commands

```bash
python -m pytest tests/ -q                      # full suite
python -m pytest tests/test_pipeline.py -q      # one file
python -m pytest tests/ -k assume_name_size     # one test

python -m fancyfilesync --local ~/Photos \
  --remote-host user@nas --remote-dir /volume1/media --show-plan
```

`--show-plan` prints the exact remote commands and exits without connecting —
use it to verify any change to remote command construction without a network.

`run.py` and its siblings (`run_PL01.py`, `run_extVsLocal.py`,
`run_glintdatadisk_01.py`) are the user's personal launchers: constants at the
top, `build_argv()` converts them to CLI flags, then calls `cli.main(argv)`.
They are near-duplicates of each other with different paths; adding a CLI flag
means the launchers only pick it up if `build_argv()` is updated too (they have
drifted before — `run_PL01.py` lacks `ASSUME_NAME_SIZE`).

## The read-only guarantee (the central invariant)

`fancyfilesync/remote.py` is the only module that touches the remote host, and
it is structured so remote writes are impossible, not merely unintended:

- `ALLOWED_REMOTE_PROGRAMS` is a frozen set — `find`, `xargs`, `sha256sum`,
  `sha1sum`, `md5sum`. `tests/test_safety.py` asserts its exact contents, so
  changing it fails a test on purpose.
- `_assert_read_only()` gates every path to the remote and raises
  `UnsafeRemoteCommand` for anything unlisted.
- The public surface is only `list_files` and `hash_files`. Both build their
  command from a constant template. Directories are `shlex.quote`d; the file
  list for hashing goes over stdin NUL-delimited via `xargs -0`, so **no path
  ever appears in a remote command string**.

Preserve all three properties in any change here. Do not add a general-purpose
remote-exec helper, and do not interpolate paths into command strings.

## Architecture

Pipeline in `core.find_duplicates`, deliberately shaped to minimise WAN traffic
and remote disk I/O:

1. `local.scan_local` walks local dirs → `{path: size}`.
2. `remote.list_files` runs one `find -printf '%s\t%p\0'` — metadata only.
3. Intersect the two sides on `(basename, size)`. Everything not in that
   intersection is classified with **zero hashing**; this is what keeps the scan
   cheap and is why the same-filename assumption exists.
4. Hash only the candidates — locally via `hashlib`, remotely via `sha*sum`
   *run on the remote*. Only digests cross the network.
5. Group by `(basename, digest)`.

Two modes alter this: `--match-renamed` adds `_find_renamed`, a content-only
second pass over leftover local files (scoped to remote files of a matching
size, reusing first-pass hashes); `--assume-name-size` skips step 4 entirely and
declares name+size matches duplicates unverified. The two are mutually
exclusive — `assume_name_size` wins and `match_renamed` is ignored, since
rename detection requires hashing.

### The duck-typed remote

`local.LocalTarget` implements the same `host` / `is_local` /
`executed_commands` / `list_files` / `hash_files` interface as
`RemoteExecutor`, which is how local-to-local comparison works with no SSH
(omit `--remote-host`). `tests/test_pipeline.py` uses a third implementation,
`FakeRemote`, backed by an in-memory dict — the whole suite runs with no network
and no ssh. **Any change to that interface must be made in all three places**,
and `is_local` is what makes the reports say "location B" instead of "remote".

### Output

`report.py` renders one `ScanResult` three ways — `render_text` (ANSI colour via
`Palette`, plus a compressed directory tree), `render_markdown`, `render_json`.
Text and Markdown honour `--max-examples`; JSON is always complete. When adding
a field to `ScanResult`, all three renderers usually need it.

Progress reporting is threaded through as callbacks (`progress`,
`hash_progress`, `list_progress`) that `cli.py` implements as in-place `\r`
updates on stderr. `cli.py` tracks `listing_active` so a normal message doesn't
overwrite a live status line — keep that bookkeeping intact when adding output.
