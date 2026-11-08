#!/usr/bin/env python3
"""Controller Replay desktop recorder.

Records your game controller in the background, even while a game is fullscreen
or this window is not focused, and serves the Controller Replay website at
http://localhost:8765 so you can start, stop, browse and replay recordings.

    pip install pygame
    python recorder.py

Start or stop a recording at any time:
  * hold Back + Start (View + Menu, Share + Options) on the controller for half a second
  * press Record (or R) on the website
  * press Enter in this window

Recordings are saved as .json files in the "recordings" folder next to this file.
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Must be set before pygame loads SDL: keep reading the controller when another window has focus.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

HERE = Path(__file__).resolve().parent
APP_ID = "controller-replay-recorder"
NB = 17
BUTTONS = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "Back", "Start", "L3", "R3", "Up", "Down", "Left", "Right", "Home"]
HZ = 60
COMBO_HOLD = 0.5  # seconds Back + Start must be held to start or stop
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.json$")


def thresh(i):
    return 0.1 if i in (6, 7) else 0.5


def r3(v):
    return round(float(v), 3)


# ---------------------------------------------------------------- controller input

class PadReader:
    """Reads the first connected controller in the browser's standard layout."""

    def __init__(self, pg):
        self.pg = pg
        from pygame._sdl2 import controller as gc
        self.gc = gc
        gc.init()
        self.pads = {}  # SDL instance id -> (kind, device, name)
        self.scan()

    def scan(self):
        pg, gc = self.pg, self.gc
        for i in range(pg.joystick.get_count()):
            try:
                joy = pg.joystick.Joystick(i)
                iid = joy.get_instance_id()
                if iid in self.pads:
                    continue
                if gc.is_controller(i):
                    c = gc.Controller(i)
                    self.pads[iid] = ("gc", c, c.name or joy.get_name())
                else:
                    joy.init()
                    self.pads[iid] = ("joy", joy, joy.get_name())
                kind, _, name = self.pads[iid]
                note = "" if kind == "gc" else " (not a recognized controller, so a generic layout is used and some labels may not match)"
                print(f"Controller connected: {name}{note}")
            except Exception as e:  # a device that can't be opened shouldn't stop the recorder
                print(f"Couldn't open controller {i}: {e}")

    def remove(self, iid):
        pad = self.pads.pop(iid, None)
        if pad:
            print(f"Controller disconnected: {pad[2]}")

    def handle(self, ev):
        pg = self.pg
        if ev.type in (pg.JOYDEVICEADDED, pg.CONTROLLERDEVICEADDED):
            self.scan()
        elif ev.type in (pg.JOYDEVICEREMOVED, pg.CONTROLLERDEVICEREMOVED):
            iid = getattr(ev, "instance_id", None)
            if iid is not None:
                self.remove(iid)

    def names(self):
        return [p[2] for p in self.pads.values()]

    def read(self):
        """Return (sample_without_t, controller_name) for the first controller, or (None, None)."""
        pg = self.pg
        for kind, dev, name in list(self.pads.values()):
            try:
                if kind == "gc":
                    if not dev.attached():
                        continue
                    ax = lambda a: max(-1.0, min(1.0, dev.get_axis(a) / 32767))
                    a = [ax(pg.CONTROLLER_AXIS_LEFTX), ax(pg.CONTROLLER_AXIS_LEFTY),
                         ax(pg.CONTROLLER_AXIS_RIGHTX), ax(pg.CONTROLLER_AXIS_RIGHTY)]
                    order = [pg.CONTROLLER_BUTTON_A, pg.CONTROLLER_BUTTON_B, pg.CONTROLLER_BUTTON_X, pg.CONTROLLER_BUTTON_Y,
                             pg.CONTROLLER_BUTTON_LEFTSHOULDER, pg.CONTROLLER_BUTTON_RIGHTSHOULDER, None, None,
                             pg.CONTROLLER_BUTTON_BACK, pg.CONTROLLER_BUTTON_START,
                             pg.CONTROLLER_BUTTON_LEFTSTICK, pg.CONTROLLER_BUTTON_RIGHTSTICK,
                             pg.CONTROLLER_BUTTON_DPAD_UP, pg.CONTROLLER_BUTTON_DPAD_DOWN,
                             pg.CONTROLLER_BUTTON_DPAD_LEFT, pg.CONTROLLER_BUTTON_DPAD_RIGHT, pg.CONTROLLER_BUTTON_GUIDE]
                    b = [float(dev.get_button(x)) if x is not None else 0.0 for x in order]
                    b[6] = max(0.0, dev.get_axis(pg.CONTROLLER_AXIS_TRIGGERLEFT) / 32767)
                    b[7] = max(0.0, dev.get_axis(pg.CONTROLLER_AXIS_TRIGGERRIGHT) / 32767)
                else:
                    # Generic layout, matching how most Xbox-style pads appear to SDL without a mapping.
                    n_ax, n_bt = dev.get_numaxes(), dev.get_numbuttons()
                    axv = lambda i: dev.get_axis(i) if i < n_ax else 0.0
                    btv = lambda i: float(dev.get_button(i)) if i < n_bt else 0.0
                    a = [axv(0), axv(1), axv(3), axv(4)]
                    b = [0.0] * NB
                    for dst, src in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (8, 6), (9, 7), (16, 8), (10, 9), (11, 10)):
                        b[dst] = btv(src)
                    b[6] = (axv(2) + 1) / 2 if n_ax > 5 else 0.0
                    b[7] = (axv(5) + 1) / 2 if n_ax > 5 else 0.0
                    if dev.get_numhats():
                        hx, hy = dev.get_hat(0)
                        b[12], b[13], b[14], b[15] = float(hy > 0), float(hy < 0), float(hx < 0), float(hx > 0)
                return {"a": [r3(v) for v in a], "b": [r3(v) for v in b]}, name
            except Exception:
                continue
        return None, None


