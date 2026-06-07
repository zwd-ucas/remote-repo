const state = {
  view: "dashboard",
  settings: {},
  voices: [
    "苏瑶(Serena)",
    "小婉(Seren)",
    "四月(Maia)",
    "凯(Kai)",
    "月白(Moon)",
    "晨煦(Ethan)",
    "萌宝(Bella)",
    "诡婆婆(Ebona)",
    "田叔(Vincent)",
    "沧明子(Eldric Sage)"
  ],
  voiceCatalog: [],
  tasks: [],
  activeTaskId: null,
  task: null,
  cues: [],
  sourceSubtitles: [],
  audioFiles: {},
  queuePoll: null,
  lastStatus: null,
  autoOpened: new Set()
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", async () => {
  bindNav();
  bindDashboard();
  bindTaskBar();
  bindTabs();
  bindSettings();
  await loadVoices();
  await loadSettings();
  await refreshQueue();
  startQueuePolling();
});

// ---------------- views / navigation ----------------
function showView(name) {
  state.view = name;
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${name}`));
  document.querySelectorAll(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
}

function bindNav() {
  document.querySelectorAll("[data-view]").forEach((el) => {
    el.addEventListener("click", () => showView(el.dataset.view));
  });
  $("backBtn").addEventListener("click", () => {
    state.activeTaskId = null;
    showView("dashboard");
    refreshQueue();
  });
}

function bindTabs() {
  document.querySelectorAll("#taskTabs .tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });
  document.querySelectorAll(".secret-toggle").forEach((button) => {
    button.addEventListener("click", () => toggleSecret(button));
  });
}

function activateTab(name) {
  document.querySelectorAll("#taskTabs .tab").forEach((el) => el.classList.toggle("active", el.dataset.tab === name));
  ["subtitles", "roles", "export"].forEach((id) => $(id).classList.toggle("active", id === name));
}

// ---------------- dashboard / run ----------------
function bindDashboard() {
  $("startBtn").addEventListener("click", submitRun);
}

async function submitRun() {
  const urls = $("urlInput").value.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  if (!urls.length) {
    $("dashHint").textContent = "请粘贴至少一个 YouTube 链接。";
    return;
  }
  const mode = document.querySelector('input[name="mode"]:checked').value;
  $("startBtn").disabled = true;
  $("dashHint").textContent = "提交中...";
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, mode })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    $("urlInput").value = "";
    $("dashHint").textContent = `已加入队列 ${data.tasks.length} 个任务（${mode === "manual" ? "手动" : "自动"}模式，按顺序处理）。`;
    await refreshQueue();
  } catch (err) {
    $("dashHint").textContent = `提交失败：${err.message}`;
  } finally {
    $("startBtn").disabled = false;
  }
}

// ---------------- queue ----------------
function startQueuePolling() {
  if (state.queuePoll) clearInterval(state.queuePoll);
  state.queuePoll = setInterval(refreshQueue, 1500);
}

async function refreshQueue() {
  let list = [];
  try {
    const res = await fetch("/api/tasks");
    list = await res.json();
  } catch (_error) {
    return;
  }
  state.tasks = Array.isArray(list) ? list : [];
  renderQueue();
  updateGlobalStatus();
  // Manual mode: when a video first needs confirmation, surface it — but only from the
  // dashboard and only once per task, so we never yank the user out of what they're doing.
  if (state.view === "dashboard") {
    const review = state.tasks.find((t) => t.status === "awaiting_review" && !state.autoOpened.has(t.task_id));
    if (review) {
      state.autoOpened.add(review.task_id);
      await openTask(review.task_id);
      return;
    }
  }
  if (state.view === "task" && state.activeTaskId) await refreshActiveTask();
}

function renderQueue() {
  const el = $("queueList");
  $("queueCount").textContent = state.tasks.length ? `${state.tasks.length} 个任务` : "";
  if (!state.tasks.length) {
    el.innerHTML = '<p class="empty">还没有任务，粘贴链接开始。</p>';
    return;
  }
  el.innerHTML = "";
  state.tasks.forEach((task) => {
    const card = document.createElement("div");
    card.className = `queue-card status-${task.status}`;
    const cancelable = ["queued", "running", "awaiting_review", "paused", "pausing"].includes(task.status);
    const processing = ["running", "pausing"].includes(task.status);
    const pct = task.progress || 0;
    card.innerHTML = `
      <div class="qc-main">
        <span class="qc-icon">${statusIcon(task.status)}</span>
        <div class="qc-text">
          <div class="qc-title">${escapeHtml(task.title || task.url || task.task_id)}</div>
          <div class="qc-meta">
            <span class="badge mode">${task.mode === "manual" ? "手动" : "自动"}</span>
            <span class="badge ${task.status}">${escapeHtml(statusLabel(task))}</span>
            <span class="muted">${escapeHtml(stepLabel(task.step))}</span>
            ${processing ? `<span class="qc-pct">${pct}%</span>` : ""}
          </div>
          ${processing ? `<div class="qc-bar"><div class="qc-bar-fill" style="width:${pct}%"></div></div>` : ""}
        </div>
      </div>
      <div class="qc-actions">
        <button data-open="${task.task_id}" type="button">${task.status === "awaiting_review" ? "去确认" : task.has_video ? "查看成片" : "打开"}</button>
        ${cancelable ? `<button class="danger" data-cancel="${task.task_id}" type="button">取消</button>` : ""}
      </div>`;
    el.appendChild(card);
  });
  el.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openTask(b.dataset.open)));
  el.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => cancelTask(b.dataset.cancel)));
}

function updateGlobalStatus() {
  const count = (statuses) => state.tasks.filter((t) => statuses.includes(t.status)).length;
  const parts = [];
  const running = count(["running", "pausing"]);
  const review = count(["awaiting_review"]);
  const queued = count(["queued"]);
  if (running) parts.push(`${running} 进行中`);
  if (review) parts.push(`${review} 待确认`);
  if (queued) parts.push(`${queued} 排队`);
  $("globalStatus").textContent = parts.length ? parts.join(" · ") : "就绪";
}

function statusIcon(status) {
  return { queued: "⏳", running: "▶", pausing: "⏸", paused: "⏸", awaiting_review: "✋", ready: "✓", error: "⚠", cancelled: "✕" }[status] || "•";
}

function statusLabel(task) {
  return { queued: "排队中", running: "处理中", pausing: "暂停中", paused: "已暂停", awaiting_review: "待确认", ready: "已完成", error: "失败", cancelled: "已取消" }[task.status] || task.status;
}

function stepLabel(step) {
  const s = String(step || "").toLowerCase();
  if (!s || s === "queued") return "等待中";
  if (s.startsWith("awaiting")) return "等待人工确认";
  if (s.startsWith("download") || s.startsWith("import")) return "下载视频";
  if (s.startsWith("transcribe")) return "语音识别";
  if (s.startsWith("translate")) return "翻译";
  if (s.startsWith("segment")) return "断句分配角色";
  if (s.startsWith("review")) return "角色校对";
  if (s.startsWith("tts")) return `配音 ${step.includes(":") ? step.split(":")[1] : ""}`;
  if (/(compose|separate|compress|mux|prepare video|assemble|remove|bgm)/.test(s)) return "混音合成";
  if (s.startsWith("saved")) return "保存成片";
  if (s === "ready") return "完成";
  return step;
}

// ---------------- task detail ----------------
async function openTask(id) {
  state.activeTaskId = id;
  state.lastStatus = null;
  state.cues = [];
  state.audioFiles = {};
  state.sourceSubtitles = [];
  showView("task");
  await refreshActiveTask(true);
}

async function refreshActiveTask(initial) {
  if (!state.activeTaskId) return;
  let task;
  try {
    const res = await fetch(`/api/tasks/${state.activeTaskId}`);
    if (!res.ok) return;
    task = await res.json();
  } catch (_error) {
    return;
  }
  state.task = task;
  renderTask(task, initial);
}

function renderTask(task, initial) {
  state.task = task; // keep state.task authoritative so renderCues reads the right status
  $("tTitle").textContent = task.title || task.url || task.task_id;
  $("tMode").textContent = task.mode === "manual" ? "手动" : "自动";
  $("tBadge").textContent = statusLabel(task);
  $("tBadge").className = `badge ${task.status}`;
  $("taskStepText").textContent = `${stepLabel(task.step)}${task.error ? " — " + task.error : ""}`;
  setActiveStep(task);

  const processing = ["running", "pausing"].includes(task.status);
  $("taskProgress").hidden = !processing;
  if (processing) {
    const pct = task.progress || 0;
    $("taskProgressFill").style.width = `${pct}%`;
    $("taskProgressPct").textContent = `${pct}%`;
  }

  const awaiting = task.status === "awaiting_review";
  $("reviewBanner").hidden = !awaiting;
  $("confirmBtn").hidden = !awaiting;
  $("taskCancel").hidden = !["queued", "running", "awaiting_review", "paused", "pausing"].includes(task.status);
  const pausable = ["running", "pausing", "paused"].includes(task.status);
  $("taskPause").hidden = !pausable;
  $("taskPause").textContent = task.pause_requested || task.status === "paused" ? "继续" : "暂停";
  ["saveCues", "regenerateAllTts", "applyNarratorVoice", "bulkVoice"].forEach((id) => {
    $(id).disabled = !awaiting;
  });

  if (task.manifest) {
    state.sourceSubtitles = task.manifest.source_subtitles || [];
    state.audioFiles = task.manifest.audio_files || {};
    // Do NOT reload/re-render cues on every poll — that would clobber the user's
    // in-progress manual edits and steal input focus. Load them only when first opening,
    // when they first become available (review reached), or when the run finishes.
    const reachedReview = state.lastStatus !== "awaiting_review" && task.status === "awaiting_review";
    const becameReady = state.lastStatus !== "ready" && task.status === "ready";
    if (initial || reachedReview || becameReady || state.cues.length === 0) {
      state.cues = task.manifest.cues || [];
      renderCues(state.cues);
    }
    renderExport(task.manifest);
  } else {
    state.cues = [];
    $("cueTable").innerHTML = "";
    $("roleTable").innerHTML = "";
    $("cueCount").textContent = "等待断句...";
  }

  if (initial) activateTab(task.status === "ready" ? "export" : "subtitles");
  if (state.lastStatus !== "ready" && task.status === "ready") activateTab("export");
  state.lastStatus = task.status;
}

function setActiveStep(task) {
  let stage;
  if (task.status === "awaiting_review") stage = "awaiting_review";
  else if (task.status === "ready") stage = "ready";
  else stage = stepToStage(task.step);
  document.querySelectorAll("#steps li").forEach((li) => li.classList.toggle("active", li.dataset.step === stage));
}

function stepToStage(step) {
  const s = String(step || "").toLowerCase();
  if (s.startsWith("awaiting")) return "awaiting_review";
  if (s.startsWith("tts")) return "tts";
  if (/(compose|separate|compress|mux|prepare video|assemble|remove|bgm|saved)/.test(s)) return "compose";
  if (/(segment|review)/.test(s)) return "segment";
  if (/(transcribe|translate)/.test(s)) return "translate";
  if (/(download|import|prepare)/.test(s)) return "download";
  if (s === "ready") return "ready";
  return "download";
}

function bindTaskBar() {
  $("confirmBtn").addEventListener("click", confirmTask);
  $("taskCancel").addEventListener("click", () => state.activeTaskId && cancelTask(state.activeTaskId));
  $("taskPause").addEventListener("click", togglePause);
  $("saveCues").addEventListener("click", () => saveCues(false));
  $("regenerateAllTts").addEventListener("click", regenerateAllTts);
  $("applyNarratorVoice").addEventListener("click", () => {
    const voice = $("bulkVoice").value;
    state.cues = state.cues.map((cue) => (cue.speaker_type === "narrator" ? { ...cue, voice } : cue));
    renderCues(state.cues);
  });
}

async function confirmTask() {
  if (!state.activeTaskId) return;
  $("confirmBtn").disabled = true;
  $("confirmBtn").textContent = "保存并生成中...";
  try {
    await saveCues(true); // must persist edits before we let the pipeline continue
    await fetch(`/api/tasks/${state.activeTaskId}/confirm`, { method: "POST" });
    await refreshActiveTask();
  } catch (err) {
    $("taskStepText").textContent = `确认失败：${err.message}（修改未保存，请重试）`;
  } finally {
    $("confirmBtn").disabled = false;
    $("confirmBtn").textContent = "确认并生成视频";
  }
}

async function cancelTask(id) {
  await fetch(`/api/tasks/${id}/cancel`, { method: "POST" });
  await refreshQueue();
  if (state.activeTaskId === id) await refreshActiveTask();
}

async function togglePause() {
  if (!state.activeTaskId || !state.task) return;
  const action = state.task.pause_requested || state.task.status === "paused" ? "resume" : "pause";
  await fetch(`/api/tasks/${state.activeTaskId}/${action}`, { method: "POST" });
  await refreshActiveTask();
}

// ---------------- voices / settings ----------------
function populateVoices() {
  ["bulkVoice", "qwenDefaultVoice"].forEach((id) => {
    const select = $(id);
    if (!select) return;
    select.innerHTML = "";
    state.voices.forEach((voice) => {
      const option = document.createElement("option");
      option.value = voice;
      option.textContent = voiceOptionText(voice);
      option.title = voiceOptionTitle(voice);
      select.appendChild(option);
    });
  });
}

async function loadVoices() {
  try {
    const response = await fetch("/api/voices");
    const data = await response.json();
    if (Array.isArray(data.voice_catalog)) state.voiceCatalog = data.voice_catalog;
    if (Array.isArray(data.voices) && data.voices.length > 0) state.voices = data.voices;
  } catch (_error) {
    /* keep bundled defaults */
  }
  populateVoices();
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  state.settings = await response.json();
  applySettings(state.settings);
}

function applySettings(settings) {
  $("translationEngine").value = settings.translation_engine || "google";
  $("llmProvider").value = settings.llm_provider || "deepseek";
  $("llmModel").value = settings.llm_model || "";
  $("llmSegmentModel").value = settings.llm_segment_model || "";
  $("llmReasoningEffort").value = settings.llm_reasoning_effort || "";
  $("llmBaseUrl").value = settings.llm_base_url || "";
  $("llmApiKey").value = settings.llm_api_key || "";
  $("temperature").value = settings.temperature ?? 0.2;
  $("maxTokens").value = settings.max_tokens ?? 16384;
  $("systemPrompt").value = settings.system_prompt || "";
  $("userPromptTemplate").value = settings.user_prompt_template || "";
  $("subtitleMode").value = settings.subtitle_mode || "hard";
  $("targetLanguageCode").value = settings.target_language_code || "zh-cn";
  $("outputDir").value = settings.output_dir || "";
  $("computeDevice").value = settings.compute_device || "auto";
  $("qwenDefaultVoice").value = settings.qwen_default_voice || state.voices[0];
  $("qwenTtsKey").value = settings.qwen_tts_key || "";
  $("qwenTtsModel").value = settings.qwen_tts_model || "qwen3-tts-instruct-flash";
  $("qwenTtsType").value = settings.qwen_tts_type ?? 14;
  $("bgmVolume").value = settings.bgm_volume ?? 0.8;
  $("youtubeCookiesFromBrowser").value = settings.youtube_cookies_from_browser || "";
  $("youtubeCookiesFile").value = settings.youtube_cookies_file || "";
  $("youtubePlayerClient").value = settings.youtube_player_client || "";
  $("youtubePoToken").value = settings.youtube_po_token || "";
  $("youtubeProxy").value = settings.youtube_proxy || "";
  $("localVideoPath").value = settings.local_video_path || "";
  $("localSubtitlePath").value = settings.local_subtitle_path || "";
}

function collectSettings() {
  return {
    translation_engine: $("translationEngine").value,
    llm_provider: $("llmProvider").value,
    llm_model: $("llmModel").value,
    llm_segment_model: $("llmSegmentModel").value,
    llm_reasoning_effort: $("llmReasoningEffort").value,
    llm_base_url: $("llmBaseUrl").value,
    llm_api_key: $("llmApiKey").value,
    temperature: Number($("temperature").value || 0.2),
    max_tokens: Number($("maxTokens").value || 16384),
    system_prompt: $("systemPrompt").value,
    user_prompt_template: $("userPromptTemplate").value,
    subtitle_mode: $("subtitleMode").value,
    target_language_code: $("targetLanguageCode").value,
    output_dir: $("outputDir").value,
    compute_device: $("computeDevice").value,
    qwen_default_voice: $("qwenDefaultVoice").value,
    qwen_tts_key: $("qwenTtsKey").value,
    qwen_tts_model: $("qwenTtsModel").value,
    qwen_tts_type: Number($("qwenTtsType").value || 14),
    bgm_volume: Number($("bgmVolume").value || 0.8),
    youtube_cookies_from_browser: $("youtubeCookiesFromBrowser").value,
    youtube_cookies_file: $("youtubeCookiesFile").value,
    youtube_player_client: $("youtubePlayerClient").value,
    youtube_po_token: $("youtubePoToken").value,
    youtube_proxy: $("youtubeProxy").value,
    local_video_path: $("localVideoPath").value,
    local_subtitle_path: $("localSubtitlePath").value
  };
}

function bindSettings() {
  $("saveSettings").addEventListener("click", () => saveSettings(true));
  $("testLlmConnection").addEventListener("click", () => testConnection("llm"));
  $("testQwenConnection").addEventListener("click", () => testConnection("qwen"));
}

async function saveSettings(showMessage) {
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  state.settings = data.settings;
  if (showMessage) $("settingsState").textContent = "已保存 ✓";
}

function toggleSecret(button) {
  const input = $(button.dataset.target);
  const visible = input.type === "text";
  input.type = visible ? "password" : "text";
  button.textContent = visible ? "显示" : "隐藏";
}

async function testConnection(kind) {
  const stateId = kind === "llm" ? "llmTestState" : "qwenTestState";
  $(stateId).textContent = "测试中...";
  const response = await fetch(`/api/settings/test-${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  $(stateId).textContent = `${data.status}: ${data.message || ""}`;
}

// ---------------- cue editor ----------------
function renderCues(cues) {
  // Editing is only allowed while the task is paused for manual review — otherwise the run
  // is already generating and edits would be silently discarded.
  const editable = !!(state.task && state.task.status === "awaiting_review");
  const dis = editable ? "" : "disabled";
  $("cueCount").textContent = `${cues.length} cues${editable ? "" : "（只读）"}`;
  $("cueTable").innerHTML = "";
  $("roleTable").innerHTML = "";
  cues.forEach((cue, idx) => {
    const cueRow = document.createElement("tr");
    cueRow.innerHTML = `
      <td>${(cue.source_lines || []).join(", ")}</td>
      <td><div class="source-text">${escapeHtml(sourceTextForCue(cue))}</div></td>
      <td class="time-cell">
        <input class="time-input" data-cue="${idx}" data-field="start_ms" type="number" value="${cue.start_ms}" ${dis} />
        <input class="time-input" data-cue="${idx}" data-field="end_ms" type="number" value="${cue.end_ms}" ${dis} />
      </td>
      <td><textarea data-cue="${idx}" data-field="zh_text" ${dis}>${escapeHtml(cue.zh_text || "")}</textarea></td>
      <td>${cue.needs_review ? '<span class="issue">需确认</span>' : "OK"}</td>
      <td class="cue-actions">
        <button data-action="split" data-index="${idx}" type="button" ${!editable || (cue.source_lines || []).length < 2 ? "disabled" : ""}>拆分</button>
        <button data-action="merge-next" data-index="${idx}" type="button" ${!editable || idx >= cues.length - 1 ? "disabled" : ""}>合并下一条</button>
      </td>`;
    $("cueTable").appendChild(cueRow);

    const roleRow = document.createElement("tr");
    roleRow.innerHTML = `
      <td><input data-cue="${idx}" data-field="speaker" value="${escapeAttr(cue.speaker || "")}" ${dis} /></td>
      <td>${escapeHtml(cue.speaker_type || "")}</td>
      <td>${voiceSelect(idx, cue.voice, editable)}</td>
      <td>${escapeHtml(cue.zh_text || "")}</td>
      <td class="cue-actions">
        <button data-tts-action="play" data-cue-id="${escapeAttr(cue.id)}" type="button" ${state.audioFiles[cue.id] ? "" : "disabled"}>试听</button>
        <button data-tts-action="regenerate" data-cue-id="${escapeAttr(cue.id)}" type="button" ${dis}>重配</button>
      </td>`;
    $("roleTable").appendChild(roleRow);
  });
  document.querySelectorAll("[data-cue]").forEach((input) => {
    input.addEventListener("change", (event) => {
      const index = Number(event.target.dataset.cue);
      const field = event.target.dataset.field;
      let value = event.target.value;
      if (field === "start_ms" || field === "end_ms") value = Number(value);
      state.cues[index][field] = value;
    });
  });
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.action === "split") splitCue(Number(button.dataset.index));
      if (button.dataset.action === "merge-next") mergeNextCue(Number(button.dataset.index));
    });
  });
  document.querySelectorAll("[data-tts-action]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.ttsAction === "play") playCueAudio(button.dataset.cueId);
      if (button.dataset.ttsAction === "regenerate") regenerateCueTts(button.dataset.cueId);
    });
  });
}

