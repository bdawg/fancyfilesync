"""Read-only access to the remote machine.

This module is the *only* place in the codebase that runs commands on the remote
host, and it is built so that those commands can never be anything other than a
fixed set of read-only operations.

The read-only guarantee rests on three layers of defence:

1. **A frozen allowlist of programs** (:data:`ALLOWED_REMOTE_PROGRAMS`). Every
   program that may ever run remotely is listed here, and every one of them only
   reads data.

2. **A single choke point** (:meth:`RemoteExecutor._run_remote`). All remote
   execution goes through this one method, which refuses to run anything whose
   program is not on the allowlist.

3. **No general-purpose remote-exec API.** The public surface of
   :class:`RemoteExecutor` is just :meth:`list_files` and :meth:`hash_files`.
   Both build their command from a constant template; the only dynamic values
   are filesystem paths, which are either shell-quoted (the search directories)
   or passed NUL-delimited over stdin (the files to hash). There is no way for a
   caller to inject an arbitrary command.

If you ever need to be 100% certain the remote cannot be written to, you should
*also* enforce it on the remote side (a forced-command/read-only SSH key, a
read-only mount, or a restricted account). This module makes the client
incapable of issuing a write; belt-and-braces is the right posture for data you
care about.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# The read-only contract.
#
# This frozen set is the single source of truth for "what may run on the remote
# machine". Every entry is a read-only program:
#   find       -- enumerate files and their sizes; reads directory metadata only
#   xargs      -- as used here, only ever fans file paths into a hashing program
#   sha256sum/ -- read file contents and emit a hash; never modify anything
#   sha1sum/
#   md5sum
#
# Adding anything to this set is a deliberate, reviewable act. Nothing outside
# this set can be executed remotely (see RemoteExecutor._run_remote).
# ---------------------------------------------------------------------------
ALLOWED_REMOTE_PROGRAMS = frozenset(
    {
        "find",
        "xargs",
        "sha256sum",
        "sha1sum",
        "md5sum",
    }
)

# Map of the algorithms we support to the remote coreutils program that
# computes them. Local hashing (hashlib) uses the same algorithm names.
HASH_PROGRAMS: Dict[str, str] = {
    "sha256": "sha256sum",
    "sha1": "sha1sum",
    "md5": "md5sum",
}


class RemoteCommandError(RuntimeError):
    """A remote command exited unsuccessfully."""


class UnsafeRemoteCommand(RuntimeError):
    """A command was about to run remotely that is not on the read-only allowlist.

    Raising this (rather than silently dropping the command) makes any attempt
    to step outside the allowlist loud and unmissable.
    """


@dataclass
class RemoteExecutor:
    """Runs the small, fixed set of read-only commands on the remote host.

    Parameters
    ----------
    host:
        SSH destination, e.g. ``"user@server"`` or a ``Host`` alias from your
        ``~/.ssh/config``.
    ssh_binary:
        The ssh client to use. Defaults to ``"ssh"`` on ``PATH``.
    ssh_options:
        Extra options passed to ssh. ``BatchMode=yes`` is used by default so the
        tool fails fast instead of hanging on an interactive password prompt.
    dry_run:
        If true, no command is actually executed; the commands that *would* run
        are recorded in :attr:`executed_commands` and empty output is returned.
        Useful for auditing exactly what the tool would do remotely.
    """

    # Distinguishes this from local.LocalTarget in the pipeline/report.
    is_local: ClassVar[bool] = False

    host: str
    ssh_binary: str = "ssh"
    ssh_options: Sequence[str] = field(
        default_factory=lambda: ["-o", "BatchMode=yes"]
    )
    dry_run: bool = False

    # An audit trail of every remote command string this executor has issued.
    executed_commands: List[str] = field(default_factory=list, init=False)

    # -- public, read-only operations ---------------------------------------

    def list_files(self, directories: Sequence[str]) -> List[Tuple[int, str]]:
        """List every regular file under ``directories`` with its size.

        Returns a list of ``(size_bytes, path)`` tuples. This is the only cheap
        remote operation: it transfers just metadata (sizes + paths), never file
        contents, so it is safe to run over a slow link even for huge trees.
        """
        results: List[Tuple[int, str]] = []
        for directory in directories:
            quoted = shlex.quote(directory)
            # %s = size in bytes, %p = path. Records are NUL-terminated and the
            # size/path are tab-separated, so filenames containing spaces,
            # tabs or newlines are handled correctly.
            command = f"find {quoted} -type f -printf '%s\\t%p\\0'"
            # find returns non-zero if it hits an unreadable directory; that is
            # expected and harmless, so we keep whatever it managed to list.
            stdout, stderr, code = self._run_remote(
                command, programs=("find",), allow_nonzero=True
            )
            if code != 0 and stderr.strip():
                # Surface permission warnings etc. without aborting the scan.
                _warn(f"find on {directory!r} reported: {stderr.strip()}")
            results.extend(_parse_find_output(stdout))
        return results

    def hash_files(
        self,
        paths: Sequence[str],
        algorithm: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, str]:
        """Hash the given remote files *on the remote machine*.

        Only the resulting hash strings come back across the network; the file
        contents never leave the remote host. Returns a mapping of
        ``{remote_path: hex_digest}``.

        ``on_progress(done, total)`` is called as each remote hash completes, so
        callers can show a live counter.
        """
        if algorithm not in HASH_PROGRAMS:
            raise ValueError(
                f"Unsupported algorithm {algorithm!r}; "
                f"choose one of {sorted(HASH_PROGRAMS)}"
            )
        program = HASH_PROGRAMS[algorithm]

        result: Dict[str, str] = {}
        if not paths:
            return result

        # Feed the file list to the remote as a NUL-delimited stream on stdin and
        # let `xargs -0` invoke the hashing program. This means no path ever
        # appears in the command string, so there is nothing to escape and
        # nothing to inject -- the command is a constant. xargs also handles
        # batching so we never blow the command-line length limit, no matter how
        # many candidate files there are.
        command = f"xargs -0 {program}"
        stdin = b"\0".join(
            p.encode("utf-8", "surrogateescape") for p in paths
        )
        total = len(paths)

        # Validate and record before doing anything (the read-only choke point).
        self._assert_read_only(("xargs", program))
        self.executed_commands.append(command)
        if self.dry_run:
            return result

        # Stream sha*sum's stdout: it prints one line per file as it finishes,
        # so reading line by line lets us report live progress.
        argv = [self.ssh_binary, *self.ssh_options, self.host, command]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Feed stdin from a separate thread so a large file list can't deadlock
        # against the stdout we're draining at the same time.
        def _feed() -> None:
            try:
                proc.stdin.write(stdin)
            except BrokenPipeError:
                pass
            finally:
                proc.stdin.close()

        feeder = threading.Thread(target=_feed)
        feeder.start()

        done = 0
        for raw_line in proc.stdout:
            parsed = _parse_hash_line(raw_line.decode("utf-8", "surrogateescape"))
            if parsed is None:
                continue
            path, digest = parsed
            result[path] = digest
            done += 1
            if on_progress is not None:
                on_progress(done, total)

        feeder.join()
        proc.wait()
        stderr_text = proc.stderr.read().decode("utf-8", "replace")
        if proc.returncode != 0 and stderr_text.strip():
            _warn(f"remote hashing reported: {stderr_text.strip()}")
        return result

    # -- the single execution choke point -----------------------------------

    def _assert_read_only(self, programs: Sequence[str]) -> None:
        """Raise :class:`UnsafeRemoteCommand` unless every program is allowed.

        This is the structural guarantee: nothing reaches the remote without
        first passing through this allowlist check.
        """
        for program in programs:
            if program not in ALLOWED_REMOTE_PROGRAMS:
                raise UnsafeRemoteCommand(
                    f"Refusing to run {program!r} on the remote host. "
                    f"Only these read-only programs are permitted: "
                    f"{sorted(ALLOWED_REMOTE_PROGRAMS)}"
                )

    def _run_remote(
        self,
        command: str,
        programs: Sequence[str],
        stdin: Optional[bytes] = None,
        allow_nonzero: bool = False,
    ) -> Tuple[bytes, str, int]:
        """Validate and run a single remote command (non-streaming exec path).

        ``programs`` lists every program the ``command`` string invokes; each is
        checked against :data:`ALLOWED_REMOTE_PROGRAMS` before anything runs.
        """
        self._assert_read_only(programs)

        self.executed_commands.append(command)

        if self.dry_run:
            return b"", "", 0

        argv = [self.ssh_binary, *self.ssh_options, self.host, command]
        completed = subprocess.run(
            argv,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stderr_text = completed.stderr.decode("utf-8", "replace")
        if completed.returncode != 0 and not allow_nonzero:
            raise RemoteCommandError(
                f"Remote command failed (exit {completed.returncode}): "
                f"{command}\n{stderr_text}"
            )
        return completed.stdout, stderr_text, completed.returncode


# ---------------------------------------------------------------------------
# Output parsing helpers
# ---------------------------------------------------------------------------


def _parse_find_output(data: bytes) -> List[Tuple[int, str]]:
    """Parse NUL-terminated ``<size>\\t<path>`` records from find."""
    results: List[Tuple[int, str]] = []
    for record in data.split(b"\0"):
        if not record:
            continue
        size_bytes, tab, path_bytes = record.partition(b"\t")
        if not tab:
            continue  # malformed record, skip defensively
        try:
            size = int(size_bytes)
        except ValueError:
            continue
        path = path_bytes.decode("utf-8", "surrogateescape")
        results.append((size, path))
    return results


def _parse_hash_output(data: bytes) -> Dict[str, str]:
    """Parse coreutils ``<hexdigest>  <path>`` lines.

    coreutils escapes paths that contain a newline or backslash by prefixing the
    line with ``\\`` and escaping those characters; we reverse that here so the
    returned paths match what we sent.
    """
    results: Dict[str, str] = {}
    text = data.decode("utf-8", "surrogateescape")
    for line in text.splitlines():
        parsed = _parse_hash_line(line)
        if parsed is not None:
            path, digest = parsed
            results[path] = digest
    return results


def _parse_hash_line(line: str) -> Optional[Tuple[str, str]]:
    """Parse one coreutils ``<hexdigest>  <path>`` line into ``(path, digest)``.

    Returns ``None`` for blank or non-hash lines. Reverses coreutils' escaping
    of paths containing a newline or backslash.
    """
    line = line.rstrip("\n")
    if not line:
        return None
    escaped = line.startswith("\\")
    if escaped:
        line = line[1:]
    digest, sep, path = line.partition("  ")
    if not sep:
        return None  # not a hash line
    if escaped:
        path = _unescape_coreutils(path)
    return path, digest


def _unescape_coreutils(path: str) -> str:
    """Reverse coreutils' ``\\n`` / ``\\\\`` escaping of awkward filenames."""
    out: List[str] = []
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "\\" and i + 1 < len(path):
            nxt = path[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _warn(message: str) -> None:
    import sys

    print(f"[warning] {message}", file=sys.stderr)
