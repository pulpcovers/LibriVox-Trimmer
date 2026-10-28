# LibriVox Trimmer

A Windows-first desktop app for reviewing and trimming LibriVox audiobook
chapters. It analyses chapter openings and endings, suggests cut points, and
exports approved MP3 intervals without re-encoding.

Current version: 0.5.0

## Highlights

- Fetch chapters from a LibriVox project or open local MP3 files.
- Analyse one chapter or a complete loaded book.
- Preview proposed cuts and retained audio, then adjust boundaries manually.
- Save projects and resume work later; export one chapter or batch-export all.

## Safety

Detection only suggests cuts. It never trims automatically, and exports do not
modify source MP3s. Speech recognition can miss or misidentify words, so review
and preview uncertain boundaries before exporting.

This repository contains the Python source; packaged desktop installers are not
currently published.

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
files** to load local chapters. Load the whole book when possible: the first and
last chapters in the loaded list provide context for opening announcements and
book endings.

### Analyse and Review

Select a chapter and choose **Analyse selected chapter**, or choose **Analyse all
chapters** to process the loaded list. The app suggests where to remove intro
notices and chapter-ending announcements; it does not apply cuts automatically.
Review the Intro and Outro fields and listen to the previews before exporting.

Chapters marked **Review intro** or **Review outro** need extra attention. Hover
over a marked chapter to see the reason. These warnings are saved with the
project. Re-running analysis updates warnings for the sections that were checked.
On the first loaded track, unique spoken title or introduction text before the
LibriVox notice is kept; an earlier announcement can be removed when it is
repeated after the notice.

On later tracks, attached reader credits are checked using a short independent
transcription and a measured pause. If the credit ending cannot be confirmed,
the credit and following speech are retained and marked **Review intro** rather
than risking removal of the chapter title or narration. Check these previews and
adjust the cut manually if needed. Pauses alone do not identify a reader credit.

### Preview and Export

- **Preview intro cut** plays the proposed removed notice.
- **Preview Kept Intro** starts with the preserved opening, skips the removed
  notice, and continues through kept narration and any retained book ending.
- **Preview outro cut** plays the proposed removed ending marker.
- **Preview chapter ending** plays the end of the kept narration and any retained
  book ending.
- **Export trimmed chapter...** exports the selected chapter. **Export all
  chapters...** exports every loaded chapter with its current cut settings and
  asks you to confirm that you have reviewed the cut points.

Use the Intro and Outro time fields to adjust suggestions. **Remove intro notice
from** and **Resume audio at** control the opening cut. **End narration at** and
**Retain book ending from** control the outro. A retained book ending is optional.

Below the time fields, **Fine-tune** shows a five-second waveform around the
selected cut. Choose the cut in the drop-down, or click into its time field, then
click or drag on the waveform to place it. The removed side is shaded red. Cuts
snap to MP3 frame boundaries (about 26 ms for LibriVox 64 kbps files), so the
line shows exactly where the exported file will cut. With the waveform focused,
the arrow keys move the cut one frame; hold Shift to move ten frames.

Exports use MP3 stream copy and restore ID3 tags. MP3 frame boundaries limit cut
precision, so listen to uncertain transitions. Outro markers include explicit
chapter, section, introduction, and prologue endings. Numbered endings such as
**End of Part XX**, **End of Volume XX**, and **End of Book 2, Chapter 3** are
also recognized near the end of a track; generic phrases such as "The End" are
not used as cut markers.

## Settings

Open **Settings > Preferences** to change project/output folders, download
quality, the English speech model, intro/outro detection toggles, search-window
lengths, the extra intro removal margin, and outro silence refinement. Preferences
use the intro search-window length as a maximum, not the initial slice length.
Existing saved projects keep their configured maximum.
Preferences are saved in `%LOCALAPPDATA%/LibriVoxTrimmer/settings.json` on Windows. Models
remain cached in the app-data models folder and are downloaded on first use.

Detection preferences are also stored in each `.lvproj`. Opening a project
restores its detection preferences; older projects use the current app defaults.
Saving Preferences updates the current project's detection preferences and the
app defaults, but never recalculates its saved trim times. Rerun detection to use
changed detection preferences. Folder changes apply to new projects, not existing
files. **Restore Defaults** resets the dialog; **Save** applies those defaults.

## Projects

Use **Project > Save Project** to create a `.lvproj` manifest containing chapter
locations, audio metadata, and the selected keep ranges. Reopen that file with
**Project > Open Project** to resume. Chapters downloaded by the app are stored
beside the manifest in a project-owned `*_files` folder. **Project > Clean Up
Project** removes the manifest and that managed download folder; MP3s opened from
elsewhere are not deleted. **Fetch chapters** creates a project automatically in
the per-user app-data `Projects` folder (on Windows,
`%LOCALAPPDATA%/LibriVoxTrimmer/Projects`) using the URL's project slug; it does
not ask for a save location. To preserve downloads made before project files
were added, open those MP3s and save them into a project once.

While downloading, the ZIP is stored inside the project's `*_files` folder.
If the app exits before its `.lvproj` manifest is written, that folder is an
incomplete download. The next app launch removes incomplete project folders;
completed projects are preserved, and folders locked by another active app
instance are skipped.

## Roadmap

- More detailed progress and completion messages for extraction, analysis, and
  export.
- A packaged desktop release.

## License

Licensed under GPL-3.0. See [`LICENSE`](LICENSE).
