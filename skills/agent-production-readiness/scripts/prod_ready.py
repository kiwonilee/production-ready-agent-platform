#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi>=0.110.0",
#   "uvicorn>=0.27.0",
#   "pydantic>=2.0.0",
# ]
# ///
"""Self-contained Agent Skills (`agentskills.io`) CLI & Workbench Runner.

Works both inside the repository (`src/prod_ready`) AND when `skills/agent-production-readiness`
is imported standalone into Antigravity (`~/.gemini/config/skills/agent-production-readiness`),
Gemini CLI, Claude Code, or Cursor (`scripts/prod_ready/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from prod_ready.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