class DemoReader:
    """Synthetic controller for trying the recorder without one plugged in (--demo)."""

    def __init__(self):
        self.t0 = time.perf_counter()

    def handle(self, ev):
        pass

    def names(self):
        return ["Demo controller"]

    def read(self):
        t = time.perf_counter() - self.t0
        b = [0.0] * NB
        b[0] = 1.0 if (t % 1.3) < 0.15 else 0.0
        b[2] = 1.0 if (t % 2.9) < 0.1 else 0.0
        b[7] = r3(max(0.0, math.sin(t * 0.9)))
        a = [math.sin(t * 1.3) * 0.9, -abs(math.cos(t * 0.6)) * 0.8, math.sin(t * 2.1) * 0.5, math.cos(t * 1.7) * 0.4]
        return {"a": [r3(v) for v in a], "b": b}, "Demo controller"


# ---------------------------------------------------------------- recording

class Recorder:
    def __init__(self, outdir):
        self.outdir = outdir
        self.lock = threading.RLock()
        self.recording = False
        self.samples = []
        self.start_at = 0.0
        self.controller = None
        self.last = None
        self.last_name = None
        self.skip_until_release = False
        self.combo_since = None
        self.combo_fired = False
        self.message = ""
        self.last_saved = None

    def status(self, names):
        with self.lock:
            return {
                "app": APP_ID,
                "recording": self.recording,
                "elapsed": round((time.perf_counter() - self.start_at) * 1000) if self.recording and not self.skip_until_release else 0,
                "samples": len(self.samples),
                "controller": self.last_name,
                "controllers": names,
                "folder": str(self.outdir),
                "message": self.message,
                "lastSaved": self.last_saved,
            }

    def start(self, from_combo=False):
        with self.lock:
            if self.recording:
                return True, "Already recording."
            if self.last is None:
                self.message = "No controller found. Plug one in, then try again."
                return False, self.message
            self.recording = True
            self.samples = []
            self.controller = self.last_name
            self.skip_until_release = from_combo
            self.start_at = time.perf_counter()
            self.message = "Recording."
            print("● Recording started" + (" (from the controller)" if from_combo else ""))
            return True, self.message

    def stop(self, cut_at=None):
        with self.lock:
            if not self.recording:
                return None
            self.recording = False
            samples = self.samples
            self.samples = []
            if cut_at is not None:
                cut = (cut_at - self.start_at) * 1000
                samples = [s for s in samples if s["t"] < cut]
            if len(samples) < 2:
                self.message = "That recording was too short to keep."
                print("■ Recording stopped: too short, nothing saved")
                return None
            path = self._save(samples)
            self.last_saved = path.name
            self.message = f"Saved {path.name}."
            return path

    def _save(self, samples):
        self.outdir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stem = "controller-recording-" + now.strftime("%Y%m%d-%H%M%S")
        path, n = self.outdir / f"{stem}.json", 2
        while path.exists():
            path, n = self.outdir / f"{stem}-{n}.json", n + 1
        presses, held = 0, [False] * NB
        for s in samples:
            for i in range(NB):
                on = s["b"][i] >= thresh(i)
                if on and not held[i]:
                    presses += 1
                held[i] = on
        data = {
            "version": 1,
            "name": "Recording · " + now.strftime("%b %d, %I:%M:%S %p").replace(" 0", " "),
            "controller": self.controller or "Unknown controller",
            "mapping": "standard",
            "recordedAt": now.astimezone().isoformat(timespec="seconds"),
            "duration": samples[-1]["t"],
            "presses": presses,
            "samples": samples,
        }
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        print(f"■ Saved {path.name}  ({samples[-1]['t'] / 1000:.1f} s, {presses} press{'' if presses == 1 else 'es'})")
        return path

    def feed(self, now, sample, name):
        with self.lock:
            self.last, self.last_name = sample, name
            if sample is None:
                self.combo_since, self.combo_fired = None, False
                return
            b = sample["b"]
            held = b[8] >= 0.5 and b[9] >= 0.5
            if held:
                if self.combo_since is None:
                    self.combo_since = now
                if not self.combo_fired and now - self.combo_since >= COMBO_HOLD:
                    self.combo_fired = True
                    if self.recording:
                        self.stop(cut_at=self.combo_since)
                    else:
                        self.start(from_combo=True)
            else:
                self.combo_since, self.combo_fired = None, False
            if not self.recording:
                return
            if self.skip_until_release:
                # Started from the controller: begin once Back and Start are let go, so the combo isn't recorded.
                if b[8] < 0.5 and b[9] < 0.5:
                    self.skip_until_release = False
                    self.start_at = now
                    self.samples.append({"t": 0, **sample})
                return
            self.samples.append({"t": round((now - self.start_at) * 1000), **sample})


