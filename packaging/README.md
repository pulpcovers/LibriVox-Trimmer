# Windows Builds and Releases

The manually triggered **Windows Build and Release** workflow builds the tested
source revision into an unsigned per-user NSIS installer and a portable ZIP.
It does not run on ordinary pushes and never publishes unless explicitly requested.

## Test Build

After the workflow is committed and pushed to the default branch:

1. Open the repository's **Actions** tab and select **Windows Build and Release**.
2. Select **Run workflow** and choose the source branch.
3. Leave **Publish a GitHub release** unchecked. Leave the optional tag blank.
4. Download `windows-<run-id>` from the completed run's **Artifacts** section.

That artifact contains the portable ZIP, `*-windows-setup.exe`, and their individual
SHA-256 checksum files. Artifacts expire after three days; download test builds
before then. No release or tag is created by a test build. The current version can
be test-built even if that version was previously released.

The workflow uses Windows Server 2022, Python 3.11, pinned PyInstaller 6.22.3,
and NSIS 3.11. Application dependencies are installed from the version ranges in
`pyproject.toml`; a fully locked dependency set remains future work. No speech
model is bundled or downloaded by the build. Automated tests use mocked speech
models and recorded fixtures.

## Publish a Release

1. Update `pyproject.toml` and `src/librivox_trimmer/__init__.py` to the same unused
  numeric version, such as `0.9.0`, and update the release-facing documentation.
2. Commit and push the source to the repository's default branch.
3. Run **Windows Build and Release** from that branch with **Publish a GitHub
   release** checked.
4. Leave **Mark a published release as a beta/prerelease** checked for a beta;
   uncheck it only when intentionally publishing a stable release.
5. The optional tag may be blank, which derives `v0.9.0`, or must exactly match
   `v` plus the package version.

Publishing from another branch is rejected. A tag pointing at another commit or
an existing release (including a draft) is rejected before the expensive build.
Previously published versions such as `v0.7.0` must not be overwritten: bump
the version before publishing a new build. The workflow never bumps versions itself.

The build job has read-only repository permission. A separate publishing job gets
`contents: write` only for an explicitly requested publication. It downloads the
same tested artifacts, revalidates versions and checksums, and creates a draft
release targeting the exact tested commit. Only after uploads succeed is the
draft published. Existing release assets are never replaced. If upload or final
publication fails and leaves a draft, inspect that draft before retrying; the
workflow will not silently overwrite it. GitHub's built-in `GITHUB_TOKEN` is used;
no personal access token or signing secret is required. Repository/organization
policy must allow Actions and the requested release-writing permission.

## Installer Behavior

- Installs for the current user under
  `%LOCALAPPDATA%\Programs\LibriVox Trimmer`, without administrator elevation.
- Copies the complete PyInstaller one-folder payload, including FFmpeg and the
  `_internal` dependencies, and includes the app license.
- Adds a Start-menu entry and Windows uninstall registration. The interactive
  finish page can launch the app and optionally create a desktop shortcut.
- Supports reinstalling/upgrading an installation created by this installer.
  Close the app first. An unmanaged nonempty destination is refused.
- The uninstaller removes known application files and shortcuts. It preserves
  projects, models, preferences, and outputs, which live outside the install folder.
- The installer and app are unsigned. Windows security warnings or blocking can
  still occur. This workflow does not claim that Windows trusts the binaries.

The automated smoke test runs only on a clean disposable CI profile. It performs
silent install, same-version reinstall/upgrade, and uninstall, checking payload,
registration and preservation of a dummy app-data file. It refuses to run on a
profile containing an installation or app data. It does not replace testing
preview playback, first model download, analysis, export, or installation on a
clean end-user Windows desktop without Python.

## Local Build

Use the app's Python environment and an installed or portable NSIS compiler:

```powershell
python -m pip install '.[dev,build]'
.\packaging\build_release.ps1 -Python python
```

Use `-MakeNSIS 'C:\path\to\makensis.exe'` when NSIS is not on PATH or in its
standard Program Files location. Use `-SkipInstaller` for a portable-ZIP-only build.
The version is read from `pyproject.toml`. Verify all outputs with:

```powershell
python packaging/release_tools.py verify --directory dist
```

Outputs are written to ignored `dist/`. Do not run `smoke_install.ps1` on a real
user profile. Do not copy test binaries over an existing published release.