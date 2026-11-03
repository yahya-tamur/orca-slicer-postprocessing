"""The printer's PrusaLink page, served on localhost with a camera view added.

Every request is passed to the printer, so the page is always the printer's own, current one. On the
way through, the page gets one extra script (camera.js next to this file), and every request gets the
PrusaLink login, so the page never asks for an API key. The script puts the camera where the job's
thumbnail is shown. The camera's RTSP stream is turned into MJPEG by ffmpeg, which only runs while
someone is watching.

    python printer_page.py [--port 51201]

The usb_print_host helper starts this with Orca; Orca's Device tab then points at
http://127.0.0.1:51201. The printer's and the camera's addresses come from the plugin's settings in
Orca ("printer", "camera", "ffmpeg"; see load_config), the password from the Orca printer preset.
"""
import argparse
import gzip
import http.client
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 51201
HERE = os.path.dirname(os.path.abspath(__file__))
# The Orca plugin's settings, as saved in Orca's Plugins dialog (the plugin passes the path).
SETTINGS_FILE = os.environ.get("ORCA_PLUGIN_SETTINGS") or os.path.join(
    os.environ.get("APPDATA", ""), "OrcaSlicer", "orca_plugins", "config.json")
SETTINGS_ENTRY = ("usb_bgcode_export", "USB bgcode helper")  # plugin key, capability holding them
SCRIPT_FILE = os.path.join(HERE, "camera.js")
SCRIPT_TAG = b'<script src="/_camera/camera.js" defer></script>'
CREATE_NO_WINDOW = 0x08000000
# Not passed on in either direction: they describe one connection, not the content.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade", "host", "content-length"}
log = logging.getLogger("usb_print_host")


def load_config():
    """The plugin's settings ("printer_preset", "printer", "camera", "ffmpeg", ...) from Orca's
    orca_plugins/config.json, where Orca keeps them as the cap_config of the plugin's helper
    capability; {} if they were never saved."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            entries = json.load(f).get("config", [])
    except (FileNotFoundError, ValueError):
        return {}
    for entry in entries:
        if (entry.get("plugin_key"), entry.get("capability")) == SETTINGS_ENTRY:
            return entry.get("cap_config") or {}
    return {}


def find_ffmpeg(configured=""):
    """The configured ffmpeg, else one on PATH, else winget's install of Gyan.FFmpeg."""
    if configured:
        return os.path.expandvars(configured)
    found = shutil.which("ffmpeg")
    if found:
        return found
    packages = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    for root, _dirs, files in os.walk(packages) if os.path.isdir(packages) else ():
        if "ffmpeg.exe" in files and root.lower().endswith("bin"):
            return os.path.join(root, "ffmpeg.exe")
    return None


