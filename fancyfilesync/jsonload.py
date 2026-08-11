"""Rebuild a :class:`ScanResult` from a previously exported ``--json`` file.

``report.render_json`` is lossless for reporting purposes even though it does
not store the ``local_files`` / ``remote_files`` maps verbatim: every scanned
file appears exactly once, either inside a duplicate group, inside a renamed
group, or in the local-only / remote-only lists, and each carries its size. This
module reassembles those maps so the text/Markdown renderers -- which read
``local_files`` for totals -- work unchanged on a loaded result.

Nothing here touches the network or the filesystem beyond reading the JSON file
itself, so re-rendering an old scan is free and needs no remote host.
"""

from __future__ import annotations

import json
import sys
from typing import Dict, List, Optional

from .core import DuplicateGroup, RenamedGroup, ScanResult

# What ``LocalTarget.host`` is set to; used to infer ``remote_is_local`` for
# JSON files written before that flag was exported.
_LOCAL_HOST = "(local filesystem)"


def scan_result_from_json(payload: dict, warn=None) -> ScanResult:
    """Build a :class:`ScanResult` from a decoded ``render_json`` payload.

    ``warn(message)`` is called for recoverable inconsistencies (e.g. a file
    count that disagrees with the stored summary, which means the JSON was
    hand-edited or truncated). Rendering still proceeds.
    """
    if warn is None:
        warn = lambda message: None  # noqa: E731

    missing = [k for k in ("algorithm", "duplicate_groups") if k not in payload]
    if missing:
        raise ValueError(
            "this does not look like a fancyfilesync --json export "
            f"(missing key(s): {', '.join(missing)})"
        )

    duplicate_groups = [
        DuplicateGroup(
            name=g["name"],
            digest=g["digest"],
            size=g["size"],
            local_paths=list(g["local_paths"]),
            remote_paths=list(g["remote_paths"]),
        )
        for g in payload.get("duplicate_groups", [])
    ]
    renamed_groups = [
        RenamedGroup(
            digest=g["digest"],
            size=g["size"],
            local_paths=list(g["local_paths"]),
            remote_paths=list(g["remote_paths"]),
        )
        for g in payload.get("renamed_groups", [])
    ]

    # Rebuild {path: size} for both sides from every place a path can appear.
    local_files: Dict[str, int] = {}
    remote_files: Dict[str, int] = {}
    for group in list(duplicate_groups) + list(renamed_groups):
        for path in group.local_paths:
            local_files[path] = group.size
        for path in group.remote_paths:
            remote_files[path] = group.size

    local_only: List[str] = []
    for entry in payload.get("local_only", []):
        local_files[entry["path"]] = entry["size"]
        local_only.append(entry["path"])
    remote_only: List[str] = []
    for entry in payload.get("remote_only", []):
        remote_files[entry["path"]] = entry["size"]
        remote_only.append(entry["path"])

    summary = payload.get("summary", {})
    _check_count(warn, "local", len(local_files), summary.get("local_files"))
    _check_count(warn, "remote", len(remote_files), summary.get("remote_files"))
    if summary.get("remote_only", 0) and not remote_only:
        # --show-remote-only was off for the scan? No: JSON is always complete,
        # so an empty list with a non-zero count means the file was trimmed.
        warn(
            f"summary reports {summary['remote_only']} remote-only files but "
            "none are listed; remote totals will be understated"
        )

    remote_host = payload.get("remote_host", "")
    return ScanResult(
        algorithm=payload["algorithm"],
        local_roots=list(payload.get("local_roots", [])),
        remote_host=remote_host,
        remote_roots=list(payload.get("remote_roots", [])),
        local_files=local_files,
        remote_files=remote_files,
        duplicate_groups=duplicate_groups,
        local_only=local_only,
        remote_only=remote_only,
        renamed_groups=renamed_groups,
        # Older exports stored neither flag; infer both rather than refuse.
        renamed_checked=payload.get("renamed_checked", bool(renamed_groups)),
        exclude=list(payload.get("exclude", [])),
        remote_is_local=payload.get("remote_is_local", remote_host == _LOCAL_HOST),
        assume_name_size=payload.get("assume_name_size", False),
        local_files_hashed=summary.get("local_files_hashed", 0),
        remote_files_hashed=summary.get("remote_files_hashed", 0),
        local_hash_seconds=summary.get("local_hash_seconds", 0.0),
        remote_hash_seconds=summary.get("remote_hash_seconds", 0.0),
        total_seconds=summary.get("total_seconds", 0.0),
        remote_commands=list(payload.get("remote_commands", [])),
    )


def load_scan_result(path: str, warn=None) -> ScanResult:
    """Read a ``--json`` export from ``path`` and return a :class:`ScanResult`."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return scan_result_from_json(payload, warn=warn)


def _check_count(warn, side: str, rebuilt: int, recorded) -> None:
    if recorded is not None and rebuilt != recorded:
        warn(
            f"rebuilt {rebuilt} {side} files but the summary records "
            f"{recorded}; the JSON may be incomplete or hand-edited"
        )


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="report-from-json",
        description=(
            "Re-render a report from a result.json previously written by "
            "fancyfilesync --json. Reads only that file: no scanning, no "
            "hashing, no SSH."
        ),
    )
    parser.add_argument("json_file", metavar="FILE", help="The result.json to read.")
    parser.add_argument(
        "--md",
        metavar="FILE",
        help="Write the report as Markdown to FILE instead of printing text.",
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="Write the text report to FILE as well as printing it.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=0,
        metavar="N",
        help="Limit listed entries per section (0 = no limit).",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colourise the text report (default: auto = only on a terminal).",
    )
    parser.add_argument(
        "--show-remote-only",
        action="store_true",
        help="List every remote file that had no local match.",
    )
    parser.add_argument(
        "--local-only-dirs",
        action="store_true",
        help="Instead of the full report, summarise the local files with no "
        "match by the directory holding them, flagging whether each directory "
        "is entirely unmatched (copy it wholesale) or mixed.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress warnings on stderr.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from . import report as report_mod

    args = build_parser().parse_args(argv)

    def warn(message: str) -> None:
        if not args.quiet:
            print(f"[warning] {message}", file=sys.stderr)

    try:
        result = load_scan_result(args.json_file, warn=warn)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot load {args.json_file}: {exc}", file=sys.stderr)
        return 2

    if args.md:
        if args.local_only_dirs:
            markdown = report_mod.render_local_only_dirs_markdown(
                result, max_examples=args.max_examples
            )
        else:
            markdown = report_mod.render_markdown(
                result, show_remote_only=args.show_remote_only
            )
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        print(f"Markdown report written to {args.md}", file=sys.stderr)
        return 0

    if args.color == "always":
        use_color = True
    elif args.color == "never":
        use_color = False
    else:
        use_color = sys.stdout.isatty()

    def _text(color: bool) -> str:
        if args.local_only_dirs:
            return report_mod.render_local_only_dirs(
                result, max_examples=args.max_examples, color=color
            )
        return report_mod.render_text(
            result,
            max_examples=args.max_examples,
            color=color,
            show_remote_only=args.show_remote_only,
        )

    text = _text(use_color)
    try:
        print(text)
    except BrokenPipeError:
        return 0

    if args.out:
        plain = _text(False)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(plain + "\n")
        print(f"Text report written to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
