import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import zipfile

import pytest


TOOLS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "packaging/release_tools.py"))


def test_test_builds_need_no_tag_or_default_branch():
    assert TOOLS["validate_request"]("0.8.0", "0.8.0", False, ref="refs/heads/experiment") == "v0.8.0"


def test_publication_requires_matching_versions_and_default_branch():
    assert TOOLS["validate_request"]("0.8.0", "0.8.0", True, "v0.8.0", "refs/heads/main") == "v0.8.0"
    for version, runtime, tag, ref in [
        ("0.8.0", "0.7.0", "v0.8.0", "refs/heads/main"),
        ("0.8.0", "0.8.0", "v0.7.0", "refs/heads/main"),
        ("0.8.0", "0.8.0", "v0.8.0", "refs/heads/experiment"),
        ("0.8.0rc1", "0.8.0rc1", "v0.8.0rc1", "refs/heads/main"),
    ]:
        with pytest.raises(ValueError):
            TOOLS["validate_request"](version, runtime, True, tag, ref)


def test_package_and_runtime_versions_are_read_without_importing_app():
    root = Path(__file__).resolve().parents[1]
    version, runtime = TOOLS["read_versions"](root)
    assert version == runtime


def _artifacts(directory):
    archive_path = directory / "LibriVox-Trimmer-0.8.0-windows.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("LibriVox Trimmer/LibriVox Trimmer.exe", b"MZapp")
        archive.writestr("LibriVox Trimmer/_internal/library.dll", b"library")
    installer = directory / "LibriVox-Trimmer-0.8.0-windows-setup.exe"
    installer.write_bytes(b"MZinstaller")
    for path in (archive_path, installer):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return archive_path, installer


def test_artifact_checksums_and_zip_layout_are_verified(tmp_path):
    _artifacts(tmp_path)
    assert len(TOOLS["verify_artifacts"](tmp_path, "0.8.0")) == 2


def test_corrupt_installer_is_rejected(tmp_path):
    _archive, installer = _artifacts(tmp_path)
    installer.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        TOOLS["verify_artifacts"](tmp_path, "0.8.0")


def test_checksum_cannot_name_another_file(tmp_path):
    archive, _installer = _artifacts(tmp_path)
    archive.with_suffix(".zip.sha256").write_text("0" * 64 + "  ../elsewhere.zip\n", encoding="ascii")
    with pytest.raises(ValueError, match="Invalid checksum"):
        TOOLS["verify_artifacts"](tmp_path, "0.8.0")


def test_existing_tag_on_another_commit_is_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(TOOLS["subprocess"], "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="old-commit\n"))
    with pytest.raises(ValueError, match="different commit"):
        TOOLS["check_publish_target"](tmp_path, "v0.8.0", "owner/repo", "new-commit", "test-token")


def test_existing_release_is_never_overwritten(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(TOOLS["subprocess"], "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setitem(TOOLS["check_publish_target"].__globals__, "urlopen", lambda *_args, **_kwargs: nullcontext())
    with pytest.raises(ValueError, match="already exists"):
        TOOLS["check_publish_target"](tmp_path, "v0.8.0", "owner/repo", "commit", "test-token")


@pytest.mark.parametrize("status", [404, 403])
def test_missing_release_is_allowed_but_permission_failures_are_not(tmp_path, monkeypatch, status):
    monkeypatch.setattr(TOOLS["subprocess"], "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""))

    def request(*_args, **_kwargs):
        raise HTTPError("https://api.github.com", status, "response", {}, None)

    monkeypatch.setitem(TOOLS["check_publish_target"].__globals__, "urlopen", request)
    if status == 404:
        TOOLS["check_publish_target"](tmp_path, "v0.8.0", "owner/repo", "commit", "test-token")
    else:
        with pytest.raises(HTTPError):
            TOOLS["check_publish_target"](tmp_path, "v0.8.0", "owner/repo", "commit", "test-token")