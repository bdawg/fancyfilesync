"""Tests that the read-only guarantee is structural, not just conventional."""

import subprocess

import pytest

from fancyfilesync.remote import (
    ALLOWED_REMOTE_PROGRAMS,
    RemoteExecutor,
    UnsafeRemoteCommand,
    _parse_find_output,
    _parse_hash_output,
)


def test_allowlist_is_all_read_only():
    # A guard so nobody quietly adds a writing program to the allowlist.
    assert ALLOWED_REMOTE_PROGRAMS == frozenset(
        {"find", "xargs", "sha256sum", "sha1sum", "md5sum"}
    )


def test_run_remote_rejects_unlisted_program():
    ex = RemoteExecutor(host="example")
    with pytest.raises(UnsafeRemoteCommand):
        ex._run_remote("rm -rf /", programs=("rm",))


def test_no_remote_call_when_command_unsafe(monkeypatch):
    # Even if validation were bypassed, nothing should reach subprocess.
    ex = RemoteExecutor(host="example")

    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("subprocess.run was called for an unsafe command")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(UnsafeRemoteCommand):
        ex._run_remote("dd if=/dev/zero of=/data", programs=("dd",))


def test_public_api_only_builds_allowlisted_commands():
    # Exercise the real command builders in dry-run mode and confirm every
    # command starts with an allowlisted program.
    ex = RemoteExecutor(host="example", dry_run=True)
    ex.list_files(["/data/a", "/data/b"])
    ex.hash_files(["/data/a/file1", "/data/a/file2"], "sha256")
    assert ex.executed_commands  # something was planned
    for cmd in ex.executed_commands:
        first_token = cmd.split()[0]
        assert first_token in ALLOWED_REMOTE_PROGRAMS, cmd


def test_directory_argument_is_shell_quoted():
    ex = RemoteExecutor(host="example", dry_run=True)
    ex.list_files(["/data; rm -rf /"])  # malicious-looking path
    # The whole path must be quoted into a single safe argument.
    assert "'/data; rm -rf /'" in ex.executed_commands[0]


def test_hash_command_never_contains_paths():
    # Paths are sent over stdin, so they can't appear in (and thus can't
    # corrupt) the command string regardless of their contents.
    ex = RemoteExecutor(host="example", dry_run=True)
    ex.hash_files(["/data/$(reboot)/x", "/data/`id`/y"], "sha256")
    assert ex.executed_commands == ["xargs -0 sha256sum"]


def test_parse_find_output_handles_awkward_names():
    data = b"123\t/a/normal.txt\x00456\t/b/with\tinside\x00"
    assert _parse_find_output(data) == [
        (123, "/a/normal.txt"),
        (456, "/b/with\tinside"),
    ]


def test_parse_hash_output_basic_and_escaped():
    out = (
        b"abc123  /data/plain.bin\n"
        b"\\def456  /data/odd\\nname\n"  # coreutils-escaped newline in name
    )
    parsed = _parse_hash_output(out)
    assert parsed["/data/plain.bin"] == "abc123"
    assert parsed["/data/odd\nname"] == "def456"
