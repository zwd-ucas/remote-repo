import threading
import time
import uuid
from dataclasses import asdict
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
ROOT_DIR = Path.cwd()
OUTPUT_ROOT = ROOT_DIR / "output" / "story"
SETTINGS_PATH = default_settings_path(OUTPUT_ROOT)


class RunRequest(BaseModel):
    youtube_url: str
    settings: dict[str, Any] | None = None


class SettingsRequest(BaseModel):
    settings: dict[str, Any]


class TaskState:
    def __init__(self, task_id: str, work_dir: Path):
        self.task_id = task_id
        self.work_dir = work_dir
        self.status = "queued"
        self.step = "queued"
        self.logs: list[str] = []
        self.manifest: dict[str, Any] | None = None
        self.error: str | None = None
        self.pause_requested = False
        self._resume_event = threading.Event()
        self._resume_event.set()
        self.created_at = time.time()
        self.updated_at = self.created_at

    def progress(self, text: str) -> None:
        self.step = text
        self.logs.append(text)
        self.updated_at = time.time()
        self.wait_if_paused()

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "step": self.step,
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
    if not req.youtube_url.strip():
        raise HTTPException(status_code=400, detail="youtube_url is required")
    settings = _settings_from_dict(req.settings or load_settings(SETTINGS_PATH).to_dict())
    save_settings(settings, SETTINGS_PATH)
    task_id = uuid.uuid4().hex[:12]
    work_dir = OUTPUT_ROOT / task_id
    state = TaskState(task_id, work_dir)
    tasks[task_id] = state
    thread = threading.Thread(target=_run_task, args=(state, req.youtube_url, settings), daemon=True)
    thread.start()
    return state.to_dict()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    state = tasks.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    return state.to_dict()


@app.get("/api/tasks")
def list_tasks():
    return [state.to_dict() for state in sorted(tasks.values(), key=lambda item: item.created_at, reverse=True)]


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
    path = state.work_dir / "story_cues.json"
    path.write_text(__import__("json").dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
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
    cues = [StoryCue(**cue) for cue in state.manifest.get("cues", [])]
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
    audio_files = _synthesize_task_cues(state, [StoryCue(**cue_data)], settings)
    return {"status": "ready", "audio_files": audio_files}


def _run_task(state: TaskState, url: str, settings: StoryPipelineSettings) -> None:
    state.status = "running"
    try:
        pipeline = StoryPipeline(state.work_dir, PipelineDependencies(), progress=state.progress)
        manifest = pipeline.run(url, settings)
        state.manifest = manifest.to_dict()
        state.status = "ready"
        state.progress("ready")
    except Exception as exc:
        state.status = "error"
        state.error = str(exc)
        state.progress(f"error: {exc}")


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
