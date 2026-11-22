**English** | [简体中文](../zh-CN/BGM.md)

# BGM

`inputs/bgm/` is a **local input directory** for background music. It is not part of the repository and is ignored by Git.

## Inputs layout

```text
inputs/
  bgm/      # background music (used by the pipeline today)
  fonts/    # reserved for custom subtitle fonts
  models/   # reserved for local model files
```

Only `inputs/bgm/` is consumed today. `fonts/` and `models/` are reserved convention slots; the pipeline does not read them yet.

## What it is for

`reachreel run` scans `inputs/bgm/` for audio files, picks one at random (or uses `--bgm FILE`), loops it to the video length, and ducks it under narration and ambient sound.

- If `inputs/bgm/` is missing or empty, the pipeline runs without BGM.
- `--bgm FILE` selects a specific track.
- `--no-bgm` disables BGM for that run.
- The directory is created automatically on first run.

## Supported formats

`.mp3` `.wav` `.m4a` `.aac` `.flac` `.ogg` `.opus`

## Naming and selection

Naming tracks by mood makes manual selection easier:

```text
inputs/bgm/
  calm-morning.mp3
  mountain-air.mp3
  river-walk.mp3
```

```bash
reachreel run video.mp4 --bgm inputs/bgm/calm-morning.mp3
```

## No bundled tracks

No BGM is bundled with the repository. Put your own tracks in `inputs/bgm/`; all audio files are ignored by Git. If you intentionally want to version a track, force-add it with `git add -f`.

## Licensing

Use CC0 or clearly commercial-use-licensed music, and verify the license for every track before publishing. Do not redistribute third-party tracks in this repository without your own license review.

## Tracks used during local validation

These tracks were used locally and are **not distributed** with the repository:

| File | Title | Source | Metadata license |
|---|---|---|---|
| `niagara-ambient.mp3` | Endless Ethereal Ambient Texture with Glockenspiel, Floating Pads and Water | https://archive.org/details/soundica-fs-868373-endless-ethereal-ambient-texture-with-glockenspi | CC0 1.0 |
| `ski-ukulele.mp3` | UKULELE BALLAD BUTTERFLY | https://archive.org/details/UkuleleBalladButterfly | CC0 1.0 |

The Internet Archive metadata currently marks both items as CC0 1.0, but that does not prove the uploader had the right to dedicate them. Do not redistribute them without your own license review.
