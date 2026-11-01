"""Convert an ASCII G-code file to Prusa binary G-code in place.

Run by the "USB bgcode export" Orca plugin on Export G-code, when the chosen name ends in .bgcode:

    pythonw convert_in_place.py          (file path in the BGCODE_IN_PLACE environment variable)

The path goes through the environment, not the arguments, so the plugin's command line never
changes and Orca's one-time permission for it stays valid. Exit code 0 = converted (or already
binary), 1 = error (details in %LOCALAPPDATA%\\OrcaUsbBgcode\\helper.log).
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "converter"))
import bgcode  # noqa: E402

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "OrcaUsbBgcode")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR, "helper.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s [export] %(message)s")
log = logging.getLogger("convert_in_place")


def main():
    path = os.environ.get("BGCODE_IN_PLACE", "")
    if not path or not os.path.isfile(path):
        log.error("no input file (BGCODE_IN_PLACE=%r)", path)
        return 1
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] == bgcode.MAGIC:
        log.info("%s is already binary G-code", path)
        return 0
    data = bgcode.convert(raw.decode("latin-1"))
    tmp = path + ".bgtmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    log.info("converted %s in place: %d -> %d bytes", path, len(raw), len(data))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("conversion failed")
        sys.exit(1)