function sourceTextForCue(cue) {
  const rows = sourceRowsForLines(cue.source_lines || []);
  return rows.map((row) => `${row.line}. ${row.text}`).join("\n");
}

function sourceRowsForLines(lines) {
  const wanted = new Set(lines.map(Number));
  return state.sourceSubtitles.filter((row) => wanted.has(Number(row.line)));
}

function boundsForLines(lines) {
  const rows = sourceRowsForLines(lines);
  if (rows.length === 0) return { start_ms: 0, end_ms: 0 };
  return { start_ms: Number(rows[0].start_ms), end_ms: Number(rows[rows.length - 1].end_ms) };
}

function splitCue(index) {
  const cue = state.cues[index];
  const lines = (cue.source_lines || []).map(Number);
  if (lines.length < 2) return;
  const cut = Math.ceil(lines.length / 2);
  const firstLines = lines.slice(0, cut);
  const secondLines = lines.slice(cut);
  const [firstText, secondText] = splitCueText(cue.zh_text || "");
  const firstBounds = boundsForLines(firstLines);
  const secondBounds = boundsForLines(secondLines);
  state.cues.splice(
    index,
    1,
    { ...cue, id: `${cue.id || "cue"}-a`, source_lines: firstLines, start_ms: firstBounds.start_ms, end_ms: firstBounds.end_ms, zh_text: firstText, needs_review: true },
    { ...cue, id: `${cue.id || "cue"}-b`, source_lines: secondLines, start_ms: secondBounds.start_ms, end_ms: secondBounds.end_ms, zh_text: secondText, needs_review: true }
  );
  renderCues(state.cues);
}

