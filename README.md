# LibriVox Trimmer

A Windows-first desktop app for reviewing and trimming LibriVox audiobook
chapters. It analyses chapter openings and endings, suggests cut points, and
exports approved MP3 intervals without re-encoding.

Current version: 0.8.0 (beta), with an unsigned per-user Windows installer and a
portable ZIP. Narrator hints, fresh transcription, metadata normalization, and
delayed waveform recentering are included in this version.

## Highlights

- Fetch chapters from a LibriVox project or open local MP3 files.
- Analyse one chapter or a complete loaded book.
- Preview proposed cuts and retained audio, then adjust boundaries manually.
- Save projects and resume work later; export one chapter or batch-export all.

## Safety

Detection only suggests cuts. It never trims automatically, and exports do not
modify source MP3s. Speech recognition can miss or misidentify words, so review
and preview uncertain boundaries before exporting.

When available, LibriVox catalog book and chapter titles help identify headings
and book endings. Chapter reader names help confirm spoken reader credits, but
removing a credit still requires transcription and pause checks. Chapters are
matched by audio filename; missing or ambiguous chapter metadata falls back to
the existing MP3-based detection.

For matching, chapter titles are normalized to remove catalog numbering such as
`1-4.`, `IV.`, `Ch. 02`, or an explicit trailing `(Chapter 1)` label. Genuine
numeric titles such as `20,000 Leagues Under the Sea` are preserved. Narrator
names are cleaned of trailing lifespan dates, with initials and typography
normalized before credit matching and hints. Stored catalog text, displayed
titles, MP3 tags, and exported filenames are not rewritten.

## Windows Beta Download

