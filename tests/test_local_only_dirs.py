"""Tests for the directories-to-copy summary (--local-only-dirs)."""

from fancyfilesync.core import DuplicateGroup, ScanResult
from fancyfilesync.report import (
    render_local_only_dirs,
    render_local_only_dirs_markdown,
    summarize_local_only_dirs,
)


def _result(local_files, local_only, duplicate_groups=None, roots=("/local",)):
    return ScanResult(
        algorithm="sha256",
        local_roots=list(roots),
        remote_host="user@nas",
        remote_roots=["/nas"],
        local_files=dict(local_files),
        remote_files={},
        duplicate_groups=list(duplicate_groups or []),
        local_only=sorted(local_only),
        remote_only=[],
    )


def _by_path(dirs):
    return {d.path: d for d in dirs}


def test_splits_complete_from_mixed_directories():
    result = _result(
        local_files={
            "/local/all_new/a.txt": 10,
            "/local/all_new/b.txt": 20,
            "/local/mixed/new.txt": 5,
            "/local/mixed/already_there.txt": 7,
        },
        local_only=[
            "/local/all_new/a.txt",
            "/local/all_new/b.txt",
            "/local/mixed/new.txt",
        ],
    )
    dirs, _ = summarize_local_only_dirs(result)
    by_path = _by_path(dirs)

    complete = by_path["/local/all_new"]
    assert complete.complete is True
    assert complete.unmatched_files == 2
    assert complete.total_files == 2
    assert complete.other_files == 0
    assert complete.unmatched_bytes == 30

    mixed = by_path["/local/mixed"]
    assert mixed.complete is False
    assert mixed.unmatched_files == 1
    assert mixed.total_files == 2
    assert mixed.other_files == 1


def test_directories_without_unmatched_files_are_omitted():
    result = _result(
        local_files={"/local/new/a.txt": 1, "/local/done/b.txt": 1},
        local_only=["/local/new/a.txt"],
    )
    dirs, _ = summarize_local_only_dirs(result)
    assert [d.path for d in dirs] == ["/local/new"]


def test_sorted_by_unmatched_bytes_descending():
    result = _result(
        local_files={
            "/local/small/a": 10,
            "/local/big/b": 5000,
            "/local/medium/c": 100,
        },
        local_only=["/local/small/a", "/local/big/b", "/local/medium/c"],
    )
    dirs, _ = summarize_local_only_dirs(result)
    assert [d.path for d in dirs] == ["/local/big", "/local/medium", "/local/small"]


def test_subtree_completeness_distinguishes_own_files_from_children():
    """A directory can have all its OWN files unmatched while a sub-directory
    is already backed up -- copying it recursively would then be wrong."""
    result = _result(
        local_files={
            "/local/parent/mine.txt": 10,
            "/local/parent/child/theirs.txt": 20,
        },
        local_only=["/local/parent/mine.txt"],
    )
    dirs, copy_roots = summarize_local_only_dirs(result)
    parent = _by_path(dirs)["/local/parent"]

    assert parent.complete is True  # every file directly in it is unmatched
    assert parent.subtree_complete is False  # but the child is already there
    assert copy_roots == []

    text = render_local_only_dirs(result)
    assert "in sub-directories" in text


def test_recursive_copy_targets_collapse_to_the_topmost_directory():
    result = _result(
        local_files={
            "/local/tree/a.txt": 1,
            "/local/tree/sub/b.txt": 2,
            "/local/tree/sub/deeper/c.txt": 3,
        },
        local_only=[
            "/local/tree/a.txt",
            "/local/tree/sub/b.txt",
            "/local/tree/sub/deeper/c.txt",
        ],
    )
    dirs, copy_roots = summarize_local_only_dirs(result)

    # /local itself is fully unmatched, and it is the configured root.
    assert copy_roots == ["/local"]
    assert all(d.subtree_complete for d in dirs)


def test_copy_targets_never_escape_the_configured_local_roots():
    """If everything under the root is unmatched, the suggestion must be the
    root itself -- never its parent, and never '/'."""
    result = _result(
        local_files={"/local/deep/nested/a.txt": 1},
        local_only=["/local/deep/nested/a.txt"],
        roots=("/local",),
    )
    _, copy_roots = summarize_local_only_dirs(result)
    assert copy_roots == ["/local"]


def test_multiple_roots_are_reported_separately():
    result = _result(
        local_files={"/one/a.txt": 1, "/two/b.txt": 2, "/two/c.txt": 3},
        local_only=["/one/a.txt", "/two/b.txt", "/two/c.txt"],
        roots=("/one", "/two"),
    )
    _, copy_roots = summarize_local_only_dirs(result)
    assert copy_roots == ["/one", "/two"]


def test_nothing_to_copy_when_everything_matched():
    result = _result(
        local_files={"/local/a.txt": 1},
        local_only=[],
        duplicate_groups=[
            DuplicateGroup(
                name="a.txt",
                digest="d",
                size=1,
                local_paths=["/local/a.txt"],
                remote_paths=["/nas/a.txt"],
            )
        ],
    )
    dirs, copy_roots = summarize_local_only_dirs(result)
    assert dirs == [] and copy_roots == []
    assert "nothing to copy" in render_local_only_dirs(result)


def test_text_report_names_directories_not_individual_files():
    result = _result(
        local_files={f"/local/bulk/f{i}.txt": 1 for i in range(50)},
        local_only=[f"/local/bulk/f{i}.txt" for i in range(50)],
    )
    text = render_local_only_dirs(result)
    assert "/local/bulk/" in text
    assert "f7.txt" not in text  # the whole point: no per-file listing
    assert "50 files" in text


def test_max_examples_limits_each_section():
    local_files = {f"/local/d{i}/f.txt": 1 for i in range(10)}
    result = _result(local_files=local_files, local_only=list(local_files))
    text = render_local_only_dirs(result, max_examples=3)
    assert "and 7 more directories" in text


def test_unverified_mode_carries_its_warning_into_the_summary():
    result = _result(
        local_files={"/local/a/x.txt": 1}, local_only=["/local/a/x.txt"]
    )
    result.assume_name_size = True
    assert "assume-name-size" in render_local_only_dirs(result)
    assert "assume-name-size" in render_local_only_dirs_markdown(result)


def test_wording_follows_local_to_local_mode():
    result = _result(
        local_files={"/local/a/x.txt": 1}, local_only=["/local/a/x.txt"]
    )
    result.remote_is_local = True
    text = render_local_only_dirs(result)
    assert "second location" in text and "remote" not in text.lower()


def test_markdown_summary_has_both_tables():
    result = _result(
        local_files={
            "/local/all_new/a.txt": 10,
            "/local/mixed/new.txt": 5,
            "/local/mixed/old.txt": 7,
        },
        local_only=["/local/all_new/a.txt", "/local/mixed/new.txt"],
    )
    md = render_local_only_dirs_markdown(result)
    assert "# Directories To Copy" in md
    assert "## Complete directories" in md
    assert "## Mixed directories" in md
    assert "`/local/all_new`" in md
    assert "1 of 2" in md  # the mixed directory's unmatched count
