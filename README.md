# fancyfilesync

Find local files that are **duplicated on a remote machine**, even when the
remote copies live in a different directory structure or carry different
modification times. Duplicates are assumed to keep the **same filename**;
matching is by *filename + content*, not by path or date.

> Synchronisation is **not** part of this stage. This tool only *reports* what
> is duplicated. It is also strictly **read-only on the remote machine** (see
> [The read-only guarantee](#the-read-only-guarantee)).

## Why it's built this way

Two constraints shaped the design:

1. **The remote link is slow and the files are huge (hundreds of GB).** So file
   contents must never be copied across the network just to compare them.
   Instead, hashes of remote files are computed **on the remote machine** with
   `sha256sum`, and only the short hash strings come back.

2. **The remote must never be written to.** The set of programs that can run
   remotely is a tiny, frozen allowlist — all read-only — and every remote
   invocation is checked against it. There is no code path that can create,
   move, modify or delete remote data.

## How it works

Duplicates are assumed to **share a filename** (they may live at different
paths). That assumption lets the tool rule out almost everything for free:

1. Scan local files and record their names + sizes (fast, local).
2. List remote files with their names + sizes via a single `find` — **metadata
   only**, no contents transferred.
3. A file can only be a duplicate of another file with the **same filename and
   the same size**. Everything else — different name, or same name but different
   size — is classified immediately, with **zero hashing**.
4. Hash only those name+size candidates: locally with Python's `hashlib`,
   remotely with `sha256sum` *run on the remote host*.
5. Group by `(filename, hash)`. Files sharing a name and a hash across the two
   machines are byte-for-byte duplicates. (Identical content under a *different*
   name is deliberately **not** reported as a match.)

This keeps both network transfer and remote disk I/O to the minimum needed.

## Requirements

- Python 3.8+ (standard library only; no third-party packages to run it).
- An `ssh` client on your machine, with key-based access to the remote host
  (the tool uses `BatchMode=yes` and will not prompt for a password).
- The remote host is assumed to be Linux with GNU coreutils (`find -printf`,
  `sha256sum`). Hashing happens there.
- `pytest` is only needed to run the test suite.

## Usage

```bash
python -m fancyfilesync \
  --local ~/Photos --local ~/Videos \
  --remote-host user@nas \
  --remote-dir /volume1/media \
  --json result.json
```

Key options:

| Option | Meaning |
| --- | --- |
| `--local DIR` | A local directory to scan (repeatable). |
| `--remote-host [user@]host` | SSH destination, or an `ssh_config` alias. |
| `--remote-dir DIR` | A remote directory to scan (repeatable). |
| `--algo {sha256,sha1,md5}` | Hash algorithm (default `sha256`). |
| `--ssh-option OPT` | Extra `ssh -o OPT`, e.g. `--ssh-option Port=2222`. |
| `--json FILE` | Also write the complete result as JSON. |
| `--max-examples N` | Cap entries shown per section in the text report. |
| `--color {auto,always,never}` | Colourise the report (`auto` = colour only on a terminal). |
| `--show-plan` | Print the exact read-only remote commands and exit **without connecting**. |
| `--quiet` | Suppress progress output. |

### See exactly what would run remotely

```bash
python -m fancyfilesync --local ~/Photos \
  --remote-host user@nas --remote-dir /volume1/media --show-plan
```

Prints the precise commands (and exits without contacting the remote), e.g.:

```
find /volume1/media -type f -printf '%s\t%p\0'
xargs -0 sha256sum
```

The text report also lists every remote command it actually executed.

## The read-only guarantee

All remote access lives in [`fancyfilesync/remote.py`](fancyfilesync/remote.py)
and is constrained on three levels:

1. **A frozen allowlist** — `ALLOWED_REMOTE_PROGRAMS` is the complete set of
   programs that may ever run remotely: `find`, `xargs`, `sha256sum`,
   `sha1sum`, `md5sum`. Every one only reads data.
2. **A single choke point** — all remote execution goes through
   `RemoteExecutor._run_remote`, which refuses (raising `UnsafeRemoteCommand`)
   to run anything whose program isn't on the allowlist.
3. **No arbitrary-command API** — the only public operations are `list_files`
   and `hash_files`. They build their commands from constant templates; the
   only dynamic values are paths, which are shell-quoted (search directories)
   or passed NUL-delimited over stdin (files to hash), so nothing can be
   injected.

These guarantees are enforced by tests in
[`tests/test_safety.py`](tests/test_safety.py).

> **Defence in depth:** the client being incapable of writing is strong, but if
> the remote data is precious, also enforce read-only on the remote side — e.g.
> a forced-command/read-only SSH key, a read-only bind mount, or a dedicated
> account without write permission to the data.

## Running the tests

```bash
python -m pytest tests/ -q
```

The suite covers the read-only allowlist enforcement, command quoting, output
parsing, and a full end-to-end run against an in-memory fake remote (including
the case where two files share a size but differ in content).

## Roadmap

- Synchronisation of missing/changed files (a later, separate, explicitly
  opt-in stage — the remote stays read-only until then).
- Optional partial/head hashing to further reduce remote disk I/O on large
  size-collision groups.
- BSD/macOS remote support (currently assumes GNU coreutils).
