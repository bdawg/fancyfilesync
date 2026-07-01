"""Scanning and hashing of local files.

The local filesystem is assumed to be fast, so we read it directly. Hashing uses
the same algorithm names as the remote side (see :data:`fancyfilesync.remote.
HASH_PROGRAMS`) so the digests are directly comparable.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from typing import Dict, Iterable, List, Sequence

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def is_excluded(name: str, patterns: Sequence[str]) -> bool:
    """True if ``name`` (a basename) matches any of the glob ``patterns``."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def scan_local(
    directories: Iterable[str], exclude: Sequence[str] = ()
) -> Dict[str, int]:
    """Walk ``directories`` and return ``{absolute_path: size_bytes}``.

    Files (and directories) whose basename matches an ``exclude`` glob pattern
    are skipped entirely. Symlinks are not followed (we only report real files)
    and unreadable entries are skipped with a warning rather than aborting.
    """
    results: Dict[str, int] = {}
    for directory in directories:
        root = os.path.abspath(directory)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune excluded directories in place so we don't descend into them.
            dirnames[:] = [d for d in dirnames if not is_excluded(d, exclude)]
            for name in filenames:
                if is_excluded(name, exclude):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.islink(path):
                    continue
                try:
                    size = os.path.getsize(path)
                except OSError as exc:
                    _warn(f"cannot stat {path!r}: {exc}")
                    continue
                results[path] = size
    return results


def hash_local_file(path: str, algorithm: str) -> str:
    """Return the hex digest of a local file, read in chunks."""
    hasher = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_local_files(
    paths: List[str], algorithm: str, on_progress=None
) -> Dict[str, str]:
    """Hash many local files, returning ``{path: hex_digest}``.

    Files that cannot be read are skipped with a warning. ``on_progress(done,
    total)`` is called after each file (including skipped ones) so callers can
    show a live counter.
    """
    results: Dict[str, str] = {}
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        try:
            results[path] = hash_local_file(path, algorithm)
        except OSError as exc:
            _warn(f"cannot read {path!r}: {exc}")
        if on_progress is not None:
            on_progress(index, total)
    return results


class LocalTarget:
    """A drop-in replacement for :class:`~fancyfilesync.remote.RemoteExecutor`
    when the second set of directories is also on the local machine.

    It exposes the same ``list_files`` / ``hash_files`` interface the pipeline
    expects, so comparing two local trees needs no other changes. There is no
    SSH and no command execution, so ``executed_commands`` stays empty.
    """

    is_local = True

    def __init__(self) -> None:
        self.host = "(local filesystem)"
        self.executed_commands: List[str] = []

    def list_files(self, directories: Sequence[str]) -> List:
        # Exclusions are applied centrally by the pipeline, so scan everything.
        return [(size, path) for path, size in scan_local(directories).items()]

    def hash_files(
        self, paths: Sequence[str], algorithm: str, on_progress=None
    ) -> Dict[str, str]:
        return hash_local_files(list(paths), algorithm, on_progress=on_progress)


def _warn(message: str) -> None:
    import sys

    print(f"[warning] {message}", file=sys.stderr)
