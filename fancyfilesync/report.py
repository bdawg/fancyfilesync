"""Human-readable and machine-readable rendering of a :class:`ScanResult`."""

from __future__ import annotations

import json
from typing import List

from .core import ScanResult


def human_size(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def render_text(result: ScanResult, max_examples: int = 0) -> str:
    """Render a clear, scannable text report.

    ``max_examples`` limits how many entries are listed under the
    duplicate / local-only / remote-only sections (0 = no limit).
    """
    lines: List[str] = []
    add = lines.append

    def total_size(paths, lookup) -> int:
        return sum(lookup.get(p, 0) for p in paths)

    local_total = sum(result.local_files.values())
    remote_total = sum(result.remote_files.values())

    dup_local_paths = [p for g in result.duplicate_groups for p in g.local_paths]
    dup_bytes = sum(g.size * len(g.local_paths) for g in result.duplicate_groups)
    local_only_bytes = total_size(result.local_only, result.local_files)

    add("=" * 70)
    add("  DUPLICATE FILE REPORT")
    add("=" * 70)
    add(f"  Local roots : {', '.join(result.local_roots)}")
    add(f"  Remote      : {result.remote_host}")
    add(f"  Remote roots: {', '.join(result.remote_roots)}")
    add(f"  Hash algo   : {result.algorithm}")
    add("")
    add("  SUMMARY")
    add("  " + "-" * 66)
    add(
        f"  Local files scanned       : {len(result.local_files):>8}  "
        f"({human_size(local_total)})"
    )
    add(
        f"  Remote files scanned      : {len(result.remote_files):>8}  "
        f"({human_size(remote_total)})"
    )
    add(
        f"  Files hashed (local/remote): {result.local_files_hashed:>7}"
        f" / {result.remote_files_hashed}"
    )
    add("")
    add(
        f"  Duplicate groups          : {len(result.duplicate_groups):>8}"
    )
    add(
        f"  Local files WITH a remote copy : {len(dup_local_paths):>8}  "
        f"({human_size(dup_bytes)})"
    )
    add(
        f"  Local files with NO remote copy: {len(result.local_only):>8}  "
        f"({human_size(local_only_bytes)})"
    )
    add(
        f"  Remote files not matched       : {len(result.remote_only):>8}"
    )
    add("")

    add("  DUPLICATES  (identical contents, local <-> remote)")
    add("  " + "-" * 66)
    if not result.duplicate_groups:
        add("    (none)")
    else:
        shown = _limit(result.duplicate_groups, max_examples)
        for group in shown:
            add(
                f"  {group.name}  [{human_size(group.size)}]  "
                f"{group.digest[:16]}..."
            )
            for path in group.local_paths:
                add(f"      local  : {path}")
            for path in group.remote_paths:
                add(f"      remote : {path}")
            add("")
        _maybe_more(add, len(result.duplicate_groups), len(shown), "groups")
    add("")

    add("  LOCAL FILES WITH NO REMOTE DUPLICATE")
    add("  " + "-" * 66)
    if not result.local_only:
        add("    (none)")
    else:
        shown = _limit(result.local_only, max_examples)
        for path in shown:
            add(f"      {path}  ({human_size(result.local_files[path])})")
        _maybe_more(add, len(result.local_only), len(shown), "files")
    add("")

    add("  REMOTE FILES NOT MATCHED LOCALLY")
    add("  " + "-" * 66)
    if not result.remote_only:
        add("    (none)")
    else:
        shown = _limit(result.remote_only, max_examples)
        for path in shown:
            add(f"      {path}  ({human_size(result.remote_files[path])})")
        _maybe_more(add, len(result.remote_only), len(shown), "files")
    add("")

    add("  REMOTE COMMANDS EXECUTED  (all read-only)")
    add("  " + "-" * 66)
    for cmd in result.remote_commands:
        add(f"      {cmd}")
    add("=" * 70)

    return "\n".join(lines)


def render_json(result: ScanResult) -> str:
    """Render the full result as JSON for downstream tooling."""
    payload = {
        "algorithm": result.algorithm,
        "local_roots": result.local_roots,
        "remote_host": result.remote_host,
        "remote_roots": result.remote_roots,
        "summary": {
            "local_files": len(result.local_files),
            "remote_files": len(result.remote_files),
            "local_bytes": sum(result.local_files.values()),
            "remote_bytes": sum(result.remote_files.values()),
            "local_files_hashed": result.local_files_hashed,
            "remote_files_hashed": result.remote_files_hashed,
            "duplicate_groups": len(result.duplicate_groups),
            "local_only": len(result.local_only),
            "remote_only": len(result.remote_only),
        },
        "duplicate_groups": [
            {
                "name": g.name,
                "digest": g.digest,
                "size": g.size,
                "local_paths": g.local_paths,
                "remote_paths": g.remote_paths,
            }
            for g in result.duplicate_groups
        ],
        "local_only": [
            {"path": p, "size": result.local_files[p]} for p in result.local_only
        ],
        "remote_only": [
            {"path": p, "size": result.remote_files[p]}
            for p in result.remote_only
        ],
        "remote_commands": result.remote_commands,
    }
    return json.dumps(payload, indent=2)


def _limit(items, max_examples):
    if max_examples and len(items) > max_examples:
        return items[:max_examples]
    return items


def _maybe_more(add, total, shown, noun):
    if shown < total:
        add(f"    ... and {total - shown} more {noun} (use --json for the full list)")
