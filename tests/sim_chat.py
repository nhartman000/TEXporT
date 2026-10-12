"""End-to-end test of OCR + stitching on a simulated Claude-style chat.

Renders a long chat page in headless Chromium, takes viewport screenshots at
successive scroll offsets (like a user scrolling), runs them through the real
OCR + stitcher, and compares the result to the page's ground-truth text.
Run with: python3 tests/sim_chat.py  (needs playwright + tesseract)
"""
import io
import random
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from texport.ocr import ocr_png, clean_frame_lines   # noqa: E402
from texport.stitch import Stitcher, norm            # noqa: E402
from texport import export                           # noqa: E402

import os
SCALE = int(os.environ.get('SCALE', '2'))
random.seed(7)
WORDS = ("gate state trajectory encoder ledger qson manifest orchestration invariant "
         "deterministic bounded condition exit predicate python script output token "
         "symbol context intent extraction marker capture region frame overlap line "
         "paragraph register handoff model review commit test pipeline schema").split()


def sentence():
    n = random.randint(8, 18)
    s = " ".join(random.choice(WORDS) for _ in range(n))
    return s[0].upper() + s[1:] + random.choice([".", ".", "?", "!"]) + f" ({random.randint(10, 9999)})"


def build_page():
    msgs = []
    for t in range(8):
        msgs.append(("user", " ".join(sentence() for _ in range(random.randint(1, 2)))))
        paras = [" ".join(sentence() for _ in range(random.randint(2, 4))) for _ in range(random.randint(2, 4))]
        code = None
        if t % 3 == 1:
            code = "\n".join([f"def step_{t}(state):", f"    if state['n'] > {t * 7}:",
                              "        return run_gate(state)", "    return None"])
        msgs.append(("assistant", paras, code))
    html_parts = ["""<html><head><style>
      body{margin:0;background:#faf9f5;font-family:'DejaVu Sans',sans-serif;color:#1f1e1d;}
      .side{position:fixed;left:0;top:0;bottom:0;width:220px;background:#f0eee6;}
      .col{margin-left:320px;width:680px;padding:40px 0 400px;font-size:16px;line-height:1.6;}
      .u{background:#e8e6dc;border-radius:12px;padding:10px 16px;margin:24px 0;}
      .a p{margin:0 0 14px;} pre{background:#2b2a27;color:#eee;padding:12px;border-radius:8px;font-size:14px;}
    </style></head><body><div class="side"></div><div class="col">"""]
    truth = []
    for m in msgs:
        if m[0] == "user":
            html_parts.append(f'<div class="u">{m[1]}</div>')
            truth.append(m[1])
        else:
            html_parts.append('<div class="a">' + "".join(f"<p>{p}</p>" for p in m[1]) + "</div>")
            truth.extend(m[1])
            if m[2]:
                html_parts.append(f"<pre>{m[2]}</pre>")
                truth.append(m[2])
    html_parts.append("</div></body></html>")
    return "".join(html_parts), truth


def run(step_px: int, label: str, jitter_back: bool = False):
    from playwright.sync_api import sync_playwright
    page_html, truth = build_page()
    st = Stitcher()
    stats = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        pg.set_content(page_html)
        total = pg.evaluate("document.body.scrollHeight")
        y = 0
        clip = {"x": 300, "y": 60, "width": 720, "height": 700}   # the "region"
        while True:
            pg.evaluate(f"window.scrollTo(0,{y})")
            png = pg.screenshot(clip=clip)
            # upscale x2 like the app does at 1x DPI
            from PIL import Image
            im = Image.open(io.BytesIO(png))
            if SCALE > 1:
                im = im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "PNG")
            lines, h = ocr_png(buf.getvalue())
            r = st.feed(clean_frame_lines(lines, h))
            stats[r.status] = stats.get(r.status, 0) + 1
            if y >= total:
                break
            if jitter_back and random.random() < 0.2:
                y = max(0, y - step_px // 2)
            else:
                y += step_px
        b.close()
    got = "\n".join(l.text for l in st.lines)
    want = "\n".join(truth)
    gn, wn = norm(got), norm(want)
    ratio = SequenceMatcher(None, gn, wn, autojunk=False).ratio()
    print(f"[{label}] step={step_px}px frames={sum(stats.values())} {stats} lines={len(st.lines)} "
          f"similarity_to_truth={ratio:.3f}")
    return st, ratio, stats


if __name__ == "__main__":
    st, r1, _ = run(250, "slow scroll")
    _, r2, _ = run(450, "medium scroll")
    _, r3, _ = run(250, "scroll with back-jitter", jitter_back=True)
    _, r4, s4 = run(900, "too fast (expect gaps)")
    assert r1 > 0.95 and r2 > 0.95 and r3 > 0.95, "stitch quality too low"
    assert s4.get("gap", 0) > 0, "fast scroll should report gaps"
    # export smoke test with markers
    lines = [{"text": l.text, "par_break": l.par_break, "gap": bool(l.meta.get("gap"))} for l in st.lines]
    sess = {"title": "Sim chat", "source": "test", "captured_at": "2026-10-06T10:00:00",
            "lines": lines, "markers": [{"start": 3, "end": 5, "note": "key"}, {"start": 10, "end": 10, "note": ""}]}
    md = export.render_markdown([sess, sess], "Test export")
    html = export.render_html([sess], "Test export")
    assert md.count("[[MARK M1 START]]") == 3 and "[[MARK M4 END]]" in md  # register x2 + inline
    Path("/tmp/claude-0/sim_export.md").write_text(md)
    Path("/tmp/claude-0/sim_export.html").write_text(html)
    print("export ok; all checks passed")
