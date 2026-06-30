"""fancyfilesync — find files that are duplicated on a remote machine.

This package locates local files whose *contents* are identical to files on a
remote machine, even when the remote files live in a different directory layout
or carry different modification times.

Two hard design constraints drive the whole architecture:

1. File contents must never be transferred across the (slow) WAN just to
   compare them. Hashes of remote files are therefore computed *on the remote
   machine* and only the (tiny) hash strings travel back.

2. Everything done on the remote machine must be strictly read-only. The set of
   programs that may ever run remotely is a small, frozen allowlist defined in
   :mod:`fancyfilesync.remote`, and every remote invocation is validated against
   it before execution. There is deliberately no code path that can create,
   modify, move or delete remote data.
"""

from .core import (
    DuplicateGroup,
    RenamedGroup,
    ScanResult,
    find_duplicates,
)

__all__ = [
    "DuplicateGroup",
    "RenamedGroup",
    "ScanResult",
    "find_duplicates",
]
__version__ = "0.1.0"
