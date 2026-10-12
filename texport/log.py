"""Tiny append-only debug log: ~/.local/share/texport/texport.log (also echoed to stderr)."""
import sys
import time
from pathlib import Path

LOG = Path.home() / ".local" / "share" / "texport" / "texport.log"


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print("[texport]", line, file=sys.stderr)
