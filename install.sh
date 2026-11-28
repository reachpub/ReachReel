#!/usr/bin/env bash
# ReachReel automated installer & initializer for Apple Silicon macOS.
# Usage:
#   ./install.sh [options]
#   curl -fsSL https://raw.githubusercontent.com/reachpub/ReachReel/main/install.sh | bash

set -euo pipefail

REPO_URL="${REACHREEL_REPO_URL:-https://github.com/reachpub/ReachReel.git}"
INSTALL_DIR=""
UI_LANG=""
CONTENT_LANG=""
MODEL_PROFILE=""
INSTALL_TTS_EN=""
SKIP_OLLAMA=0
NON_INTERACTIVE=0
DRY_RUN=0
LINK_GLOBAL=""

TOTAL_STEPS=7
CURRENT_STEP=0

# ---------- terminal styling ----------

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'
  C_BLUE=$'\033[34m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  IS_TTY=1
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_CYAN=""
  C_BLUE=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
  IS_TTY=0
fi

# ---------- bilingual helper ----------

is_zh() {
  [[ "${UI_LANG}" == "zh-CN" ]]
}

msg() {
  local en="$1"
  local zh="$2"
  if is_zh; then
    printf "%s" "$zh"
  else
    printf "%s" "$en"
  fi
}

# ---------- banner & progress UI ----------

