from videotrans.story_pipeline.server import TaskState, app


def test_story_pipeline_web_app_exposes_core_routes():
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/api/settings" in paths
    assert "/api/settings/test-llm" in paths
    assert "/api/settings/test-qwen" in paths
    assert "/api/run" in paths
    assert "/api/tasks/{task_id}" in paths
    assert "/api/tasks/{task_id}/pause" in paths
    assert "/api/tasks/{task_id}/resume" in paths
    assert "/api/tasks/{task_id}/cues" in paths
    assert "/api/tasks/{task_id}/tts" in paths
    assert "/api/tasks/{task_id}/tts/{cue_id}" in paths


def test_task_state_supports_pause_and_resume(tmp_path):
    state = TaskState("task-1", tmp_path)
    state.status = "running"

    state.request_pause()

    assert state.pause_requested is True
    assert state.status == "pausing"

    state.resume()

    assert state.pause_requested is False
    assert state.status == "running"
