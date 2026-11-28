/* ReachReel Fine-Tuning Studio frontend (zero external dependencies) */
(() => {
  "use strict";

  const I18N = {
    en: {
      auth_title: "Connect to ReachReel Studio",
      auth_desc: "Paste the session token printed by reachreel serve (or ~/.reachreel/token).",
      auth_connect: "Connect",
      btn_reveal: "Reveal",
      btn_undo: "Undo",
      btn_apply: "Apply & Render",
      btn_cancel: "Cancel",
      preview_final: "Final Output",
      preview_source: "Source Video",
      player_empty: "No video available yet. Run render or switch to Source Video.",
      live_sub_overlay: "Live Subtitle Preview",
      timeline_title: "Timeline",
      btn_split_cut: "Split at Playhead",
      btn_delete_cut: "Delete Clip",
      btn_reset_cuts: "Reset Cuts",
      track_ruler: "Time",
      track_cuts: "Video Cuts",
      track_scenes: "Scenes",
      track_narration: "Narration",
      timeline_hint: "Drag narration blocks horizontally to lock start time. Click ruler or scenes to seek.",
      tab_segment: "Segment",
      tab_subtitles: "Subtitles",
      tab_mix: "Mix & I/O",
      tab_qa: "QA & Logs",
      select_segment_hint: "Select a narration segment on the timeline to fine-tune it.",
      seg_enabled: "Enabled",
      seg_text: "Narration Text",
      seg_voice: "Voice Override",
      seg_speed: "Speed (0 = default)",
      seg_instruct: "Delivery Instruction (Qwen3)",
      seg_start: "Start Override (s)",
      seg_gain: "Gain (dB)",
      btn_save_segment: "Save Segment",
      btn_audition: "Audition",
      seg_audio_override: "Custom Segment Audio Override",
      btn_upload: "Upload",
      btn_clear: "Clear",
      sub_style_heading: "Global Subtitle Style",
      sub_size: "Font Size (px)",
      sub_preset: "Safe Area Preset",
      sub_render: "Render Look",
      sub_margin_v: "Bottom Margin (px)",
      sub_burn: "Burn Hard Subtitles",
      btn_save_style: "Save Style",
      sub_cues_heading: "Subtitle Cues (SRT)",
      btn_save_srt: "Save SRT",
      mix_heading: "Audio Mix & Ducking",
      btn_save_mix: "Save Mix Settings",
      import_heading: "Import Artifact / NLE Timeline",
      import_kind: "Import Type",
      import_file: "Select File",
      btn_dry_run: "Dry Run",
      btn_import: "Import",
      export_heading: "Export to External NLE",
      btn_export: "Export Bundle",
      qa_heading: "QA Gate",
      logs_heading: "Live Pipeline Logs",
      status_uptodate: "Up to date",
      status_pending: "Pending",
      status_running: "Running",
      no_jobs: "No jobs found in output/",
    },
    "zh-CN": {
      auth_title: "连接到 ReachReel 微调台",
      auth_desc: "请粘贴 reachreel serve 输出的会话 Token（或查看 ~/.reachreel/token）。",
      auth_connect: "连接",
      btn_reveal: "在访达显示",
      btn_undo: "撤销",
      btn_apply: "应用并重跑",
      btn_cancel: "取消",
      preview_final: "成片预览",
      preview_source: "原片预览",
      player_empty: "暂无可播放视频，请先渲染或切换到原片预览。",
      live_sub_overlay: "实时字幕叠加",
      timeline_title: "微调时间轴",
      btn_split_cut: "在播放头切分",
      btn_delete_cut: "删除选中片段",
      btn_reset_cuts: "重置裁剪",
      track_ruler: "时间标尺",
      track_cuts: "画面裁剪",
      track_scenes: "镜头抽帧",
      track_narration: "旁白段落",
      timeline_hint: "左右拖动旁白块可锁定出现时间；点击标尺或镜头缩略图可跳转画面。",
      tab_segment: "旁白段",
      tab_subtitles: "字幕",
      tab_mix: "混音与导入",
      tab_qa: "质检与日志",
      select_segment_hint: "请在左侧时间轴点击任意一段旁白进行微调。",
      seg_enabled: "启用该段",
      seg_text: "旁白文案",
      seg_voice: "单段音色覆盖",
      seg_speed: "语速（0 = 默认）",
      seg_instruct: "语气指令（Qwen3）",
      seg_start: "锁定入点（秒）",
      seg_gain: "单段增益（dB）",
      btn_save_segment: "保存该段",
      btn_audition: "试听该段",
      seg_audio_override: "替换为自定义音频（WAV/MP3）",
      btn_upload: "上传替换",
      btn_clear: "清除替换",
      sub_style_heading: "全局字幕样式",
      sub_size: "字号（像素）",
      sub_preset: "平台安全区预设",
      sub_render: "字幕外观",
      sub_margin_v: "底部边距（像素）",
      sub_burn: "烧录硬字幕",
      btn_save_style: "保存样式",
      sub_cues_heading: "字幕条目（SRT）",
      btn_save_srt: "保存 SRT",
      mix_heading: "混音与闪避",
      btn_save_mix: "保存混音参数",
      import_heading: "导入外部产物 / 剪辑时间线",
      import_kind: "导入类型",
      import_file: "选择文件",
      btn_dry_run: "预览校验",
      btn_import: "立即导入",
      export_heading: "导出到剪辑软件",
      btn_export: "导出素材包",
      qa_heading: "QA 质检闸门",
      logs_heading: "实时流水线日志",
      status_uptodate: "已是最新",
      status_pending: "待重跑",
      status_running: "执行中",
      no_jobs: "工作目录中暂无任务",
    },
  };

  const state = {
    lang: "zh-CN",
    token: "",
    jobs: [],
    jobId: null,
    job: null,
    voices: null,
    previewMode: "final",
    selectedSegId: null,
    selectedCutIndex: null,
    pxPerSec: 18,
    eventSource: null,
    cuesDraft: [],
  };

  const $ = (id) => document.getElementById(id);

  function tr(key) {
    return (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
  }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = tr(key);
    });
    $("lang-btn").textContent = state.lang === "zh-CN" ? "EN" : "中文";
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2800);
  }

  function formatTime(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const mins = Math.floor(s / 60);
    const rem = s - mins * 60;
    return `${String(mins).padStart(2, "0")}:${rem.toFixed(2).padStart(5, "0")}`;
  }

  function authedUrl(url) {
    if (!url) return "";
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(state.token)}&v=${Date.now()}`;
  }

  async function api(path, options = {}) {
    const headers = {
      "X-ReachReel-Token": state.token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    };
    const resp = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (resp.status === 401) {
      openAuthModal("Invalid or expired session token.");
      throw new Error("Unauthorized");
    }
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {
      const msg = (data.error && data.error.message) || data.error || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function readInitialToken() {
    const hash = window.location.hash || "";
    const match = hash.match(/token=([^&]+)/);
    if (match && match[1]) {
      const tok = decodeURIComponent(match[1]);
      sessionStorage.setItem("reachreel_token", tok);
      history.replaceState(null, "", window.location.pathname + window.location.search);
      return tok;
    }
    return sessionStorage.getItem("reachreel_token") || "";
  }

  function openAuthModal(errMsg = "") {
    $("auth-error").textContent = errMsg;
    $("auth-modal").classList.remove("hidden");
    $("auth-token-input").focus();
  }

  async function init() {
    state.token = readInitialToken();
    try {
      const health = await fetch("/api/health").then((r) => r.json());
      if (health.default_lang && I18N[health.default_lang]) {
        state.lang = health.default_lang;
      }
    } catch {
      // Ignore health error on startup.
    }
    applyI18n();
    bindEvents();

    if (!state.token) {
      openAuthModal();
      return;
    }
    await bootstrap();
  }

  async function bootstrap() {
    try {
      const [voicesRes, jobsRes] = await Promise.all([
        api("/api/voices"),
        api("/api/jobs"),
      ]);
      $("auth-modal").classList.add("hidden");
      state.voices = voicesRes;
      populateVoiceSelect();
      state.jobs = jobsRes.jobs || [];
      renderJobSelector(jobsRes.pinned_job);
      if (state.jobs.length > 0) {
        const preferred =
          jobsRes.pinned_job && state.jobs.some((j) => j.id === jobsRes.pinned_job)
            ? jobsRes.pinned_job
            : state.jobs[0].id;
        await loadJob(preferred);
      }
    } catch (err) {
      if (String(err.message) !== "Unauthorized") {
        showToast(err.message);
      }
    }
  }

  function populateVoiceSelect() {
    const sel = $("seg-voice");
    sel.innerHTML = "";
    const defOpt = document.createElement("option");
    defOpt.value = "default";
    defOpt.textContent = "(Job Default)";
    sel.appendChild(defOpt);
    if (!state.voices || !state.voices.voices) return;
    for (const [engine, groups] of Object.entries(state.voices.voices)) {
      const og = document.createElement("optgroup");
      og.label = engine.toUpperCase();
      for (const [langTag, names] of Object.entries(groups)) {
        for (const name of names) {
          const opt = document.createElement("option");
          opt.value = name;
          const meta = (state.voices.details && state.voices.details[name]) || {};
          opt.textContent = `${name} (${langTag}${meta.tone ? " · " + meta.tone : ""})`;
          og.appendChild(opt);
        }
      }
      sel.appendChild(og);
    }
  }

  function renderJobSelector(pinned) {
    const sel = $("job-select");
    sel.innerHTML = "";
    if (!state.jobs.length) {
      const opt = document.createElement("option");
      opt.textContent = tr("no_jobs");
      sel.appendChild(opt);
      return;
    }
    for (const job of state.jobs) {
      const opt = document.createElement("option");
      opt.value = job.id;
      opt.textContent = job.id + (job.id === pinned ? " ★" : "");
      sel.appendChild(opt);
    }
  }

  async function loadJob(jobId, { keepSelection = false } = {}) {
    state.jobId = jobId;
    $("job-select").value = jobId;
    const detail = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    setJobDetail(detail, { keepSelection });
    connectEvents(jobId);
  }

  function setJobDetail(detail, { keepSelection = true } = {}) {
    state.job = detail;
    state.cuesDraft = (detail.subtitles || []).map((c) => ({ ...c }));
    if (!keepSelection) {
      state.selectedSegId = detail.segments && detail.segments.length ? detail.segments[0].id : null;
      state.selectedCutIndex = null;
    } else if (
      state.selectedSegId !== null &&
      !(detail.segments || []).some((s) => s.id === state.selectedSegId)
    ) {
      state.selectedSegId = detail.segments && detail.segments.length ? detail.segments[0].id : null;
    }

    renderStatusPills();
    renderPlayer();
    renderTimeline();
    renderSegmentInspector();
    renderSubtitlesInspector();
    renderMixInspector();
    renderQAInspector();
    renderLogs(detail.events || []);
  }

  function effectiveDuration() {
    if (!state.job) return 10;
    if (state.job.cuts && state.job.cuts.edited_duration > 0) {
      return state.job.cuts.edited_duration;
    }
    return Number((state.job.video && state.job.video.duration) || 10);
  }

  function renderStatusPills() {
    const job = state.job;
    if (!job) return;
    const pending = (job.status && job.status.pending_stages) || [];
    const pill = $("dirty-pill");
    if (pending.length === 0) {
      pill.textContent = tr("status_uptodate");
      pill.className = "pill ok";
    } else {
      pill.textContent = `${tr("status_pending")}: ${pending.join(" → ")}`;
      pill.className = "pill stale";
    }

    const running = job.runtime_state === "running" || job.runtime_state === "queued";
    $("runtime-pill").classList.toggle("hidden", !running);
    $("runtime-pill").textContent = `${tr("status_running")} (${job.runtime_state})`;
    $("runtime-pill").className = running ? "pill running" : "pill hidden";
    $("apply-btn").classList.toggle("hidden", running);
    $("cancel-btn").classList.toggle("hidden", !running);

    const hCount = Number(job.history_count || 0);
    $("undo-count").textContent = String(hCount);
    $("undo-btn").disabled = hCount <= 0 || running;
  }

  function renderPlayer() {
    const job = state.job;
    if (!job) return;
    const video = $("video-player");
    const wantFinal = state.previewMode === "final" && job.has_final;
    const url = wantFinal ? job.final_url : job.source_url;
    $("mode-final-btn").classList.toggle("active", state.previewMode === "final");
    $("mode-source-btn").classList.toggle("active", state.previewMode === "source");

    const v = job.video || {};
    const cutsTxt = job.cuts ? ` · Cut ${job.cuts.edited_duration}s (${job.cuts.clips.length} clips)` : "";
    $("video-meta").textContent = `${v.width || "?"}x${v.height || "?"} · ${v.fps || "?"}fps · ${v.duration || 0}s${cutsTxt}`;

    if (!url) {
      video.removeAttribute("src");
      video.load();
      $("player-empty").classList.remove("hidden");
      return;
    }
    $("player-empty").classList.add("hidden");
    const nextSrc = authedUrl(url);
    const prevTime = video.currentTime || 0;
    const curBase = (video.dataset.baseUrl || "");
    if (curBase !== url || video.dataset.forceReload === "1") {
      video.dataset.baseUrl = url;
      video.dataset.forceReload = "0";
      video.src = nextSrc;
      video.addEventListener(
        "loadedmetadata",
        () => {
          if (prevTime > 0 && prevTime < video.duration) video.currentTime = prevTime;
          updateTimecode();
        },
        { once: true }
      );
    }
    updateSubtitleOverlay();
  }

  function updateTimecode() {
    const video = $("video-player");
    const cur = video.currentTime || 0;
    const dur = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : effectiveDuration();
    $("timecode").textContent = `${formatTime(cur)} / ${formatTime(dur)}`;
    $("playhead").style.left = `${96 + cur * state.pxPerSec}px`;
    updateSubtitleOverlay();
  }

  function updateSubtitleOverlay() {
    const overlay = $("subtitle-overlay");
    if (!$("overlay-toggle").checked || !state.job) {
      overlay.innerHTML = "";
      return;
    }
    const t = $("video-player").currentTime || 0;
    const cue = state.cuesDraft.find((c) => t >= c.start && t <= c.end);
    const s = state.job.settings || {};
    const renderMode = s.subtitle_render || "outline";
    overlay.className = `subtitle-overlay style-${renderMode}`;
    const scaledSize = Math.max(13, Math.min(34, Math.round((Number(s.subtitle_size) || 48) * 0.42)));
    overlay.style.fontSize = `${scaledSize}px`;
    if (!cue) {
      overlay.innerHTML = "";
      return;
    }
    const span = document.createElement("span");
    span.textContent = cue.text;
    overlay.innerHTML = "";
    overlay.appendChild(span);
  }

  function renderTimeline() {
    const job = state.job;
    if (!job) return;
    const dur = Math.max(1, effectiveDuration());
    const containerW = Math.max(680, $("timeline-scroll").clientWidth - 108);
    state.pxPerSec = Math.max(14, Math.min(60, containerW / dur));
    const laneWidth = Math.ceil(dur * state.pxPerSec + 40);

    ["ruler-track", "cuts-track", "scenes-track", "segments-track"].forEach((id) => {
      $(id).style.width = `${laneWidth}px`;
      $(id).innerHTML = "";
    });

    // 1. Ruler ticks
    const step = state.pxPerSec >= 35 ? 2 : state.pxPerSec >= 20 ? 5 : 10;
    const ruler = $("ruler-track");
    for (let t = 0; t <= dur; t += step) {
      const tick = document.createElement("div");
      tick.className = "ruler-tick";
      tick.style.left = `${t * state.pxPerSec}px`;
      tick.textContent = `${t}s`;
      ruler.appendChild(tick);
    }

    // 2. Video Cuts track
    const cutsLane = $("cuts-track");
    const srcDur = Number((job.video && job.video.duration) || dur);
    const clips = job.cuts && job.cuts.clips ? job.cuts.clips : [{ id: 1, start: 0, end: srcDur, label: "full" }];
    let cursor = 0;
    clips.forEach((clip, idx) => {
      const clipDur = Math.max(0.05, clip.end - clip.start);
      const el = document.createElement("div");
      el.className = "cut-block" + (state.selectedCutIndex === idx ? " selected" : "");
      el.style.left = `${cursor * state.pxPerSec}px`;
      el.style.width = `${Math.max(24, clipDur * state.pxPerSec - 2)}px`;
      el.textContent = `#${idx + 1} [${clip.start.toFixed(1)}-${clip.end.toFixed(1)}s]`;
      el.title = `Source ${clip.start.toFixed(2)}s -> ${clip.end.toFixed(2)}s`;
      const seekAt = cursor;
      el.addEventListener("click", () => {
        state.selectedCutIndex = idx;
        $("delete-cut-btn").disabled = clips.length <= 1;
        seekVideo(seekAt);
        renderTimeline();
      });
      cutsLane.appendChild(el);
      cursor += clipDur;
    });
    $("delete-cut-btn").disabled = state.selectedCutIndex === null || clips.length <= 1;

    // 3. Scenes strip
    const scenesLane = $("scenes-track");
    for (const sc of job.scenes || []) {
      const scDur = Math.max(0.3, sc.end - sc.start);
      const el = document.createElement("div");
      el.className = "scene-block";
      el.style.left = `${sc.start * state.pxPerSec}px`;
      el.style.width = `${Math.max(28, scDur * state.pxPerSec - 2)}px`;
      if (sc.frame_urls && sc.frame_urls.length) {
        const img = document.createElement("img");
        img.src = authedUrl(sc.frame_urls[0]);
        img.loading = "lazy";
        el.appendChild(img);
      }
      const cap = document.createElement("span");
      cap.className = "scene-cap";
      cap.textContent = `S${sc.id} ${sc.summary || ""}`;
      el.appendChild(cap);
      el.addEventListener("click", () => seekVideo(sc.start));
      scenesLane.appendChild(el);
    }

    // 4. Draggable Narration Segments track
    const segsLane = $("segments-track");
    for (const seg of job.segments || []) {
      const start = seg.start_override !== null && seg.start_override !== undefined ? seg.start_override : (seg.start || 0);
      const segDur = Math.max(0.6, seg.duration || Math.max(1.2, (seg.text || "").length / 4.2));
      const el = document.createElement("div");
      el.className =
        "seg-block" +
        (seg.id === state.selectedSegId ? " selected" : "") +
        (seg.start_override !== null && seg.start_override !== undefined ? " locked" : "") +
        (!seg.enabled ? " disabled" : "");
      el.style.left = `${start * state.pxPerSec}px`;
      el.style.width = `${Math.max(40, segDur * state.pxPerSec)}px`;

      const badges = [];
      if (seg.start_override !== null && seg.start_override !== undefined) badges.push("🔒");
      if (seg.audio_override) badges.push("🎙");
      if (Math.abs(seg.gain_db || 0) > 0.05) badges.push(`${seg.gain_db > 0 ? "+" : ""}${seg.gain_db}dB`);

      el.innerHTML = `<div class="seg-top"><span>#${seg.id} ${start.toFixed(1)}s</span><span>${badges.join(" ")}</span></div><div class="seg-preview"></div>`;
      el.querySelector(".seg-preview").textContent = seg.text || "";

      attachSegmentDrag(el, seg, start);
      segsLane.appendChild(el);
    }

    updateTimecode();
  }

  function attachSegmentDrag(el, seg, initialStart) {
    let dragging = false;
    let moved = false;
    let startX = 0;
    let newStart = initialStart;

    el.addEventListener("pointerdown", (e) => {
      dragging = true;
      moved = false;
      startX = e.clientX;
      newStart = initialStart;
      el.setPointerCapture(e.pointerId);
    });

    el.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - startX;
      if (Math.abs(dx) > 4) moved = true;
      if (!moved) return;
      newStart = Math.max(0, Math.round((initialStart + dx / state.pxPerSec) * 20) / 20);
      el.style.left = `${newStart * state.pxPerSec}px`;
      el.querySelector(".seg-top span").textContent = `#${seg.id} ${newStart.toFixed(2)}s`;
    });

    el.addEventListener("pointerup", async (e) => {
      if (!dragging) return;
      dragging = false;
      el.releasePointerCapture(e.pointerId);
      state.selectedSegId = seg.id;
      if (!moved) {
        seekVideo(initialStart);
        renderTimeline();
        renderSegmentInspector();
        return;
      }
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/segments/${seg.id}`, {
          method: "PATCH",
          body: { start_override: newStart },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast(`Segment #${seg.id} -> ${newStart.toFixed(2)}s`);
      } catch (err) {
        showToast(err.message);
        renderTimeline();
      }
    });
  }

  function seekVideo(sec) {
    const video = $("video-player");
    if (video.readyState >= 1) {
      video.currentTime = Math.max(0, Math.min(video.duration || sec, sec));
    }
    updateTimecode();
  }

  function renderSegmentInspector() {
    const seg =
      state.job && (state.job.segments || []).find((s) => s.id === state.selectedSegId);
    if (!seg) {
      $("segment-empty").classList.remove("hidden");
      $("segment-editor").classList.add("hidden");
      return;
    }
    $("segment-empty").classList.add("hidden");
    $("segment-editor").classList.remove("hidden");

    $("seg-heading").textContent = `#${seg.id} · Scenes [${(seg.scene_ids || []).join(", ")}]`;
    $("seg-enabled").checked = seg.enabled !== false;
    $("seg-text").value = seg.text || "";
    $("seg-voice").value = seg.voice || "default";
    $("seg-speed").value = seg.speed || 0;
    $("seg-instruct").value = seg.instruct || "";
    $("seg-start").value =
      seg.start_override !== null && seg.start_override !== undefined
        ? String(seg.start_override)
        : "";
    const gain = Number(seg.gain_db || 0);
    $("seg-gain").value = String(gain);
    $("seg-gain-val").textContent = `${gain >= 0 ? "+" : ""}${gain.toFixed(1)}`;
    $("seg-override-status").textContent = seg.audio_override || "None (using TTS)";

    const box = $("seg-audio-box");
    const player = $("seg-audio-player");
    if (seg.audio_url) {
      box.classList.remove("hidden");
      player.src = authedUrl(seg.audio_url);
    } else {
      box.classList.add("hidden");
      player.removeAttribute("src");
    }
  }

  function renderSubtitlesInspector() {
    const s = (state.job && state.job.settings) || {};
    $("set-sub-size").value = s.subtitle_size || 48;
    $("set-sub-preset").value = s.subtitle_preset || "";
    $("set-sub-render").value = s.subtitle_render || "outline";
    $("set-sub-margin-v").value = s.subtitle_margin_v ?? 36;
    $("set-burn-sub").checked = s.burn_subtitles !== false;

    const list = $("cues-list");
    list.innerHTML = "";
    state.cuesDraft.forEach((cue, idx) => {
      const card = document.createElement("div");
      card.className = "cue-item";
      card.innerHTML = `
        <div class="cue-times">
          <span class="muted mono small">#${idx + 1}</span>
          <input type="number" step="0.05" class="cue-start" value="${Number(cue.start).toFixed(2)}" />
          <span class="muted">→</span>
          <input type="number" step="0.05" class="cue-end" value="${Number(cue.end).toFixed(2)}" />
          <button class="btn ghost compact cue-jump" title="Jump">▶</button>
          <button class="btn ghost compact cue-del" title="Delete">×</button>
        </div>
        <textarea rows="2" class="cue-text"></textarea>
      `;
      const ta = card.querySelector(".cue-text");
      ta.value = cue.text || "";
      card.querySelector(".cue-start").addEventListener("input", (e) => {
        state.cuesDraft[idx].start = parseFloat(e.target.value) || 0;
        updateSubtitleOverlay();
      });
      card.querySelector(".cue-end").addEventListener("input", (e) => {
        state.cuesDraft[idx].end = parseFloat(e.target.value) || 0;
        updateSubtitleOverlay();
      });
      ta.addEventListener("input", (e) => {
        state.cuesDraft[idx].text = e.target.value;
        updateSubtitleOverlay();
      });
      card.querySelector(".cue-jump").addEventListener("click", () => seekVideo(state.cuesDraft[idx].start));
      card.querySelector(".cue-del").addEventListener("click", () => {
        state.cuesDraft.splice(idx, 1);
        renderSubtitlesInspector();
        updateSubtitleOverlay();
      });
      list.appendChild(card);
    });
  }

  function renderMixInspector() {
    const s = (state.job && state.job.settings) || {};
    $("set-bgm-vol").value = s.bgm_volume ?? 0.2;
    $("set-bgm-duck").value = s.bgm_duck_db ?? -16;
    $("set-amb-vol").value = s.ambient_volume ?? 0.16;
    $("set-lufs").value = s.target_lufs ?? -14;
  }

  function renderQAInspector() {
    const qa = state.job && state.job.qa;
    const list = $("qa-list");
    list.innerHTML = "";
    const badge = $("qa-summary-badge");
    if (!qa || !qa.checks) {
      badge.textContent = "Not run";
      badge.className = "pill";
      return;
    }
    const sum = qa.summary || {};
    badge.textContent = `pass=${sum.pass || 0} warn=${sum.warn || 0} fail=${sum.fail || 0}`;
    badge.className =
      sum.fail > 0 ? "pill fail" : sum.warn > 0 ? "pill stale" : "pill ok";
    for (const check of qa.checks) {
      const el = document.createElement("div");
      el.className = "qa-item";
      const top = document.createElement("div");
      top.className = "qa-item-top";
      const idSpan = document.createElement("span");
      idSpan.textContent = String(check.id || "");
      const statusSpan = document.createElement("span");
      const st = String(check.status || "").toLowerCase();
      statusSpan.className = st === "pass" || st === "warn" || st === "fail" ? `qa-${st}` : "";
      statusSpan.textContent = st.toUpperCase();
      top.appendChild(idSpan);
      top.appendChild(statusSpan);

      const msgDiv = document.createElement("div");
      msgDiv.className = "muted small";
      msgDiv.textContent = String(check.message || "");

      el.appendChild(top);
      el.appendChild(msgDiv);

      const details = check.details || {};
      const timestamps = [];
      if (Array.isArray(details.intervals)) {
        for (const iv of details.intervals) {
          if (iv && typeof iv.start === "number") timestamps.push(iv.start);
        }
      }
      if (typeof details.at === "number") timestamps.push(details.at);
      if (timestamps.length) {
        const jumps = document.createElement("div");
        jumps.className = "inline-group";
        timestamps.slice(0, 6).forEach((ts) => {
          const btn = document.createElement("button");
          btn.className = "btn ghost compact mono";
          btn.textContent = `▶ ${formatTime(ts)}`;
          btn.addEventListener("click", () => seekVideo(ts));
          jumps.appendChild(btn);
        });
        el.appendChild(jumps);
      }
      list.appendChild(el);
    }
  }

  function renderLogs(events) {
    const box = $("log-console");
    const lines = events
      .filter((ev) => ev.type !== "heartbeat")
      .map((ev) => {
        if (ev.type === "log") return `[${ev.stage || "job"}] ${ev.msg || ""}`;
        if (ev.type === "stage_start") return `=== Stage start: ${ev.stage} ===`;
        if (ev.type === "stage_end") return `=== Stage end: ${ev.stage} (ok=${ev.ok}, ${ev.elapsed}s) ===`;
        if (ev.type === "job_end") return `*** Job ${ev.status} (${ev.elapsed || 0}s) ${ev.error || ""} ***`;
        return `[${ev.type}] ${JSON.stringify(ev)}`;
      });
    box.textContent = lines.join("\n");
    box.scrollTop = box.scrollHeight;
  }

  function connectEvents(jobId) {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    const lastSeq =
      state.job && state.job.events && state.job.events.length
        ? state.job.events[state.job.events.length - 1].seq || 0
        : 0;
    const url = `/api/jobs/${encodeURIComponent(jobId)}/events?since=${lastSeq}&token=${encodeURIComponent(state.token)}`;
    const es = new EventSource(url);
    state.eventSource = es;

    const handleEvent = (rawEv) => {
      try {
        const ev = JSON.parse(rawEv.data);
        if (ev.type === "heartbeat") return;
        const box = $("log-console");
        const line =
          ev.type === "log"
            ? `[${ev.stage || "job"}] ${ev.msg || ""}`
            : ev.type === "stage_start"
            ? `=== Stage start: ${ev.stage} ===`
            : ev.type === "stage_end"
            ? `=== Stage end: ${ev.stage} (ok=${ev.ok}, ${ev.elapsed}s) ===`
            : `*** ${ev.type}: ${ev.status || ""} ${ev.error || ""} ***`;
        box.textContent = (box.textContent ? box.textContent + "\n" : "") + line;
        box.scrollTop = box.scrollHeight;

        if (ev.type === "job_start" || ev.type === "job_queued") {
          if (state.job) {
            state.job.runtime_state = "running";
            renderStatusPills();
          }
        } else if (ev.type === "job_end") {
          $("video-player").dataset.forceReload = "1";
          loadJob(jobId, { keepSelection: true });
          showToast(ev.status === "done" ? "Stages complete!" : `Job ${ev.status}`);
        }
      } catch {
        // Ignore malformed event.
      }
    };

    ["job_queued", "job_start", "stage_start", "log", "stage_end", "job_end"].forEach((name) => {
      es.addEventListener(name, handleEvent);
    });
  }

  async function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        resolve(result.includes(",") ? result.split(",", 2)[1] : result);
      };
      reader.onerror = () => reject(new Error("Failed to read file."));
      reader.readAsDataURL(file);
    });
  }

  function bindEvents() {
    $("auth-submit-btn").addEventListener("click", async () => {
      const tok = $("auth-token-input").value.trim();
      if (!tok) return;
      state.token = tok;
      sessionStorage.setItem("reachreel_token", tok);
      await bootstrap();
    });

    $("lang-btn").addEventListener("click", () => {
      state.lang = state.lang === "zh-CN" ? "en" : "zh-CN";
      applyI18n();
      renderStatusPills();
    });

    $("job-select").addEventListener("change", (e) => {
      if (e.target.value) loadJob(e.target.value);
    });
    $("refresh-btn").addEventListener("click", () => {
      if (state.jobId) loadJob(state.jobId, { keepSelection: true });
    });
    $("reveal-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      await api(`/api/jobs/${encodeURIComponent(state.jobId)}/reveal`, { method: "POST" });
    });

    // Tabs
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-tab");
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
        document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `tab-${target}`));
      });
    });

    // Player controls
    const video = $("video-player");
    video.addEventListener("timeupdate", updateTimecode);
    video.addEventListener("play", () => ($("play-btn").textContent = "⏸"));
    video.addEventListener("pause", () => ($("play-btn").textContent = "▶"));

    $("play-btn").addEventListener("click", () => {
      if (video.paused) video.play();
      else video.pause();
    });
    $("step-back-btn").addEventListener("click", () => seekVideo((video.currentTime || 0) - 1));
    $("step-fwd-btn").addEventListener("click", () => seekVideo((video.currentTime || 0) + 1));
    $("rate-select").addEventListener("change", (e) => {
      video.playbackRate = parseFloat(e.target.value) || 1;
    });
    $("overlay-toggle").addEventListener("change", updateSubtitleOverlay);
    $("mode-final-btn").addEventListener("click", () => {
      state.previewMode = "final";
      renderPlayer();
    });
    $("mode-source-btn").addEventListener("click", () => {
      state.previewMode = "source";
      renderPlayer();
    });

    // Ruler seek
    $("ruler-track").addEventListener("click", (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const t = Math.max(0, (e.clientX - rect.left) / state.pxPerSec);
      seekVideo(t);
    });

    // Cut controls
    $("split-cut-btn").addEventListener("click", async () => {
      if (!state.job) return;
      const playT = $("video-player").currentTime || 0;
      const srcDur = Number((state.job.video && state.job.video.duration) || 0);
      const clips =
        state.job.cuts && state.job.cuts.clips
          ? state.job.cuts.clips.map((c) => ({ start: c.start, end: c.end }))
          : [{ start: 0, end: srcDur }];
      let cursor = 0;
      const nextClips = [];
      let splitDone = false;
      for (const c of clips) {
        const dur = c.end - c.start;
        if (!splitDone && playT > cursor + 0.15 && playT < cursor + dur - 0.15) {
          const splitSrc = Number((c.start + (playT - cursor)).toFixed(3));
          nextClips.push({ start: c.start, end: splitSrc });
          nextClips.push({ start: splitSrc, end: c.end });
          splitDone = true;
        } else {
          nextClips.push(c);
        }
        cursor += dur;
      }
      if (!splitDone) {
        showToast("Move playhead inside a clip to split.");
        return;
      }
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cuts`, {
          method: "PUT",
          body: { clips: nextClips },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast("Split clip at playhead");
      } catch (err) {
        showToast(err.message);
      }
    });

    $("delete-cut-btn").addEventListener("click", async () => {
      if (!state.job || state.selectedCutIndex === null) return;
      const clips = (state.job.cuts && state.job.cuts.clips) || [];
      if (clips.length <= 1) return;
      const next = clips
        .filter((_, idx) => idx !== state.selectedCutIndex)
        .map((c) => ({ start: c.start, end: c.end }));
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cuts`, {
          method: "PUT",
          body: { clips: next },
        });
        state.selectedCutIndex = null;
        setJobDetail(res.job, { keepSelection: true });
        showToast("Deleted video clip");
      } catch (err) {
        showToast(err.message);
      }
    });

    $("reset-cuts-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cuts`, {
          method: "PUT",
          body: { reset: true },
        });
        state.selectedCutIndex = null;
        setJobDetail(res.job, { keepSelection: true });
        showToast("Reset to uncut source video");
      } catch (err) {
        showToast(err.message);
      }
    });

    // Segment editor
    $("seg-gain").addEventListener("input", (e) => {
      const v = parseFloat(e.target.value) || 0;
      $("seg-gain-val").textContent = `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
    });
    $("seg-start-playhead").addEventListener("click", () => {
      $("seg-start").value = ($("video-player").currentTime || 0).toFixed(2);
    });
    $("seg-start-auto").addEventListener("click", () => {
      $("seg-start").value = "";
    });

    $("seg-save-btn").addEventListener("click", async () => {
      if (!state.jobId || state.selectedSegId === null) return;
      const rawStart = $("seg-start").value.trim();
      try {
        const res = await api(
          `/api/jobs/${encodeURIComponent(state.jobId)}/segments/${state.selectedSegId}`,
          {
            method: "PATCH",
            body: {
              text: $("seg-text").value,
              voice: $("seg-voice").value,
              instruct: $("seg-instruct").value || "none",
              speed: parseFloat($("seg-speed").value) || 0,
              start_override: rawStart === "" ? "auto" : rawStart,
              gain_db: parseFloat($("seg-gain").value) || 0,
              enabled: $("seg-enabled").checked,
            },
          }
        );
        setJobDetail(res.job, { keepSelection: true });
        showToast(`Saved segment #${state.selectedSegId}`);
      } catch (err) {
        showToast(err.message);
      }
    });

    $("seg-audition-btn").addEventListener("click", async () => {
      if (!state.jobId || state.selectedSegId === null) return;
      showToast("Synthesizing segment preview...");
      try {
        const res = await api(
          `/api/jobs/${encodeURIComponent(state.jobId)}/segments/${state.selectedSegId}/audition`,
          { method: "POST", body: {} }
        );
        const box = $("seg-audio-box");
        const audio = $("seg-audio-player");
        box.classList.remove("hidden");
        audio.src = authedUrl(res.audio_url);
        audio.play().catch(() => {});
        showToast(`Audition ready (${res.duration}s)`);
      } catch (err) {
        showToast(err.message);
      }
    });

    $("seg-upload-audio-btn").addEventListener("click", async () => {
      const file = $("seg-audio-file").files && $("seg-audio-file").files[0];
      if (!file || !state.jobId || state.selectedSegId === null) return;
      try {
        const b64 = await fileToBase64(file);
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/import`, {
          method: "POST",
          body: {
            kind: "segment_audio",
            segment_id: state.selectedSegId,
            filename: file.name,
            content_base64: b64,
          },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast(`Replaced segment #${state.selectedSegId} audio`);
      } catch (err) {
        showToast(err.message);
      }
    });

    $("seg-clear-audio-btn").addEventListener("click", async () => {
      if (!state.jobId || state.selectedSegId === null) return;
      try {
        const res = await api(
          `/api/jobs/${encodeURIComponent(state.jobId)}/segments/${state.selectedSegId}`,
          { method: "PATCH", body: { clear_audio_override: true } }
        );
        setJobDetail(res.job, { keepSelection: true });
        showToast(`Cleared audio override on segment #${state.selectedSegId}`);
      } catch (err) {
        showToast(err.message);
      }
    });

    // Subtitles & Mix settings
    $("save-sub-style-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/settings`, {
          method: "PUT",
          body: {
            subtitle_size: parseInt($("set-sub-size").value, 10) || 48,
            subtitle_preset: $("set-sub-preset").value,
            subtitle_render: $("set-sub-render").value,
            subtitle_margin_v: parseInt($("set-sub-margin-v").value, 10) || 36,
            burn_subtitles: $("set-burn-sub").checked,
          },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast("Saved subtitle style");
      } catch (err) {
        showToast(err.message);
      }
    });

    $("add-cue-btn").addEventListener("click", () => {
      const t = Number(($("video-player").currentTime || 0).toFixed(2));
      const lastEnd = state.cuesDraft.length ? state.cuesDraft[state.cuesDraft.length - 1].end : 0;
      const start = t > 0 ? t : Number((lastEnd + 0.2).toFixed(2));
      state.cuesDraft.push({ start, end: Number((start + 2.0).toFixed(2)), text: "" });
      state.cuesDraft.sort((a, b) => a.start - b.start);
      renderSubtitlesInspector();
      updateSubtitleOverlay();
    });

    $("save-cues-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/subtitles`, {
          method: "PUT",
          body: { cues: state.cuesDraft },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast("Saved subtitles.srt");
      } catch (err) {
        showToast(err.message);
      }
    });

    $("save-mix-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/settings`, {
          method: "PUT",
          body: {
            bgm_volume: parseFloat($("set-bgm-vol").value),
            bgm_duck_db: parseFloat($("set-bgm-duck").value),
            ambient_volume: parseFloat($("set-amb-vol").value),
            target_lufs: parseFloat($("set-lufs").value),
          },
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast("Saved mix settings");
      } catch (err) {
        showToast(err.message);
      }
    });

    // Import & Export
    async function runImport(dryRun) {
      const file = $("import-file").files && $("import-file").files[0];
      if (!file || !state.jobId) {
        showToast("Select a file first.");
        return;
      }
      try {
        const b64 = await fileToBase64(file);
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/import`, {
          method: "POST",
          body: {
            kind: $("import-kind").value,
            filename: file.name,
            content_base64: b64,
            dry_run: dryRun,
          },
        });
        const box = $("import-result");
        box.classList.remove("hidden");
        box.textContent = JSON.stringify(
          {
            dry_run: res.dry_run,
            imported: res.imported,
            warnings: res.warnings,
            affected_stages: res.affected_stages,
          },
          null,
          2
        );
        if (!dryRun && res.job) {
          setJobDetail(res.job, { keepSelection: true });
          showToast("Import complete");
        }
      } catch (err) {
        showToast(err.message);
      }
    }
    $("import-dryrun-btn").addEventListener("click", () => runImport(true));
    $("import-run-btn").addEventListener("click", () => runImport(false));

    $("export-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      showToast("Exporting NLE bundle...");
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/export`, {
          method: "POST",
          body: {
            format: $("export-format").value,
            narration_mode: $("export-mode").value,
          },
        });
        $("export-status").textContent = `Exported -> ${res.output}`;
        showToast("Export ready!");
      } catch (err) {
        showToast(err.message);
      }
    });

    // Undo / Apply / Cancel
    $("undo-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/undo`, {
          method: "POST",
          body: {},
        });
        setJobDetail(res.job, { keepSelection: true });
        showToast("Undone last fine-tuning edit");
      } catch (err) {
        showToast(err.message);
      }
    });

    $("apply-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/stages`, {
          method: "POST",
          body: {
            stages: "pending",
            asr: $("asr-select").value,
          },
        });
        if (!res.queued) {
          showToast("Already up to date!");
          return;
        }
        // Switch to QA & Logs tab so progress is immediately visible.
        document.querySelector('.tab-btn[data-tab="qa"]').click();
        showToast(`Queued: ${(res.stages || []).join(" -> ")}`);
      } catch (err) {
        showToast(err.message);
      }
    });

    $("cancel-btn").addEventListener("click", async () => {
      if (!state.jobId) return;
      await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, { method: "POST" });
      showToast("Cancellation requested");
    });

    window.addEventListener("resize", () => {
      if (state.job) renderTimeline();
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