# ---------------------------------------------------------------- website

def list_recordings(outdir):
    items = []
    if not outdir.is_dir():
        return items
    for p in outdir.glob("*.json"):
        try:
            with p.open(encoding="utf-8") as f:
                d = json.load(f)
            samples = d.get("samples") or []
            items.append({
                "id": p.name,
                "name": d.get("name") or p.stem,
                "duration": d.get("duration", samples[-1]["t"] if samples else 0),
                "presses": d.get("presses", 0),
                "createdAt": int(p.stat().st_mtime * 1000),
            })
        except Exception:
            continue
    items.sort(key=lambda x: x["createdAt"], reverse=True)
    return items


def make_handler(rec, reader_names, outdir):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ControllerReplay/1"

        def log_message(self, *args):
            pass

        def _host_ok(self):
            # Refuse requests addressed to any other host name (protects against DNS rebinding).
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("localhost", "127.0.0.1", "::1")

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _rec_file(self, name):
            if not SAFE_NAME.match(name):
                return None
            p = (outdir / name).resolve()
            return p if p.parent == outdir.resolve() and p.is_file() else None

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "Forbidden"})
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, rec.status(reader_names()))
            if path == "/api/live":
                with rec.lock:
                    return self._send(200, {"sample": rec.last, "recording": rec.recording})
            if path == "/api/recordings":
                return self._send(200, list_recordings(outdir))
            if path.startswith("/api/recordings/"):
                p = self._rec_file(path.rsplit("/", 1)[1])
                if not p:
                    return self._send(404, {"error": "No such recording"})
                return self._send(200, p.read_bytes())
            return self._send(404, {"error": "Not found"})

        def _write_ok(self):
            # A custom header can't be sent cross-site without a preflight we never approve,
            # so other websites can't start, stop or delete recordings.
            return self._host_ok() and self.headers.get("X-Recorder") == "1"

        def do_POST(self):
            if not self._write_ok():
                return self._send(403, {"error": "Forbidden"})
            if self.path == "/api/record/start":
                ok, msg = rec.start()
                return self._send(200 if ok else 409, {"ok": ok, "message": msg, **rec.status(reader_names())})
            if self.path == "/api/record/stop":
                saved = rec.stop()
                return self._send(200, {"ok": True, "saved": saved.name if saved else None, **rec.status(reader_names())})
            return self._send(404, {"error": "Not found"})

        def do_DELETE(self):
            if not self._write_ok():
                return self._send(403, {"error": "Forbidden"})
            if self.path.startswith("/api/recordings/"):
                p = self._rec_file(self.path.rsplit("/", 1)[1])
                if not p:
                    return self._send(404, {"error": "No such recording"})
                p.unlink()
                print(f"Deleted {p.name}")
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "Not found"})

    return Handler


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Record a game controller in the background and replay it in your browser.")
    ap.add_argument("--port", type=int, default=8765, help="port for the website (default 8765)")
    ap.add_argument("--dir", default=str(HERE / "recordings"), help="folder to save recordings in")
    ap.add_argument("--no-browser", action="store_true", help="don't open the website automatically")
    ap.add_argument("--demo", action="store_true", help="use a pretend controller, to try it without one")
    ap.add_argument("--list", action="store_true", help="list connected controllers and exit")
    args = ap.parse_args()
    outdir = Path(args.dir).expanduser().resolve()

    try:
        import pygame as pg
    except ImportError:
        sys.exit("This recorder needs pygame. Install it with:  pip install pygame")
    pg.display.init()  # needed for the event pump; audio is never started
    pg.joystick.init()
    reader = DemoReader() if args.demo else PadReader(pg)

    if args.list:
        names = reader.names()
        print("\n".join(names) if names else "No controllers connected.")
        return

    rec = Recorder(outdir)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(rec, reader.names, outdir))
    except OSError:
        sys.exit(f"Port {args.port} is in use. Is the recorder already running? Otherwise try:  python recorder.py --port {args.port + 1}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{args.port}/"

    print("Controller Replay recorder")
    print(f"  Website:     {url}")
    print(f"  Recordings:  {outdir}")
    print("  Start/stop:  hold Back + Start on the controller, press Record on the website, or press Enter here")
    print("  Quit:        Ctrl+C" + (" or type q and Enter" if sys.stdin and sys.stdin.isatty() else ""))
    if not reader.names():
        print("No controller yet. Plug one in; it's picked up automatically.")

    stop_event = threading.Event()

    if sys.stdin and sys.stdin.isatty():
        def console():
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd in ("q", "quit", "exit"):
                    stop_event.set()
                    return
                if rec.recording:
                    rec.stop()
                else:
                    ok, msg = rec.start()
                    if not ok:
                        print(msg)
        threading.Thread(target=console, daemon=True).start()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    period = 1 / HZ
    next_tick = time.perf_counter()
    try:
        while not stop_event.is_set():
            for ev in pg.event.get():
                reader.handle(ev)
            sample, name = reader.read()
            rec.feed(time.perf_counter(), sample, name)
            next_tick += period
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.perf_counter()
    except KeyboardInterrupt:
        pass
    finally:
        if rec.recording:
            rec.stop()
        server.shutdown()
        pg.quit()
        print("Recorder closed.")


if __name__ == "__main__":
    main()
