# Story Dubbing Workbench

Local web app for turning an English YouTube story video into a Chinese-dubbed video with the original picture, BGM-only backing audio, Chinese subtitles, and Qwen3 TTS character voices.

## Run

```powershell
python -m videotrans.story_pipeline.web
```

Open:

```text
http://127.0.0.1:7860/
```

## Main Flow

1. Enter a YouTube story video URL, or set a local downloaded video path in Settings.
2. Load English captions from YouTube or from a local `.srt` / `.vtt` file.
3. Translate and re-segment with the configured LLM prompt.
4. Review Chinese cues, speakers, and Qwen3 voice choices.
5. Generate per-cue Qwen TTS audio.
6. Mix Chinese voice with BGM-only backing audio and export the final MP4.

## Notes

- The slim web-only project does not include the legacy GUI, old ASR runtime, old translator/TTS provider modules, or old desktop tools.
- If YouTube blocks anonymous downloads for a video, use Settings to provide cookies / PO Token, or download the video externally and set `本地高清视频路径`.
- If no English subtitles are available, provide a local English subtitle file. The removed legacy ASR runtime is no longer bundled.
