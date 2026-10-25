import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath


def extract_mp3s(archive_path: Path, destination: Path) -> list[Path]:
    """Extract MP3 members while rejecting paths that escape the destination."""
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    seen: set[Path] = set()

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            normalized_name = member.filename.replace("\\", "/")
            posix_name = PurePosixPath(normalized_name)
            windows_name = PureWindowsPath(member.filename)
            if (
                posix_name.is_absolute()
                or windows_name.is_absolute()
                or windows_name.drive
                or ".." in posix_name.parts
            ):
                raise ValueError(f"Unsafe path in ZIP archive: {member.filename}")
            if posix_name.suffix.lower() != ".mp3":
                continue

            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Symbolic link in ZIP archive: {member.filename}")

            output_path = (root / Path(*posix_name.parts)).resolve()
            if not output_path.is_relative_to(root):
                raise ValueError(f"Unsafe path in ZIP archive: {member.filename}")
            if output_path in seen:
                raise ValueError(f"Duplicate MP3 path in ZIP archive: {member.filename}")
            seen.add(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, output_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted.append(output_path)

    if not extracted:
        raise ValueError("The archive did not contain any MP3 files.")
    return extracted