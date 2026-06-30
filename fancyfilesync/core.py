"""The duplicate-detection pipeline.

Strategy (designed to do the minimum possible work on the slow remote link):

1. List local files + sizes (cheap, local).
2. List remote files + sizes with a single ``find`` (cheap; metadata only).
3. By assumption, duplicates always share the same *filename* (basename), though
   they may live at different paths. So a file can only be a duplicate of another
   file with the **same name and the same size**. We intersect the two sides on
   the ``(basename, size)`` key; everything else is classified with no hashing.
4. Hash the candidates: locally with hashlib, remotely with ``sha256sum`` run on
   the remote host. Only hash strings cross the network.
5. Group by ``(basename, digest)``. Files sharing a name and a digest across the
   two sides are duplicates.

Because matching is gated on filename first, we hash only the (typically tiny)
set of files that share both a name and a size across the two machines, and we
never transfer file contents over the WAN.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from . import local as local_mod
from .remote import RemoteExecutor


def _local_basename(path: str) -> str:
    return os.path.basename(path)


def _remote_basename(path: str) -> str:
    # Remote paths are POSIX-style regardless of the local OS.
    return path.rsplit("/", 1)[-1]


@dataclass
class DuplicateGroup:
    """A set of byte-for-byte identical files spanning local and remote.

    All paths in a group share the same filename (:attr:`name`) and the same
    content (:attr:`digest`).
    """

    name: str
    digest: str
    size: int
    local_paths: List[str]
    remote_paths: List[str]


@dataclass
class ScanResult:
    """The full outcome of a duplicate scan."""

    algorithm: str
    local_roots: List[str]
    remote_host: str
    remote_roots: List[str]

    # All local/remote files discovered, as {path: size}.
    local_files: Dict[str, int]
    remote_files: Dict[str, int]

    # Files proven identical across the two machines.
    duplicate_groups: List[DuplicateGroup]

    # Local files with no identical copy on the remote.
    local_only: List[str]
    # Remote files with no identical copy locally.
    remote_only: List[str]

    # How many files actually had to be hashed on each side, for transparency.
    local_files_hashed: int = 0
    remote_files_hashed: int = 0

    # The exact commands issued on the remote host (audit trail).
    remote_commands: List[str] = field(default_factory=list)


def find_duplicates(
    local_dirs: Sequence[str],
    remote: RemoteExecutor,
    remote_dirs: Sequence[str],
    algorithm: str = "sha256",
    progress=None,
) -> ScanResult:
    """Run the full pipeline and return a :class:`ScanResult`.

    ``progress`` is an optional callable taking a single status string; pass a
    printer to get live feedback during long scans.
    """

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    report("Scanning local files...")
    local_files = local_mod.scan_local(local_dirs)
    report(f"  found {len(local_files)} local files")

    report("Listing remote files (metadata only)...")
    remote_listing = remote.list_files(remote_dirs)
    remote_files: Dict[str, int] = {path: size for size, path in remote_listing}
    report(f"  found {len(remote_files)} remote files")

    # Group paths by (filename, size) on each side. Duplicates are assumed to
    # share a filename, so only files matching on both name and size can
    # possibly be duplicates -- everything else is ruled out for free.
    local_by_key: Dict[tuple, List[str]] = defaultdict(list)
    for path, size in local_files.items():
        local_by_key[(_local_basename(path), size)].append(path)
    remote_by_key: Dict[tuple, List[str]] = defaultdict(list)
    for path, size in remote_files.items():
        remote_by_key[(_remote_basename(path), size)].append(path)

    candidate_keys = set(local_by_key) & set(remote_by_key)

    local_to_hash = [
        path for key in candidate_keys for path in local_by_key[key]
    ]
    remote_to_hash = [
        path for key in candidate_keys for path in remote_by_key[key]
    ]
    report(
        f"Name+size pre-filter: {len(local_to_hash)} local and "
        f"{len(remote_to_hash)} remote files share a filename and size "
        f"and need hashing"
    )

    report("Hashing candidate local files...")
    local_hashes = local_mod.hash_local_files(local_to_hash, algorithm)

    report("Hashing candidate remote files (on the remote machine)...")
    remote_hashes = remote.hash_files(remote_to_hash, algorithm)

    # Group hashed files by (filename, digest). Requiring the filename to match
    # here too means two files with identical content but different names are
    # never reported as duplicates, per the stated assumption.
    local_by_nd: Dict[tuple, List[str]] = defaultdict(list)
    for path, digest in local_hashes.items():
        local_by_nd[(_local_basename(path), digest)].append(path)
    remote_by_nd: Dict[tuple, List[str]] = defaultdict(list)
    for path, digest in remote_hashes.items():
        remote_by_nd[(_remote_basename(path), digest)].append(path)

    duplicate_groups: List[DuplicateGroup] = []
    matched_local: set = set()
    matched_remote: set = set()
    for key in set(local_by_nd) & set(remote_by_nd):
        name, digest = key
        local_paths = sorted(local_by_nd[key])
        remote_paths = sorted(remote_by_nd[key])
        size = local_files[local_paths[0]]
        duplicate_groups.append(
            DuplicateGroup(
                name=name,
                digest=digest,
                size=size,
                local_paths=local_paths,
                remote_paths=remote_paths,
            )
        )
        matched_local.update(local_paths)
        matched_remote.update(remote_paths)

    duplicate_groups.sort(key=lambda g: g.size, reverse=True)

    local_only = sorted(p for p in local_files if p not in matched_local)
    remote_only = sorted(p for p in remote_files if p not in matched_remote)

    return ScanResult(
        algorithm=algorithm,
        local_roots=[str(d) for d in local_dirs],
        remote_host=remote.host,
        remote_roots=[str(d) for d in remote_dirs],
        local_files=local_files,
        remote_files=remote_files,
        duplicate_groups=duplicate_groups,
        local_only=local_only,
        remote_only=remote_only,
        local_files_hashed=len(local_hashes),
        remote_files_hashed=len(remote_hashes),
        remote_commands=list(remote.executed_commands),
    )
