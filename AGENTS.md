# AGENTS.md — ReachReel

This file is the working agreement for AI agents and contributors in this repository.

## Language policy (mandatory)

- All source code, identifiers, comments, docstrings, log messages, error messages, commit messages, and PR descriptions MUST be written in English.
- All documentation is bilingual: `README.md` (English) and `README.zh-CN.md` (Simplified Chinese); `docs/en/` and `docs/zh-CN/` hold the two language versions of every document.
- User documentation is Chinese-authoritative; engineering documentation is English-authoritative. Both versions must be kept in sync.
- Prompt content is data, not source code. Language-specific prompts live under `src/reachreel/prompts/<language>/`.
- Do not add Chinese comments or Chinese log messages to Python source.

## What this repository is

ReachReel is a fully local video narration pipeline:

```text
source video -> scene detection -> VLM understanding -> narration script
             -> local TTS -> subtitles -> audio mix -> FFmpeg render -> QA
```

The product is a pipeline, not a timeline editor. The authoritative state is the work directory: `manifest.json` plus the stage artifacts.

## Repository layout

```text
src/reachreel/
  cli/          # argparse entry points and command wiring
  core/         # config, subprocess, JSON, logging, shared utilities
  domain/       # pure models and timeline placement
  engines/      # external model adapters (VLM, TTS, ASR)
  generation/   # LLM script generation
  i18n/         # UI language negotiation and catalog loader
  locales/      # en.toml, zh-CN.toml UI copy
  media/        # FFmpeg-facing work: scenes, subtitles, audio, render
  pipeline/     # orchestration, caching, manifest, QA
  prompts/      # language-specific prompts and content fallbacks
tests/
  unit/         # fast, deterministic tests
  integration/  # optional end-to-end tests (may require FFmpeg/models)
  fixtures/     # small generated media fixtures
docs/
  en/           # English versions of all docs
  zh-CN/        # Chinese versions of all docs
  *.md          # bilingual index and glossary
inputs/         # local input root (ignored): bgm/, fonts/, models/
output/         # local workdir/render root (ignored)
```

Dependency direction is one-way:

```text
cli -> pipeline -> {generation, engines, media, domain} -> core
```

`domain/` must not import adapters, media tooling, or the pipeline. `engines/` and `media/` may import `domain/` and `core/`. Do not introduce cycles.

## Commands

```bash
# Environment (Python 3.11-3.12; 3.12 is recommended)
python3.12 -m venv .venv
uv pip install --python .venv/bin/python -e .

# Quality gates
uvx ruff check src/reachreel tests scripts
.venv/bin/python -m unittest discover -s tests -v

# Commit-message hook (once per clone)
git config core.hooksPath .githooks

# Environment check
.venv/bin/reachreel doctor

# Build
uv build
```

The installed command is `reachreel`.

## Architecture invariants

- **Disk is the source of truth.** A job is defined by its work directory: `manifest.json` and the artifacts beside it. Do not keep a second in-memory source of truth.
- **One pipeline.** CLI commands, future HTTP endpoints, and the future local service must call the same `Pipeline` methods. Do not duplicate stage logic in a client.
- **Stages are independently runnable.** The canonical order is `probe -> vlm -> script -> tts -> subtitles -> mix -> render -> qa`. Every stage must validate its inputs and report the next actionable command when an input is missing.
- **Caching is explicit.** Cache keys must include every input that changes the result: source identity, model/provider, voice, prompt version, settings, and tool versions where relevant.
- **Local-first.** The default path must not require a cloud API. Remote providers are optional adapters, never a hidden requirement.
- **Local data is not repo data.** `inputs/` and `output/` are local-only; never commit input media or rendered output.
- **No timeline editor.** Do not add multi-track editing, transitions, or a full NLE. Keep the product focused on the handoff between existing tools and publishing.

## Code rules

- Python 3.11+, `from __future__ import annotations`.
- Prefer dataclasses and explicit types. Avoid global mutable state.
- Keep the CLI thin. Business logic belongs in `pipeline/`, `generation/`, `engines/`, or `media/`.
- Run external commands through `core.util.run`. Keep a bounded timeout and preserve the stderr tail in errors.
- All subprocess decoding must use UTF-8 with replacement. Never rely on the system locale.
- Write JSON and manifests with `core.util.atomic_write_json` (temporary file + fsync + replace).
- Validate user-provided input paths. Do not allow path traversal or symlink escape out of an allowed directory.
- Never write API keys, tokens, or credentials into `manifest.json`, logs, or generated packages.
- A new configuration key must be added in all of these places:
  1. `core/config.py`
  2. `config.example.toml`
  3. `docs/en/CONFIGURATION.md` and `docs/zh-CN/CONFIGURATION.md`
  4. CLI options when users need to override it
- A new artifact or stage must be added to the orchestrator, `manifest.json`, the relevant docs, and tests.

## Internationalization

- Log messages and internal errors are canonical English.
- User-visible messages should be rendered through `reachreel.i18n.t()` once the i18n layer lands.
- Keep language-specific prompt text out of Python source. Prompt packs belong in `src/reachreel/prompts/<language>/`.
- Do not merge UI language and content language into one setting.
- CI must reject CJK characters in Python source outside `prompts/` and `locales/`.

## Documentation rules

- `README.md` is English; `README.zh-CN.md` is Simplified Chinese. Keep the command examples identical in both.
- Every document under `docs/` has a Chinese version in `docs/zh-CN/` and an English version in `docs/en/`. A change to one language must update the other in the same change.
- `docs/README.md` is the only bilingual navigation page.
- Every translated document starts with a language banner that links to the real counterpart.
- Never translate command names, option names, paths, or default values.
- Update `CHANGELOG.md` and `CHANGELOG.zh-CN.md` for behavior changes and breaking changes.

## Testing and verification

- Run `uvx ruff check src/reachreel tests` and `.venv/bin/python -m unittest discover -s tests -v` before handing off.
- Add characterization tests before restructuring behavior. A passing unit test is not proof that the CLI wiring works.
- Keep model-dependent tests out of the default suite. Use `say`, `--asr none`, and generated fixtures for smoke tests.
- For packaging changes, run `uv build` and verify the wheel contains `src/reachreel`, not the old package.
- Do not claim success without command output or test results.

## Change discipline

- Read the owning module and the relevant document before editing.
- Prefer the existing seam over a new abstraction. A new module needs one written sentence explaining why the current model cannot express the change.
- Do not rename files, modules, CLI flags, environment variables, or manifest fields silently. Add a migration note and update the docs in the same change.
- Do not rewrite pushed history. Do not commit secrets. Do not run destructive git commands without explicit confirmation.
- Use Conventional Commits in English, for example:
  - `refactor(package): move reachreel to src layout`
  - `feat(media): add scene-aware frame selection`
  - `docs(i18n): add English user guide`
  - `chore(ci): run ruff and unit tests`
- Changelog parity is enforced by `scripts/check_changelog.py` in CI: same version headings, same sections (English file in English, Chinese file in Chinese), and the same number of bullets per section.
- The format is enforced by `scripts/check_commit_messages.py`: locally through `.githooks/commit-msg` and in CI over the commits a push or pull request adds. Header is `type(scope): subject` (scope optional, `!` for a breaking change), at most 72 characters, no trailing period, English only.
