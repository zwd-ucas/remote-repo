#!/usr/bin/env python3
"""Assemble the Story Dubbing desktop payload.

Produces ``<out>/runtime`` (a relocatable standalone CPython with the project + all deps
installed) and ``<out>/node`` (the JS runtime yt-dlp needs). Run on each target OS in CI;
the OS-specific wrappers (.app/.dmg, Inno Setup .exe) package this payload afterwards.

Variants:
  cpu  — onnxruntime / CPU torch (default; macOS is always cpu).
  cuda — onnxruntime-gpu + torch+cu121 so an NVIDIA GPU accelerates ASR + separation.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

NODE_VERSION = "v20.18.1"
PY_VERSION = "3.12"


def run(cmd: list) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def uv_managed_python(version: str) -> Path:
    run(["uv", "python", "install", version])
    base = subprocess.run(["uv", "python", "dir"], capture_output=True, text=True, check=True).stdout.strip()
    candidates = sorted(p for p in Path(base).glob(f"cpython-{version}.*") if p.is_dir())
    if not candidates:
        raise SystemExit(f"no uv-managed cpython {version} found under {base}")
    return candidates[-1]


def runtime_python(runtime: Path) -> Path:
    win = runtime / "python.exe"
    return win if win.exists() else runtime / "bin" / "python3"


def download_node(dest: Path) -> None:
    system = platform.system()
    tmp = dest.parent / "_node_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    if system == "Darwin":
        url = f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-darwin-arm64.tar.gz"
        archive = dest.parent / "node.tar.gz"
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp)
    elif system == "Windows":
        url = f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-win-x64.zip"
        archive = dest.parent / "node.zip"
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    else:
        raise SystemExit(f"unsupported platform {system}")
    inner = next(tmp.glob("node-*"))
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(inner), str(dest))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--variant", default="cpu", choices=["cpu", "cuda"])
    args = parser.parse_args()

    out = Path(args.out).resolve()
    repo = Path(__file__).resolve().parents[1]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    runtime = out / "runtime"
    shutil.copytree(uv_managed_python(PY_VERSION), runtime, symlinks=True)
    py = runtime_python(runtime)

    run(["uv", "pip", "install", "--python", py, "--upgrade", "pip", "wheel"])
    run(["uv", "pip", "install", "--python", py, f"{repo.as_posix()}[desktop]"])
    if args.variant == "cuda":
        run(["uv", "pip", "uninstall", "--python", py, "onnxruntime"])
        run(["uv", "pip", "install", "--python", py, "onnxruntime-gpu"])
        run(["uv", "pip", "install", "--python", py, "--reinstall-package", "torch", "torch", "--index-url", "https://download.pytorch.org/whl/cu121"])

    download_node(out / "node")
    print("payload ready:", out)


if __name__ == "__main__":
    main()
