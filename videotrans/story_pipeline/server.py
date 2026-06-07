import json
import os
import queue as queuelib
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except Exception as exc:  # pragma: no cover - import-time runtime dependency guard
    raise RuntimeError("FastAPI, pydantic, and uvicorn are required for the story pipeline web app.") from exc

from .pipeline import PipelineDependencies, StoryPipeline
from .settings import StoryPipelineSettings, default_settings_path, load_settings, save_settings
from .story_segments import StoryCue

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
# Settings (with API keys), per-task work dirs, and downloaded models must live somewhere
# writable. In a dev checkout that's the cwd; the packaged desktop app sets
# STORY_DUBBING_HOME to a per-user folder (the program dir itself is read-only).
ROOT_DIR = Path(os.environ["STORY_DUBBING_HOME"]).expanduser() if os.environ.get("STORY_DUBBING_HOME") else Path.cwd()
OUTPUT_ROOT = ROOT_DIR / "output" / "story"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = default_settings_path(OUTPUT_ROOT)


class _Cancelled(BaseException):
    """Raised from a task's progress callback to abort the pipeline when cancelled.

    A BaseException (not Exception) so the pipeline's internal `except Exception` blocks
    don't swallow it — it propagates up to _run_task which returns cleanly.
    """


class RunRequest(BaseModel):
    youtube_url: str | None = None
    urls: list[str] | None = None
    mode: str | None = "auto"
    settings: dict[str, Any] | None = None


class SettingsRequest(BaseModel):
    settings: dict[str, Any]


# Map a pipeline step to an overall 0-100% (TTS, the long stage, has per-cue sub-progress).
# Keys are matched as substrings of the step text, in order, so it stays monotonic.
_PROGRESS_TABLE = [
    ("saved", 98),
    ("mux", 95),
    ("separate", 91),
    ("remove original", 91),
    ("compress", 89),
    ("assemble", 88),
    ("prepare video", 86),
    ("compose", 86),
    ("tts", 55),
    ("review", 52),
    ("segment", 46),
    ("translate", 38),
    ("transcribe", 22),
    ("importing", 8),
    ("import", 8),
    ("download", 6),
]


def _progress_pct(step: str, status: str) -> int:
    if status == "ready":
        return 100
    if status == "queued":
        return 0
    s = (step or "").strip().lower()
    if s.startswith("tts:") and "/" in s:
        try:
            done_str, rest = s[4:].split("/", 1)
            done, total = int(done_str), int(rest.split()[0])
            if total > 0:
                return max(55, min(85, 55 + round(30 * done / total)))
        except ValueError:
            pass
    for key, pct in _PROGRESS_TABLE:
        if key in s:
            return pct
    return 4