function mergeNextCue(index) {
  const cue = state.cues[index];
  const next = state.cues[index + 1];
  if (!cue || !next) return;
  const lines = [...(cue.source_lines || []), ...(next.source_lines || [])].map(Number).sort((a, b) => a - b);
  const bounds = boundsForLines(lines);
  state.cues.splice(index, 2, {
    ...cue,
    id: `${cue.id || "cue"}-merged`,
    source_lines: lines,
    start_ms: bounds.start_ms,
    end_ms: bounds.end_ms,
    zh_text: [cue.zh_text, next.zh_text].filter(Boolean).join(""),
    confidence: Math.min(Number(cue.confidence || 0), Number(next.confidence || 0)),
    needs_review: true
  });
  renderCues(state.cues);
}

function splitCueText(text) {
  const trimmed = String(text).trim();
  if (!trimmed) return ["", ""];
  const match = trimmed.match(/^(.+?[。！？；;,.，])(.+)$/);
  if (match) return [match[1].trim(), match[2].trim()];
  const midpoint = Math.ceil(trimmed.length / 2);
  return [trimmed.slice(0, midpoint), trimmed.slice(midpoint)];
}

function voiceSelect(index, value, editable) {
  const options = state.voices
    .map((voice) => {
      const selected = voice === value ? "selected" : "";
      return `<option value="${escapeAttr(voice)}" title="${escapeAttr(voiceOptionTitle(voice))}" ${selected}>${escapeHtml(voiceOptionText(voice))}</option>`;
    })
    .join("");
  return `<select data-cue="${index}" data-field="voice" ${editable ? "" : "disabled"}>${options}</select>`;
}

