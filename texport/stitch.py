"""Join OCR'd frames from a scrolling capture into one transcript.

Frames are aligned by matching lines of *text* (not pixels): the top of a new
frame should overlap the bottom of what has been captured so far.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .ocr import Line

GAP_TEXT = "[… gap: scrolled faster than capture — scroll back up a little to fill it …]"


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def similar(a: str, b: str) -> bool:
    if a == b:
        return bool(a)
    if min(len(a), len(b)) < 6:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.82


@dataclass
class FeedResult:
    added: int          # lines appended
    status: str         # "new" | "same" | "behind" | "gap" | "empty"


class Stitcher:
    def __init__(self, window: int = 80):
        self.lines: list[Line] = []
        self._norm: list[str] = []
        self.window = window

    def _append(self, line: Line) -> None:
        self.lines.append(line)
        self._norm.append(norm(line.text))

    def _alignment(self, tail: list[str], frame: list[str]) -> tuple[int, int] | None:
        """Best placement of frame[0] at tail[s]. Returns (s, overlap_len) or None."""
        best = None
        best_key = None
        for s in range(len(tail)):
            overlap = min(len(tail) - s, len(frame))
            matches = 0
            chars = 0
            for i in range(overlap):
                if similar(tail[s + i], frame[i]):
                    matches += 1
                    chars += len(frame[i])
            if overlap == 1:
                ok = matches == 1 and chars >= 20
            else:
                ok = matches >= max(2, 0.6 * overlap) and chars >= 12
            if not ok:
                continue
            key = (matches, -s)   # most matches; tie -> earliest start (longest overlap)
            if best_key is None or key > best_key:
                best_key, best = key, (s, overlap)
        return best

    def classify(self, frame_lines: list[Line]) -> tuple[str, int]:
        """What feed() would do with this frame, without changing anything.
        Returns (status, number_of_overlapping_frame_lines)."""
        if not frame_lines:
            return "empty", 0
        f = [norm(l.text) for l in frame_lines]
        if not self.lines:
            return "new", 0
        tail_start = max(0, len(self._norm) - max(self.window, 2 * len(f)))
        tail = self._norm[tail_start:]
        al = self._alignment(tail, f)
        if al is not None:
            s, overlap = al
            if s + overlap < len(tail):
                return "behind", overlap
            return ("new" if overlap < len(frame_lines) else "same"), overlap
        found = sum(1 for x in f if x and any(similar(x, y) for y in self._norm[:tail_start]))
        if found >= max(1, 0.6 * len(f)):
            return "behind", 0
        return "gap", 0

    def feed(self, frame_lines: list[Line]) -> FeedResult:
        status, overlap = self.classify(frame_lines)
        if not self.lines and frame_lines:
            for l in frame_lines:
                self._append(l)
            return FeedResult(len(frame_lines), "new")
        if status in ("empty", "behind", "same"):
            return FeedResult(0, status)
        if status == "new":
            new = frame_lines[overlap:]
            for l in new:
                self._append(l)
            return FeedResult(len(new), "new")
        gap = Line(text=GAP_TEXT, top=0, bottom=1, left=0, conf=100.0, par_break=True,
                   meta={"gap": True})
        self._append(gap)
        frame_lines[0].par_break = True
        for l in frame_lines:
            self._append(l)
        return FeedResult(len(frame_lines), "gap")
