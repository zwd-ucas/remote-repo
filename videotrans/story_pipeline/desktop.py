"""Desktop entry point for the packaged Story Dubbing app.

Starts the FastAPI server in a background thread and opens it in a native window
(pywebview), falling back to the default browser. All writable data — settings (with
API keys), per-task work dirs, and the downloaded ASR / separation models — goes to a
per-user folder so the app works even when its program directory is read-only.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

HOST = "127.0.0.1"
PORT = 7860


def _data_home() -> Path:
    home = os.environ.get("STORY_DUBBING_HOME")
    path = Path(home).expanduser() if home else (Path.home() / "StoryDubbing")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _setup_env(data_home: Path) -> None:
    os.environ["STORY_DUBBING_HOME"] = str(data_home)
    # Downloaded whisper / HuggingFace models go to the writable data dir (must be set
    # before huggingface_hub / faster_whisper are imported, which happens lazily later).
    os.environ.setdefault("HF_HOME", str(data_home / "models" / "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    _add_bundled_node_to_path()
    _setup_cuda_libs()


def _setup_cuda_libs() -> None:
    """Make NVIDIA CUDA libs (shipped by torch's wheels in the CUDA build) discoverable so
    ctranslate2 / onnxruntime-gpu can use the GPU. No-op on the CPU build (no nvidia dir)."""
    try:
        import sysconfig

        nvidia = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    except Exception:
        return
    if not nvidia.exists():
        return
    for sub in nvidia.iterdir():
        for lib in (sub / "bin", sub / "lib"):
            if not lib.exists():
                continue
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(lib))
                except OSError:
                    pass


def _add_bundled_node_to_path() -> None:
    """yt-dlp's 1080p challenge needs a JS runtime; put the bundled node on PATH."""
    explicit = os.environ.get("STORY_DUBBING_NODE")
    node_dir: str | None = None
    if explicit and Path(explicit).exists():
        node_dir = str(Path(explicit).parent)
    else:
        runtime = Path(sys.executable).resolve()
        for root in [runtime.parents[i] for i in range(1, 4) if i < len(runtime.parents)]:
            for rel in ("node/node.exe", "node/bin/node", "node/node"):
                candidate = root / rel
                if candidate.exists():
                    node_dir = str(candidate.parent)
                    break
            if node_dir:
                break
    if node_dir:
        os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")


def _wait_for_server(timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _run_server() -> None:
    import uvicorn

    from videotrans.story_pipeline.server import app

    # Server runs in a background (non-main) thread; uvicorn skips signal handlers there.
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")).run()


def main() -> None:
    _setup_env(_data_home())
    threading.Thread(target=_run_server, daemon=True).start()
    if not _wait_for_server():
        print("Story Dubbing: backend failed to start", file=sys.stderr)

    url = f"http://{HOST}:{PORT}"
    try:
        import webview

        webview.create_window("Story Dubbing Workbench", url, width=1280, height=860, min_size=(960, 640))
        webview.start()
    except Exception:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
