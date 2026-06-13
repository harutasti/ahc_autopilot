#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / ".codex" / "skills" / "ahc-ai-lab"
    dest_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"
    dest = dest_root / "ahc-ai-lab"
    if not src.exists():
        raise SystemExit(f"missing skill source: {src}")
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"synced {src} -> {dest}")
    print("Restart Codex to pick up new skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

