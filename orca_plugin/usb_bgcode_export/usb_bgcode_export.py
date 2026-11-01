# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "USB bgcode export"
# description = "Converts G-code to Prusa .bgcode on Export (for .bgcode names) and on Print/upload; the Print button's helper writes the file to a USB drive and ejects it."
# author = "local"
# version = "0.4.0"
# ///
"""Two capabilities:

* "Export as bgcode" (slicing pipeline, post-process step) does all conversion to binary G-code,
  in place on Orca's working copy via helper/convert_in_place.py:
    - Export G-code ("File"): only when the chosen name ends in .bgcode;
    - Print / upload (any other host): always.
  It only runs while ticked in the process settings (Others > Slicing Pipeline Plugin). Unticked,
  exports and uploads stay plain G-code, and the helper then writes .gcode to the USB drive.
* "USB bgcode helper" (script):
    - starts helper/usb_print_host.py (system Python, no console) when Orca loads the plugin. The
      helper is the Print button's host (127.0.0.1:51200) and only does the USB drive part. It gets
      Orca's pid via ORCA_USB_HELPER_PID and quits when Orca exits;
    - ticks "Export as bgcode" in the edited process settings (in memory, never written to a project
      or preset file) when a project is opened or created and when a process preset is selected,
      so older projects that saved an empty plugin list still convert. Unticking it by hand lasts
      until the next of those events.
  Running it from the Plugins dialog restarts the helper.

Orca audits subprocess.Popen against the exact command line, so both command lines are fixed (pid
and file path go in the environment). They and thread creation (preset edits must come from a
background thread) are requested once via orca.request_permissions().
"""
import os
import subprocess
import threading
import time

import orca

# Built from the environment, so the same file works for any Windows user. The expanded strings must
# stay identical between runs: Orca's process permission is saved per exact command line.
PROJECT = os.path.join(os.environ["USERPROFILE"], "Desktop", "claude environemnt", "orca slicer postprocessing")
PYTHONW = os.path.join(os.environ["LOCALAPPDATA"], "Python", "pythoncore-3.14-64", "pythonw.exe")
HELPER_COMMAND = [PYTHONW, os.path.join(PROJECT, "helper", "usb_print_host.py")]
CONVERT_COMMAND = [PYTHONW, os.path.join(PROJECT, "helper", "convert_in_place.py")]
CREATE_NO_WINDOW = 0x08000000

PIPELINE_CAPABILITY = "Export as bgcode"
# Manifest ref "plugin_key;cloud_uuid;capability": empty uuid for a local plugin (key = .py file stem).
PLUGIN_REF = "usb_bgcode_export;;" + PIPELINE_CAPABILITY


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _with_entry(serialized, entry):
    """Append entry to a serialized string list ("a";"b") unless it is already there."""
    serialized = serialized or ""
    if _quoted(entry) in serialized or serialized == entry:
        return serialized
    return serialized + ";" + _quoted(entry) if serialized else _quoted(entry)


class UsbBgcodeHelper(orca.script.ScriptPluginCapabilityBase):
    proc = None
    last_error = ""
    _ensure_lock = threading.Lock()

    def get_name(self):
        return "USB bgcode helper"

    # ---- helper process
    def _start(self):
        if self.proc is not None and self.proc.poll() is None:
            return "already running"
        env = dict(os.environ, ORCA_USB_HELPER_PID=str(os.getpid()))
        self.proc = subprocess.Popen(HELPER_COMMAND, env=env, creationflags=CREATE_NO_WINDOW, close_fds=True)
        return "started"

    def _stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    # ---- keep "Export as bgcode" ticked in the edited process settings
    def _ensure_export_plugin(self, delay=0.0):
        if delay:
            time.sleep(delay)
        if not self._ensure_lock.acquire(blocking=False):
            return  # another check is already running
        try:
            preset = orca.host.preset_bundle().current_process_preset()
            caps = preset.config_value("slicing_pipeline_plugin")
            refs = preset.config_value("plugins")
            new_caps, new_refs = _with_entry(caps, PIPELINE_CAPABILITY), _with_entry(refs, PLUGIN_REF)
            if new_caps == (caps or "") and new_refs == (refs or ""):
                return
            tx = orca.host.preset.process.begin_update()
            tx.set("slicing_pipeline_plugin", new_caps)
            tx.set("plugins", new_refs)
            tx.commit()
            print("USB bgcode export: ticked 'Export as bgcode' for", getattr(preset, "name", "the process preset"))
        except Exception as e:
            print("USB bgcode export: could not tick 'Export as bgcode':", f"{type(e).__name__}: {e}")
        finally:
            self._ensure_lock.release()

    def _ensure_later(self, delay=0.0):
        # Preset edits block until the UI thread applies them, so they must run off the UI thread.
        threading.Thread(target=self._ensure_export_plugin, args=(delay,), daemon=True).start()

    # ---- capability hooks (never let an error escape: a failing hook can block Orca)
    def on_load(self):
        try:
            self._start()
            self.last_error = ""
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            print("USB bgcode helper could not start:", self.last_error)
        try:
            self._ensure_later(delay=5.0)  # the project/preset opened at startup may predate this load
        except Exception as e:
            print("USB bgcode export: startup check failed:", e)

    def on_unload(self):
        try:
            self._stop()
        except Exception as e:
            print("USB bgcode helper stop failed:", e)

    def on_lifecycle_event(self, event, ctx):
        try:
            if event in (orca.LifecycleEvent.ProjectOpened, orca.LifecycleEvent.NewProject) or (
                    event == orca.LifecycleEvent.PresetSelected and ctx.msg == "print"):
                self._ensure_later()
        except Exception as e:
            print("USB bgcode export: lifecycle hook failed:", e)

    def execute(self):
        try:
            self._stop()
            state = self._start()
        except Exception as e:
            return orca.ExecutionResult.success(f"USB bgcode helper could not start: {type(e).__name__}: {e}")
        return orca.ExecutionResult.success("USB bgcode helper " + state + ".")


class ExportAsBgcode(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return PIPELINE_CAPABILITY

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess or not ctx.gcode_path:
            return orca.ExecutionResult.success()
        if ctx.host == "File" and not (ctx.output_name or "").lower().endswith(".bgcode"):
            return orca.ExecutionResult.success()  # Export G-code to a .gcode (or other) name: keep text
        env = dict(os.environ, BGCODE_IN_PLACE=ctx.gcode_path)
        result = subprocess.run(CONVERT_COMMAND, env=env, creationflags=CREATE_NO_WINDOW, timeout=600)
        if result.returncode != 0:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "Converting to .bgcode failed; see %LOCALAPPDATA%\\OrcaUsbBgcode\\helper.log")
        return orca.ExecutionResult.success("Converted to binary G-code: " + os.path.basename(ctx.output_name or ""))


@orca.plugin
class UsbBgcodeExport(orca.base):
    def register_capabilities(self):
        # Asked once by Orca after registration; grants are saved for these exact command lines.
        orca.request_permissions(process=[subprocess.list2cmdline(HELPER_COMMAND),
                                          subprocess.list2cmdline(CONVERT_COMMAND)],
                                 threading=True)
        orca.register_capability(UsbBgcodeHelper)
        orca.register_capability(ExportAsBgcode)
