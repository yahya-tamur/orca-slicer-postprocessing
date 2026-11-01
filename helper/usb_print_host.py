"""USB print host for OrcaSlicer: the Print button writes the file to a USB drive.

Pretends to be an OctoPrint / PrusaLink server on 127.0.0.1 so Orca's Print button uploads the sliced
G-code here. The upload is written as received to the root of a removable drive, and the drive is
ejected; a small window shows the progress. No conversion happens here: the "Export as bgcode"
slicing-pipeline step of the Orca plugin converts uploads to binary G-code before they are sent.
With that step ticked the drive gets .bgcode, unticked it gets plain .gcode. Nothing is written to
the PC's disk except a short log in %LOCALAPPDATA%\\OrcaUsbBgcode.

    pythonw usb_print_host.py [--orca-pid PID] [--port 51200]

Started by the "USB bgcode export" Orca plugin; exits when the Orca process given by --orca-pid
(or the ORCA_USB_HELPER_PID environment variable) ends. Only one instance runs at a time (the port is the lock).
"""
import argparse
import ctypes
import hashlib
import http.client
import json
import logging
import os
import queue
import re
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import ttk

DEFAULT_PORT = 51200
BGCODE_MAGIC = b"GCDE"  # first bytes of a Prusa binary G-code file
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "OrcaUsbBgcode")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR, "helper.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("usb_print_host")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetVolumeInformationW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD)
DRIVE_REMOVABLE = 2
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102
CREATE_NO_WINDOW = 0x08000000
# Querying a drive whose media was just ejected must fail quietly, not pop up "insert a disk".
kernel32.SetErrorMode(0x0001 | 0x8000)  # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX


# ------------------------------------------------------------------ drives
def removable_drives():
    """[(letter, label)] for drives Windows reports as removable and that have media."""
    drives = []
    mask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) != DRIVE_REMOVABLE:
            continue
        label = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0):
            continue  # card reader slot without media
        drives.append((letter, label.value))
    return drives


def drive_letter_present(letter):
    return bool(kernel32.GetLogicalDrives() & (1 << (ord(letter) - ord("A"))))


def drive_ready(letter):
    """True while the drive's volume is mounted and readable. After Windows' "safe to remove" some
    USB sticks keep their drive letter (with no volume behind it) until unplugged, so the letter
    alone can't tell that the eject finished."""
    if not drive_letter_present(letter):
        return False
    return bool(kernel32.GetVolumeInformationW(f"{letter}:\\", None, 0, None, None, None, None, 0))


def eject(letter, timeout=25):
    """Explorer's 'Eject' (safe removal) and wait until the volume is gone."""
    cmd = ("$d=(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s:'); "
           "if ($d) { $d.InvokeVerb('Eject') }" % letter)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                   creationflags=CREATE_NO_WINDOW, timeout=60, capture_output=True)
    end = time.time() + timeout
    while time.time() < end:
        if not drive_ready(letter):
            return True
        time.sleep(0.5)
    return False


_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")  # ".gcode", ".txt" - not the ".2mm" in "part_0.2mm"


def safe_name(name, binary):
    """The name picked in Orca's upload dialog, usable as a file name on the drive.

    Returns (name, ok): folder part dropped, characters Windows forbids replaced with "_". The
    expected extension is .bgcode for binary content and .gcode for plain G-code. Ends in it -> kept
    (ok). No extension -> it is added (ok). Any other extension is kept but ok=False: the window asks
    first, since the printer judges the content by the extension."""
    expected = ".bgcode" if binary else ".gcode"
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", base).strip(" .")[:200] or "print"
    m = _EXTENSION.search(base)
    if m is None:
        return base + expected, True
    return base, m.group(0).lower() == expected


def with_extension(name, extension):
    """name with its extension replaced by extension."""
    m = _EXTENSION.search(name)
    return (name[:m.start()] if m else name) + extension


