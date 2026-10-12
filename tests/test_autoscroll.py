"""Simulated auto-scroll: random 400-line page, 30-line viewport.
Runs the real Stitcher + AutoScroller loop (no OCR) and checks full, gap-free coverage."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from texport.autoscroll import AutoScroller  # noqa: E402
from texport.ocr import Line  # noqa: E402
from texport.stitch import Stitcher  # noqa: E402

W = ("gate state trajectory encoder ledger manifest invariant bounded condition exit script output "
     "token symbol context intent marker capture region frame overlap paragraph register handoff").split()


def run(page_len=400, view=30, lines_per_notch=3, notches=2):
    rnd = random.Random(page_len)
    page = [" ".join(rnd.choice(W) for _ in range(rnd.randint(6, 12))) + f" {rnd.randint(100, 99999)}"
            for _ in range(page_len)]
    top, st, ctl = 0, Stitcher(), AutoScroller(steps=notches)

    def frame():
        return [Line(text=t, top=k * 20 + 5, bottom=k * 20 + 18, left=0, conf=95)
                for k, t in enumerate(page[top:top + view])]
    st.feed(frame())
    d, prev, n = ctl.first(), 0, 0
    while d.action == "scroll" and n < 1000:
        n += 1
        top = min(page_len - view, top + d.steps * lines_per_notch)
        if top == prev:
            d = ctl.after_frame(None)
        else:
            f = frame()
            r = st.feed(f)
            d = ctl.after_frame(r.status, r.added, len(f))
        prev = top
    return [l.text for l in st.lines] == page, ctl.gaps, d.reason, n


for lpn in (1, 3, 5, 7):     # Claude desktop is ~3 lines per notch
    ok, gaps, reason, frames = run(lines_per_notch=lpn)
    print(f"{lpn} lines/notch: complete={ok} gaps={gaps} stop={reason} frames={frames}")
    assert ok and gaps == 0 and reason == "end"

c = AutoScroller()
c.first()
assert c.after_frame(None).action == "scroll"
assert c.after_frame(None).reason == "no_scroll"
c = AutoScroller(); c.first(); c.after_frame("new", 10, 20); c.backed_off()
for _ in range(6):                       # re-reading captured text after back-ups is never "the end"
    assert c.after_frame("behind", 0, 20).action == "scroll"
assert c.after_frame(None).action == "scroll"
d = c.after_frame(None); assert d.reason == "confirm" and d.steps >= 5
assert c.after_frame(None).reason == "end"
c2 = AutoScroller(); c2.first(); c2.after_frame("new", 10, 20); c2.after_frame(None); c2.after_frame(None)
assert c2.after_frame("new", 8, 20).action == "scroll" and not c2.confirming   # confirm scroll found more text
# at the bottom the pointer/hover keeps changing pixels: re-reads with no back-up end the run
c3 = AutoScroller(); c3.first(); c3.after_frame("new", 10, 20)
ds = [c3.after_frame("same", 0, 19) for _ in range(8)]
assert any(d.reason == "confirm" for d in ds) and ds[-1].action == "stop" and ds[-1].reason == "end", ds
from texport.autoscroll import same_view  # noqa: E402
base = bytes(range(256)) * 20
hover = bytearray(base); hover[100:150] = bytes(50)
shifted = base[120:] + base[:120]
assert same_view(base, bytes(hover)) and not same_view(base, shifted)
print("all autoscroll checks passed")


def run_with_backup(page_len=400, view=30, lines_per_notch=16, notches=2):
    """Mirrors the app: classify first; on 'gap' back up 1 notch (max 2 tries) and use 1-notch steps."""
    rnd = random.Random(7)
    page = [" ".join(rnd.choice(W) for _ in range(rnd.randint(6, 12))) + f" {rnd.randint(100, 99999)}"
            for _ in range(page_len)]
    top, st, ctl, retries, n, prev = 0, Stitcher(), AutoScroller(steps=notches), 0, 0, 0

    def frame():
        return [Line(text=t, top=k * 20 + 5, bottom=k * 20 + 18, left=0, conf=95)
                for k, t in enumerate(page[top:top + view])]
    st.feed(frame())
    d = ctl.first()
    move = d.steps
    while n < 2000:
        n += 1
        top = max(0, min(page_len - view, top + move * lines_per_notch))
        if top == prev and move > 0:
            d = ctl.after_frame(None)
        else:
            f = frame()
            status, _ = st.classify(f)
            if status == "gap" and retries < 2:
                retries += 1
                ctl.steps = 1
                move, prev = -1, top
                continue
            retries = 0
            r = st.feed(f)
            d = ctl.after_frame(r.status, r.added, len(f))
        prev = top
        if d.action != "scroll":
            break
        move = d.steps
    gaps = sum(1 for l in st.lines if l.meta.get("gap"))
    return [l.text for l in st.lines if not l.meta.get("gap")] == page, gaps, d.reason


ok, gaps, reason = run_with_backup()
print(f"oversized step (2 notches = 32 lines > 30-line view) with back-up: complete={ok} gaps={gaps} stop={reason}")
assert ok and gaps == 0 and reason == "end"
print("back-up checks passed")
