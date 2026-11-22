# Test fixtures

Large or generated media does not live in Git. The entries below are created on
demand by tests or by contributors:

- `tests/integration/test_smoke.py` builds its own 6-second clip with FFmpeg in
  a temporary directory, so it needs no checked-in video.
- When you add a fixture, keep it small (a few seconds, low resolution), generate
  it with a documented FFmpeg command, and leave the media file out of Git.

`.gitignore` excludes `tests/fixtures/*.mp4`, `*.mov`, `*.wav`, `*.mp3`,
`*.aiff`, `tests/fixtures/work*/`, and `tests/fixtures/list.txt`.

Suggested pattern for a new fixture:

```bash
ffmpeg -y -f lavfi -i testsrc2=size=320x180:rate=24:duration=3 \
  -pix_fmt yuv420p tests/fixtures/clip_3s.mp4
```
