"""killswitch — a dead-simple, out-of-band trading halt.

A file-based kill switch is the most reliable stop you can give an autonomous
agent: it needs no running process, no network, and no coordination with the
agent's own state. Anyone (a human, a monitor, a cron job) can create the halt
file and the very next `is_engaged()` check refuses all new entries.

Sells / exits should ALWAYS be allowed to flow even when engaged — halting ents,
not exits, is the safe failure mode. This module only answers "may I open a new
position?"; keep your exit path unconditional.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class KillSwitch:
    def __init__(self, halt_file: str | Path):
        self.halt_file = Path(halt_file)

    def is_engaged(self) -> bool:
        """True if new entries are halted (the halt file exists)."""
        return self.halt_file.exists()

    def engage(self, reason: str = "") -> bool:
        """Create the halt file. Returns False on IO error rather than raising."""
        try:
            self.halt_file.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat()
            self.halt_file.write_text(f"{stamp} {reason}".strip() + "\n", encoding="utf-8")
            return True
        except OSError:
            return False

    def release(self) -> bool:
        """Remove the halt file (resume entries). Idempotent."""
        try:
            self.halt_file.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def reason(self) -> Optional[str]:
        """The recorded halt reason, or None if not engaged / unreadable."""
        try:
            return self.halt_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
