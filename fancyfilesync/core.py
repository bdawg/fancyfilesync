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
import time
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
class RenamedGroup:
    """Files with identical content but *different* filenames.

    These are only discovered when ``match_renamed`` is enabled, since by
    default a differing filename rules a match out entirely.
    """

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

    # Files proven identical across the two machines (same name and content).
    duplicate_groups: List[DuplicateGroup]

    # Local files with no identical copy on the remote.
    local_only: List[str]
    # Remote files with no identical copy locally.
    remote_only: List[str]

    # Renamed duplicates: identical content under a different filename. Only
    # populated when match_renamed is enabled.
    renamed_groups: List[RenamedGroup] = field(default_factory=list)
    # Whether the rename-detection pass actually ran (so the report can say
    # "checked, none found" rather than staying silent).
    renamed_checked: bool = False

    # Glob patterns excluded from both the local and remote scans.
    exclude: List[str] = field(default_factory=list)

    # How many files actually had to be hashed on each side, for transparency.
    local_files_hashed: int = 0
    remote_files_hashed: int = 0

    # Wall-clock time spent hashing on each side, and the overall run time, in
    # seconds. Hash times cover both the main pass and the rename pass.
    local_hash_seconds: float = 0.0
    remote_hash_seconds: float = 0.0
    total_seconds: float = 0.0

    # The exact commands issued on the remote host (audit trail).
    remote_commands: List[str] = field(default_factory=list)


def find_duplicates(
    local_dirs: Sequence[str],
    remote: RemoteExecutor,
    remote_dirs: Sequence[str],
    algorithm: str = "sha256",
    match_renamed: bool = False,
    exclude: Sequence[str] = (),
    progress=None,
) -> ScanResult:
    """Run the full pipeline and return a :class:`ScanResult`.

    When ``match_renamed`` is True, a second pass takes the local files that the
    name-based pass left unmatched and looks for content matches among remote
    files of the *same size, regardless of name*. This catches renamed copies.
    The extra hashing is scoped to those leftover files only, so the remote
    workload stays small.

    ``progress`` is an optional callable taking a single status string; pass a
    printer to get live feedback during long scans.
    """

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    start_total = time.monotonic()
    # Accumulates hashing time across the main pass and the rename pass.
    timings = {"local_hash": 0.0, "remote_hash": 0.0}

    exclude = list(exclude)

    report("Scanning local files...")
    local_files = local_mod.scan_local(local_dirs, exclude=exclude)
    report(f"  found {len(local_files)} local files")

    report("Listing remote files (metadata only)...")
    remote_listing = remote.list_files(remote_dirs)
    # Apply the same exclusions to the remote side. A pattern matches if any
    # path component matches it, so excluding e.g. ".git" drops everything under
    # any .git directory, mirroring how the local walk prunes directories. The
    # remote listing is metadata-only and cheap, so filtering here (rather than
    # in the remote command) keeps the read-only command set unchanged.
    if exclude:
        remote_listing = [
            (size, path)
            for size, path in remote_listing
            if not any(
                local_mod.is_excluded(part, exclude)
                for part in path.split("/")
                if part
            )
        ]
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
    _t = time.monotonic()
    local_hashes = local_mod.hash_local_files(local_to_hash, algorithm)
    timings["local_hash"] += time.monotonic() - _t

    report("Hashing candidate remote files (on the remote machine)...")
    _t = time.monotonic()
    remote_hashes = remote.hash_files(remote_to_hash, algorithm)
    timings["remote_hash"] += time.monotonic() - _t

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

    renamed_groups: List[RenamedGroup] = []
    if match_renamed:
        renamed_groups = _find_renamed(
            local_files=local_files,
            remote_files=remote_files,
            matched_local=matched_local,
            matched_remote=matched_remote,
            local_hashes=local_hashes,
            remote_hashes=remote_hashes,
            remote=remote,
            algorithm=algorithm,
            report=report,
            timings=timings,
        )

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
        renamed_groups=renamed_groups,
        renamed_checked=match_renamed,
        exclude=exclude,
        local_files_hashed=len(local_hashes),
        remote_files_hashed=len(remote_hashes),
        local_hash_seconds=timings["local_hash"],
        remote_hash_seconds=timings["remote_hash"],
        total_seconds=time.monotonic() - start_total,
        remote_commands=list(remote.executed_commands),
    )


def _find_renamed(
    local_files: Dict[str, int],
    remote_files: Dict[str, int],
    matched_local: set,
    matched_remote: set,
    local_hashes: Dict[str, str],
    remote_hashes: Dict[str, str],
    remote: RemoteExecutor,
    algorithm: str,
    report,
    timings: Dict[str, float],
) -> List[RenamedGroup]:
    """Second pass: match leftover local files to remote files by content only.

    Mutates ``matched_local`` / ``matched_remote`` / the hash caches so the
    caller's downstream stats and local_only/remote_only lists stay correct.
    ``timings`` is updated with any additional hashing time.
    """
    remaining_local = [p for p in local_files if p not in matched_local]
    if not remaining_local:
        return []

    # The only remote files worth considering are those whose size matches some
    # leftover local file -- a same-size requirement keeps the extra remote
    # hashing minimal even on a huge remote tree.
    wanted_sizes = {local_files[p] for p in remaining_local}
    remote_candidates = [
        path for path, size in remote_files.items() if size in wanted_sizes
    ]
    report(
        f"Rename pass: {len(remaining_local)} unmatched local files; "
        f"{len(remote_candidates)} remote files share a size and will be hashed"
    )

    # Hash whatever isn't already hashed from the first pass (reuse the rest).
    local_to_hash = [p for p in remaining_local if p not in local_hashes]
    if local_to_hash:
        report("Hashing leftover local files...")
        _t = time.monotonic()
        local_hashes.update(local_mod.hash_local_files(local_to_hash, algorithm))
        timings["local_hash"] += time.monotonic() - _t
    remote_to_hash = [p for p in remote_candidates if p not in remote_hashes]
    if remote_to_hash:
        report("Hashing extra remote candidates (on the remote machine)...")
        _t = time.monotonic()
        remote_hashes.update(remote.hash_files(remote_to_hash, algorithm))
        timings["remote_hash"] += time.monotonic() - _t

    # Group by content alone (name ignored).
    local_by_digest: Dict[str, List[str]] = defaultdict(list)
    for path in remaining_local:
        digest = local_hashes.get(path)
        if digest is not None:
            local_by_digest[digest].append(path)
    remote_by_digest: Dict[str, List[str]] = defaultdict(list)
    for path in remote_candidates:
        digest = remote_hashes.get(path)
        if digest is not None:
            remote_by_digest[digest].append(path)

    renamed_groups: List[RenamedGroup] = []
    for digest in set(local_by_digest) & set(remote_by_digest):
        local_paths = sorted(local_by_digest[digest])
        remote_paths = sorted(remote_by_digest[digest])
        size = local_files[local_paths[0]]
        renamed_groups.append(
            RenamedGroup(
                digest=digest,
                size=size,
                local_paths=local_paths,
                remote_paths=remote_paths,
            )
        )
        matched_local.update(local_paths)
        matched_remote.update(remote_paths)

    renamed_groups.sort(key=lambda g: g.size, reverse=True)
    return renamed_groups