function voiceOptionText(voice) {
  const meta = voiceMeta(voice);
  if (!meta) return voice;
  return `${meta.zh_name} (${meta.voice_param})`;
}

function voiceOptionTitle(voice) {
  const meta = voiceMeta(voice);
  if (!meta) return voice;
  const roles = Array.isArray(meta.recommended_roles) ? meta.recommended_roles.join("、") : "";
  return [meta.gender, meta.feature, roles].filter(Boolean).join(" | ");
}

function voiceMeta(voice) {
  return state.voiceCatalog.find((item) => item.label === voice || item.voice_param === voice || item.zh_name === voice);
}

function playCueAudio(cueId) {
  if (!state.activeTaskId || !cueId) return;
  const audio = $("previewAudio");
  audio.src = `/api/tasks/${state.activeTaskId}/tts/${encodeURIComponent(cueId)}?t=${Date.now()}`;
  audio.play().catch(() => {});
}

async function regenerateCueTts(cueId) {
  if (!state.activeTaskId || !cueId) return;
  await saveCues(true);
  const response = await fetch(`/api/tasks/${state.activeTaskId}/tts/${encodeURIComponent(cueId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  if (data.audio_files) state.audioFiles = data.audio_files;
  renderCues(state.cues);
  playCueAudio(cueId);
}

async function regenerateAllTts() {
  if (!state.activeTaskId) return;
  await saveCues(true);
  $("regenerateAllTts").disabled = true;
  $("regenerateAllTts").textContent = "重配中...";
  try {
    const response = await fetch(`/api/tasks/${state.activeTaskId}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: collectSettings() })
    });
    const data = await response.json();
    if (data.audio_files) state.audioFiles = data.audio_files;
    renderCues(state.cues);
  } finally {
    $("regenerateAllTts").disabled = false;
    $("regenerateAllTts").textContent = "全部重配";
  }
}

