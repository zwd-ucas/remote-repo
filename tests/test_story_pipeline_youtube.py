from videotrans.story_pipeline.settings import StoryPipelineSettings
from videotrans.story_pipeline.youtube import _download_strategies, _english_subtitle_choices, _yt_dlp_options, download_youtube, parse_srt_file


def test_youtube_download_prefers_high_quality_without_android_client(tmp_path):
    opts = _yt_dlp_options(tmp_path)

    assert _download_strategies() == ["default"]
    assert opts["format"].startswith("bv*")
    assert opts["merge_output_format"] == "mp4"
    assert "extractor_args" not in opts


def test_youtube_download_can_use_cookies_and_po_token_without_android(tmp_path):
    settings = StoryPipelineSettings(
        youtube_cookies_from_browser="chrome:Default",
        youtube_cookies_file="D:/cookies/youtube.txt",
        youtube_player_client="mweb",
        youtube_po_token="abc123",
        youtube_proxy="http://127.0.0.1:7897",
    )

    opts = _yt_dlp_options(tmp_path, settings)

    assert opts["cookiesfrombrowser"] == ("chrome", "Default", None, None)
    assert opts["cookiefile"] == "D:/cookies/youtube.txt"
    assert opts["proxy"] == "http://127.0.0.1:7897"
    assert opts["extractor_args"] == {"youtube": {"player_client": ["mweb"], "po_token": ["mweb.gvs+abc123"]}}


def test_english_subtitle_choices_skip_translated_caption_keys():
    info = {
        "automatic_captions": {
            "en-ar": [{"ext": "vtt", "url": "https://example.test/en-ar.vtt"}],
            "en": [{"ext": "vtt", "url": "https://example.test/en.vtt"}],
        }
    }

    choices = _english_subtitle_choices(info)

    assert [lang for lang, _entry in choices] == ["en"]


def test_parse_srt_file_deduplicates_and_strips_zero_width_chars(tmp_path):
    srt = tmp_path / "auto.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n\u200b Hello \u200b\n\n"
        "2\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )

    items = parse_srt_file(srt)

    assert len(items) == 1
    assert items[0]["text"] == "Hello"


def test_download_youtube_can_import_local_video_and_subtitle(tmp_path):
    video = tmp_path / "story.mp4"
    subtitle = tmp_path / "story.en.srt"
    video.write_bytes(b"fake-mp4")
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    work = tmp_path / "work"

    result = download_youtube(
        "https://www.youtube.com/watch?v=unused",
        work,
        settings=StoryPipelineSettings(local_video_path=str(video), local_subtitle_path=str(subtitle)),
    )

    assert result["video_id"] == "local-story"
    assert result["video_path"].endswith("story.mp4")
    assert result["subtitle_path"].endswith("story.en.srt")
    assert result["source_subtitles"][0]["text"] == "Hello"
