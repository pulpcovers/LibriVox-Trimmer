"""Validate manual release requests and verify the resulting Windows artifacts."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def read_versions(root):
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    source = ast.parse((root / "src/librivox_trimmer/__init__.py").read_text(encoding="utf-8"))
    runtime = next(
        ast.literal_eval(node.value) for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    )
    return metadata["project"]["version"], runtime


def validate_request(version, runtime_version, publish, tag="", ref="", default_branch="main"):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("The Windows installer requires a numeric major.minor.patch version.")
    if version != runtime_version:
        raise ValueError("pyproject.toml and the runtime __version__ must match.")
    expected_tag = "v" + version
    if tag and tag != expected_tag:
        raise ValueError(f"Release tag must be {expected_tag}, matching the package version.")
    if publish and ref != "refs/heads/" + default_branch:
        raise ValueError("Publishing is allowed only from the repository default branch.")
    return expected_tag


def check_publish_target(root, tag, repository, commit, token):
    existing_tag = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", f"refs/tags/{tag}^{{}}"],
        text=True, capture_output=True, check=False,
    )
    if existing_tag.returncode == 0 and existing_tag.stdout.strip() != commit:
        raise ValueError("The release tag already points to a different commit; bump the version.")
    request = Request(
        os.environ.get("GITHUB_API_URL", "https://api.github.com") + f"/repos/{repository}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token},
    )
    try:
        with urlopen(request, timeout=30):
            pass
    except HTTPError as error:
        if error.code == 404:
            return
        raise
    raise ValueError("A release already exists for this version; existing releases are never overwritten.")


def verify_artifacts(directory, version):
    filenames = [f"LibriVox-Trimmer-{version}-windows.zip", f"LibriVox-Trimmer-{version}-windows-setup.exe"]
    for filename in filenames:
        path = directory / filename
        sidecar = (directory / (filename + ".sha256")).read_text(encoding="ascii").strip()
        match = re.fullmatch(r"([a-fA-F0-9]{64})  (.+)", sidecar)
        if match is None or match[2] != filename:
            raise ValueError("Invalid checksum file for " + filename)
        with path.open("rb") as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != match[1].lower():
            raise ValueError("Checksum mismatch for " + filename)
    with zipfile.ZipFile(directory / filenames[0]) as archive:
        if archive.testzip() is not None:
            raise ValueError("The portable ZIP failed its CRC check.")
        names = archive.namelist()
        if "LibriVox Trimmer/LibriVox Trimmer.exe" not in names or not any(
            name.startswith("LibriVox Trimmer/_internal/") for name in names
        ):
            raise ValueError("The portable ZIP is missing its executable or dependencies.")
    with (directory / filenames[1]).open("rb") as source:
        if source.read(2) != b"MZ":
            raise ValueError("The installer is not a Windows executable.")
    return filenames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version, runtime = read_versions(root)
    publish = os.environ.get("PUBLISH_RELEASE", "false").lower() == "true"
    tag = validate_request(
        version, runtime, publish, os.environ.get("RELEASE_TAG", "").strip(),
        os.environ.get("GITHUB_REF", ""), os.environ.get("DEFAULT_BRANCH", "main"),
    )
    if args.command == "verify":
        print("Verified: " + ", ".join(verify_artifacts(args.directory, version)))
        return
    if publish:
        check_publish_target(root, tag, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"], os.environ["GH_TOKEN"])
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
            target.write(f"version={version}\ntag={tag}\n")
    print(json.dumps({"version": version, "tag": tag, "publish": publish}))


if __name__ == "__main__":
    main()