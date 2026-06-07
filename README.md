# Story Dubbing Workbench

Local web app for turning an English YouTube story video into a Chinese-dubbed video with the original picture, BGM-only backing audio, Chinese subtitles, and Qwen3 TTS character voices.

## Setup

Use Python 3.10–3.12. (On Python 3.13 `pydub` needs the `audioop-lts` backport, which
is installed automatically.) `ffmpeg` must be on `PATH` (or rely on the bundled
`imageio-ffmpeg`).

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

## Run

```bash
.venv/bin/python -m videotrans.story_pipeline.web
```

Open:

```text
http://127.0.0.1:7860/
```

## YouTube 1080p downloads

Modern YouTube scrambles stream URLs (the n-sig challenge) and gates the 1080p web
formats behind a Proof-of-Origin (PO) token; without these, anonymous downloads fall
back to images-only or 360p. To download 1080p for free the project uses:

- **Node.js** as the JS runtime that solves the n-sig challenge. Install it (`brew
  install node`) and keep it on `PATH`. `videotrans/story_pipeline/youtube.py` enables
  it automatically via yt-dlp's `js_runtimes`. The challenge-solver scripts come from
  the `yt-dlp-ejs` dependency.
- **bgutil PO token provider** to generate GVS PO tokens. The `bgutil-ytdlp-pot-provider`
  yt-dlp plugin is a project dependency, but the token generator is a separate Node
  project that must be built once at the plugin's default discovery path:

  ```bash
  git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider ~/bgutil-ytdlp-pot-provider
  cd ~/bgutil-ytdlp-pot-provider/server && npm install && npx tsc
  ```

  With it at `~/bgutil-ytdlp-pot-provider`, yt-dlp uses **script mode** automatically —
  nothing needs to stay running. Optionally start the HTTP server
  (`node ~/bgutil-ytdlp-pot-provider/server/build/main.js`, port 4416) for faster
  per-download token generation.
- **Player client `web_safari`** (Settings → `youtube_player_client`), the web-family
  client that exposes HLS formats up to 1080p. The download format is capped at 1080p.

## Main Flow

1. Enter a YouTube story video URL, or set a local downloaded video path in Settings.
2. Load English captions from YouTube or from a local `.srt` / `.vtt` file.
3. Translate and re-segment with the configured LLM prompt.
4. Review Chinese cues, speakers, and Qwen3 voice choices.
5. Generate per-cue Qwen TTS audio.
6. Mix Chinese voice with BGM-only backing audio and export the final MP4.

## Notes

- The slim web-only project does not include the legacy GUI, old ASR runtime, old translator/TTS provider modules, or old desktop tools.
- 1080p downloads need the Node + bgutil setup above. If YouTube still blocks a specific video (e.g. some "made for kids" titles are DRM/SABR-locked), provide cookies / a PO Token in Settings, or download the video externally and set `本地高清视频路径`.
- If no English subtitles are available, provide a local English subtitle file. The removed legacy ASR runtime is no longer bundled.