Download the Windows installer from [GitHub Releases](https://github.com/pulpcovers/LibriVox-Trimmer/releases/tag/v0.8.0)
and run **LibriVox-Trimmer-0.8.0-windows-setup.exe**. It installs for your account
without administrator rights and adds a Start-menu shortcut.

Alternatively, extract the entire portable ZIP and run **LibriVox Trimmer.exe**
inside the extracted folder. Keep the accompanying `_internal` folder beside the
executable. Python and a separate FFmpeg installation are not required. The speech
model downloads on first analysis.

This beta is unsigned, so Windows may show an unknown-publisher or SmartScreen warning.
Only proceed after checking that the download came from this repository's release.
A SHA-256 checksum file is provided alongside the ZIP. In PowerShell, use
`Get-FileHash .\LibriVox-Trimmer-0.8.0-windows-setup.exe -Algorithm SHA256` to compare
the installer with its matching checksum file. The portable ZIP has its own checksum.

## Install and Test on Windows

The steps below install the app from its source code. You do not need Git, and
you do not need to install FFmpeg separately.

### 1. Install Python

Open PowerShell and install Python 3.13 with Windows Package Manager:

```powershell
winget install --id Python.Python.3.13 --exact
```

If `winget` is unavailable, install Python 3.10 or later from
[python.org](https://www.python.org/downloads/). During setup, enable
**Add python.exe to PATH** if the installer offers it. Then check that Python
is available:

```powershell
py -3 --version
```

It should print Python 3.10 or later. If Windows says `py` is not recognized,
rerun the Python installer and include the Python launcher.

### 2. Download the app

1. Open this repository on GitHub.
2. Select the green **Code** button, then **Download ZIP**.
3. In File Explorer, right-click the downloaded ZIP and choose **Extract All**.
4. Open the extracted `LibriVox-Trimmer` folder in File Explorer.
5. Open a terminal in that folder. On Windows 11, right-click an empty area in
  the folder and choose **Open in Terminal**. Alternatively, click File
  Explorer's address bar, type `powershell`, and press Enter.
6. Check that the terminal is in the project folder by running `dir`. The file
  list should include `pyproject.toml`.

### 3. Install the app dependencies

Copy and run these commands in PowerShell, one at a time:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The first command creates a private Python environment in the project folder.
The next commands install the app and its test tools. Installation can take a
few minutes. FFmpeg is included through `imageio-ffmpeg`.

### 4. Start the app

```powershell
.\.venv\Scripts\python.exe -m librivox_trimmer
```

The desktop window should open. On the first analysis, the English speech model
downloads automatically; this requires an internet connection and may take a
few minutes. The model is cached in your Windows user data folder for later use.

### 5. Run the automated tests

In the same terminal, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Pytest prints a summary when the tests finish. A successful run reports all tests
as passed; the number can change as the project evolves.

## Using the App

### Load Chapters

Choose **Fetch chapters** and enter a LibriVox project URL, or choose **Open MP3
files** to load local chapters. Opened files are copied into a new project (you
name it), so the project keeps working if the originals are moved or deleted;
the originals are never changed. Opening an older project that points at MP3s
elsewhere offers to copy them in. Load the whole book when possible: the first and
last chapters in the loaded list provide context for opening announcements and
book endings.

### Analyse and Review

Select a chapter and choose **Analyse selected chapter**, or choose **Analyse all
chapters** to process the loaded list. The app suggests where to remove intro
notices and chapter-ending announcements; it does not apply cuts automatically.
Review the Intro and Outro fields and listen to the previews before exporting.

Catalog details are saved with fetched projects. If they are missing, **Analyse all
chapters** tries to retrieve them from the project's saved LibriVox URL. If they
cannot be retrieved, analysis continues without them.

While all chapters are being analysed, the button changes to **Cancel Analysis**.
Cancelling keeps and saves the results for chapters that already finished; the
chapter being analysed at that moment is left unchanged. Cancellation takes
effect at the next analysis step, so it may take a few seconds.

Chapters marked **Review intro** or **Review outro** need extra attention. Hover
over a marked chapter to see the reason. These warnings are saved with the
project. Re-running analysis updates warnings for the sections that were checked.
Check **Mark reviewed** beside the selected chapter's Analyse button to acknowledge
its warnings and clear its highlight. This autosaves without changing the cuts;
unchecking it restores the warnings. New analysis results reset the acknowledgement.
On the first loaded track, unique book and chapter information is kept; an
opening announcement may be removed when its repetition is confirmed after the
notice. This includes unnumbered headings such as Foreword, Introduction and
Preface. On later tracks, the app attempts to remove the notice, reader credit
and redundant opening information while retaining the chapter heading. When the
app cannot confirm where a credit ends, it keeps the audio and marks the chapter
**Review intro** rather than risk cutting the chapter title or narration. Check
these previews and adjust the cut manually if needed.

### Preview and Export

- **Play removed intro** plays the proposed removed notice.
- **Play Kept Intro** starts with the preserved opening, skips the removed
  notice, and continues through kept narration and any retained book ending.
- **Play removed outro** plays the proposed removed ending marker.
- **Play kept ending** plays the end of the kept narration and any retained
  book ending.
- **Export trimmed chapter...** exports the selected chapter. **Export all
  chapters...** exports every loaded chapter with its current cut settings and
  asks you to confirm that you have reviewed the cut points.

Exports keep the original filenames in the chosen output folder. Source files
and existing destination files are never overwritten.

Use the Intro and Outro time fields to adjust suggestions. **Remove intro notice
from** and **Resume audio at** control the opening cut. **End narration at** and
**Retain book ending from** control the outro. A retained book ending is optional.

Below the time fields, **Fine-tune** shows a five-second waveform around the
selected cut. Choose the cut in the drop-down, or click into its time field, then
click or drag on the waveform to place it. The removed side is shaded red. Cuts
snap to MP3 frame boundaries (about 26 ms for LibriVox 64 kbps files), so the
line shows exactly where the exported file will cut. With the waveform focused,
the arrow keys move the cut one frame; hold Shift to move ten frames.

After a cut is moved, the waveform recenters after one second without another
edit. Dragging defers recentering until the mouse is released. The current
waveform stays visible while the new window loads; near a file's start or end,
the view stays within the available audio.

Exports use MP3 stream copy and restore ID3 tags. MP3 frame boundaries limit cut
precision, so listen to uncertain transitions. The app recognizes spoken endings
such as "End of Chapter 3" or "End of Part XX" near the end of a track; generic
phrases such as "The End" are not used as cut markers.

## Settings

Open **Settings > Preferences** to change project/output folders, download
quality, the English speech model, intro/outro detection toggles, search-window
lengths, the extra intro removal margin, and outro silence refinement.
Preferences are saved in `%LOCALAPPDATA%/LibriVoxTrimmer/settings.json` on Windows. Models
remain cached in the app-data models folder and are downloaded on first use.

Both single-chapter analysis and **Analyse all chapters** always rerun
transcription. Previous transcripts are neither reused from disk nor saved for
future runs. Silence and speech-region measurements remain cached in both modes,
and downloaded speech models are kept for reuse.
**Clean Up Project** removes a project's remembered results with the project.

Longer intro retries are skipped when the notice and a complete numbered heading
are already recovered and only later narration reaches the initial window edge.
Missing notices, incomplete headings, unresolved front matter, and speech-coverage
gaps still receive the existing recovery checks.

Detection preferences are also stored in each `.lvproj`. Opening a project
restores its detection preferences; older projects use the current app defaults.
Saving Preferences updates the current project's detection preferences and the
app defaults, but never recalculates its saved trim times. Rerun detection to use
changed detection preferences. Folder changes apply to new projects, not existing
files. **Restore Defaults** resets the dialog; **Save** applies those defaults.

### Narrator-Name Hints

**Settings > Preferences > Advanced > Use narrator-name hints** is on by default.
When enabled, chapter-specific catalog narrator names are supplied as vocabulary
hints only to the focused reader-credit retry and its shorter fallback. Initial
intro/outro transcription, heading retries, and speech-gap recovery stay unhinted.
Tracks without matched narrator metadata behave as before. Existing saved on/off
choices in preferences and projects are preserved; **Restore Defaults** enables
hints. Disable the option when checking suspected bias or incorrect metadata.

For an A/B comparison, analyse a chapter with the option off, note its credit cut
and review warnings, then enable the option, save Preferences, and analyse the same
chapter again. Preview the credit and start of narration in both cases. Include
unusual names and chapters where the expected name is not spoken. Rerunning analysis
replaces the current suggestions, so record the baseline before running the hinted case.

Hinted credit retries are freshly transcribed too. The progress message names
the hotwords when used. Hints may invent or bias words; the existing credit-cue
and pause checks still apply. A development comparison across 13 narrators found
fewer intro warnings with hints enabled, but this is not a measured accuracy rate
or a guarantee of safe cuts. The option remains experimental, as its tooltip
notes. It is available in the current source,
and in the 0.8.0 beta installer and portable ZIP.

## Projects

Use **Project > Save Project** to create a `.lvproj` manifest containing chapter
locations, audio metadata, and the selected keep ranges. Reopen that file with
**Project > Open Project** to resume. Chapters downloaded by the app are stored
beside the manifest in a project-owned `*_files` folder. **Project > Clean Up
Project** removes the manifest and that managed download folder; MP3s opened from
elsewhere are not deleted. **Fetch chapters** creates a project automatically in
the per-user app-data `Projects` folder (on Windows,
`%LOCALAPPDATA%/LibriVoxTrimmer/Projects`) using the URL's project slug; it does
not ask for a save location. Incomplete downloads are removed the next time the
app starts.

## Automated Windows Builds

The **Windows Build and Release** GitHub Actions workflow can be run manually.
Its default is an artifact-only test build: an unsigned per-user installer,
portable ZIP, and checksum files, without publishing a release. Publishing is an
explicit choice restricted to the default branch and an unused version/tag.
See [the Windows build guide](packaging/README.md) for the controls and safeguards.

The 0.8.0 release includes the installer and portable ZIP. Unsigned installers
may still trigger Windows warnings.

## Roadmap

- More detailed progress and completion messages for extraction, analysis, and
  export.
- Signed Windows releases.

## License

Licensed under GPL-3.0. See [`LICENSE`](LICENSE).