class Camera:
    """One ffmpeg process turning the RTSP stream into JPEG frames, shared by every viewer. Started by
    the first viewer, restarted while viewers remain if it stops, ended 15 s after the last one."""

    def __init__(self, url, ffmpeg):
        self.url, self.ffmpeg = url, ffmpeg
        self.cond = threading.Condition()
        self.frame, self.seq = None, 0
        self.viewers = 0
        self.proc = None
        self.started = 0.0
        self.last_frame = 0.0
        self.idle_since = None
        threading.Thread(target=self._watch, daemon=True).start()

    def _start(self):
        """Called with the lock held."""
        if self.proc is not None and self.proc.poll() is None:
            return
        if time.time() - self.started < 5:
            return  # it just stopped; don't hammer an offline camera
        self.started = time.time()
        # -timeout: give up when the connection goes quiet for 10 s (camera or network gone). A camera
        # can also keep the connection alive and send no video (one did after its resolution was
        # changed); _watch restarts ffmpeg when no picture has come for 20 s.
        cmd = [self.ffmpeg, "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "10000000",
               "-fflags", "nobuffer", "-i", self.url, "-an", "-vf", "fps=10", "-q:v", "6", "-f", "mpjpeg", "pipe:1"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            log.warning("camera: can't start ffmpeg (%s): %s", self.ffmpeg, e)
            self.proc = None
            return
        log.info("camera: ffmpeg started for %s", self.url)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        """Parse ffmpeg's multipart JPEG output: --boundary, headers, Content-length bytes of JPEG."""
        out = proc.stdout
        try:
            while True:
                line = out.readline()
                if not line:
                    break
                if not line.startswith(b"--"):
                    continue
                length = None
                while True:
                    header = out.readline()
                    if not header or header in (b"\r\n", b"\n"):
                        break
                    name, _, value = header.decode("latin-1").partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                if length is None:
                    continue
                data = out.read(length)
                if len(data) < length:
                    break
                with self.cond:
                    self.frame, self.seq = data, self.seq + 1
                    self.last_frame = time.time()
                    self.cond.notify_all()
        finally:
            log.info("camera: ffmpeg stopped (exit %s)", proc.poll())
            with self.cond:
                if self.proc is proc:
                    self.frame = None  # don't show a new viewer an old picture
                self.cond.notify_all()

    def _watch(self):
        while True:
            time.sleep(1)
            with self.cond:
                if self.viewers:
                    self.idle_since = None
                    running = self.proc is not None and self.proc.poll() is None
                    if running and time.time() - max(self.last_frame, self.started) > 20:
                        # Still running but no picture for 20 s: stuck. End it; it's started again below.
                        log.warning("camera: no picture for 20 s, restarting ffmpeg")
                        self.proc.kill()
                        self.proc, self.frame = None, None
                        self.started = 0.0
                    self._start()
                elif self.proc is not None and self.proc.poll() is None:
                    self.idle_since = self.idle_since or time.time()
                    if time.time() - self.idle_since > 15:
                        self.proc.kill()
                        self.proc, self.frame = None, None

    def frames(self):
        """Yield each new frame for one viewer, as long as it keeps reading."""
        with self.cond:
            self.viewers += 1
            self._start()
        try:
            seen = 0
            while True:
                with self.cond:
                    self.cond.wait_for(lambda: self.seq != seen, timeout=1)
                    if self.seq == seen:
                        continue  # no frame yet (camera starting or offline); keep waiting
                    seen, frame = self.seq, self.frame
                if frame is not None:
                    yield frame
        finally:
            with self.cond:
                self.viewers -= 1


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    printer = None   # callable -> (base_url, user, password)
    camera = None    # Camera, or None without a camera address

    def log_message(self, fmt, *args):
        pass  # every page poll would end up in the helper's log

    def do_GET(self):
        if self.path.startswith("/_camera/"):
            return self.serve_camera()
        self.forward()

    def do_HEAD(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_PUT(self):
        self.forward()

    def do_DELETE(self):
        self.forward()

    def do_PATCH(self):
        self.forward()

    def serve_camera(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/_camera/camera.js":
            with open(SCRIPT_FILE, "rb") as f:
                body = f.read()
            return self.reply(200, body, "text/javascript; charset=utf-8")
        if path == "/_camera/stream.mjpg":
            if self.camera is None:
                return self.reply(404, b"no camera configured", "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for frame in self.camera.frames():
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    self.wfile.flush()
            except (ConnectionError, OSError):
                pass  # the viewer went away
            return
        self.reply(404, b"not found", "text/plain")

    def reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def forward(self):
        """Pass the request to the printer with the PrusaLink login, and its answer back."""
        try:
            base, _user, password = self.printer()
        except Exception as e:
            return self.reply(502, f"No printer address: {e}".encode(), "text/plain; charset=utf-8")
        u = urllib.parse.urlsplit(base)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP and k.lower() not in ("x-api-key", "authorization")}
        # The printer accepts its PrusaLink password as an API key. Replacing whatever key the page or
        # Orca sent means a wrong or stale key can never make the page ask for one.
        headers["X-Api-Key"] = password
        index = self.command == "GET" and urllib.parse.urlsplit(self.path).path in ("/", "/index.html")
        if index:
            headers.pop("If-None-Match", None)  # always get the page itself, to add the script
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError as e:
            return self.reply(502, f"The printer didn't answer: {e}".encode(), "text/plain; charset=utf-8")
        finally:
            conn.close()
        out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
        if index and resp.status == 200:
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            data = data.replace(b"</head>", SCRIPT_TAG + b"</head>", 1) if b"</head>" in data \
                else data + SCRIPT_TAG
            out_headers = [(k, v) for k, v in out_headers if k.lower() not in ("content-encoding", "etag")]
        self.send_response(resp.status, resp.reason)
        for k, v in out_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def serve(port, printer, config):
    """Run the page server (blocking). printer: callable -> (base_url, user, password)."""
    Proxy.printer = staticmethod(printer)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Proxy)
    except OSError as e:
        log.warning("printer page: port %d is busy (%s)", port, e)
        return
    if config.get("camera"):
        ffmpeg = find_ffmpeg(config.get("ffmpeg", ""))
        if ffmpeg:
            Proxy.camera = Camera(config["camera"], ffmpeg)
        else:
            log.warning("camera: ffmpeg not found; the page is served without a camera")
    server.daemon_threads = True
    log.info("printer page on http://127.0.0.1:%d (camera: %s)", port, "on" if Proxy.camera else "off")
    server.serve_forever()


def stop():
    """End ffmpeg, if it's running (the helper calls this when Orca closes)."""
    cam = Proxy.camera
    if cam is not None:
        with cam.cond:
            if cam.proc is not None and cam.proc.poll() is None:
                cam.proc.kill()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import usb_print_host  # the printer's address and login from the Orca preset, as the helper reads them
    serve(args.port, usb_print_host.prusalink_settings, load_config())
