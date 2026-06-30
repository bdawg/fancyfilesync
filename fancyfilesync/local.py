"""Scanning and hashing of local files.

The local filesystem is assumed to be fast, so we read it directly. Hashing uses
the same algorithm names as the remote side (see :data:`fancyfilesync.remote.
HASH_PROGRAMS`) so the digests are directly comparable.
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, Iterable, List

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def scan_local(directories: Iterable[str]) -> Dict[str, int]:
    """Walk ``directories`` and return ``{absolute_path: size_bytes}``.

    Symlinks are not followed (we only report real files) and unreadable entries
    are skipped with a warning rather than aborting the whole scan.
    """
    results: Dict[str, int] = {}
    for directory in directories:
        root = os.path.abspath(directory)
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
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


def hash_local_files(paths: List[str], algorithm: str) -> Dict[str, str]:
    """Hash many local files, returning ``{path: hex_digest}``.

    Files that cannot be read are skipped with a warning.
    """
    results: Dict[str, str] = {}
    for path in paths:
        try:
            results[path] = hash_local_file(path, algorithm)
        except OSError as exc:
            _warn(f"cannot read {path!r}: {exc}")
    return results


def _warn(message: str) -> None:
    import sys

    print(f"[warning] {message}", file=sys.stderr)
