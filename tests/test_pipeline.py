"""End-to-end pipeline test using a fake remote (no network, no ssh)."""

import hashlib
import os
from typing import Dict, List, Sequence, Tuple

from fancyfilesync.core import find_duplicates


class FakeRemote:
    """Stand-in for RemoteExecutor backed by an in-memory file table.

    Crucially it computes hashes from its own bytes, mimicking the real
    behaviour of hashing on the remote side -- the local machine never sees
    these bytes other than through this fake.
    """

    def __init__(self, files: Dict[str, bytes]):
        self._files = files
        self.host = "fake-host"
        self.executed_commands: List[str] = []

    def list_files(self, directories: Sequence[str]) -> List[Tuple[int, str]]:
        self.executed_commands.append("find ... (fake)")
        out = []
        for path, data in self._files.items():
            if any(path.startswith(d.rstrip("/") + "/") or path == d for d in directories):
                out.append((len(data), path))
        return out

    def hash_files(self, paths: Sequence[str], algorithm: str) -> Dict[str, str]:
        self.executed_commands.append("xargs -0 sha256sum (fake)")
        return {
            p: hashlib.new(algorithm, self._files[p]).hexdigest()
            for p in paths
            if p in self._files
        }


def _write(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def test_finds_duplicate_same_name_different_path(tmp_path):
    local_dir = tmp_path / "local"
    dup = b"identical contents here" * 100
    only_local = b"this one is unique locally"
    same_name_diff = b"A" * len(only_local)  # same name+size, different content

    _write(str(local_dir / "photos" / "a.jpg"), dup)
    _write(str(local_dir / "notes.txt"), only_local)

    remote_files = {
        # Same NAME as a.jpg but a different directory -> a duplicate.
        "/remote/backup/2021/a.jpg": dup,
        # Same name AND size as notes.txt but different bytes -> NOT a match.
        "/remote/misc/notes.txt": same_name_diff,
    }
    remote = FakeRemote(remote_files)

    result = find_duplicates(
        local_dirs=[str(local_dir)],
        remote=remote,
        remote_dirs=["/remote"],
        algorithm="sha256",
    )

    # The duplicate is found despite the different directory.
    assert len(result.duplicate_groups) == 1
    group = result.duplicate_groups[0]
    assert group.name == "a.jpg"
    assert group.local_paths == [str(local_dir / "photos" / "a.jpg")]
    assert group.remote_paths == ["/remote/backup/2021/a.jpg"]

    # notes.txt matched on name+size, was hashed, but content differs -> no match.
    assert str(local_dir / "notes.txt") in result.local_only
    assert "/remote/misc/notes.txt" in result.remote_only

    # Both name+size collisions were hashed (a.jpg pair + notes.txt pair).
    assert result.local_files_hashed == 2
    assert result.remote_files_hashed == 2


def test_same_content_different_name_is_not_matched(tmp_path):
    # Identical bytes but different filenames must NOT be reported as duplicates,
    # and must not even be hashed.
    local_dir = tmp_path / "local"
    dup = b"identical bytes" * 50
    _write(str(local_dir / "left.bin"), dup)
    remote = FakeRemote({"/remote/right.bin": dup})

    result = find_duplicates(
        local_dirs=[str(local_dir)],
        remote=remote,
        remote_dirs=["/remote"],
    )
    assert result.duplicate_groups == []
    assert result.local_files_hashed == 0
    assert result.remote_files_hashed == 0
    assert result.local_only == [str(local_dir / "left.bin")]
    assert result.remote_only == ["/remote/right.bin"]


def test_match_renamed_finds_renamed_copy(tmp_path):
    local_dir = tmp_path / "local"
    renamed = b"the same bytes under a new name" * 100
    truly_unique = b"nothing matches this one"
    _write(str(local_dir / "report_final.pdf"), renamed)
    _write(str(local_dir / "orphan.dat"), truly_unique)

    remote = FakeRemote(
        {
            # Same content as report_final.pdf but a different name.
            "/remote/archive/report_v3.pdf": renamed,
            "/remote/archive/unrelated.bin": b"x" * 12345,
        }
    )

    # Without the flag: no match (names differ), both sides reported as orphans.
    plain = find_duplicates([str(local_dir)], remote, ["/remote"])
    assert plain.duplicate_groups == []
    assert plain.renamed_groups == []
    assert str(local_dir / "report_final.pdf") in plain.local_only

    # With the flag: the renamed copy is found and removed from local_only.
    renamed_remote = FakeRemote(
        {
            "/remote/archive/report_v3.pdf": renamed,
            "/remote/archive/unrelated.bin": b"x" * 12345,
        }
    )
    result = find_duplicates(
        [str(local_dir)], renamed_remote, ["/remote"], match_renamed=True
    )
    assert result.duplicate_groups == []
    assert len(result.renamed_groups) == 1
    group = result.renamed_groups[0]
    assert group.local_paths == [str(local_dir / "report_final.pdf")]
    assert group.remote_paths == ["/remote/archive/report_v3.pdf"]
    # The matched local file is no longer counted as local-only.
    assert str(local_dir / "report_final.pdf") not in result.local_only
    # The genuinely unique local file stays local-only.
    assert str(local_dir / "orphan.dat") in result.local_only


def test_unique_names_are_never_hashed(tmp_path):
    local_dir = tmp_path / "local"
    _write(str(local_dir / "x"), b"short")
    remote = FakeRemote({"/remote/y": b"a much longer different length file"})

    result = find_duplicates(
        local_dirs=[str(local_dir)],
        remote=remote,
        remote_dirs=["/remote"],
    )
    # No shared names -> nothing hashed on either side.
    assert result.local_files_hashed == 0
    assert result.remote_files_hashed == 0
    assert result.duplicate_groups == []
    assert result.local_only == [str(local_dir / "x")]
    assert result.remote_only == ["/remote/y"]
