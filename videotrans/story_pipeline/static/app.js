const state = {
  settings: {},
  taskId: null,
  poll: null,
  cues: [],
  sourceSubtitles: [],
  audioFiles: {},
  voiceCatalog: [],
  voices: [
    "沧明子(Eldric Sage)",
    "少女阿月(Stella)",
    "苏瑶(Serena)",
    "芊悦(Cherry)",
    "晨煦(Ethan)",
    "萌宝(Bella)",
    "卡捷琳娜(Katerina)",
    "田叔(Vincent)"
  ]
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", async () => {
  bindTabs();
  bindForms();
  await loadVoices();
  await loadSettings();
});

function bindTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab, .tab-page").forEach((el) => el.classList.remove("active"));
      button.classList.add("active");
      $(button.dataset.tab).classList.add("active");
    });
  });
}

function bindForms() {
  $("runForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveSettings(false);
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        youtube_url: $("youtubeUrl").value.trim(),
        settings: collectSettings()
      })
    });
    const data = await response.json();
    state.taskId = data.task_id;
    renderTask(data);
    startPolling();
  });

  $("pauseToggle").addEventListener("click", togglePause);
  document.querySelectorAll(".secret-toggle").forEach((button) => {
    button.addEventListener("click", () => toggleSecret(button));
  });
  $("testLlmConnection").addEventListener("click", () => testConnection("llm"));
  $("testQwenConnection").addEventListener("click", () => testConnection("qwen"));
  $("saveSettings").addEventListener("click", () => saveSettings(true));
  $("saveCues").addEventListener("click", saveCues);
  $("regenerateAllTts").addEventListener("click", regenerateAllTts);
  $("applyNarratorVoice").addEventListener("click", () => {
    const voice = $("bulkVoice").value;
    state.cues = state.cues.map((cue) => cue.speaker_type === "narrator" ? { ...cue, voice } : cue);
    renderCues(state.cues);
  });
}