async function saveCues(silent) {
  if (!state.activeTaskId) return;
  const response = await fetch(`/api/tasks/${state.activeTaskId}/cues`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cues: state.cues })
  });
  if (!response.ok) throw new Error(`保存失败 (HTTP ${response.status})`);
  await response.json();
  if (!silent) $("cueCount").textContent = `${state.cues.length} cues · 已保存`;
}

function renderExport(manifest) {
  $("manifestJson").textContent = JSON.stringify(manifest, null, 2);
  $("workDir").textContent = manifest.work_dir || "-";
  const player = $("finalVideoPlayer");
  const link = $("finalVideo");
  if (manifest.final_video && state.activeTaskId) {
    const url = `/api/tasks/${state.activeTaskId}/final-video`;
    if (player.src.indexOf(url) === -1) player.src = url;
    player.classList.add("ready");
    link.textContent = "⬇ 下载视频";
    link.href = url;
    $("finalVideoPath").textContent = manifest.final_video;
  } else {
    player.removeAttribute("src");
    player.classList.remove("ready");
    link.textContent = "尚未生成";
    link.href = "#";
    $("finalVideoPath").textContent = "-";
  }
  const savedLine = $("savedPathLine");
  if (manifest.saved_video) {
    savedLine.hidden = false;
    $("savedPath").textContent = manifest.saved_video;
  } else {
    savedLine.hidden = true;
  }
}

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}
