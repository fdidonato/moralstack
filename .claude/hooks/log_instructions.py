#!/usr/bin/env python3
"""InstructionsLoaded: logga quali file istruzione vengono caricati. Fail-open."""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    log = Path(root) / ".claude" / ".instructions-loaded.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()} {json.dumps(data)}\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