function populateVoices() {
  ["bulkVoice", "qwenDefaultVoice"].forEach((id) => {
    const select = $(id);
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
    if (Array.isArray(data.voice_catalog)) {
      state.voiceCatalog = data.voice_catalog;
    }
    if (Array.isArray(data.voices) && data.voices.length > 0) {
      state.voices = data.voices;
    }
  } catch (_error) {
    // Keep bundled defaults when the backend cannot load the full voice list.
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
  $("llmBaseUrl").value = settings.llm_base_url || "";
  $("llmApiKey").value = settings.llm_api_key || "";
  $("temperature").value = settings.temperature ?? 0.2;
  $("maxTokens").value = settings.max_tokens ?? 4096;
  $("systemPrompt").value = settings.system_prompt || "";
  $("userPromptTemplate").value = settings.user_prompt_template || "";
  $("subtitleMode").value = settings.subtitle_mode || "hard";
  $("targetLanguageCode").value = settings.target_language_code || "zh-cn";
  $("qwenDefaultVoice").value = settings.qwen_default_voice || state.voices[0];
  $("qwenTtsKey").value = settings.qwen_tts_key || "";
  $("qwenTtsModel").value = settings.qwen_tts_model || "qwen3-tts-flash";
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
    llm_base_url: $("llmBaseUrl").value,
    llm_api_key: $("llmApiKey").value,
    temperature: Number($("temperature").value || 0.2),
    max_tokens: Number($("maxTokens").value || 4096),
    system_prompt: $("systemPrompt").value,
    user_prompt_template: $("userPromptTemplate").value,
    subtitle_mode: $("subtitleMode").value,
    target_language_code: $("targetLanguageCode").value,
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

async function saveSettings(showMessage) {
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  state.settings = data.settings;
  if (showMessage) $("settingsState").textContent = "已保存";
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

async function togglePause() {
  if (!state.taskId) return;
  const action = $("pauseToggle").textContent === "继续" ? "resume" : "pause";
  const response = await fetch(`/api/tasks/${state.taskId}/${action}`, { method: "POST" });
  const data = await response.json();
  renderTask(data);
}

function startPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(async () => {
    if (!state.taskId) return;
    const response = await fetch(`/api/tasks/${state.taskId}`);
    const data = await response.json();
    renderTask(data);
    if (["ready", "error"].includes(data.status)) {
      clearInterval(state.poll);
      state.poll = null;
    }
  }, 1200);
}

function renderTask(task) {
  $("taskId").textContent = task.task_id || "-";
  $("taskStep").textContent = task.step || "-";
  $("statusText").textContent = task.status || "Ready";
  renderPauseButton(task);
  setActiveStep(task.step);
  if (task.error) $("statusText").textContent = task.error;
  if (task.manifest) {
    state.sourceSubtitles = task.manifest.source_subtitles || [];
    state.audioFiles = task.manifest.audio_files || {};
    state.cues = task.manifest.cues || [];
    renderCues(state.cues);
    renderExport(task.manifest);
  }
}

function renderPauseButton(task) {
  const active = task.task_id && ["queued", "running", "pausing", "paused"].includes(task.status);
  $("pauseToggle").disabled = !active;
  $("pauseToggle").textContent = task.pause_requested || task.status === "paused" ? "继续" : "暂停";
}

function setActiveStep(step) {
  document.querySelectorAll("#steps li").forEach((item) => {
    item.classList.toggle("active", item.dataset.step === step);
  });
}

function renderCues(cues) {
  $("cueCount").textContent = `${cues.length} cues`;
  $("cueTable").innerHTML = "";
  $("roleTable").innerHTML = "";
  cues.forEach((cue, idx) => {
    const cueRow = document.createElement("tr");
    cueRow.innerHTML = `
      <td>${(cue.source_lines || []).join(", ")}</td>
      <td><div class="source-text">${escapeHtml(sourceTextForCue(cue))}</div></td>
      <td>${cue.start_ms} - ${cue.end_ms}</td>
      <td><textarea data-cue="${idx}" data-field="zh_text">${escapeHtml(cue.zh_text || "")}</textarea></td>
      <td>${cue.needs_review ? '<span class="issue">需确认</span>' : 'OK'}</td>
      <td class="cue-actions">
        <button data-action="split" data-index="${idx}" type="button" ${(cue.source_lines || []).length < 2 ? "disabled" : ""}>拆分</button>
        <button data-action="merge-next" data-index="${idx}" type="button" ${idx >= cues.length - 1 ? "disabled" : ""}>合并下一条</button>
      </td>
    `;
    $("cueTable").appendChild(cueRow);

    const roleRow = document.createElement("tr");
    roleRow.innerHTML = `
      <td><input data-cue="${idx}" data-field="speaker" value="${escapeAttr(cue.speaker || "")}" /></td>
      <td>${escapeHtml(cue.speaker_type || "")}</td>
      <td>${voiceSelect(idx, cue.voice)}</td>
      <td>${escapeHtml(cue.zh_text || "")}</td>
      <td class="cue-actions">
        <button data-tts-action="play" data-cue-id="${escapeAttr(cue.id)}" type="button" ${state.audioFiles[cue.id] ? "" : "disabled"}>试听</button>
        <button data-tts-action="regenerate" data-cue-id="${escapeAttr(cue.id)}" type="button">重配</button>
      </td>
    `;
    $("roleTable").appendChild(roleRow);
  });
  document.querySelectorAll("[data-cue]").forEach((input) => {
    input.addEventListener("change", (event) => {
      const index = Number(event.target.dataset.cue);
      const field = event.target.dataset.field;
      state.cues[index][field] = event.target.value;
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
  return {
    start_ms: Number(rows[0].start_ms),
    end_ms: Number(rows[rows.length - 1].end_ms)
  };
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
    {
      ...cue,
      id: `${cue.id || "cue"}-a`,
      source_lines: firstLines,
      start_ms: firstBounds.start_ms,
      end_ms: firstBounds.end_ms,
      zh_text: firstText,
      needs_review: true
    },
    {
      ...cue,
      id: `${cue.id || "cue"}-b`,
      source_lines: secondLines,
      start_ms: secondBounds.start_ms,
      end_ms: secondBounds.end_ms,
      zh_text: secondText,
      needs_review: true
    }
  );
  renderCues(state.cues);
}

function mergeNextCue(index) {
  const cue = state.cues[index];
  const next = state.cues[index + 1];
  if (!cue || !next) return;
  const lines = [...(cue.source_lines || []), ...(next.source_lines || [])].map(Number).sort((a, b) => a - b);
  const contiguous = lines.every((line, pos) => pos === 0 || line === lines[pos - 1] + 1);
  if (!contiguous) return;
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

function voiceSelect(index, value) {
  const options = state.voices.map((voice) => {
    const selected = voice === value ? "selected" : "";
    return `<option value="${escapeAttr(voice)}" title="${escapeAttr(voiceOptionTitle(voice))}" ${selected}>${escapeHtml(voiceOptionText(voice))}</option>`;
  }).join("");
  return `<select data-cue="${index}" data-field="voice">${options}</select>`;
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
  if (!state.taskId || !cueId) return;
  const audio = $("previewAudio");
  audio.src = `/api/tasks/${state.taskId}/tts/${encodeURIComponent(cueId)}?t=${Date.now()}`;
  audio.play();
}

async function regenerateCueTts(cueId) {
  if (!state.taskId || !cueId) return;
  const response = await fetch(`/api/tasks/${state.taskId}/tts/${encodeURIComponent(cueId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  if (data.audio_files) state.audioFiles = data.audio_files;
  renderCues(state.cues);
  if (data.audio_files?.[cueId]) playCueAudio(cueId);
}

async function regenerateAllTts() {
  if (!state.taskId) return;
  const response = await fetch(`/api/tasks/${state.taskId}/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() })
  });
  const data = await response.json();
  if (data.audio_files) state.audioFiles = data.audio_files;
  renderCues(state.cues);
}

async function saveCues() {
  if (!state.taskId) return;
  const response = await fetch(`/api/tasks/${state.taskId}/cues`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cues: state.cues })
  });
  await response.json();
  $("cueCount").textContent = `${state.cues.length} cues saved`;
}

function renderExport(manifest) {
  $("manifestJson").textContent = JSON.stringify(manifest, null, 2);
  $("workDir").textContent = manifest.work_dir || "-";
  if (manifest.final_video) {
    $("finalVideo").textContent = manifest.final_video;
    $("finalVideo").href = state.taskId ? `/api/tasks/${state.taskId}/final-video` : "#";
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}
