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

Select a chapter and choose **Analyse selected chapter** for an English `base.en` Whisper
analysis starting with the first 20 seconds and final 30 seconds, or choose **Analyse all
chapters** to process every loaded chapter sequentially. If the main outro pass
misses a closing phrase, the detector checks the final 10 seconds separately,
then retries with 15 seconds if needed.
If an intro notice is missed, it retries the first 10 seconds, then checks
seconds 5-18 as a notice-focused slice. It expands to 30 seconds and then the
configured intro search maximum (60 seconds by default) when no notice is found,
the notice approaches the slice edge, speech ends mid-sentence near that edge,
or a retained first-chapter opening needs following chapter context. All slices
respect the configured maximum and track duration. Reliable earlier words and
original-file timestamps are retained during expansion. Final chapters
may also need 12-, 20-, 18-, or 22-second checks to recognize the book ending.

Whisper can omit audible speech while confidently transcribing its surroundings,
so intro detection also checks transcript coverage. Silero voice-activity
detection (bundled with faster-whisper) finds speech in the analyzed opening;
speech of a second or more with no transcribed words, near the notice or in the
first 40 seconds when no notice was found, is re-transcribed as a targeted crop.
Recovered words replace any misplaced words in that range and are kept only when
they reduce untranscribed speech. Speech that still cannot be transcribed marks
the chapter **Review intro** with the affected times, including chapters where
no notice was matched at all, because a notice may have been missed.

Chapter endings get the same check over the outro window: untranscribed speech
within 6 seconds before the closing marker or later, or anywhere in the window
when no marker matched, is re-transcribed. On the last chapter, speech covered by
any of its tail retries counts as transcribed. Speech that still cannot be
transcribed marks the chapter **Review outro**. Book-ending titles match whether
numbers are spoken or written as digits ("Twenty Thousand" or "20,000"), and a
closing marker such as "End of Part 2, Chapter 22" is removed as a whole.
On first use, the model is downloaded to the per-user app-data models folder.
Detection only suggests keep times; each completed chapter shows its keep range
in the list. Preview the proposed cuts and adjust them before trimming.
For a detected outro, FFmpeg can move the keep end to the measured speech onset
when a sustained silence begins at the candidate boundary; otherwise it keeps
the Whisper timestamp.

When a recognized chapter/part/section announcement precedes the LibriVox notice,
detection separates the chapter information from the notice. **Remove
intro notice from** marks where removal starts; **Resume audio at** marks where
narration resumes. Audio before the notice-start time is retained. A notice-start
value of zero keeps the previous behavior of removing the entire opening.

The selected-chapter pane places detection above separate **Intro** and **Outro**
sections. **End narration at** is the end of the main retained interval.
**Preview Kept Intro** starts with the preserved opening, skips the removed
notice, then continues through the kept narration and any retained book ending;
**Export trimmed chapter** writes the output using the selected boundaries without
modifying source files.
When a suitable nearby pause is found, the notice-start cut is placed inside
that pause using FFmpeg silence detection, rather than at an estimated word end,
to help retain the complete announcement. Without a suitable pause, the suggested
boundary remains unchanged and should be checked with playback.

**Preview intro cut** plays only the removed notice. **Preview Kept Intro** plays
the preserved opening, skips the removed notice, and continues through kept
narration and any retained book ending so you can assess the transition. Export
joins all retained segments using FFmpeg stream copy and restores ID3 tags;
MP3 frame boundaries still limit cut precision. The notice-start value is saved
per chapter in the project file. Rerun detection on existing projects to generate
announcement-preserving suggestions; old saved cuts are not changed automatically.

For the first chapter in the loaded list, detection checks whether the opening
announcement is repeated after the LibriVox notice. If its chapter/part identity
and title/author content are repeated, it removes the earlier copy along with the
notice and keeps the richer announcement after it. Unique opening information
is preserved when repetition cannot be established.

After chapter 1, recognized book-title and author headers are removed. If the
chapter number repeats after the notice, the earlier header and notice are
removed and the later chapter number/title is retained. Word, digit, and Roman
chapter numbers are compared. If Whisper misses or mishears that repeated number,
a repeated phrase from the earlier book title before the author credit can confirm
the duplicate heading. If neither the number nor title repeats, a `Chapter N of
[book]` header keeps its chapter/part declaration while removing the book details.
Other unique chapter titles are not shortened speculatively.
A reader credit directly attached to a notice is included in its proposed
removal when its ending can be identified. Short pauses before the credit and
within its name or location are allowed, but a following chapter/part/section
declaration stops the credit.

For **Resume audio at**, transcription identifies the notice or credit ending,
then FFmpeg looks for a complete nearby pause before the following speech.
When confirmed, the cut is placed at the pause midpoint instead of adding a
fixed margin to an estimated word timestamp. Pauses inside the credit and
pauses truncated by the analysis window are rejected. If no suitable pause is
confirmed, the timestamp-plus-margin suggestion remains and the chapter is
marked **Review intro**, with details in its tooltip and the detection status.
Review warnings are saved per chapter in the project file, so the markers and
tooltip return when the project is reopened. Rerunning detection replaces or
clears the intro or outro warning for each section it analyzed; editing a cut
by hand does not. Preview uncertain cuts before exporting.

Numbered metadata endings such as **End of Volume XX** and **End of Book 2,
Chapter 3** are also recognized, but only when they start in the final 10 seconds
of the track. Generic phrases such as "The End" are not used as markers.

For the last chapter, detection uses the book's ID3 album title to distinguish
an **End of chapter** marker from a following **End of [book title]** announcement.
It suggests removing only the chapter marker when both can be separated. If the
title is unavailable or the two markers cannot be separated, it makes no new
outro suggestion; review any existing saved cut manually. Additional short-tail
checks may be needed to recognize both announcements.
Spoken title prefixes such as `Conan, Beyond the Black River` can match the album
title. A numbered `Chapter VIII` closing can qualify without `End of` only when
it immediately precedes a confirmed book-ending announcement. Retry transcripts
are matched separately to avoid interleaving overlapping words.

**Retain book ending from** is the editable point where playback/export resumes
after the removed chapter marker; **None** means there is no retained ending.
**Preview outro cut** plays only the removed marker, while **Preview chapter ending**
includes the retained book ending. **Preview Kept Intro** skips the removed intro
and outro sections while playing the preserved opening, kept narration, and any
retained book ending. These boundaries are saved per chapter and export
joins all retained segments without re-encoding. Both **Analyse selected chapter** and
**Analyse all chapters** use first/last positions in the currently loaded list;
load the whole book for those positions to identify the correct book chapters.

**Preview chapter ending**, below **Preview outro cut**, plays the last five
seconds of retained narration and then any retained book-ending announcement,
skipping the removed outro. For shorter retained narration it starts at the
retained start time; if no outro is removed, it plays the retained chapter's
last five seconds. Click it again to stop, as with the other previews.

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

Full app-data removal remains a mandatory release-packaging TODO, not a command
in this settings dialog yet.

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
