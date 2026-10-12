"""Auto-scroll pacing (pure logic, no Qt).

Starts at 2 mouse-wheel notches per step and adjusts so each new screen is
about half new text. A missed overlap slows it down by one notch; it speeds
back up once screens line up again.

End detection is strict: only a page that did not move at all after a scroll
(pixel-identical) counts toward the end — re-reading text already captured
(after a back-up) never does. Two still scrolls in a row trigger one bigger
confirmation scroll; only if that also doesn't move the page is it the end.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TARGET_LOW, TARGET_HIGH = 0.35, 0.65     # fraction of a screen that should be new text
CONFIRM_STEPS = 6
REREAD_LIMIT = 4         # scrolls in a row that only re-show captured text (no back-up involved)


def same_view(a: bytes | None, b: bytes | None, tol: float = 0.02) -> bool:
    """Two grayscale thumbnails show the same page position. Small changes — the pointer,
    hover buttons, a blinking caret — don't count as scrolling; a scroll shifts every text row."""
    if a is None or b is None or len(a) != len(b):
        return False
    if a == b:
        return True
    diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 40)
    return diff <= tol * len(a)


@dataclass
class Decision:
    action: str          # "scroll" | "stop"
    steps: int = 0
    reason: str = ""     # "end" | "no_scroll" | "confirm"


@dataclass
class AutoScroller:
    steps: int = 2
    min_steps: int = 1
    max_steps: int = 12
    progressed: bool = False
    still: int = 0               # consecutive scrolls after which the page did not move at all
    confirming: bool = False
    gaps: int = 0
    rereads: int = 0             # consecutive screens with nothing new, not caused by a back-up
    backed_up: bool = False
    history: list = field(default_factory=list)

    def first(self) -> Decision:
        return Decision("scroll", self.steps)

    def backed_off(self):
        """Called when a screen didn't overlap and the app scrolled back to re-read."""
        self.steps = max(self.min_steps, self.steps - 1)
        self.backed_up = True
        self.rereads = 0

    def after_frame(self, status: str | None, added: int = 0, frame_lines: int = 0) -> Decision:
        """status: the stitcher's FeedResult.status, or None if the page did not move after the scroll."""
        self.history.append((status, added, frame_lines, self.steps))
        if status in ("same", "behind") and not self.backed_up:
            self.rereads += 1
            if self.rereads >= REREAD_LIMIT:     # the view keeps changing but the text doesn't: treat as still
                status = None
        elif status is not None:
            self.rereads = 0
        if status is None:
            self.still += 1
            if self.confirming:
                return Decision("stop", reason="end" if self.progressed else "no_scroll")
            if self.still >= 2:
                if not self.progressed:
                    return Decision("stop", reason="no_scroll")
                self.confirming = True
                return Decision("scroll", CONFIRM_STEPS, reason="confirm")
            return Decision("scroll", self.steps)
        # the page moved: whatever we read, we are not at the end
        self.still = 0
        self.confirming = False
        if status in ("same", "behind", "empty"):
            return Decision("scroll", self.steps)            # re-read / no text: keep going
        self.progressed = True
        self.backed_up = False
        if status == "gap":
            self.gaps += 1
            self.steps = max(self.min_steps, self.steps - 1)
            return Decision("scroll", self.steps)
        frac = added / max(1, frame_lines)
        if frac > TARGET_HIGH and self.steps > self.min_steps:
            self.steps -= 1
        elif frac < TARGET_LOW and self.steps < self.max_steps:
            self.steps += 1
        return Decision("scroll", self.steps)
