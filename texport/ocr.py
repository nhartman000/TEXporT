"""OCR one captured frame into ordered text lines using the tesseract CLI.

No Python imaging dependency: the caller hands over PNG bytes.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class Line:
    text: str
    top: int
    bottom: int
    left: int
    conf: float
    right: int = 0
    par_break: bool = False          # visual gap before this line => new paragraph
    meta: dict = field(default_factory=dict)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


class OcrError(RuntimeError):
    pass


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_png(png: bytes, *, lang: str = "eng", psm: int = 4, dpi: int = 144,
            timeout: float = 30.0) -> tuple[list[Line], int]:
    """Return (lines, image_height). Lines are sorted top-to-bottom."""
    if not tesseract_available():
        raise OcrError("tesseract is not installed (sudo apt install tesseract-ocr)")
    try:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", lang, "--psm", str(psm),
             "--dpi", str(dpi), "-c", "preserve_interword_spaces=1", "tsv"],
            input=png, capture_output=True, timeout=timeout, check=False,
            # single-threaded tesseract is ~4x faster on small images
            env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    except subprocess.TimeoutExpired as e:
        raise OcrError("tesseract timed out") from e
    if proc.returncode != 0:
        raise OcrError(proc.stderr.decode(errors="replace").strip()[:300])
    return parse_tsv(proc.stdout.decode("utf-8", errors="replace"))


def parse_tsv(tsv: str) -> tuple[list[Line], int]:
    rows = tsv.splitlines()
    if not rows:
        return [], 0
    img_h = 0
    line_boxes: dict[tuple, list] = {}
    words: dict[tuple, list] = {}
    for row in rows[1:]:
        parts = row.split("\t")
        if len(parts) < 12:
            continue
        level, _page, block, par, line, _word = (int(x) for x in parts[:6])
        left, top, width, height = (int(x) for x in parts[6:10])
        conf = float(parts[10])
        text = parts[11]
        key = (block, par, line)
        if level == 1:
            img_h = top + height
        elif level == 4:
            line_boxes[key] = [left, top, left + width, top + height]
        elif level == 5 and text.strip():
            words.setdefault(key, []).append((left, text, conf))

    lines: list[Line] = []
    for key, ws in words.items():
        box = line_boxes.get(key)
        if not box:
            continue
        ws.sort()
        text = " ".join(w[1] for w in ws)
        text = re.sub(r"[ \t]+", " ", text).strip()
        confs = [w[2] for w in ws if w[2] >= 0]
        conf = sum(confs) / len(confs) if confs else 0.0
        lines.append(Line(text=text, top=box[1], bottom=box[3], left=box[0], conf=conf, right=box[2]))
    lines.sort(key=lambda l: (l.top, l.left))
    return lines, img_h


def clean_frame_lines(lines: list[Line], img_h: int, *, edge: int = 3,
                      min_conf: float = 35.0) -> list[Line]:
    """Drop lines cut by the frame edge and OCR noise; mark paragraph breaks."""
    out: list[Line] = []
    for l in lines:
        if l.top <= edge or (img_h and l.bottom >= img_h - edge):
            continue  # partially visible line; it will be read whole in another frame
        alnum = sum(c.isalnum() for c in l.text)
        if alnum < 2 or l.conf < min_conf:
            continue
        out.append(l)
    if not out:
        return out
    heights = sorted(l.height for l in out)
    med = heights[len(heights) // 2]
    prev = None
    for l in out:
        if prev is None:
            l.par_break = False
        else:
            gap = l.top - prev.bottom
            l.par_break = gap > 0.75 * med
        prev = l
    return out
