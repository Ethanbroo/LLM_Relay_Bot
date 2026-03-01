"""Real-Time Preview & Development Environment for the video pipeline.

Browser-based UI for previewing timelines, scrubbing through frames,
adjusting parameters live, and seeing the effect chain in real-time.

Architecture:
- FastAPI server with WebSocket frame streaming
- HTML5 Canvas renders frames with zero extra dependencies
- LRU cache prevents re-rendering when scrubbing
- No npm/node required — pure Python + vanilla JS

Usage:
    python -m connectors.video_pipeline.preview.server --timeline path/to/timeline.json

Opens http://localhost:8765 with full timeline playback and scrubbing.
"""

__version__ = "0.1.0"
