"""Command-line interface for fancyfilesync."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import report as report_mod
from .core import find_duplicates
from .remote import ALLOWED_REMOTE_PROGRAMS, RemoteExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fancyfilesync",
        description=(
            "Find local files that are duplicated on a remote machine, even "
            "when the remote layout differs. Read-only on the remote side."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The ONLY programs this tool can ever run on the remote machine "
            "are (all read-only):\n    "
            + ", ".join(sorted(ALLOWED_REMOTE_PROGRAMS))
            + "\n\nExample:\n"
            "  python -m fancyfilesync \\\n"
            "    --local ~/Photos --local ~/Videos \\\n"
            "    --remote-host user@nas \\\n"
            "    --remote-dir /volume1/media \\\n"
            "    --json result.json"
        ),
    )
    parser.add_argument(
        "--local",
        action="append",
        required=True,
        metavar="DIR",
        help="A local directory to scan (repeatable).",
    )
    parser.add_argument(
        "--remote-host",
        required=True,
        metavar="[user@]host",
        help="SSH destination of the remote machine (or an ssh_config alias).",
    )
    parser.add_argument(
        "--remote-dir",
        action="append",
        required=True,
        metavar="DIR",
        help="A directory on the remote machine to scan (repeatable).",
    )
    parser.add_argument(
        "--algo",
        choices=["sha256", "sha1", "md5"],
        default="sha256",
        help="Hash algorithm (default: sha256). Must exist on the remote.",
    )
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        metavar="OPT",
        help="Extra option passed to ssh, e.g. --ssh-option 'Port=2222'. "
        "Repeatable. Each is passed as 'ssh -o OPT'.",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="Also write the full result as JSON to FILE.",
    )
    parser.add_argument(
        "--md",
        metavar="FILE",
        help="Also write the report as a formatted Markdown file.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=0,
        metavar="N",
        help="Limit listed entries per section in the text report "
        "(0 = no limit; the JSON output is always complete).",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colourise the report. 'auto' (default) uses colour only when "
        "writing to a terminal.",
    )
    parser.add_argument(
        "--show-remote-only",
        action="store_true",
        help="List every remote file that has no local match. Off by default, "
        "since the remote tree can contain huge numbers of unrelated files.",
    )
    parser.add_argument(
        "--match-renamed",
        action="store_true",
        help="Relax the same-filename requirement: after the normal pass, check "
        "whether any unmatched local files are renamed copies of remote files "
        "(matched by size, then content). Reported separately.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip files/directories whose name matches this glob, on both "
        "sides (repeatable). E.g. --exclude .DS_Store --exclude '*.tmp' "
        "--exclude .git",
    )
    parser.add_argument(
        "--show-plan",
        action="store_true",
        help="Print the exact read-only commands that would run on the remote, "
        "without connecting or executing anything, then exit.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages on stderr.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    ssh_options: List[str] = ["-o", "BatchMode=yes"]
    for opt in args.ssh_option:
        ssh_options += ["-o", opt]

    remote = RemoteExecutor(
        host=args.remote_host,
        ssh_options=ssh_options,
        dry_run=args.show_plan,
    )

    def progress(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr)

    result = find_duplicates(
        local_dirs=args.local,
        remote=remote,
        remote_dirs=args.remote_dir,
        algorithm=args.algo,
        match_renamed=args.match_renamed,
        exclude=args.exclude,
        progress=progress,
    )

    if args.show_plan:
        print("Read-only commands that WOULD run on the remote host:")
        print(f"  (ssh {args.remote_host} ...)")
        for cmd in result.remote_commands:
            print(f"    {cmd}")
        print(
            "\nNo connection was made and nothing was executed. "
            "Re-run without --show-plan to perform the scan."
        )
        return 0

    if args.color == "always":
        use_color = True
    elif args.color == "never":
        use_color = False
    else:
        use_color = sys.stdout.isatty()

    text = report_mod.render_text(
        result,
        max_examples=args.max_examples,
        color=use_color,
        show_remote_only=args.show_remote_only,
    )
    try:
        print(text)
    except BrokenPipeError:
        # Output was piped into something that closed early (e.g. `head`).
        return 0

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(report_mod.render_json(result))
        print(f"\nFull JSON report written to {args.json}", file=sys.stderr)

    if args.md:
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(
                report_mod.render_markdown(
                    result, show_remote_only=args.show_remote_only
                )
            )
        print(f"Markdown report written to {args.md}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
