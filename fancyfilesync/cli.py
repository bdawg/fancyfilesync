"""Command-line interface for fancyfilesync."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import report as report_mod
from .core import find_duplicates
from .local import LocalTarget
from .remote import ALLOWED_REMOTE_PROGRAMS, RemoteExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fancyfilesync",
        description=(
            "Find local files that are duplicated on a remote machine (or a "
            "second local directory), even when the layout differs. Read-only "
            "on the remote side."
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
        default=None,
        metavar="[user@]host",
        help="SSH destination of the remote machine (or an ssh_config alias). "
        "Omit this to treat --remote-dir as a second LOCAL directory set "
        "(local-to-local comparison, no SSH).",
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
        "--assume-name-size",
        action="store_true",
        help="Treat files that share a filename AND size as duplicates WITHOUT "
        "hashing to confirm. Fast (no file contents are read on either side), "
        "but UNVERIFIED: different files with the same name and size will be "
        "falsely reported as duplicates. Ignores --match-renamed.",
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

    if args.remote_host:
        ssh_options: List[str] = ["-o", "BatchMode=yes"]
        for opt in args.ssh_option:
            ssh_options += ["-o", opt]
        remote = RemoteExecutor(
            host=args.remote_host,
            ssh_options=ssh_options,
            dry_run=args.show_plan,
        )
    else:
        # No host given: compare against a second set of LOCAL directories.
        if args.show_plan:
            print(
                "Both locations are local, so no remote commands are run "
                "(nothing to preview)."
            )
            return 0
        remote = LocalTarget()

    if args.assume_name_size:
        print(
            "WARNING: --assume-name-size is ON. Files sharing a name and size "
            "will be reported as duplicates WITHOUT hashing to confirm; "
            "matches are unverified.",
            file=sys.stderr,
        )

    # Track the in-place listing line so the next normal message can finish it
    # with a newline (rather than printing over the top of it).
    listing_active = {"on": False}

    def progress(message: str) -> None:
        if args.quiet:
            return
        if listing_active["on"]:
            sys.stderr.write("\n")
            listing_active["on"] = False
        print(message, file=sys.stderr)

    def hash_progress(stage: str, done: int, total: int) -> None:
        if args.quiet:
            return
        # Update a single line in place; finish with a newline at completion.
        if stage == "local":
            where = "local"
        else:
            where = "second location" if remote.is_local else "remote"
        sys.stderr.write(f"\r    hashed {done}/{total} files on {where}   ")
        if done >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()

    def list_progress(side: str, count: int, current_dir=None) -> None:
        # The remote find can walk a huge/slow tree for a long time; this shows
        # a running count and the directory it's currently in, so it's clear the
        # listing is still advancing (and roughly where).
        if args.quiet:
            return
        where = ""
        if current_dir:
            shown = current_dir
            if len(shown) > 60:  # keep the most specific (rightmost) part
                shown = "..." + shown[-57:]
            where = f"  in {shown}"
        # Pad and clear to end of line so a shorter path can't leave stale text.
        sys.stderr.write(
            f"\r    listing {side} files: {count} found so far{where}\033[K"
        )
        sys.stderr.flush()
        listing_active["on"] = True

    result = find_duplicates(
        local_dirs=args.local,
        remote=remote,
        remote_dirs=args.remote_dir,
        algorithm=args.algo,
        match_renamed=args.match_renamed,
        assume_name_size=args.assume_name_size,
        exclude=args.exclude,
        progress=progress,
        hash_progress=hash_progress,
        list_progress=list_progress,
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