print_banner() {
  printf "\n%s%s" "${C_BOLD}" "${C_CYAN}"
  cat <<'EOF'
  ____                 _     ____            _
 |  _ \ ___  __ _  ___| |__ |  _ \ ___  ___| |
 | |_) / _ \/ _` |/ __| '_ \| |_) / _ \/ _ \ |
 |  _ <  __/ (_| | (__| | | |  _ <  __/  __/ |
 |_| \_\___|\__,_|\___|_| |_|_| \_\___|\___|_|
EOF
  printf "%s" "${C_RESET}"
  printf "  %s%s%s\n" "${C_DIM}" \
    "$(msg 'Local-First Video Narration Pipeline for Apple Silicon Mac' 'Apple Silicon Mac 本地视频解说全自动流水线')" \
    "${C_RESET}"
  printf "  %s──────────────────────────────────────────────────────────%s\n\n" "${C_DIM}" "${C_RESET}"
}

draw_progress_bar() {
  local step="$1"
  local total="$2"
  local width=24
  local filled=$(( step * width / total ))
  local empty=$(( width - filled ))
  local pct=$(( step * 100 / total ))
  local bar=""
  local i
  for (( i = 0; i < filled; i++ )); do bar+="█"; done
  for (( i = 0; i < empty; i++ )); do bar+="░"; done
  printf "%s[%s]%s %3d%%" "${C_CYAN}" "${bar}" "${C_RESET}" "${pct}"
}

step_header() {
  CURRENT_STEP=$(( CURRENT_STEP + 1 ))
  local title_en="$1"
  local title_zh="$2"
  printf "\n"
  draw_progress_bar "${CURRENT_STEP}" "${TOTAL_STEPS}"
  printf "  %s[%d/%d] %s%s\n" "${C_BOLD}" "${CURRENT_STEP}" "${TOTAL_STEPS}" "$(msg "$title_en" "$title_zh")" "${C_RESET}"
}

info() {
  printf "  %s•%s %s\n" "${C_CYAN}" "${C_RESET}" "$1"
}

ok() {
  printf "  %s✓%s %s\n" "${C_GREEN}" "${C_RESET}" "$1"
}

warn() {
  printf "  %s!%s %s\n" "${C_YELLOW}" "${C_RESET}" "$1"
}

fail() {
  printf "  %s✗%s %s\n" "${C_RED}" "${C_RESET}" "$1" >&2
  exit 1
}

_SPINNER_PID=""
_SPINNER_LOG=""

_cleanup_spinner() {
  if [[ -n "${_SPINNER_PID}" ]]; then
    kill "${_SPINNER_PID}" 2>/dev/null || true
    wait "${_SPINNER_PID}" 2>/dev/null || true
    _SPINNER_PID=""
  fi
  if [[ -n "${_SPINNER_LOG}" ]]; then
    rm -f "${_SPINNER_LOG}"
    _SPINNER_LOG=""
  fi
}

trap '_cleanup_spinner; exit 130' INT TERM

run_with_spinner() {
  local label="$1"
  shift
  if (( DRY_RUN )); then
    info "[dry-run] $label -> $*"
    return 0
  fi
  local logfile
  logfile="$(mktemp -t reachreel-install.XXXXXX)"
  _SPINNER_LOG="$logfile"
  local start_ts
  start_ts="$(date +%s)"

  if (( IS_TTY )); then
    "$@" >"$logfile" 2>&1 &
    local pid=$!
    _SPINNER_PID="$pid"
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    local idx=0
    while kill -0 "$pid" 2>/dev/null; do
      local now_ts elapsed
      now_ts="$(date +%s)"
      elapsed=$(( now_ts - start_ts ))
      printf "\r  %s%s%s %s %s(%ds)%s   " \
        "${C_CYAN}" "${frames[$idx]}" "${C_RESET}" "$label" "${C_DIM}" "$elapsed" "${C_RESET}"
      idx=$(( (idx + 1) % ${#frames[@]} ))
      sleep 0.12
    done
    local rc=0
    wait "$pid" || rc=$?
    _SPINNER_PID=""
    local elapsed=$(( $(date +%s) - start_ts ))
    if (( rc == 0 )); then
      printf "\r  %s✓%s %s %s(%ds)%s\n" "${C_GREEN}" "${C_RESET}" "$label" "${C_DIM}" "$elapsed" "${C_RESET}"
      rm -f "$logfile"
      _SPINNER_LOG=""
      return 0
    else
      printf "\r  %s✗%s %s %s(%ds)%s\n" "${C_RED}" "${C_RESET}" "$label" "${C_DIM}" "$elapsed" "${C_RESET}"
      printf "\n%s--- Command output tail ---%s\n" "${C_DIM}" "${C_RESET}" >&2
      tail -n 25 "$logfile" >&2
      rm -f "$logfile"
      _SPINNER_LOG=""
      return "$rc"
    fi
  else
    info "$label ..."
    if "$@" >"$logfile" 2>&1; then
      local elapsed=$(( $(date +%s) - start_ts ))
      ok "$label (${elapsed}s)"
      rm -f "$logfile"
      _SPINNER_LOG=""
      return 0
    else
      local rc=$?
      tail -n 25 "$logfile" >&2
      rm -f "$logfile"
      _SPINNER_LOG=""
      return "$rc"
    fi
  fi
}

# ---------- interactive prompt helpers ----------

can_prompt() {
  (( NON_INTERACTIVE == 0 )) && (: </dev/tty) 2>/dev/null
}

prompt_choice() {
  local prompt_text="$1"
  local default_val="$2"
  local answer=""
  if ! can_prompt; then
    printf "%s" "$default_val"
    return 0
  fi
  printf "  %s?%s %s %s[%s]%s: " "${C_BLUE}" "${C_RESET}" "$prompt_text" "${C_DIM}" "$default_val" "${C_RESET}" >/dev/tty
  read -r answer </dev/tty || answer=""
  if [[ -z "$answer" ]]; then
    printf "%s" "$default_val"
  else
    printf "%s" "$answer"
  fi
}

prompt_yes_no() {
  local prompt_text="$1"
  local default_yn="$2" # y or n
  if ! can_prompt; then
    [[ "$default_yn" == "y" ]]
    return $?
  fi
  local hint="Y/n"
  [[ "$default_yn" == "n" ]] && hint="y/N"
  local answer=""
  printf "  %s?%s %s %s[%s]%s: " "${C_BLUE}" "${C_RESET}" "$prompt_text" "${C_DIM}" "$hint" "${C_RESET}" >/dev/tty
  read -r answer </dev/tty || answer=""
  answer="${answer:-$default_yn}"
  case "$answer" in
    y|Y|yes|YES|是) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------- argument parsing ----------

usage() {
  cat <<EOF
ReachReel Installer

Usage:
  ./install.sh [options]

Options:
  --lang en|zh-CN            Installer & default CLI language (auto-detected)
  --content-lang zh|en       Default narration & subtitle language (default: zh)
  --dir PATH                 Install directory when running outside a git checkout (default: ~/ReachReel)
  --models standard|light|skip
                             Ollama model tier to download:
                               standard = qwen3-vl:8b-instruct + qwen3:30b-instruct (32GB+ RAM)
                               light    = qwen3-vl:4b-instruct + qwen3.5:9b (16GB RAM)
                               skip     = skip downloading Ollama models
  --tts-en                   Install optional English Kokoro TTS dependencies (spacy + en_core_web_sm)
  --no-tts-en                Skip optional English Kokoro TTS dependencies
  --skip-ollama              Skip Ollama installation and model pulling
  --link-global              Symlink reachreel into ~/.local/bin/reachreel
  -y, --yes                  Non-interactive mode using smart defaults
  --dry-run                  Preview every step without modifying the system
  -h, --help                 Show this help message
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lang)
        shift
        case "${1:-}" in
          zh|zh-CN|zh_CN) UI_LANG="zh-CN" ;;
          en|en-US|en_US) UI_LANG="en" ;;
          *) fail "Unsupported --lang value: ${1:-}" ;;
        esac
        ;;
      --content-lang)
        shift
        case "${1:-}" in
          zh|en) CONTENT_LANG="$1" ;;
          *) fail "Unsupported --content-lang value: ${1:-}" ;;
        esac
        ;;
      --dir)
        shift
        INSTALL_DIR="${1:-}"
        ;;
      --models)
        shift
        case "${1:-}" in
          standard|light|skip) MODEL_PROFILE="$1" ;;
          *) fail "Unsupported --models value: ${1:-} (expected: standard, light, skip)" ;;
        esac
        ;;
      --tts-en)
        INSTALL_TTS_EN="yes"
        ;;
      --no-tts-en)
        INSTALL_TTS_EN="no"
        ;;
      --skip-ollama)
        SKIP_OLLAMA=1
        ;;
      --link-global)
        LINK_GLOBAL="yes"
        ;;
      -y|--yes)
        NON_INTERACTIVE=1
        ;;
      --dry-run)
        DRY_RUN=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1 (use --help for usage)"
        ;;
    esac
    shift
  done
}

detect_default_lang() {
  if [[ -n "$UI_LANG" ]]; then
    return
  fi
  local locale="${REACHREEL_LANG:-${LC_ALL:-${LANG:-en}}}"
  case "$locale" in
    zh*|ZH*|Zh*) UI_LANG="zh-CN" ;;
    *) UI_LANG="en" ;;
  esac
}

# ---------- main steps ----------

main() {
  parse_args "$@"
  detect_default_lang
  print_banner

  if (( DRY_RUN )); then
    warn "$(msg 'DRY-RUN MODE: no packages, models, or files will be modified.' '预览模式（--dry-run）：不会实际安装软件、下载模型或修改文件。')"
  fi

  # ---- Step 1: Hardware & OS check ----
  step_header "Checking macOS & hardware" "检查系统与硬件环境"

  local os_name arch_name ram_bytes ram_gb chip_name
  os_name="$(uname -s)"
  arch_name="$(uname -m)"
  if [[ "$os_name" != "Darwin" || "$arch_name" != "arm64" ]]; then
    fail "$(msg \
      "ReachReel requires macOS on Apple Silicon (M1/M2/M3/M4). Detected: ${os_name}/${arch_name}." \
      "ReachReel 目前仅支持 Apple Silicon Mac（M1/M2/M3/M4）。当前环境：${os_name}/${arch_name}。")"
  fi
  ram_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
  ram_gb=$(( ram_bytes / 1024 / 1024 / 1024 ))
  chip_name="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
  ok "$(msg "Detected: ${chip_name}, ${ram_gb} GB RAM (macOS arm64)" "检测到：${chip_name}，${ram_gb} GB 内存（macOS arm64）")"

  local rec_profile="standard"
  local rec_workers=2
  if (( ram_gb > 0 && ram_gb < 28 )); then
    rec_profile="light"
    rec_workers=1
    info "$(msg \
      "Recommended for ${ram_gb} GB RAM: light model pack (qwen3-vl:4b-instruct + qwen3.5:9b, vlm_workers=1)" \
      "针对 ${ram_gb} GB 内存推荐：轻量模型档（qwen3-vl:4b-instruct + qwen3.5:9b，vlm_workers=1）")"
  else
    info "$(msg \
      "Recommended for ${ram_gb} GB RAM: standard model pack (qwen3-vl:8b-instruct + qwen3:30b-instruct, vlm_workers=2)" \
      "针对 ${ram_gb} GB 内存推荐：标准模型档（qwen3-vl:8b-instruct + qwen3:30b-instruct，vlm_workers=2）")"
  fi

  # ---- Step 2: Interactive preferences ----
  step_header "Configuring installation options" "配置安装选项"

  if can_prompt; then
    local lang_ans
    lang_ans="$(prompt_choice "$(msg 'Interface language (1=English, 2=简体中文)' '界面语言（1=English, 2=简体中文）')" "$(is_zh && echo '2' || echo '1')")"
    if [[ "$lang_ans" == "2" || "$lang_ans" == "zh" || "$lang_ans" == "zh-CN" ]]; then
      UI_LANG="zh-CN"
    else
      UI_LANG="en"
    fi
  fi

  if [[ -z "$CONTENT_LANG" ]]; then
    local default_cl="zh"
    is_zh || default_cl="en"
    if can_prompt; then
      local cl_ans
      cl_ans="$(prompt_choice "$(msg 'Default narration language (zh / en)' '默认成片旁白语言（zh / en）')" "$default_cl")"
      case "$cl_ans" in
        en|EN) CONTENT_LANG="en" ;;
        *) CONTENT_LANG="zh" ;;
      esac
    else
      CONTENT_LANG="$default_cl"
    fi
  fi

  if [[ -z "$MODEL_PROFILE" ]]; then
    if (( SKIP_OLLAMA )); then
      MODEL_PROFILE="skip"
    elif can_prompt; then
      info "$(msg \
        'Ollama model packs: [1] standard (qwen3-vl:8b + qwen3:30b, ~24GB)  [2] light (qwen3-vl:4b + qwen3.5:9b, ~9GB)  [3] skip' \
        'Ollama 模型档位：[1] standard 标准档（qwen3-vl:8b + qwen3:30b，约24GB）  [2] light 轻量档（qwen3-vl:4b + qwen3.5:9b，约9GB）  [3] skip 稍后手动下载')"
      local def_m="1"
      [[ "$rec_profile" == "light" ]] && def_m="2"
      local m_ans
      m_ans="$(prompt_choice "$(msg 'Select model pack (1/2/3)' '选择模型档位（1/2/3）')" "$def_m")"
      case "$m_ans" in
        1|standard) MODEL_PROFILE="standard" ;;
        2|light) MODEL_PROFILE="light" ;;
        *) MODEL_PROFILE="skip" ;;
      esac
    else
      MODEL_PROFILE="$rec_profile"
    fi
  fi

  if [[ -z "$INSTALL_TTS_EN" ]]; then
    local def_en="n"
    [[ "$CONTENT_LANG" == "en" ]] && def_en="y"
    if prompt_yes_no "$(msg 'Install optional English Kokoro voiceover pack (tts-en + spaCy)?' '是否同时安装英文配音扩展包（tts-en + spaCy）？')" "$def_en"; then
      INSTALL_TTS_EN="yes"
    else
      INSTALL_TTS_EN="no"
    fi
  fi

  if [[ -z "$LINK_GLOBAL" ]]; then
    if prompt_yes_no "$(msg 'Link `reachreel` into ~/.local/bin so it can be run from any folder?' '是否将 `reachreel` 软链接到 ~/.local/bin 以便全局直接调用？')" "y"; then
      LINK_GLOBAL="yes"
    else
      LINK_GLOBAL="no"
    fi
  fi

  local vlm_model llm_model
  if [[ "$MODEL_PROFILE" == "light" ]]; then
    vlm_model="qwen3-vl:4b-instruct"
    llm_model="qwen3.5:9b"
  else
    vlm_model="qwen3-vl:8b-instruct"
    llm_model="qwen3:30b-instruct"
  fi
  ok "$(msg \
    "Plan: ui=${UI_LANG}, content=${CONTENT_LANG}, models=${MODEL_PROFILE} (${vlm_model} / ${llm_model}), tts-en=${INSTALL_TTS_EN}" \
    "配置确认：界面=${UI_LANG}，成片=${CONTENT_LANG}，模型=${MODEL_PROFILE}（${vlm_model} / ${llm_model}），英文配音=${INSTALL_TTS_EN}")"

  # ---- Step 3: Locate or clone repository ----
  step_header "Preparing ReachReel workspace" "准备 ReachReel 项目目录"

  local repo_dir=""
  if [[ -f "./pyproject.toml" && -d "./src/reachreel" ]]; then
    repo_dir="$(pwd)"
    ok "$(msg "Using current repository: ${repo_dir}" "使用当前仓库目录：${repo_dir}")"
  else
    repo_dir="${INSTALL_DIR:-${HOME}/ReachReel}"
    if [[ -d "${repo_dir}/.git" ]]; then
      ok "$(msg "Found existing checkout: ${repo_dir}" "发现已有目录：${repo_dir}")"
    else
      command -v git >/dev/null 2>&1 || fail "$(msg 'git is required. Run: xcode-select --install' '缺少 git，请先运行：xcode-select --install')"
      run_with_spinner "$(msg "Cloning ReachReel into ${repo_dir}" "正在克隆 ReachReel 到 ${repo_dir}")" \
        git clone --depth 1 "${REPO_URL}" "${repo_dir}"
    fi
  fi
  if (( ! DRY_RUN )); then
    cd "${repo_dir}"
  fi

  # ---- Step 4: Homebrew & ffmpeg-full ----
  step_header "Checking Homebrew & FFmpeg (with libass)" "检查 Homebrew 与 FFmpeg（含 libass 字幕引擎）"

  if [[ -x "/opt/homebrew/bin/brew" ]] && ! command -v brew >/dev/null 2>&1; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
  if ! command -v brew >/dev/null 2>&1; then
    fail "$(msg \
      'Homebrew is required. Install it from https://brew.sh and re-run ./install.sh' \
      '未检测到 Homebrew。请先访问 https://brew.sh 安装 Homebrew，再重新运行 ./install.sh')"
  fi
  ok "$(msg "Homebrew found: $(command -v brew)" "已检测到 Homebrew：$(command -v brew)")"

  local ffmpeg_full_bin="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
  local ffmpeg_filters=""
  if [[ -x "$ffmpeg_full_bin" ]]; then
    ffmpeg_filters="$("$ffmpeg_full_bin" -hide_banner -filters 2>/dev/null || true)"
  fi
  if [[ -n "$ffmpeg_filters" && "$ffmpeg_filters" == *" subtitles "* ]]; then
    ok "$(msg "ffmpeg-full (libass) ready: ${ffmpeg_full_bin}" "ffmpeg-full（含 libass 硬字幕）已就绪：${ffmpeg_full_bin}")"
  else
    run_with_spinner "$(msg 'Installing ffmpeg-full via Homebrew (this may take a few minutes)' '正在通过 Homebrew 安装 ffmpeg-full（首次编译/下载可能需要几分钟）')" \
      brew install ffmpeg-full
  fi

  # ---- Step 5: Python 3.12 venv & ReachReel ----
  step_header "Setting up Python 3.12 environment & ReachReel" "配置 Python 3.12 虚拟环境并安装 ReachReel"

  local py_bin=""
  local use_uv=0
  if command -v uv >/dev/null 2>&1; then
    use_uv=1
    ok "$(msg "Using uv for fast installation: $(command -v uv)" "检测到 uv，将加速依赖安装：$(command -v uv)")"
  else
    for cand in python3.12 python3.11 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11; do
      if command -v "$cand" >/dev/null 2>&1; then
        py_bin="$(command -v "$cand")"
        break
      fi
    done
    if [[ -z "$py_bin" ]]; then
      run_with_spinner "$(msg 'Installing Python 3.12 via Homebrew' '正在通过 Homebrew 安装 Python 3.12')" \
        brew install python@3.12
      py_bin="/opt/homebrew/bin/python3.12"
    fi
    ok "$(msg "Python found: ${py_bin}" "已检测到 Python：${py_bin}")"
  fi

  if (( use_uv )); then
    if [[ ! -d ".venv" ]]; then
      run_with_spinner "$(msg 'Creating .venv (Python 3.12) with uv' '正在使用 uv 创建 Python 3.12 虚拟环境 (.venv)')" \
        uv venv --python 3.12 .venv
    else
      ok "$(msg 'Reusing existing .venv' '复用已有 .venv 虚拟环境')"
    fi
    local pkg_spec="."
    [[ "$INSTALL_TTS_EN" == "yes" ]] && pkg_spec=".[tts-en]"
    run_with_spinner "$(msg "Installing ReachReel (${pkg_spec})" "正在安装 ReachReel（${pkg_spec}）")" \
      uv pip install --python .venv/bin/python -e "${pkg_spec}"
  else
    if [[ ! -d ".venv" ]]; then
      run_with_spinner "$(msg 'Creating .venv virtual environment' '正在创建 .venv 虚拟环境')" \
        "$py_bin" -m venv .venv
    else
      ok "$(msg 'Reusing existing .venv' '复用已有 .venv 虚拟环境')"
    fi
    run_with_spinner "$(msg 'Upgrading pip' '正在升级 pip')" \
      .venv/bin/python -m pip install --upgrade pip
    local pkg_spec="."
    [[ "$INSTALL_TTS_EN" == "yes" ]] && pkg_spec=".[tts-en]"
    run_with_spinner "$(msg "Installing ReachReel (${pkg_spec})" "正在安装 ReachReel（${pkg_spec}）")" \
      .venv/bin/python -m pip install -e "${pkg_spec}"
  fi

  if [[ "$INSTALL_TTS_EN" == "yes" ]]; then
    if (( ! DRY_RUN )) && .venv/bin/python -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null; then
      ok "$(msg 'spaCy model en_core_web_sm already installed' 'spaCy 英文模型 en_core_web_sm 已安装')"
    else
      run_with_spinner "$(msg 'Downloading spaCy English tagger (en_core_web_sm)' '正在下载 spaCy 英文词性模型（en_core_web_sm）')" \
        .venv/bin/python -m spacy download en_core_web_sm
    fi
  fi

  # ---- Step 6: Ollama & local models ----
  step_header "Configuring Ollama & local VLM/LLM models" "配置 Ollama 与本地视觉/写稿模型"

  if (( SKIP_OLLAMA )); then
    warn "$(msg 'Skipping Ollama setup (--skip-ollama).' '已跳过 Ollama 配置（--skip-ollama）。')"
  else
    if ! command -v ollama >/dev/null 2>&1; then
      if prompt_yes_no "$(msg 'Ollama is not installed. Install it via Homebrew now?' '未检测到 Ollama，是否现在通过 Homebrew 安装？')" "y"; then
        run_with_spinner "$(msg 'Installing Ollama via Homebrew' '正在通过 Homebrew 安装 Ollama')" \
          brew install ollama
      else
        warn "$(msg 'Skipping Ollama installation; install it from https://ollama.com later.' '已跳过 Ollama 安装，稍后可前往 https://ollama.com 安装。')"
      fi
    else
      ok "$(msg "Ollama found: $(command -v ollama)" "已检测到 Ollama：$(command -v ollama)")"
    fi

    if command -v ollama >/dev/null 2>&1; then
      if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        if (( DRY_RUN )); then
          info "[dry-run] Starting Ollama service (ollama serve)"
        else
          info "$(msg 'Starting Ollama background service...' '正在启动 Ollama 本地服务...')"
          nohup ollama serve >/tmp/reachreel-ollama.log 2>&1 &
          local waited=0
          while ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do
            sleep 1
            waited=$(( waited + 1 ))
            if (( waited >= 15 )); then
              warn "$(msg 'Ollama did not respond within 15s; please run `ollama serve` manually.' 'Ollama 15 秒内未响应，请手动运行 `ollama serve`。')"
              break
            fi
          done
        fi
      fi

      if [[ "$MODEL_PROFILE" == "skip" ]]; then
        info "$(msg 'Model download skipped. Pull models later with `ollama pull`.' '已跳过模型下载，后续可用 `ollama pull` 手动拉取。')"
      elif (( DRY_RUN )); then
        info "[dry-run] ollama pull ${vlm_model}"
        info "[dry-run] ollama pull ${llm_model}"
      elif curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        local installed_models
        installed_models="$(ollama list 2>/dev/null | awk '{print $1}' || true)"
        local model
        for model in "$vlm_model" "$llm_model"; do
          if printf "%s\n" "$installed_models" | grep -Fx "$model" >/dev/null 2>&1; then
            ok "$(msg "Model already present: ${model}" "模型已存在：${model}")"
          else
            info "$(msg "Pulling model ${model} (streaming live download progress)..." "正在下载模型 ${model}（下方显示实时进度）...")"
            ollama pull "$model"
            ok "$(msg "Model ready: ${model}" "模型下载完成：${model}")"
          fi
        done
      fi
    fi
  fi

  # ---- Step 7: Initialize workspace & run doctor ----
  step_header "Initializing config, folders & running doctor" "初始化配置文件、素材目录并执行环境自检"

  if (( DRY_RUN )); then
    info "[dry-run] mkdir -p inputs/bgm"
    info "[dry-run] Write starter config.toml (if absent)"
    info "[dry-run] .venv/bin/reachreel --lang ${UI_LANG} doctor"
  else
    mkdir -p inputs/bgm
    ok "$(msg 'BGM folder ready: inputs/bgm/ (drop royalty-free .mp3/.wav files here)' 'BGM 目录已就绪：inputs/bgm/（可将无版权 .mp3/.wav 音乐放入此处）')"

    if [[ ! -f "config.toml" ]]; then
      cat > config.toml <<EOF
# Generated by ReachReel install.sh — edit anytime.
ui_language = "${UI_LANG}"
content_language = "${CONTENT_LANG}"

vlm_model = "${vlm_model}"
llm_model = "${llm_model}"
vlm_workers = ${rec_workers}

bgm_dir = "inputs/bgm"
EOF
      ok "$(msg 'Created starter config.toml' '已生成初始配置文件 config.toml')"
    else
      info "$(msg 'Keeping existing config.toml untouched.' '检测到已有 config.toml，已保留原配置未覆盖。')"
    fi

    if [[ "$LINK_GLOBAL" == "yes" ]]; then
      mkdir -p "${HOME}/.local/bin"
      ln -sf "${repo_dir}/.venv/bin/reachreel" "${HOME}/.local/bin/reachreel"
      ok "$(msg "Linked ${HOME}/.local/bin/reachreel -> ${repo_dir}/.venv/bin/reachreel" "已创建全局软链接：${HOME}/.local/bin/reachreel")"
      if [[ ":${PATH}:" != *":${HOME}/.local/bin:"* ]]; then
        warn "$(msg \
          'Add ~/.local/bin to your PATH: echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> ~/.zshrc && source ~/.zshrc' \
          '请将 ~/.local/bin 加入 PATH：echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> ~/.zshrc && source ~/.zshrc')"
      fi
    fi

    printf "\n"
    if .venv/bin/reachreel --lang "${UI_LANG}" doctor --config config.toml; then
      printf "\n"
      ok "$(msg 'All checks passed! ReachReel is ready.' '全部环境自检通过！ReachReel 已准备就绪。')"
    else
      printf "\n"
      warn "$(msg 'Doctor reported items needing attention above.' '环境自检仍有需要处理的项，请参考上方提示。')"
    fi
  fi

  # ---- Final summary ----
  printf "\n%s%s══════════════════════════════════════════════════════════%s\n" "${C_BOLD}" "${C_GREEN}" "${C_RESET}"
  printf "  %s%s%s\n" "${C_BOLD}" "$(msg 'ReachReel installation complete!' 'ReachReel 安装与初始化完成！')" "${C_RESET}"
  printf "%s%s══════════════════════════════════════════════════════════%s\n\n" "${C_BOLD}" "${C_GREEN}" "${C_RESET}"

  local cmd_prefix=".venv/bin/reachreel"
  [[ "$LINK_GLOBAL" == "yes" ]] && cmd_prefix="reachreel"
  printf "  %s%s%s\n" "${C_BOLD}" "$(msg 'Next steps:' '快速上手：')" "${C_RESET}"
  printf "    1. %s\n" "$(msg 'Explore & audition built-in voices:' '查看并试听内置音色：')"
  printf "       %s%s voices%s\n" "${C_CYAN}" "${cmd_prefix}" "${C_RESET}"
  printf "       %s%s voices --preview %s%s\n" "${C_CYAN}" "${cmd_prefix}" "$( [[ "$CONTENT_LANG" == "en" ]] && echo 'af_heart' || echo 'zf_xiaoxiao' )" "${C_RESET}"
  printf "    2. %s\n" "$(msg 'Generate a script to review before voiceover:' '先生成旁白审稿，满意后再一键出片：')"
  printf "       %s%s run /path/to/video.mp4 --config config.toml --review-script --brief \"...\"%s\n" "${C_CYAN}" "${cmd_prefix}" "${C_RESET}"
  printf "    3. %s\n" "$(msg 'After editing script.json, finish everything in one command:' '审校完 script.json 后，一条命令完成配音、字幕、混音与渲染：')"
  printf "       %s%s resume output/<job-dir>%s\n\n" "${C_CYAN}" "${cmd_prefix}" "${C_RESET}"
}

main "$@"
