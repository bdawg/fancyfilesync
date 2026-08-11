"""Round-trip tests: render_json -> jsonload -> renderers."""

import json

from fancyfilesync.core import DuplicateGroup, RenamedGroup, ScanResult
from fancyfilesync.jsonload import main, scan_result_from_json
from fancyfilesync import report


def _sample_result() -> ScanResult:
    local_files = {
        "/local/a/photo.jpg": 100,
        "/local/b/photo.jpg": 100,
        "/local/notes.txt": 7,
        "/local/renamed.bin": 42,
    }
    remote_files = {
        "/nas/pics/photo.jpg": 100,
        "/nas/misc/original.bin": 42,
        "/nas/unrelated.iso": 999,
    }
    return ScanResult(
        algorithm="sha256",
        local_roots=["/local"],
        remote_host="user@nas",
        remote_roots=["/nas"],
        local_files=local_files,
        remote_files=remote_files,
        duplicate_groups=[
            DuplicateGroup(
                name="photo.jpg",
                digest="deadbeef",
                size=100,
                local_paths=["/local/a/photo.jpg", "/local/b/photo.jpg"],
                remote_paths=["/nas/pics/photo.jpg"],
            )
        ],
        local_only=["/local/notes.txt"],
        remote_only=["/nas/unrelated.iso"],
        renamed_groups=[
            RenamedGroup(
                digest="cafebabe",
                size=42,
                local_paths=["/local/renamed.bin"],
                remote_paths=["/nas/misc/original.bin"],
            )
        ],
        renamed_checked=True,
        exclude=[".DS_Store"],
        remote_is_local=False,
        local_files_hashed=3,
        remote_files_hashed=2,
        local_hash_seconds=1.5,
        remote_hash_seconds=2.5,
        total_seconds=9.0,
        remote_commands=["find /nas ... (read-only)"],
    )


def test_round_trip_rebuilds_the_scan_result():
    original = _sample_result()
    loaded = scan_result_from_json(json.loads(report.render_json(original)))

    # The file maps are not stored verbatim; they must be rebuilt exactly.
    assert loaded.local_files == original.local_files
    assert loaded.remote_files == original.remote_files

    for field in (
        "algorithm",
        "local_roots",
        "remote_host",
        "remote_roots",
        "exclude",
        "assume_name_size",
        "renamed_checked",
        "remote_is_local",
        "local_only",
        "remote_only",
        "duplicate_groups",
        "renamed_groups",
        "local_files_hashed",
        "remote_files_hashed",
        "remote_commands",
    ):
        assert getattr(loaded, field) == getattr(original, field), field


def test_round_trip_renders_identical_reports():
    original = _sample_result()
    loaded = scan_result_from_json(json.loads(report.render_json(original)))

    assert report.render_text(loaded, show_remote_only=True) == report.render_text(
        original, show_remote_only=True
    )
    assert report.render_markdown(loaded) == report.render_markdown(original)
    assert report.render_json(loaded) == report.render_json(original)


def test_local_to_local_wording_survives_old_exports_without_the_flag():
    original = _sample_result()
    original.remote_host = "(local filesystem)"
    original.remote_is_local = True
    payload = json.loads(report.render_json(original))
    del payload["remote_is_local"]  # as written by an older version

    loaded = scan_result_from_json(payload)
    assert loaded.remote_is_local is True
    assert "second location" in report.render_text(loaded)


def test_warns_when_summary_disagrees_with_the_listed_files():
    payload = json.loads(report.render_json(_sample_result()))
    payload["summary"]["local_files"] = 99

    warnings = []
    scan_result_from_json(payload, warn=warnings.append)
    assert any("local files" in w for w in warnings)


def test_rejects_a_file_that_is_not_an_export():
    try:
        scan_result_from_json({"hello": "world"})
    except ValueError as exc:
        assert "fancyfilesync" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cli_writes_markdown(tmp_path, capsys):
    json_path = tmp_path / "result.json"
    json_path.write_text(report.render_json(_sample_result()), encoding="utf-8")
    md_path = tmp_path / "report.md"

    assert main([str(json_path), "--md", str(md_path)]) == 0
    assert "photo.jpg" in md_path.read_text(encoding="utf-8")


def test_cli_reports_a_bad_file_without_traceback(tmp_path, capsys):
    bad = tmp_path / "nope.json"
    bad.write_text("{}", encoding="utf-8")
    assert main([str(bad)]) == 2
    assert "error:" in capsys.readouterr().err
