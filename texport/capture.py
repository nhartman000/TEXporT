"""Frame sources.

X11Source       – plain X11 session: grab the screen with Qt.
HyprlandSource  – Hyprland (Omarchy and other wlroots-style setups): frames from `grim`,
                  pointer via `hyprctl dispatch movecursor`, wheel via `ydotool`.
PortalSource  – Wayland (Ubuntu default): xdg-desktop-portal ScreenCast + PipeWire,
                the same "share your screen" permission video calls use. You can
                share just the Claude window; the stream follows it even when it
                is partly covered. The permission is remembered (restore token).

Both return frames as QImage via grab(); neither ever sends input to other apps.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
import time

from .log import log
from .qt import QGuiApplication, QImage


def is_hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None


def session_is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


class SourceError(RuntimeError):
    pass


class X11Source:
    name = "X11 screen"

    def __init__(self, screen_index: int = 0):
        self.screen_index = screen_index

    def start(self, on_ready, on_error):
        on_ready()

    def grab(self) -> QImage | None:
        screens = QGuiApplication.screens()
        scr = screens[min(self.screen_index, len(screens) - 1)]
        pm = scr.grabWindow(0)   # must run on the GUI thread
        return pm.toImage() if not pm.isNull() else None

    def stop(self):
        pass

    restore_token = None
    source_type = 1                      # whole screen
    can_scroll = shutil.which("xdotool") is not None

    def point_at(self, x: float, y: float):
        """Put the pointer at frame coordinates (X11 screen grab: frame == screen)."""
        g = QGuiApplication.screens()[min(self.screen_index, len(QGuiApplication.screens()) - 1)].geometry()
        subprocess.run(["xdotool", "mousemove", str(int(g.x() + x)), str(int(g.y() + y))], check=False, timeout=5)

    def scroll(self, steps: int):
        """Mouse-wheel at the current pointer position (X11 only, via xdotool)."""
        if not steps:
            return
        button = "5" if steps > 0 else "4"
        subprocess.run(["xdotool", "click", "--repeat", str(abs(steps)), "--delay", "25", button],
                       check=False, timeout=10)


class HyprlandSource:
    """Hyprland: grab the focused monitor with grim on a worker thread (latest frame cached).
    Hyprland's portal has no remote-interaction support, so auto-scroll moves the pointer with
    hyprctl and turns the wheel with ydotool (needs the ydotool service running)."""

    name = "Hyprland screen (grim)"
    restore_token = None
    source_type = 1                       # whole monitor
    scroll_method = "discrete"
    INTERVAL = 0.15

    def __init__(self):
        self._frame = None
        self._frame_lock = threading.Lock()
        self.frame_seq = 0
        self._stop = False
        self.monitor = None

    @property
    def can_scroll(self) -> bool:
        return shutil.which("ydotool") is not None

    def _monitor(self):
        import json
        out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=5).stdout
        mons = json.loads(out or "[]")
        if not mons:
            raise SourceError("hyprctl reported no monitors.")
        return next((m for m in mons if m.get("focused")), mons[0])

    def start(self, on_ready, on_error):
        if shutil.which("grim") is None:
            on_error("grim is not installed (sudo pacman -S grim).")
            return
        try:
            self.monitor = self._monitor()
        except Exception as e:  # noqa: BLE001
            on_error(f"Couldn't read the monitor layout from hyprctl: {e}")
            return
        log("hyprland source: monitor", self.monitor.get("name"), self.monitor.get("width"), self.monitor.get("height"),
            "scale", self.monitor.get("scale"), "ydotool", self.can_scroll)

        def run():
            first = True
            while not self._stop:
                t0 = time.time()
                try:
                    p = subprocess.run(["grim", "-o", self.monitor["name"], "-t", "ppm", "-"],
                                       capture_output=True, timeout=10)
                    img = QImage.fromData(p.stdout, "PPM") if p.returncode == 0 else None
                    if img is not None and not img.isNull():
                        with self._frame_lock:
                            self._frame = img
                            self.frame_seq += 1
                        if first:
                            first = False
                            on_ready()
                    elif first:
                        first = False
                        on_error("grim couldn't capture the screen: " + p.stderr.decode(errors="replace")[:200])
                        return
                except Exception as e:  # noqa: BLE001
                    if first:
                        on_error(f"grim failed: {e}")
                        return
                time.sleep(max(0.0, self.INTERVAL - (time.time() - t0)))
        threading.Thread(target=run, daemon=True).start()

    def grab(self) -> QImage | None:
        with self._frame_lock:
            return self._frame

    def stop(self):
        self._stop = True

    def point_at(self, x: float, y: float):
        """Frame pixels -> Hyprland global (logical) coordinates."""
        m = self.monitor or {}
        sc = float(m.get("scale") or 1.0)
        gx, gy = int(m.get("x", 0) + x / sc), int(m.get("y", 0) + y / sc)
        subprocess.run(["hyprctl", "dispatch", "movecursor", str(gx), str(gy)], capture_output=True, timeout=5)

    def scroll(self, steps: int, pixels: float = 0.0):
        """Wheel notches at the pointer (positive = down) via ydotool."""
        if not steps:
            return
        r = subprocess.run(["ydotool", "mousemove", "--wheel", "-x", "0", "-y", str(-steps)],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise SourceError("ydotool couldn't scroll (is the ydotool service running? "
                              "systemctl --user enable --now ydotool): " + (r.stderr or "").strip()[:200])


class PortalSource:
    """ScreenCast portal -> PipeWire -> GStreamer appsink (latest frame cached)."""

    name = "Screen-share portal"
    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.ScreenCast"
    RD_IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, restore_token: str | None = None, remote: bool = False):
        """remote=True opens a RemoteDesktop session (screen share + permission to send
        mouse-wheel events), so auto-scroll can work. Ubuntu asks the user to allow it."""
        self.restore_token = restore_token
        self.remote = remote
        if remote:
            self.name = "Screen-share + remote scroll portal"
        self._frame: QImage | None = None
        self._frame_lock = threading.Lock()
        self.frame_seq = 0
        self._pipeline = None
        self._session = None
        self._bus = None
        self._thread = None
        self._cancel = False
        self.source_type = None

    # ---- public -------------------------------------------------------
    def start(self, on_ready, on_error):
        """Runs the portal handshake on a worker thread (the user may be looking
        at the permission dialog). Callbacks are invoked from that thread."""
        def run():
            try:
                self._handshake()
                on_ready()
            except Exception as e:  # noqa: BLE001
                on_error(str(e))
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def grab(self) -> QImage | None:
        with self._frame_lock:
            return self._frame

    @property
    def can_scroll(self) -> bool:
        return self.remote and self._session is not None

    scroll_method = "discrete"     # "discrete" (wheel notches) or "smooth" (pixel deltas)

    def point_at(self, x: float, y: float):
        """Move the pointer to (x, y) in the shared stream's coordinates, so scroll
        events land on the captured text without the user clicking anything."""
        if not self.can_scroll:
            return
        from gi.repository import Gio, GLib
        try:
            self._bus.call_sync(self.BUS, self.PATH, self.RD_IFACE, "NotifyPointerMotionAbsolute",
                                GLib.Variant("(oa{sv}udd)", (self._session, {}, int(self._node), float(x), float(y))),
                                None, Gio.DBusCallFlags.NONE, 5000, None)
        except Exception as e:  # noqa: BLE001
            log("point_at", x, y, "FAILED:", e)
            raise

    def scroll(self, steps: int, pixels: float = 0.0):
        """Scroll at the current pointer position (positive = down)."""
        if not steps or not self.can_scroll:
            log("scroll skipped", steps, "can_scroll=", self.can_scroll)
            return
        from gi.repository import Gio, GLib
        if self.scroll_method == "discrete":
            args = GLib.Variant("(oa{sv}ui)", (self._session, {}, 0, int(steps)))
            method = "NotifyPointerAxisDiscrete"
        else:
            dy = float(pixels or 120.0 * steps) * (1 if steps > 0 else -1)
            args = GLib.Variant("(oa{sv}dd)", (self._session, {"finish": GLib.Variant("b", True)}, 0.0, dy))
            method = "NotifyPointerAxis"
        try:
            self._bus.call_sync(self.BUS, self.PATH, self.RD_IFACE, method, args,
                                None, Gio.DBusCallFlags.NONE, 5000, None)
            log("scroll", method, steps, pixels or "", "ok")
        except Exception as e:  # noqa: BLE001
            log("scroll", method, steps, "FAILED:", e)
            raise

    def stop(self):
        self._cancel = True
        try:
            if self._pipeline is not None:
                from gi.repository import Gst
                self._pipeline.set_state(Gst.State.NULL)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._bus is not None and self._session:
                from gi.repository import Gio
                self._bus.call_sync(self.BUS, self._session, "org.freedesktop.portal.Session",
                                    "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:  # noqa: BLE001
            pass
        self._pipeline = None
        self._session = None

    # ---- portal handshake --------------------------------------------
    def _handshake(self):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst
        except Exception as e:  # noqa: BLE001
            raise SourceError(
                "GStreamer Python bindings missing. Run: sudo apt install python3-gi "
                "gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire") from e

        self._GLib, self._Gio = GLib, Gio
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self._ctx = ctx
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._sender = self._bus.get_unique_name()[1:].replace(".", "_")
            V = GLib.Variant

            version = self._prop("version", default=1)
            cursor_modes = self._prop("AvailableCursorModes", default=0)
            main_iface = self.RD_IFACE if self.remote else self.IFACE
            rd_version = self._prop("version", default=1, iface=self.RD_IFACE) if self.remote else 0

            res = self._request("CreateSession", "(a{sv})", lambda tok: (
                {"handle_token": V("s", tok), "session_handle_token": V("s", "cc" + secrets.token_hex(6))},),
                iface=main_iface)
            self._session = res["session_handle"]

            if self.remote:
                def device_opts(tok):
                    o = {"handle_token": V("s", tok), "types": V("u", 2)}      # pointer only
                    if rd_version >= 2:
                        o["persist_mode"] = V("u", 2)
                        if self.restore_token:
                            o["restore_token"] = V("s", self.restore_token)
                    return (self._session, o)
                self._request("SelectDevices", "(oa{sv})", device_opts, iface=self.RD_IFACE)

            def select_opts(tok):
                o = {"handle_token": V("s", tok), "types": V("u", 1 | 2), "multiple": V("b", False)}
                if cursor_modes & 1:
                    o["cursor_mode"] = V("u", 1)          # hidden cursor
                if version >= 4 and not self.remote:      # remote sessions persist via SelectDevices
                    o["persist_mode"] = V("u", 2)         # remember until revoked
                    if self.restore_token:
                        o["restore_token"] = V("s", self.restore_token)
                return (self._session, o)
            self._request("SelectSources", "(oa{sv})", select_opts)

            res = self._request("Start", "(osa{sv})", lambda tok: (
                self._session, "", {"handle_token": V("s", tok)}), timeout=300, iface=main_iface)
            log("portal start: remote=", self.remote, "devices=", res.get("devices"),
                "streams=", [(st[0], dict(st[1]).get("source_type"), dict(st[1]).get("size")) for st in res.get("streams") or []],
                "screencast_v=", version, "remotedesktop_v=", rd_version)
            if self.remote and not (int(res.get("devices", 0)) & 2):
                self.restore_token = None       # never remember a grant without scrolling
                raise SourceError("Ubuntu shared the screen but not scrolling. Click Capture again and, in "
                                  "the sharing dialog, turn ON the \"Allow Remote Interaction\" switch "
                                  "before clicking Share.")
            streams = res.get("streams") or []
            if not streams:
                raise SourceError("No stream was shared.")
            node_id = streams[0][0]
            self._node = node_id
            self.source_type = dict(streams[0][1]).get("source_type")   # 1 monitor, 2 window
            if res.get("restore_token"):
                self.restore_token = res["restore_token"]

            ret, fds = self._bus.call_with_unix_fd_list_sync(
                self.BUS, self.PATH, self.IFACE, "OpenPipeWireRemote",
                V("(oa{sv})", (self._session, {})), GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE, -1, None, None)
            fd = fds.get(ret.unpack()[0])
        finally:
            ctx.pop_thread_default()

        Gst.init(None)
        sink_desc = ("videoconvert ! video/x-raw,format=BGRx ! "
                     "appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true")
        pipeline = None
        for src in (f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 resend-last=true",
                    f"pipewiresrc fd={fd} path={node_id} do-timestamp=true"):
            try:
                pipeline = Gst.parse_launch(f"{src} ! {sink_desc}")
                break
            except Exception:  # noqa: BLE001  (older pipewiresrc lacks keepalive/resend)
                continue
        if pipeline is None:
            raise SourceError("Could not open the PipeWire stream (is gstreamer1.0-pipewire installed?)")
        sink = pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise SourceError("PipeWire stream failed to start")
        self._pipeline = pipeline
        # wait for a first frame
        t0 = time.time()
        while self.grab() is None and time.time() - t0 < 10 and not self._cancel:
            time.sleep(0.05)
        if self.grab() is None:
            raise SourceError("Stream started but no frames arrived. Try sharing again.")

    def _on_sample(self, sink):
        from gi.repository import Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                data = bytes(info.data)
                stride = len(data) // h if h else w * 4
                img = QImage(data, w, h, stride, QImage.Format.Format_RGB32).copy()
            finally:
                buf.unmap(info)
            with self._frame_lock:
                self._frame = img
                self.frame_seq += 1
        return Gst.FlowReturn.OK

    def _prop(self, name, default, iface=None):
        try:
            v = self._bus.call_sync(self.BUS, self.PATH, "org.freedesktop.DBus.Properties", "Get",
                                    self._GLib.Variant("(ss)", (iface or self.IFACE, name)),
                                    self._GLib.VariantType("(v)"), self._Gio.DBusCallFlags.NONE, 5000, None)
            return v.unpack()[0]
        except Exception:  # noqa: BLE001
            return default

    def _request(self, method, sig, build_args, timeout=120, iface=None):
        """Call a portal method and wait for its Request::Response signal."""
        GLib, Gio = self._GLib, self._Gio
        token = "cc" + secrets.token_hex(6)
        path = f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"
        result: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            result["code"], result["results"] = code, results

        sub = self._bus.signal_subscribe(self.BUS, "org.freedesktop.portal.Request", "Response",
                                         path, None, Gio.DBusSignalFlags.NONE, on_response)
        tick = GLib.timeout_source_new(250)
        tick.set_callback(lambda *_: True)
        tick.attach(self._ctx)
        try:
            self._bus.call_sync(self.BUS, self.PATH, iface or self.IFACE, method,
                                GLib.Variant(sig, build_args(token)), None,
                                Gio.DBusCallFlags.NONE, -1, None)
            deadline = time.time() + timeout
            while "code" not in result:
                if self._cancel:
                    raise SourceError("cancelled")
                if time.time() > deadline:
                    raise SourceError(f"{method}: no answer from the screen-share dialog")
                self._ctx.iteration(True)
        finally:
            tick.destroy()
            self._bus.signal_unsubscribe(sub)
        if result["code"] == 1:
            raise SourceError("Screen sharing was cancelled.")
        if result["code"] != 0:
            raise SourceError(f"{method} failed (portal code {result['code']})")
        return result["results"]


def make_source(restore_token: str | None, remote: bool = False):
    if is_hyprland():
        return HyprlandSource()
    if session_is_wayland():
        return PortalSource(restore_token, remote=remote)
    return X11Source()
