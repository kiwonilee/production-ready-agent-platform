#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Self-contained Agent Skills (`agentskills.io`) pure-stdlib CLI runner — Production-Readiness Checklist v3.

Works both inside the repository and when `skills/agent-production-readiness` is imported standalone
into Antigravity (`~/.gemini/skills/agent-production-readiness`), Gemini CLI, Claude Code, or Cursor.

Usage: `python3 scripts/prod_ready.py {scan,diff,apply,report} <project_dir> [--target ...] [--items CR-F1,C4.4|ALL]`
(v3 item ids such as CR-F1 / AR-S3 / GKE-O1; legacy C* ids are accepted as aliases.)
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from prod_ready.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
