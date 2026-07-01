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

    def hash_files(
        self, paths: Sequence[str], algorithm: str, on_progress=None
    ) -> Dict[str, str]:
        self.executed_commands.append("xargs -0 sha256sum (fake)")
        result = {}
        total = len(paths)
        for index, p in enumerate(paths, start=1):
            if p in self._files:
                result[p] = hashlib.new(algorithm, self._files[p]).hexdigest()
            if on_progress is not None:
                on_progress(index, total)
        return result


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


def test_exclude_skips_files_on_both_sides(tmp_path):
    local_dir = tmp_path / "local"
    junk = b"\x00\x01junk metadata"
    real = b"a genuine duplicate payload" * 20
    _write(str(local_dir / ".DS_Store"), junk)
    _write(str(local_dir / "sub" / "keep.bin"), real)
    # An excluded directory's contents should also be skipped.
    _write(str(local_dir / ".git" / "config"), b"gitconfig")

    remote = FakeRemote(
        {
            "/remote/.DS_Store": junk,  # same name+size, would be hashed if kept
            "/remote/elsewhere/keep.bin": real,
            "/remote/proj/.git/config": b"gitconfig",
        }
    )

    result = find_duplicates(
        [str(local_dir)],
        remote,
        ["/remote"],
        exclude=[".DS_Store", ".git"],
    )

    # Excluded names never appear anywhere in the result.
    all_local = set(result.local_files)
    assert not any(".DS_Store" in p or ".git" in p for p in all_local)
    assert not any(".DS_Store" in p or ".git" in p for p in result.remote_files)

    # The real file is still matched, and the junk was never hashed.
    assert len(result.duplicate_groups) == 1
    assert result.duplicate_groups[0].name == "keep.bin"
    assert result.local_files_hashed == 1
    assert result.remote_files_hashed == 1
    assert result.exclude == [".DS_Store", ".git"]


def test_local_to_local_comparison(tmp_path):
    from fancyfilesync.local import LocalTarget

    a = tmp_path / "A"
    b = tmp_path / "B"
    dup = b"shared content" * 40
    _write(str(a / "photos" / "pic.jpg"), dup)
    _write(str(a / "only_in_a.txt"), b"unique to A")
    _write(str(b / "backup" / "pic.jpg"), dup)  # same name+content, diff path
    _write(str(b / "only_in_b.txt"), b"unique to B")

    result = find_duplicates([str(a)], LocalTarget(), [str(b)])

    assert result.remote_is_local is True
    assert len(result.duplicate_groups) == 1
    group = result.duplicate_groups[0]
    assert group.name == "pic.jpg"
    assert group.local_paths == [str(a / "photos" / "pic.jpg")]
    assert group.remote_paths == [str(b / "backup" / "pic.jpg")]
    assert str(a / "only_in_a.txt") in result.local_only
    assert str(b / "only_in_b.txt") in result.remote_only
    # No SSH/commands are ever issued in local-to-local mode.
    assert result.remote_commands == []


def test_assume_name_size_matches_without_hashing(tmp_path):
    local_dir = tmp_path / "local"
    dup = b"identical contents here" * 100
    only_local = b"this one is unique locally"
    same_name_diff = b"A" * len(only_local)  # same name+size, DIFFERENT content

    _write(str(local_dir / "photos" / "a.jpg"), dup)
    _write(str(local_dir / "notes.txt"), only_local)

    remote = FakeRemote(
        {
            "/remote/backup/2021/a.jpg": dup,
            # Same name AND size as notes.txt but different bytes.
            "/remote/misc/notes.txt": same_name_diff,
        }
    )

    result = find_duplicates(
        local_dirs=[str(local_dir)],
        remote=remote,
        remote_dirs=["/remote"],
        assume_name_size=True,
    )

    # Nothing is hashed on either side in this mode.
    assert result.local_files_hashed == 0
    assert result.remote_files_hashed == 0
    assert result.assume_name_size is True
    assert "xargs -0 sha256sum (fake)" not in remote.executed_commands

    # BOTH name+size matches are reported as duplicates -- including the
    # notes.txt pair, which differs in content (that's the documented risk).
    names = sorted(g.name for g in result.duplicate_groups)
    assert names == ["a.jpg", "notes.txt"]
    for group in result.duplicate_groups:
        assert group.digest == "(not hashed: assumed from name+size)"

    # Everything matched, so nothing is left over.
    assert result.local_only == []
    assert result.remote_only == []


def test_assume_name_size_ignores_match_renamed(tmp_path):
    local_dir = tmp_path / "local"
    renamed = b"same bytes under a new name" * 50
    _write(str(local_dir / "report_final.pdf"), renamed)
    remote = FakeRemote({"/remote/archive/report_v3.pdf": renamed})

    # assume_name_size wins: no hashing, so no rename detection is possible.
    result = find_duplicates(
        [str(local_dir)],
        remote,
        ["/remote"],
        assume_name_size=True,
        match_renamed=True,
    )
    assert result.renamed_groups == []
    assert result.renamed_checked is False
    assert result.local_files_hashed == 0
    # Different names, so not a name+size match either.
    assert result.duplicate_groups == []
    assert str(local_dir / "report_final.pdf") in result.local_only


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
