import portalocker

from librivox_trimmer.download_state import (
    cleanup_incomplete_downloads,
    lock_download_directory,
)


def test_cleanup_removes_orphaned_download_but_preserves_completed_project(tmp_path):
    projects_directory = tmp_path / "Projects"
    incomplete = projects_directory / "unfinished-book_files"
    incomplete.mkdir(parents=True)
    (incomplete / "chapters.zip").write_bytes(b"partial")
    complete = projects_directory / "finished-book_files"
    complete.mkdir()
    (projects_directory / "finished-book.lvproj").write_text("{}", encoding="utf-8")

    removed = cleanup_incomplete_downloads(projects_directory)

    assert removed == [incomplete]
    assert not incomplete.exists()
    assert complete.exists()


def test_cleanup_skips_a_download_locked_by_another_process(tmp_path):
    projects_directory = tmp_path / "Projects"
    assets_directory = projects_directory / "active-book_files"
    assets_directory.mkdir(parents=True)
    (assets_directory / "chapters.zip").write_bytes(b"in progress")

    with lock_download_directory(projects_directory, assets_directory):
        removed = cleanup_incomplete_downloads(projects_directory)
        assert removed == []
        assert assets_directory.exists()

    removed = cleanup_incomplete_downloads(projects_directory)

    assert removed == [assets_directory]
    assert not assets_directory.exists()