from videotrans.story_pipeline.settings import (
    DEFAULT_SYSTEM_PROMPT,
    StoryPipelineSettings,
    load_settings,
    save_settings,
)
from videotrans.story_pipeline.pipeline import load_qwen_voice_catalog


def test_settings_default_to_google_translate_and_qwen_tts():
    settings = StoryPipelineSettings()

    assert settings.translation_engine == "google"
    assert settings.llm_provider == "deepseek"
    assert settings.qwen_tts_type == 14
    assert "story" in DEFAULT_SYSTEM_PROMPT.lower()
    assert "不要调用外部网页查询音色" in DEFAULT_SYSTEM_PROMPT


def test_qwen_voice_catalog_contains_web_voice_params_and_roles():
    catalog = load_qwen_voice_catalog()
    by_param = {item["voice_param"]: item for item in catalog}

    assert by_param["Cherry"]["zh_name"] == "芊悦"
    assert by_param["Kai"]["label"] == "凯(Kai)"
    assert by_param["Ebona"]["zh_name"] == "诡婆婆"
    assert "女巫" in by_param["Ebona"]["recommended_roles"]


def test_settings_round_trip_preserves_prompt_and_api_values(tmp_path):
    path = tmp_path / "story-settings.json"
    settings = StoryPipelineSettings(
        translation_engine="glm",
        llm_provider="glm",
        llm_api_key="sk-test",
        llm_base_url="https://example.test/v1",
        llm_model="glm-4.5",
        system_prompt="Keep dialogue in one speaker cue.",
        user_prompt_template="Translate and segment: {subtitles_json}",
        youtube_cookies_from_browser="chrome:Default",
        youtube_cookies_file="D:/cookies.txt",
        youtube_player_client="mweb",
        youtube_po_token="token-test",
        youtube_proxy="http://127.0.0.1:7897",
        local_video_path="D:/video/story.mp4",
        local_subtitle_path="D:/video/story.en.srt",
    )

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.translation_engine == "glm"
    assert loaded.llm_provider == "glm"
    assert loaded.llm_api_key == "sk-test"
    assert loaded.llm_base_url == "https://example.test/v1"
    assert loaded.llm_model == "glm-4.5"
    assert loaded.system_prompt == "Keep dialogue in one speaker cue."
    assert "{subtitles_json}" in loaded.user_prompt_template
    assert loaded.youtube_cookies_from_browser == "chrome:Default"
    assert loaded.youtube_cookies_file == "D:/cookies.txt"
    assert loaded.youtube_player_client == "mweb"
    assert loaded.youtube_po_token == "token-test"
    assert loaded.youtube_proxy == "http://127.0.0.1:7897"
    assert loaded.local_video_path == "D:/video/story.mp4"
    assert loaded.local_subtitle_path == "D:/video/story.en.srt"


def test_settings_load_accepts_utf8_bom(tmp_path):
    path = tmp_path / "story-settings.json"
    path.write_text('{"translation_engine": "google"}', encoding="utf-8-sig")

    loaded = load_settings(path)

    assert loaded.translation_engine == "google"
