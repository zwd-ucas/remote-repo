#!/bin/bash
# Story Dubbing.app executable. Points the app at its bundled runtime + node, then starts
# the desktop launcher (which serves the UI in a native window).
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
export STORY_DUBBING_NODE="$RES/node/bin/node"
exec "$RES/runtime/bin/python3" -m videotrans.story_pipeline.desktop