class TaskState:
    def __init__(self, task_id: str, work_dir: Path, *, mode: str = "auto", url: str = ""):
        self.task_id = task_id
        self.work_dir = work_dir
        self.mode = mode if mode in {"auto", "manual"} else "auto"
        self.url = url
        self.settings: StoryPipelineSettings | None = None
        self.status = "queued"
        self.step = "queued"
        self.awaiting = ""  # the checkpoint stage name while status == "awaiting_review"
        self.logs: list[str] = []
        self.manifest: dict[str, Any] | None = None
        self.error: str | None = None
        self.pause_requested = False
        self.cancelled = False
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._confirm_event = threading.Event()
        self.created_at = time.time()
        self.updated_at = self.created_at

    def progress(self, text: str) -> None:
        self.step = text
        self.logs.append(text)
        self.updated_at = time.time()
        self.wait_if_paused()
        if self.cancelled:
            # Abort the pipeline at the next progress checkpoint instead of running the
            # whole job to completion after the user already cancelled.
            raise _Cancelled()

    def wait_for_confirm(self, stage: str) -> None:
        """Manual-mode pause: hold here until the user confirms (or cancels)."""
        self.awaiting = stage
        self.status = "awaiting_review"
        self.logs.append(f"awaiting:{stage}")
        self.updated_at = time.time()
        self._confirm_event.clear()
        while not self._confirm_event.is_set() and not self.cancelled:
            self._confirm_event.wait(timeout=0.2)
        self.awaiting = ""
        if not self.cancelled:
            self.status = "running"
            self.updated_at = time.time()

    def confirm(self) -> None:
        self._confirm_event.set()
        if self.status == "awaiting_review":
            self.status = "running"
        self.logs.append("confirmed")
        self.updated_at = time.time()

    def cancel(self) -> None:
        self.cancelled = True
        self.pause_requested = False
        self._resume_event.set()
        self._confirm_event.set()
        self.status = "cancelled"
        self.logs.append("cancelled")
        self.updated_at = time.time()

    def request_pause(self) -> None:
        if self.status not in {"running", "queued"}:
            return
        self.pause_requested = True
        self._resume_event.clear()
        if self.status == "running":
            self.status = "pausing"
        self.logs.append("pause requested")
        self.updated_at = time.time()

    def resume(self) -> None:
        if not self.pause_requested:
            return
        self.pause_requested = False
        self._resume_event.set()
        if self.status in {"paused", "pausing"}:
            self.status = "running"
        self.logs.append("resume")
        self.updated_at = time.time()

    def wait_if_paused(self) -> None:
        if not self.pause_requested or self.status in {"ready", "error"}:
            return
        self.status = "paused"
        self.logs.append("paused")
        self.updated_at = time.time()
        while self.pause_requested and self.status not in {"ready", "error"}:
            self._resume_event.wait(timeout=0.2)
        if self.status not in {"ready", "error"}:
            self.status = "running"

    def summary(self) -> dict[str, Any]:
        """Lightweight task info for the queue list (no heavy manifest payload)."""
        manifest = self.manifest or {}
        title = (manifest.get("title") or "").strip()
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "url": self.url,
            "title": title or self.url,
            "status": self.status,
            "step": self.step,
            "progress": _progress_pct(self.step, self.status),
            "awaiting": self.awaiting,
            "cancelled": self.cancelled,
            "pause_requested": self.pause_requested,
            "has_video": bool(manifest.get("final_video")),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> dict[str, Any]:
        title = ((self.manifest or {}).get("title") or "").strip()
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "url": self.url,
            "title": title or self.url,
            "status": self.status,
            "step": self.step,
            "progress": _progress_pct(self.step, self.status),
            "awaiting": self.awaiting,
            "cancelled": self.cancelled,
            "logs": self.logs[-200:],
            "manifest": self.manifest,
            "error": self.error,
            "pause_requested": self.pause_requested,
            "work_dir": self.work_dir.as_posix(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


app = FastAPI(title="Story Pipeline Workbench")
tasks: dict[str, TaskState] = {}

# Tasks are processed one at a time (serial queue). A manual-mode task blocks the worker
# at its review checkpoint until the user confirms, which is the intended serial behaviour.
_task_queue: "queuelib.Queue[str]" = queuelib.Queue()


def _worker_loop() -> None:
    while True:
        task_id = _task_queue.get()
        state = tasks.get(task_id)
        if state is None or state.cancelled:
            continue
        _run_task(state)


_worker_thread = threading.Thread(target=_worker_loop, daemon=True)
_worker_thread.start()


def _recover_tasks() -> None:
    """Re-populate finished tasks from disk so a server restart doesn't orphan past videos."""
    if not OUTPUT_ROOT.exists():
        return
    for work_dir in sorted(OUTPUT_ROOT.iterdir(), key=lambda p: p.name):
        manifest_path = work_dir / "manifest.json"
        if not work_dir.is_dir() or not manifest_path.exists() or work_dir.name in tasks:
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not manifest.get("final_video"):
            continue
        state = TaskState(work_dir.name, work_dir, mode="auto", url=manifest.get("title") or work_dir.name)
        state.manifest = manifest
        state.status = "ready"
        state.step = "ready"
        try:
            state.created_at = state.updated_at = manifest_path.stat().st_mtime
        except OSError:
            pass
        tasks[work_dir.name] = state


_recover_tasks()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/settings")
def get_settings():
    return load_settings(SETTINGS_PATH).to_dict()


@app.get("/api/voices")
def get_voices():
    from .pipeline import load_qwen_voice_catalog, load_qwen_voice_names
    from .voices import ROLE_VOICE_RECOMMENDATIONS

    return {
        "voices": sorted(load_qwen_voice_names()),
        "voice_catalog": load_qwen_voice_catalog(),
        "role_voice_recommendations": ROLE_VOICE_RECOMMENDATIONS,
    }


@app.post("/api/settings")
def post_settings(req: SettingsRequest):
    settings = _settings_from_dict(req.settings)
    save_settings(settings, SETTINGS_PATH)
    return {"status": "saved", "settings": settings.to_dict(mask_secrets=True)}


@app.post("/api/settings/test-llm")
def test_llm_settings(req: SettingsRequest):
    settings = _settings_from_dict(req.settings)
    if not settings.llm_api_key.strip():
        return {"status": "missing_key", "message": "请先填写 LLM API Key。"}
    try:
        from .pipeline import call_llm_chat

        sample = call_llm_chat(settings, "Return only OK.", "OK")
        return {"status": "ok", "message": (sample or "OK")[:120]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/settings/test-qwen")
def test_qwen_settings(req: SettingsRequest):
    settings = _settings_from_dict(req.settings)
    if not settings.qwen_tts_key.strip():
        return {"status": "missing_key", "message": "请先填写 Qwen TTS Key。"}
    from .pipeline import load_qwen_voice_names

    voices = load_qwen_voice_names()
    if settings.qwen_default_voice not in voices:
        return {"status": "warning", "message": "默认音色不在当前 Qwen 音色列表中，将在生成时回退。"}
    return {"status": "ok", "message": f"已识别 {len(voices)} 个 Qwen 音色。"}


@app.post("/api/run")
def run_pipeline(req: RunRequest):
    raw = list(req.urls or [])
    if req.youtube_url:
        raw.append(req.youtube_url)
    urls = [u.strip() for u in raw if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="at least one youtube_url is required")
    mode = (req.mode or "auto").strip().lower()
    settings = _settings_from_dict(req.settings or load_settings(SETTINGS_PATH).to_dict())
    save_settings(settings, SETTINGS_PATH)
    created: list[dict[str, Any]] = []
    for url in urls:
        task_id = uuid.uuid4().hex[:12]
        state = TaskState(task_id, OUTPUT_ROOT / task_id, mode=mode, url=url)
        state.settings = settings
        tasks[task_id] = state
        _task_queue.put(task_id)
        created.append(state.to_dict())
    return {"tasks": created, "mode": mode}


@app.post("/api/tasks/{task_id}/confirm")
def confirm_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    state.confirm()
    return state.to_dict()


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    state.cancel()
    return state.to_dict()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    return state.to_dict()


@app.get("/api/tasks")
def list_tasks():
    return [state.summary() for state in sorted(tasks.values(), key=lambda item: item.created_at, reverse=True)]


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    state.request_pause()
    return state.to_dict()


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    state.resume()
    return state.to_dict()


@app.get("/api/tasks/{task_id}/final-video")
def get_final_video(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    final_path = (state.manifest or {}).get("final_video")
    if not final_path or not Path(final_path).exists():
        raise HTTPException(status_code=404, detail="Final video not found")
    return FileResponse(final_path, media_type="video/mp4", filename=Path(final_path).name)


@app.post("/api/tasks/{task_id}/cues")
def update_cues(task_id: str, payload: dict[str, Any]):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    cues = payload.get("cues")
    if not isinstance(cues, list):
        raise HTTPException(status_code=400, detail="cues must be a list")
    state.work_dir.mkdir(parents=True, exist_ok=True)
    path = state.work_dir / "story_cues.json"
    path.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    if state.manifest:
        state.manifest["cues"] = cues
    return {"status": "saved", "cues": cues}


@app.get("/api/tasks/{task_id}/tts/{cue_id}")
def get_cue_audio(task_id: str, cue_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    audio_path = ((state.manifest or {}).get("audio_files") or {}).get(cue_id)
    if not audio_path or not Path(audio_path).exists():
        raise HTTPException(status_code=404, detail="Cue audio not found")
    return FileResponse(audio_path, media_type="audio/wav", filename=Path(audio_path).name)


@app.post("/api/tasks/{task_id}/tts")
def regenerate_all_tts(task_id: str, payload: dict[str, Any] | None = None):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    if not state.manifest:
        raise HTTPException(status_code=400, detail="Task manifest is not ready")
    settings = _settings_from_payload(payload)
    cues = [_cue_from_dict(cue) for cue in state.manifest.get("cues", [])]
    audio_files = _synthesize_task_cues(state, cues, settings)
    return {"status": "ready", "audio_files": audio_files}


@app.post("/api/tasks/{task_id}/tts/{cue_id}")
def regenerate_cue_tts(task_id: str, cue_id: str, payload: dict[str, Any] | None = None):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    if not state.manifest:
        raise HTTPException(status_code=400, detail="Task manifest is not ready")
    cue_data = next((cue for cue in state.manifest.get("cues", []) if cue.get("id") == cue_id), None)
    if not cue_data:
        raise HTTPException(status_code=404, detail="Cue not found")
    settings = _settings_from_payload(payload)
    audio_files = _synthesize_task_cues(state, [_cue_from_dict(cue_data)], settings)
    return {"status": "ready", "audio_files": audio_files}


def _run_task(state: TaskState) -> None:
    if state.cancelled:
        return
    state.status = "running"
    state.updated_at = time.time()
    settings = state.settings or load_settings(SETTINGS_PATH)
    deps = PipelineDependencies(checkpoint=_make_checkpoint(state)) if state.mode == "manual" else PipelineDependencies()
    try:
        pipeline = StoryPipeline(state.work_dir, deps, progress=state.progress)
        manifest = pipeline.run(state.url, settings)
        if state.cancelled:
            return
        state.manifest = manifest.to_dict()
        state.status = "ready"
        state.progress("ready")
    except _Cancelled:
        return  # cancelled mid-run; status already set to "cancelled"
    except Exception as exc:
        if state.cancelled:
            return
        state.status = "error"
        state.error = str(exc)
        state.logs.append(f"error: {exc}")


def _make_checkpoint(state: TaskState):
    def checkpoint(cues, review_manifest):
        if state.cancelled:
            return cues
        state.manifest = review_manifest.to_dict()
        state.wait_for_confirm("review")
        if state.cancelled:
            return cues
        cue_path = state.work_dir / "story_cues.json"
        if cue_path.exists():
            try:
                data = json.loads(cue_path.read_text(encoding="utf-8"))
                return [_cue_from_dict(item) for item in data]
            except Exception as exc:  # malformed / incomplete edits -> keep the safe originals
                state.logs.append(f"reload edited cues failed ({exc}); using originals")
        return cues

    return checkpoint


def _cue_from_dict(data: dict[str, Any]) -> StoryCue:
    fields = StoryCue.__dataclass_fields__.keys()
    return StoryCue(**{key: value for key, value in data.items() if key in fields})


def _settings_from_dict(data: dict[str, Any]) -> StoryPipelineSettings:
    allowed = StoryPipelineSettings.__dataclass_fields__.keys()
    return StoryPipelineSettings(**{key: value for key, value in data.items() if key in allowed})


def _settings_from_payload(payload: dict[str, Any] | None) -> StoryPipelineSettings:
    data = (payload or {}).get("settings") or load_settings(SETTINGS_PATH).to_dict()
    return _settings_from_dict(data)


def _synthesize_task_cues(state: TaskState, cues: list[StoryCue], settings: StoryPipelineSettings) -> dict[str, str]:
    from .pipeline import default_synthesize

    state.progress("tts")
    audio_files = default_synthesize(cues, settings, state.work_dir, state.progress)
    state.manifest = state.manifest or {}
    merged_audio = dict(state.manifest.get("audio_files") or {})
    merged_audio.update(audio_files)
    state.manifest["audio_files"] = merged_audio
    return merged_audio


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
