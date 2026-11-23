**English** | [简体中文](../zh-CN/LANGUAGES.md)

# Language and voice specification

How ReachReel names, validates, and extends languages. Read this before adding a
language, a voice, or a TTS engine; [LOCALIZATION.md](LOCALIZATION.md) covers UI
copy, which is a separate problem.

## 1. Naming rules

- Every user-facing language value is a **BCP 47 tag** (RFC 5646), the same
  scheme browsers report: `en`, `en-US`, `en-GB`, `zh-CN`, `ja-JP`, `pt-BR`.
- Input is tolerant, output is canonical: `en_us`, `EN-us`, `zh_CN.UTF-8` are
  accepted and normalized to `en-US` / `zh-CN` (primary subtag lower case,
  region upper case).
- **Engine-internal codes never appear outside the adapter.** Kokoro's `a` / `b`
  / `z` / `j`, Whisper's `zh`, and any similar shortcut stay inside
  `engines/`; they must not show up in `config.toml`, CLI flags, logs, `doctor`,
  or docs.
- No silent fallback. An unsupported tag is an error that lists the supported
  values; quietly switching to Chinese is how a wrong-language voiceover ships.

Why the engine codes stay hidden: Kokoro's single letters exist to separate
American (`a`) from British (`b`) English, which `en` alone cannot express, and
its alias table is incomplete (`zh` is accepted, `zh-CN` is not). Mapping is the
adapter's job, not the user's.

## 2. The four language axes

| Setting | Scope | Example | Consumer |
|---|---|---|---|
| `ui_language` | CLI copy and logs | `zh-CN` | `i18n` catalog |
| `content_language` | Script and subtitle text | `zh` / `en` | `prompts/<primary>/`, subtitle splitting |
| `tts_language` | Pronunciation of the voiceover | `zh-CN` | TTS engine |
| `asr_language` | Whisper transcription | `zh` | `media/subtitles.py` |

Rules:

1. An explicit setting always wins; otherwise `tts_language` and `asr_language`
   follow the primary subtag of `content_language`.
2. `tts_language` must match the language of the selected voice. A mismatch is a
   hard error, because the audio is still produced and passes QA while sounding
   wrong.
3. `asr_language` is a *transcription* hint, not an output language: it only
   improves timing, so it may differ from `tts_language` when the voice speaks a
   different language than the script.

## 3. Language profiles

One registry, `src/reachreel/languages.py`, defines every supported language as
a profile:

| Field | Purpose |
|---|---|
| `tag` | Canonical BCP 47 tag |
| `primary` | Primary subtag, used for prompt packs and Whisper |
| `speech_chars_per_second` | Script length target, so English is not sized like Chinese |
| `subtitle_max_chars` | Line length default, ~18 for CJK, ~42 for whitespace languages |
| `font_hint` | Font that covers the script |
| `default_voice` | Default Kokoro voice when `voice` is left empty |
| `default_say_voice` | Default macOS `say` debug voice when `say_voice` is left empty |
| `iso639_2` | ISO 639-2 code written to soft-subtitle track metadata (`chi`, `eng`) |
| `tts` | `{engine: internal code}` for each adapter |
| `requires` | Optional dependency extra, for example `tts-en` |

Status per release:

| Tag | TTS | Notes |
|---|---|---|
| `zh-CN` | supported | Kokoro + `misaki[zh]`; validated end to end on real footage |
| `en-US`, `en-GB` | supported | Kokoro + torch-free `tts-en` extra (`spacy` + `en_core_web_sm` + `espeakng-loader` + `phonemizer-fork`); validated end to end (0.00%–0.51% Whisper round-trip CER, QA 20/20 on Niagara Falls 1080p) |
| `ja-JP` | planned | `misaki[ja]` |
| `es-ES`, `fr-FR`, `hi-IN`, `it-IT`, `pt-BR` | planned | espeak-ng G2P |

Anything not in this table is rejected with the list of supported tags.

## 4. Engine contract

Every TTS adapter implements:

- `supported_tags() -> list[str]`
- `map_tag(tag) -> str | None` — the engine's internal code, or `None` when unsupported
- `voice_language(voice) -> str | None` — the tag implied by the voice name

The pipeline refuses to synthesize when `map_tag(configured)` and
`voice_language(voice)` disagree, and the message names both the tag and the
voice. `reachreel doctor` prints the resolved axes and the backend each language
will use, so a dependency problem shows up before a 40-minute run.

## 5. Dependency policy

- One optional extra per language group: `tts-zh`, `tts-en`, `tts-ja`. Languages
  served by espeak-ng need no extra.
- The default install stays torch-free. `mlx-audio`'s `KokoroPipeline` runs
  English G2P with `trf=False` (`en_core_web_sm` + `EspeakFallback`), so the
  `tts-en` extra installs only `spacy`, `num2words`, `espeakng-loader`, and
  `phonemizer-fork` — skipping `spacy-curated-transformers` and the ~600 MB
  `torch` download. Run `python -m spacy download en_core_web_sm` once after
  installing `reachreel[tts-en]`.
- A language is only marked supported after §6 passes on the pinned versions. A
  measured example of why: with numpy 2.5 and `misaki[en]`, pulling the
  transformer spacy model (`en_core_web_trf`) caused intermittent
  `ValueError: Only length-1 arrays can be converted to Python scalars`, while
  the non-transformer `en_core_web_sm` stack stayed stable across both `en-US`
  and `en-GB`. Intermittent dependency state is not "supported".

## 6. Gate for declaring a language supported

All of these must pass, and the results belong in the pull request:

1. `reachreel doctor` resolves the axes and the backend for that language.
2. Round trip: synthesize a sentence and a paragraph, transcribe with Whisper in
   the same language, and compare. A character error rate above 5% for English
   (10% for Chinese) means not supported.
3. A real clip runs end to end in that language and passes QA 20/20.
4. Subtitle defaults (`subtitle_max_chars`, font) look right in a rendered frame.
5. Unit tests cover the registry entry, the tag mapping, the voice/language
   mismatch error, and the unsupported-tag error.

## 7. Non-goals for the first multilingual release

- Mixed-language narration inside one project.
- Per-segment language switching.
- Automatic language detection from the footage.
- Voice cloning in any language.