# ------------------------------------------------------------------ PrusaLink (network alternative)
# Address and login come from the Orca printer preset, as entered there by the user: print_host_webui
# (the printer's own address; print_host points at this helper), printhost_user, printhost_password.
# They are read when the button is used and never stored or logged here.
PRINTER_PRESET = os.path.join(os.environ.get("APPDATA", ""), "OrcaSlicer", "user", "default", "machine",
                              "Prusa CORE One+ Gen 2 0.4 HF.json")


def prusalink_settings():
    """(base_url, user, password) from the Orca printer preset; PRUSALINK_URL overrides the address (tests)."""
    with open(PRINTER_PRESET, encoding="utf-8") as f:
        preset = json.load(f)
    base = os.environ.get("PRUSALINK_URL") or preset.get("print_host_webui") or ""
    if base and "://" not in base:
        base = "http://" + base
    if not base or "127.0.0.1" in base or "localhost" in base:
        raise RuntimeError("No PrusaLink address in the printer preset (print_host_webui).")
    return base.rstrip("/"), preset.get("printhost_user") or "maker", preset.get("printhost_password") or ""


class PrusaLink:
    """Minimal PrusaLink v1 client with HTTP digest login (realm "Printer API")."""

    def __init__(self, base, user, password):
        u = urllib.parse.urlsplit(base)
        self.host, self.port = u.hostname, u.port or 80
        self.user, self.password = user, password
        self.challenge = None
        self.nc = 0

    def _authorization(self, method, uri):
        c = self.challenge
        ha1 = hashlib.md5(f"{self.user}:{c['realm']}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        fields = {"username": self.user, "realm": c["realm"], "nonce": c["nonce"], "uri": uri}
        if "auth" in c.get("qop", "").split(","):
            self.nc += 1
            nc, cnonce = f"{self.nc:08x}", os.urandom(8).hex()
            fields.update(qop="auth", nc=nc, cnonce=cnonce)
            response = hashlib.md5(f"{ha1}:{c['nonce']}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        else:
            response = hashlib.md5(f"{ha1}:{c['nonce']}:{ha2}".encode()).hexdigest()
        fields["response"] = response
        if c.get("opaque"):
            fields["opaque"] = c["opaque"]
        return "Digest " + ", ".join(f'{k}={v}' if k in ("qop", "nc") else f'{k}="{v}"' for k, v in fields.items())

    def _request(self, method, uri, body=b"", headers=None, progress=None, cancel=None):
        for attempt in range(3):
            conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
            try:
                hdrs = dict(headers or {})
                if self.challenge:
                    hdrs["Authorization"] = self._authorization(method, uri)
                if not body:
                    conn.request(method, uri, headers=hdrs)
                else:
                    hdrs["Content-Length"] = str(len(body))
                    conn.putrequest(method, uri)
                    for k, v in hdrs.items():
                        conn.putheader(k, v)
                    conn.endheaders()
                    step = 64 * 1024
                    for off in range(0, len(body), step):
                        if cancel is not None and cancel.is_set():
                            raise RuntimeError("cancelled")
                        conn.send(body[off:off + step])
                        if progress:
                            progress(min(1.0, (off + step) / len(body)))
                resp = conn.getresponse()
                data = resp.read()
                if resp.status == 401 and attempt < 2:
                    header = resp.getheader("WWW-Authenticate", "")
                    self.challenge = dict(re.findall(r'(\w+)="?([^",]*)"?', header))
                    self.nc = 0
                    if not self.challenge.get("nonce"):
                        raise RuntimeError("PrusaLink asked for a login it doesn't describe: " + header)
                    continue  # retry with the login (upload() GETs first, so a PUT body is rarely resent)
                return resp.status, data
            finally:
                conn.close()
        return resp.status, data

    def usb_storage(self):
        """Path of the printer's writable storage (its USB stick), e.g. "/usb"."""
        status, data = self._request("GET", "/api/v1/storage")
        if status == 401:
            raise RuntimeError("PrusaLink rejected the login. Check the password in Orca's printer connection settings.")
        if status != 200:
            raise RuntimeError(f"PrusaLink storage list failed (HTTP {status}).")
        for s in json.loads(data).get("storage_list", []):
            if s.get("available", True) and not s.get("read_only", s.get("ro", False)):
                return s["path"]
        raise RuntimeError("The printer has no writable storage. Is a USB stick plugged into the printer?")

    def upload(self, name, data, progress=None, cancel=None):
        """PUT the file into the printer's storage (not started)."""
        storage = self.usb_storage().strip("/")
        uri = f"/api/v1/files/{urllib.parse.quote(storage)}/{urllib.parse.quote(name)}"
        status, body = self._request("PUT", uri, data, {"Content-Type": "text/x.gcode", "Overwrite": "?1",
                                                        "Print-After-Upload": "?0"}, progress, cancel)
        if status == 401:
            raise RuntimeError("PrusaLink rejected the login. Check the password in Orca's printer connection settings.")
        if status not in (200, 201, 204):
            raise RuntimeError(f"PrusaLink upload failed (HTTP {status}): {body[:200].decode('latin-1', 'replace')}")
        return storage


# ------------------------------------------------------------------ jobs
class Job:
    def __init__(self, filename, data, start_print):
        self.binary = data[:4] == BGCODE_MAGIC
        self.filename, self.name_ok = safe_name(filename, self.binary)
        self.data = data
        self.start_print = start_print


class Window:
    """One status window per job; driven from the Tk thread via a queue."""

    def __init__(self, root):
        self.root = root
        self.top = None
        self.choice = None

    def show(self, filename):
        if self.top is None or not self.top.winfo_exists():
            self.top = tk.Toplevel(self.root)
            self.top.title("USB export")
            self.top.resizable(False, False)
            self.top.protocol("WM_DELETE_WINDOW", self.close)
            # Fixed size: every area has a fixed height so changing text or buttons never resizes it.
            self.top.geometry("440x210")
            frm = ttk.Frame(self.top, padding=16)
            frm.pack(fill="both", expand=True)
            self.title = ttk.Label(frm, font=("Segoe UI", 11, "bold"), width=46, anchor="w")
            self.title.pack(anchor="w")
            status_box = ttk.Frame(frm, height=64, width=408)
            status_box.pack(fill="x", pady=(8, 8))
            status_box.pack_propagate(False)
            self.status = ttk.Label(status_box, wraplength=400, justify="left", anchor="nw")
            self.status.pack(fill="both", expand=True)
            self.bar = ttk.Progressbar(frm, length=408, mode="determinate", maximum=100)
            self.busy = False
            self.bar.pack(fill="x")
            self.buttons = ttk.Frame(frm, height=34)
            self.buttons.pack(fill="x", pady=(12, 0))
            self.buttons.pack_propagate(False)
        self.title.config(text=filename if len(filename) <= 46 else filename[:43] + "...")
        self.top.deiconify()
        self.top.lift()
        self.top.attributes("-topmost", True)
        self.top.after(300, lambda: self.top.attributes("-topmost", False))

    def update(self, text, progress=None, busy=False):
        """progress: 0..1 for a determinate bar; busy=True animates it (left running if already busy)."""
        self.status.config(text=text)
        if busy:
            if not self.busy:
                self.bar.config(mode="indeterminate", value=0)
                self.bar.start(15)
                self.busy = True
        else:
            if self.busy:
                self.bar.stop()
                self.busy = False
            self.bar.config(mode="determinate", value=100 * (progress if progress is not None else 0))

    def set_buttons(self, specs):
        for w in self.buttons.winfo_children():
            w.destroy()
        for text, cb in specs:
            ttk.Button(self.buttons, text=text, command=cb).pack(side="right", padx=(6, 0))

    def close(self):
        if self.top is not None and self.top.winfo_exists():
            self.top.withdraw()


class Worker(threading.Thread):
    """Handles uploads in batches in the single window: every upload that arrives before the drive is
    ejected joins the current batch; all of it is written, then the drive is ejected once."""

    def __init__(self, jobs, ui, test_dir=None):
        super().__init__(daemon=True)
        self.test_dir = test_dir
        self.jobs = jobs
        self.ui = ui  # queue of callables run on the Tk thread
        self.cancel = threading.Event()
        self.via_link = threading.Event()  # "Send via PrusaLink" clicked while waiting for a drive
        self.picked = queue.Queue()
        self.batch = []  # accepted uploads (named, data attached) not yet finished

    def call(self, fn, *a, **kw):
        self.ui.put(lambda: fn(*a, **kw))

    def run(self):
        while True:
            job = self.jobs.get()
            self.batch = []
            self.cancel.clear()
            try:
                self.add(job)
                self.process_batch()
            except Exception as e:  # keep serving later uploads
                log.exception("batch failed")
                self.call(win.update, f"Failed: {e}")
                self.call(win.set_buttons, [("Close", win.close)])
            finally:
                self.batch = []

    # ---- batch bookkeeping
    def title(self):
        if len(self.batch) == 1:
            return self.batch[0].filename
        return f"{len(self.batch)} files" if self.batch else "USB export"

    def files_text(self):
        n = len(self.batch)
        return f"{self.batch[0].filename}" if n == 1 else f"{n} files"

    def add(self, job):
        """Accept one upload into the batch, asking first if its name doesn't match its content."""
        data, job.data = job.data, None
        kind = "binary G-code" if job.binary else "plain G-code (Export as bgcode is unticked)"
        log.info("received %s: %s, %d bytes", job.filename, kind, len(data))
        self.call(win.show, job.filename if not self.batch else self.title())
        if not job.name_ok:
            # The picked name's extension doesn't match the content (.bgcode for binary, .gcode
            # for plain G-code): ask before writing.
            expected = ".bgcode" if job.binary else ".gcode"
            fixed = with_extension(job.filename, expected)
            self.call(win.show, job.filename)
            self.call(win.update, f'"{job.filename}" doesn\'t end in {expected}, but the file is {kind.split(" (")[0]}, '
                                  "so the printer may not read it under that name.")
            self.call(win.set_buttons, [("Skip file", lambda: self.picked.put(None)),
                                        (f"Keep {os.path.splitext(job.filename)[1]}", lambda: self.picked.put(job.filename)),
                                        (f"Save as {expected}", lambda: self.picked.put(fixed))])
            choice = self.picked.get()
            if choice is None:
                log.info("skipped %s (user choice)", job.filename)
                self.call(win.show, self.title())
                return
            log.info("name %s -> %s (user choice)", job.filename, choice)
            job.filename = choice
        job.data = data
        self.batch.append(job)
        self.call(win.show, self.title())

    def collect(self):
        """Add uploads that arrived meanwhile; True if any did."""
        added = False
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return added
            self.add(job)
            added = True

    def finish(self, text, progress=1.0):
        self.call(win.update, text, progress)
        self.call(win.set_buttons, [("Close", win.close)])

    # ---- the batch
    def process_batch(self):
        self.collect()
        if not self.batch:
            self.finish("Nothing to write.", 0.0)
            return

        if self.test_dir:
            done = []
            while len(done) < len(self.batch):
                for job in [j for j in self.batch if j not in done]:
                    target = os.path.join(self.test_dir, job.filename)
                    with open(target, "wb") as f:
                        f.write(job.data)
                    log.info("test mode: wrote %s", target)
                    done.append(job)
                self.collect()
            self.finish(f"Test mode: wrote {self.files_text()} to {self.test_dir}")
            return

        # Wait for a removable drive, collecting further uploads into the batch meanwhile.
        self.via_link.clear()

        def show_waiting():
            # Re-sent only when the text changes: the indeterminate bar keeps animating.
            self.call(win.update, f"{self.files_text()} ready. No removable drives found.\n"
                                  "Plug in a USB drive, or send to the printer over the network.", busy=True)
            self.call(win.set_buttons, [("Cancel", self.cancel.set),
                                        ("Send via PrusaLink", self.via_link.set)])

        drives = removable_drives()
        if not drives:
            show_waiting()
        while not drives:
            if self.cancel.wait(1.0):
                self.finish("Cancelled. Nothing was written.", 0.0)
                return
            if self.via_link.is_set():
                self.send_prusalink()
                return
            if self.collect():
                show_waiting()
            drives = removable_drives()

        if len(drives) == 1:
            letter, label = drives[0]
        else:
            self.call(win.update, "Several removable drives found. Which one?")
            self.call(win.set_buttons, [(f"{d}: {lbl or 'USB drive'}", lambda d=d: self.picked.put(d))
                                        for d, lbl in drives] + [("Cancel", lambda: self.picked.put(None))])
            letter = self.picked.get()
            if letter is None:
                self.finish("Cancelled. Nothing was written.", 0.0)
                return
            label = dict(drives).get(letter, "")
        name = f"{letter}: ({label})" if label else f"{letter}:"
        self.call(win.set_buttons, [])

        # Write every file of the batch, including uploads that arrive while copying.
        written = []
        while len(written) < len(self.batch):
            for job in [j for j in self.batch if j not in written]:
                self.write_file(job, letter, name, len(written) + 1)
                written.append(job)
            self.collect()

        # Eject once, then wait for the stick to be pulled out.
        self.call(win.update, f"Ejecting {name}...", busy=True)
        if eject(letter):
            # "Upload" and "Upload and Print" behave the same: prints are started on the printer.
            what = "is" if len(self.batch) == 1 else "are"
            self.finish(f"Done. {self.files_text()} {what} on the drive and it was ejected.\n"
                        "You can unplug it now.")
            log.info("ejected %s", letter)
            self.close_when_unplugged(letter)
        else:
            self.finish(f"{self.files_text()} written, but {name} could not be ejected "
                        "(something may still be using it). Eject it from the taskbar before unplugging.")
            log.warning("eject failed for %s", letter)

    def write_file(self, job, letter, name, index):
        target = os.path.join(f"{letter}:\\", job.filename)
        count = f" ({index} of {len(self.batch)})" if len(self.batch) > 1 else ""
        self.call(win.show, self.title())
        data, tmp = job.data, target + ".part"
        with open(tmp, "wb") as f:
            step = 1 << 20
            for off in range(0, len(data), step):
                f.write(data[off:off + step])
                frac = min(1.0, (off + step) / len(data))
                self.call(win.update, f"Copying {job.filename} to {name}{count}... {frac:.0%}", frac)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        job.data = None
        log.info("wrote %s", target)

    def send_prusalink(self):
        """Upload the batch to the printer's own storage over the network instead of a USB drive."""
        self.cancel.clear()
        self.call(win.set_buttons, [("Cancel", self.cancel.set)])
        sent = []
        try:
            base, user, password = prusalink_settings()
            self.call(win.update, f"Connecting to the printer at {base}...", busy=True)
            link = PrusaLink(base, user, password)
            storage = ""
            while len(sent) < len(self.batch):
                for job in [j for j in self.batch if j not in sent]:
                    count = f" ({len(sent) + 1} of {len(self.batch)})" if len(self.batch) > 1 else ""

                    def progress(f, job=job, count=count):
                        self.call(win.update, f"Sending {job.filename} via PrusaLink{count}... {f:.0%}", f)

                    storage = link.upload(job.filename, job.data, progress, self.cancel)
                    log.info("sent %s via PrusaLink to %s storage /%s", job.filename, base, storage)
                    job.data = None
                    sent.append(job)
                self.collect()
        except Exception as e:
            log.warning("PrusaLink upload failed after %d of %d files: %s", len(sent), len(self.batch), e)
            done = f" ({len(sent)} of {len(self.batch)} sent)" if sent else ""
            text = "Cancelled." if str(e) == "cancelled" else f"PrusaLink: {e}"
            self.finish(text + done, 0.0)
            return
        what = "is" if len(sent) == 1 else "are"
        self.finish(f"Done. {self.files_text()} {what} on the printer's {storage.upper()} storage (via PrusaLink).\n"
                    "Start the print from the printer.")

    def close_when_unplugged(self, letter, timeout=600):
        """Close the window once the ejected stick is physically removed (its drive letter goes away).
        Sticks whose letter already vanished at eject can't show that, so close after a few seconds.
        A newer upload stops the wait (the run loop then starts a new batch in the same window); so
        does the timeout (the window then stays, with Close)."""
        if not drive_letter_present(letter):
            time.sleep(4)
            if self.jobs.empty():
                self.call(win.close)
            return
        end = time.time() + timeout
        while time.time() < end:
            if not self.jobs.empty():
                return
            if not drive_letter_present(letter):
                log.info("%s: unplugged", letter)
                self.call(win.close)
                return
            time.sleep(0.5)


# ------------------------------------------------------------------ fake OctoPrint
def parse_multipart(content_type, body):
    """Yield (headers dict, content bytes) for each part of a multipart/form-data body."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        return
    delim = b"--" + m.group(1).encode()
    for part in body.split(delim)[1:]:
        if part.startswith(b"--"):
            break
        head, _, content = part.partition(b"\r\n\r\n")
        headers = {}
        for line in head.decode("latin-1").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if content.endswith(b"\r\n"):
            content = content[:-2]
        yield headers, content


class Handler(BaseHTTPRequestHandler):
    server_version = "OctoPrint"
    # HTTP/1.1 so "Expect: 100-continue" (libcurl uploads) is answered; every response sets Content-Length.
    protocol_version = "HTTP/1.1"

    def _read_body(self):
        """Request body, with or without Content-Length (libcurl may send chunked)."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass  # trailers
                    break
                parts.append(self.rfile.read(size))
                self.rfile.readline()  # CRLF after each chunk
            return b"".join(parts)
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def log_message(self, fmt, *args):
        log.info("http " + fmt, *args)

    def _json(self, code, obj, extra_headers=()):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _challenge(self):
        """Digest-auth clients (Orca's PrusaLink mode) first send an unauthenticated, bodyless
        request and expect 401 + a challenge; any credentials are then accepted. Returns True when
        the challenge was sent. API-key clients (X-Api-Key) are never challenged."""
        if "Authorization" in self.headers or "X-Api-Key" in self.headers:
            return False
        self._read_body()  # drain whatever was sent so the connection stays usable
        self._json(401, {"error": "authentication required"},
                   [("WWW-Authenticate", 'Digest realm="Printer API", nonce="%x", qop="auth"' % int(time.time()))])
        return True

    def _accept(self, filename, data, start_print):
        log.info("upload %s (%d bytes, print=%s)", filename, len(data), start_print)
        jobs.put(Job(filename, data, start_print))

    def do_GET(self):
        if self.path.startswith("/api/version"):
            # "OctoPrint" satisfies both Orca's OctoPrint and PrusaLink clients; upload-by-put makes
            # the PrusaLink client use PUT /api/v1/files/<storage>/<name> with the raw file.
            self._json(200, {"api": "0.1", "server": "1.10.0", "text": "OctoPrint 1.10.0 (USB bgcode helper)",
                             "capabilities": {"upload-by-put": True}})
        elif self.path.startswith("/api/v1/storage"):
            self._json(200, {"storage_list": [{"name": "USB drive", "path": "/usb", "type": "USB",
                                               "read_only": False, "available": True,
                                               "free_space": 64 * 1024 ** 3}]})
        else:
            self._json(404, {"error": "not found"})

    def do_PUT(self):
        # PrusaLink: PUT /api/v1/files/<storage>/<path...>, body = the G-code itself.
        if self._challenge():
            return
        if not self.path.startswith("/api/v1/files/"):
            return self._json(404, {"error": "not found"})
        body = self._read_body()
        name = urllib.parse.unquote(self.path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])
        log.info("PUT upload request: %d bytes, Transfer-Encoding=%s", len(body), self.headers.get("Transfer-Encoding"))
        if not body:
            return self._json(400, {"error": "no file"})
        self._accept(name, body, self.headers.get("Print-After-Upload", "").strip() in ("?1", "1", "true"))
        self._json(201, {"name": safe_name(name, body[:4] == BGCODE_MAGIC)[0]})

    def do_POST(self):
        # OctoPrint (and PrusaLink legacy): multipart form to /api/files/<location>.
        if self._challenge():
            return
        if not self.path.startswith("/api/files"):
            return self._json(404, {"error": "not found"})
        body = self._read_body()
        log.info("POST upload request: %d bytes, Content-Type=%s, Transfer-Encoding=%s, Expect=%s",
                 len(body), self.headers.get("Content-Type"), self.headers.get("Transfer-Encoding"),
                 self.headers.get("Expect"))
        filename, data, start_print = None, None, False
        for headers, content in parse_multipart(self.headers.get("Content-Type", ""), body):
            disp = headers.get("content-disposition", "")
            field = re.search(r'(?:^|;)\s*name="([^"]*)"', disp)
            field = field.group(1) if field else ""
            if field == "file":
                fn = re.search(r'filename="([^"]*)"', disp)
                filename = fn.group(1) if fn else "print.gcode"
                data = content
            elif field == "print":
                start_print = content.strip().lower() == b"true"
        if self.headers.get("Print-After-Upload", "").strip() in ("?1", "1", "true"):
            start_print = True
        if data is None:
            return self._json(400, {"error": "no file"})
        self._accept(filename, data, start_print)
        self._json(201, {"done": True, "files": {"local": {"name": safe_name(filename, data[:4] == BGCODE_MAGIC)[0], "origin": "local"}}})


# ------------------------------------------------------------------ main
def watch_orca(pid, root):
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        log.warning("cannot open Orca process %s; not watching", pid)
        return

    def poll():
        if kernel32.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
            log.info("Orca (pid %s) exited; shutting down", pid)
            root.quit()
        else:
            root.after(2000, poll)
    root.after(2000, poll)


def main():
    global jobs, win
    ap = argparse.ArgumentParser()
    ap.add_argument("--orca-pid", type=int)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--test-dir", help="write here instead of a USB drive and skip ejecting (testing)")
    args = ap.parse_args()
    if not args.orca_pid and os.environ.get("ORCA_USB_HELPER_PID", "").isdigit():
        args.orca_pid = int(os.environ["ORCA_USB_HELPER_PID"])  # set by the Orca plugin
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        log.info("port %s busy; another helper is already running", args.port)
        return
    log.info("listening on 127.0.0.1:%s (orca pid %s)", args.port, args.orca_pid)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    win = Window(root)
    jobs = queue.Queue()
    ui = queue.Queue()
    Worker(jobs, ui, args.test_dir).start()

    def pump():
        while True:
            try:
                ui.get_nowait()()
            except queue.Empty:
                break
        root.after(50, pump)
    pump()
    if args.orca_pid:
        watch_orca(args.orca_pid, root)
    root.mainloop()
    server.shutdown()
    log.info("stopped")


jobs = win = None
if __name__ == "__main__":
    main()
