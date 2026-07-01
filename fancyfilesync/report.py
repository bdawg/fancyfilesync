"""Human-readable and machine-readable rendering of a :class:`ScanResult`."""

from __future__ import annotations

import json
import os
from typing import Dict, List, Union

from .core import DuplicateGroup, ScanResult


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


def human_time(seconds: float) -> str:
    """Format a duration in seconds as a short human-readable string."""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


class Palette:
    """ANSI colour helper. When disabled, every method returns text unchanged."""

    _CODES = {
        "bold": "1",
        "dim": "2",
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36",
    }

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, codes: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{codes}m{text}\033[0m"

    def bold(self, t: str) -> str:
        return self._wrap(self._CODES["bold"], t)

    def dim(self, t: str) -> str:
        return self._wrap(self._CODES["dim"], t)

    def red(self, t: str) -> str:
        return self._wrap(self._CODES["red"], t)

    def green(self, t: str) -> str:
        return self._wrap(self._CODES["green"], t)

    def yellow(self, t: str) -> str:
        return self._wrap(self._CODES["yellow"], t)

    def cyan(self, t: str) -> str:
        return self._wrap(self._CODES["cyan"], t)

    def dir(self, t: str) -> str:
        return self._wrap(self._CODES["bold"] + ";" + self._CODES["blue"], t)


# ---------------------------------------------------------------------------
# Duplicate tree
# ---------------------------------------------------------------------------

# A tree node is either a sub-tree (dict) or a leaf (the DuplicateGroup the
# file belongs to).
TreeNode = Dict[str, Union["TreeNode", DuplicateGroup]]


def _abspath_roots(roots: List[str]) -> List[str]:
    return [os.path.abspath(r) for r in roots]


def _root_for(path: str, roots: List[str]) -> str:
    """Return the longest configured root that contains ``path`` (or "")."""
    best = ""
    for root in roots:
        if path == root or path.startswith(root + os.sep):
            if len(root) > len(best):
                best = root
    return best


def _build_tree(paths_and_groups) -> TreeNode:
    """Build a nested dict tree from ``(relative_path, group)`` pairs."""
    root: TreeNode = {}
    for rel_path, group in paths_and_groups:
        parts = [p for p in rel_path.split(os.sep) if p]
        node = root
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = group
    return root


def _compress(tree: TreeNode) -> TreeNode:
    """Collapse chains of single-child directories ("a/b/c") for readability."""
    new: TreeNode = {}
    for name, child in tree.items():
        if isinstance(child, dict):
            child = _compress(child)
            while len(child) == 1:
                ((only_name, only_child),) = child.items()
                if isinstance(only_child, dict):
                    name = name + "/" + only_name
                    child = only_child
                else:
                    break
            new[name] = child
        else:
            new[name] = child
    return new


def _render_tree(tree: TreeNode, palette: Palette, prefix: str = "") -> List[str]:
    lines: List[str] = []
    # Directories first, then files; both alphabetical.
    items = sorted(
        tree.items(),
        key=lambda kv: (0 if isinstance(kv[1], dict) else 1, kv[0].lower()),
    )
    for index, (name, child) in enumerate(items):
        last = index == len(items) - 1
        connector = "└── " if last else "├── "
        child_prefix = prefix + ("    " if last else "│   ")
        if isinstance(child, dict):
            lines.append(prefix + connector + palette.dir(name + "/"))
            lines.extend(_render_tree(child, palette, child_prefix))
        else:
            group = child
            label = (
                prefix
                + connector
                + palette.green(name)
                + "  "
                + palette.dim(human_size(group.size))
            )
            lines.append(label)
            # Show where each remote copy lives, nested under the file.
            for r_index, remote_path in enumerate(group.remote_paths):
                r_last = r_index == len(group.remote_paths) - 1
                r_conn = "└─ " if r_last else "├─ "
                lines.append(
                    child_prefix
                    + palette.dim(r_conn)
                    + palette.cyan("→ " + remote_path)
                )
    return lines


def _render_groups_as_tree(
    groups, local_roots: List[str], palette: Palette, empty_message: str
) -> List[str]:
    """Render any list of groups (with ``.local_paths``/``.remote_paths``/
    ``.size``) as a per-root tree of local files annotated with remote paths."""
    if not groups:
        return ["  " + palette.dim(empty_message)]

    roots = _abspath_roots(local_roots)
    by_root: Dict[str, list] = {}
    for group in groups:
        for path in group.local_paths:
            root = _root_for(path, roots)
            rel = os.path.relpath(path, root) if root else path
            by_root.setdefault(root, []).append((rel, group))

    lines: List[str] = []
    for root in sorted(by_root):
        header = root if root else "(other)"
        lines.append("  " + palette.dir(header + "/"))
        tree = _compress(_build_tree(by_root[root]))
        for line in _render_tree(tree, palette, prefix="  "):
            lines.append(line)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def render_duplicate_tree(result: ScanResult, palette: Palette) -> List[str]:
    """Render the duplicated local files as a per-root tree."""
    return _render_groups_as_tree(
        result.duplicate_groups,
        result.local_roots,
        palette,
        "(no duplicates found)",
    )


# ---------------------------------------------------------------------------
# Full text report
# ---------------------------------------------------------------------------


def render_text(
    result: ScanResult,
    max_examples: int = 0,
    color: bool = False,
    show_remote_only: bool = False,
) -> str:
    """Render a clear, scannable, optionally-coloured text report.

    ``max_examples`` limits how many entries are listed under the local-only /
    remote-only sections (0 = no limit). ``color`` enables ANSI colour.

    ``show_remote_only`` controls whether every unmatched remote file is listed.
    It defaults to False because the remote tree can contain hundreds of
    thousands of files that are simply not in your local set; the full list is
    rarely useful and is always available via ``--json``.
    """
    p = Palette(color)
    lines: List[str] = []
    add = lines.append

    local_total = sum(result.local_files.values())
    remote_total = sum(result.remote_files.values())

    dup_file_count = sum(len(g.local_paths) for g in result.duplicate_groups)
    dup_bytes = sum(g.size * len(g.local_paths) for g in result.duplicate_groups)
    local_only_bytes = sum(result.local_files.get(p_, 0) for p_ in result.local_only)

    renamed_file_count = sum(len(g.local_paths) for g in result.renamed_groups)
    renamed_bytes = sum(
        g.size * len(g.local_paths) for g in result.renamed_groups
    )

    # Wording differs when the "remote" side is actually a second local set.
    is_local = result.remote_is_local
    b_word = "second location" if is_local else "remote"
    b_cap = b_word.capitalize()
    found_phrase = "in the second location" if is_local else "on the remote"

    add(p.bold("=" * 70))
    add(p.bold("  DUPLICATE FILE REPORT"))
    add(p.bold("=" * 70))
    add(f"  Local roots : {', '.join(result.local_roots)}")
    if is_local:
        add(f"  Compared to : local filesystem")
    else:
        add(f"  Remote      : {result.remote_host}")
    add(f"  {b_cap} roots: {', '.join(result.remote_roots)}")
    add(f"  Hash algo   : {result.algorithm}")
    if result.exclude:
        add(f"  Excluding   : {', '.join(result.exclude)}")
    add("")

    # -- headline summary ---------------------------------------------------
    if dup_file_count:
        headline = (
            p.green(p.bold(f"✓ {dup_file_count} duplicated file"))
            + p.green(p.bold("s" if dup_file_count != 1 else ""))
            + p.green(p.bold(f" found {found_phrase}"))
            + p.dim(
                f"  ({len(result.duplicate_groups)} distinct, "
                f"{human_size(dup_bytes)})"
            )
        )
    else:
        headline = p.yellow(
            p.bold(f"No duplicated files found {found_phrase}")
        )
    add("  " + headline)
    if renamed_file_count:
        add(
            "  "
            + p.cyan(
                p.bold(
                    f"↺ {renamed_file_count} renamed duplicate"
                    f"{'s' if renamed_file_count != 1 else ''} "
                    f"(same content, different name)"
                )
            )
            + p.dim(f"  ({human_size(renamed_bytes)})")
        )
    add("")

    # Short label for the second side used in the aligned summary column.
    b_short = "location B" if is_local else "remote"
    b_short_cap = "Location B" if is_local else "Remote"
    hashed_sides = "A/B" if is_local else "local/remote"

    def row(label: str, value: str) -> str:
        return f"  {label:<26}: {value}"

    add("  SUMMARY")
    add("  " + "-" * 66)
    add(row("Local files scanned", f"{len(result.local_files):>8}  ({human_size(local_total)})"))
    add(
        row(
            f"{b_short_cap} files scanned",
            f"{len(result.remote_files):>8}  ({human_size(remote_total)})",
        )
    )
    add(
        row(
            f"Files hashed ({hashed_sides})",
            f"{result.local_files_hashed} / {result.remote_files_hashed}",
        )
    )
    add(row("Time hashing local", human_time(result.local_hash_seconds)))
    add(row(f"Time hashing {b_short}", human_time(result.remote_hash_seconds)))
    add(row("Total run time", human_time(result.total_seconds)))
    add(
        "  "
        + p.green(
            row("Duplicated local files", f"{dup_file_count:>8}  ({human_size(dup_bytes)})")[2:]
        )
    )
    add(row("Distinct duplicate groups", f"{len(result.duplicate_groups):>8}"))
    if result.renamed_checked:
        add(
            "  "
            + p.cyan(
                row(
                    "Renamed duplicates",
                    f"{renamed_file_count:>8}  ({human_size(renamed_bytes)})",
                )[2:]
            )
        )
    local_only_label = (
        "Local files not in location B" if is_local else "Local files not on remote"
    )
    add(
        "  "
        + p.yellow(
            row(
                local_only_label,
                f"{len(result.local_only):>8}  ({human_size(local_only_bytes)})",
            )[2:]
        )
    )
    add(row(f"{b_short_cap} files not matched", f"{len(result.remote_only):>8}"))
    add("")

    # -- duplicates as a tree ----------------------------------------------
    arrow_target = "location B" if is_local else "remote location"
    add(
        p.bold("  DUPLICATED FILES")
        + p.dim(f"  (local tree → {arrow_target})")
    )
    add("  " + "-" * 66)
    lines.extend(render_duplicate_tree(result, p))
    add("")

    # -- renamed duplicates (shown whenever the rename pass ran) -------------
    if result.renamed_checked:
        add(
            p.bold("  RENAMED DUPLICATES")
            + p.dim("  (identical content, different filename)")
        )
        add("  " + "-" * 66)
        lines.extend(
            _render_groups_as_tree(
                result.renamed_groups,
                result.local_roots,
                p,
                "checked, none found — no unmatched local file has a "
                f"same-content copy in the {b_word}",
            )
        )
        add("")

    # -- supporting flat lists ---------------------------------------------
    add(p.bold(f"  LOCAL FILES WITH NO {b_word.upper()} DUPLICATE"))
    add("  " + "-" * 66)
    if not result.local_only:
        add("    " + p.dim("(none)"))
    else:
        shown = _limit(result.local_only, max_examples)
        for path in shown:
            add(f"      {path}  " + p.dim(f"({human_size(result.local_files[path])})"))
        _maybe_more(add, len(result.local_only), len(shown), "files", p)
    add("")

    add(p.bold(f"  {b_word.upper()} FILES NOT MATCHED LOCALLY"))
    add("  " + "-" * 66)
    if not result.remote_only:
        add("    " + p.dim("(none)"))
    elif not show_remote_only:
        # The other tree is often huge and mostly unrelated to the local set,
        # so we summarise instead of flooding the screen.
        add(
            "    "
            + p.dim(
                f"{len(result.remote_only)} {b_word} files have no local match "
                f"(hidden; use --show-remote-only or --json to list them)"
            )
        )
    else:
        shown = _limit(result.remote_only, max_examples)
        for path in shown:
            add(
                f"      {path}  "
                + p.dim(f"({human_size(result.remote_files[path])})")
            )
        _maybe_more(add, len(result.remote_only), len(shown), "files", p)
    add("")

    if is_local:
        add(
            p.bold("  COMMANDS EXECUTED")
            + p.dim("  (both locations are local — no remote commands run)")
        )
    else:
        add(p.bold("  REMOTE COMMANDS EXECUTED") + p.dim("  (all read-only)"))
        add("  " + "-" * 66)
        for cmd in result.remote_commands:
            add("      " + p.dim(cmd))
    add(p.bold("=" * 70))

    return "\n".join(lines)


def render_markdown(result: ScanResult, show_remote_only: bool = False) -> str:
    """Render the report as a formatted Markdown document.

    The duplicate trees are placed in fenced code blocks so the box-drawing
    characters line up in any Markdown viewer. ``show_remote_only`` behaves as
    in :func:`render_text`.
    """
    plain = Palette(False)  # no ANSI colour inside a Markdown file
    lines: List[str] = []
    add = lines.append

    local_total = sum(result.local_files.values())
    remote_total = sum(result.remote_files.values())
    dup_file_count = sum(len(g.local_paths) for g in result.duplicate_groups)
    dup_bytes = sum(g.size * len(g.local_paths) for g in result.duplicate_groups)
    local_only_bytes = sum(
        result.local_files.get(p, 0) for p in result.local_only
    )
    renamed_file_count = sum(len(g.local_paths) for g in result.renamed_groups)
    renamed_bytes = sum(
        g.size * len(g.local_paths) for g in result.renamed_groups
    )

    is_local = result.remote_is_local
    b_cap = "Second location" if is_local else "Remote"
    b_word = "second location" if is_local else "remote"
    found_phrase = "in the second location" if is_local else "on the remote"
    arrow_target = "location B" if is_local else "remote location"

    add("# Duplicate File Report")
    add("")
    add(f"- **Local roots:** {', '.join(result.local_roots)}")
    if is_local:
        add(f"- **Compared to:** local filesystem")
    else:
        add(f"- **Remote:** {result.remote_host}")
    add(f"- **{b_cap} roots:** {', '.join(result.remote_roots)}")
    add(f"- **Hash algorithm:** {result.algorithm}")
    if result.exclude:
        add(f"- **Excluding:** {', '.join(result.exclude)}")
    add("")

    if dup_file_count:
        add(
            f"## ✓ {dup_file_count} duplicated file"
            f"{'s' if dup_file_count != 1 else ''} found {found_phrase}"
        )
        add("")
        add(
            f"_{len(result.duplicate_groups)} distinct, "
            f"{human_size(dup_bytes)}_"
        )
    else:
        add(f"## No duplicated files found {found_phrase}")
    if renamed_file_count:
        add("")
        add(
            f"**↺ {renamed_file_count} renamed duplicate"
            f"{'s' if renamed_file_count != 1 else ''}** "
            f"(same content, different name) — {human_size(renamed_bytes)}"
        )
    add("")

    add("## Summary")
    add("")
    add("| Metric | Value |")
    add("| --- | --- |")
    add(f"| Local files scanned | {len(result.local_files)} ({human_size(local_total)}) |")
    add(
        f"| {b_cap} files scanned | {len(result.remote_files)} "
        f"({human_size(remote_total)}) |"
    )
    add(
        f"| Files hashed ({'A / B' if is_local else 'local / remote'}) | "
        f"{result.local_files_hashed} / {result.remote_files_hashed} |"
    )
    add(f"| Time hashing local | {human_time(result.local_hash_seconds)} |")
    add(f"| Time hashing {b_word} | {human_time(result.remote_hash_seconds)} |")
    add(f"| Total run time | {human_time(result.total_seconds)} |")
    add(
        f"| Duplicated local files | {dup_file_count} "
        f"({human_size(dup_bytes)}) |"
    )
    add(f"| Distinct duplicate groups | {len(result.duplicate_groups)} |")
    if result.renamed_checked:
        add(
            f"| Renamed duplicates | {renamed_file_count} "
            f"({human_size(renamed_bytes)}) |"
        )
    add(
        f"| Local files not in {b_word} | {len(result.local_only)} "
        f"({human_size(local_only_bytes)}) |"
    )
    add(f"| {b_cap} files not matched | {len(result.remote_only)} |")
    add("")

    add("## Duplicated files")
    add("")
    add(f"_Local tree → {arrow_target}_")
    add("")
    add("```text")
    lines.extend(_strip_indent(render_duplicate_tree(result, plain)))
    add("```")
    add("")

    if result.renamed_checked:
        add("## Renamed duplicates")
        add("")
        add("_Identical content, different filename_")
        add("")
        add("```text")
        lines.extend(
            _strip_indent(
                _render_groups_as_tree(
                    result.renamed_groups,
                    result.local_roots,
                    plain,
                    "checked, none found — no unmatched local file has a "
                    f"same-content copy in the {b_word}",
                )
            )
        )
        add("```")
        add("")

    add(f"## Local files with no {b_word} duplicate")
    add("")
    if not result.local_only:
        add("_(none)_")
    else:
        for path in result.local_only:
            add(f"- `{path}` ({human_size(result.local_files[path])})")
    add("")

    add(f"## {b_cap} files not matched locally")
    add("")
    if not result.remote_only:
        add("_(none)_")
    elif not show_remote_only:
        add(
            f"_{len(result.remote_only)} {b_word} files have no local match "
            f"(hidden; use --show-remote-only or the JSON output to list them)._"
        )
    else:
        for path in result.remote_only:
            add(f"- `{path}` ({human_size(result.remote_files[path])})")
    add("")

    if is_local:
        add("## Commands executed")
        add("")
        add("_Both locations are local — no remote commands were run._")
    else:
        add("## Remote commands executed")
        add("")
        add("_All read-only._")
        add("")
        add("```sh")
        for cmd in result.remote_commands:
            add(cmd)
        add("```")
    add("")

    return "\n".join(lines)


def _strip_indent(tree_lines: List[str]) -> List[str]:
    """Drop the leading two-space indent the tree renderer adds for the console,
    so trees sit flush inside a Markdown code block."""
    out = []
    for line in tree_lines:
        out.append(line[2:] if line.startswith("  ") else line)
    return out


def render_json(result: ScanResult) -> str:
    """Render the full result as JSON for downstream tooling."""
    payload = {
        "algorithm": result.algorithm,
        "local_roots": result.local_roots,
        "remote_host": result.remote_host,
        "remote_roots": result.remote_roots,
        "exclude": result.exclude,
        "summary": {
            "local_files": len(result.local_files),
            "remote_files": len(result.remote_files),
            "local_bytes": sum(result.local_files.values()),
            "remote_bytes": sum(result.remote_files.values()),
            "local_files_hashed": result.local_files_hashed,
            "remote_files_hashed": result.remote_files_hashed,
            "local_hash_seconds": round(result.local_hash_seconds, 3),
            "remote_hash_seconds": round(result.remote_hash_seconds, 3),
            "total_seconds": round(result.total_seconds, 3),
            "duplicate_groups": len(result.duplicate_groups),
            "duplicated_local_files": sum(
                len(g.local_paths) for g in result.duplicate_groups
            ),
            "renamed_groups": len(result.renamed_groups),
            "renamed_local_files": sum(
                len(g.local_paths) for g in result.renamed_groups
            ),
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
        "renamed_groups": [
            {
                "digest": g.digest,
                "size": g.size,
                "local_paths": g.local_paths,
                "remote_paths": g.remote_paths,
            }
            for g in result.renamed_groups
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


def _maybe_more(add, total, shown, noun, palette):
    if shown < total:
        add(
            "    "
            + palette.dim(
                f"... and {total - shown} more {noun} "
                f"(use --json for the full list)"
            )
        )
