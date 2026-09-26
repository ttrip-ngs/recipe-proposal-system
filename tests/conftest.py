"""pytest 共通設定."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("USE_FAKE_LLM", "true")
