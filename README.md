# Orca Slicer → Prusa binary G-code and USB

An Orca Slicer plugin for Prusa printers (made for the CORE One) on Windows:

- **Binary G-code.** Orca can't write Prusa's binary G-code (`.bgcode`), which is smaller and loads faster on the printer. The plugin converts Orca's G-code to `.bgcode` after slicing, with the same result PrusaSlicer would produce.
- **Print button → USB stick.** Orca's Print / Upload button sends the file to a small helper on your PC instead of a networked printer. The helper waits for a USB stick, writes the file to it, ejects the stick and tells you when to unplug it. Nothing is left on the PC.

What you'll see in Orca:

| | Result |
|---|---|
| **Export G-code**, file name ends in `.bgcode` | binary G-code |
| **Export G-code**, any other name | plain G-code, unchanged |
| **Print / Upload** | binary G-code, written to a USB stick |

To get plain G-code for a while, untick **Export as bgcode** under Process → Others → Slicing Pipeline Plugin; Print then writes plain `.gcode` to the stick. The plugin ticks it again when a project is opened or the process preset changes, so projects and presets never need editing.

The helper's window also handles:

- several uploads in a row: they all go on the stick, with a single eject
- name clashes: Overwrite, New file or Cancel
- file names whose extension doesn't match the file type
- **Send via PrusaLink:** sends to the printer over the network instead (not yet tested with a real printer)

## Installing

Needs Windows, Orca Slicer 2.5 or newer (for its Python plugins) and Python 3. Only the Python standard library is used.

1. **Get the code.** Clone this repository somewhere permanent.
2. **Point the plugin at it.** Near the top of `orca_plugin/usb_bgcode_export/usb_bgcode_export.py`, set `PROJECT` to the folder you cloned into and `PYTHONW` to your `pythonw.exe`.
3. **Install the plugin.** With Orca closed, copy the `orca_plugin/usb_bgcode_export` folder into `%APPDATA%\OrcaSlicer\orca_plugins\`.
4. **Enable it.** Start Orca, enable **USB bgcode export** in the Plugins dialog, and click **Allow** when Orca asks whether the plugin may run the helper and the converter.
5. **Set up the printer.** In the printer's connection settings, set host type **OctoPrint**, address `127.0.0.1:51200`, and any API key. To get `.bgcode` names by default, end the process preset's file name format in `.bgcode`.

The helper starts and stops with Orca. Its log is `%LOCALAPPDATA%\OrcaUsbBgcode\helper.log`.

The converter also works on its own:

```
python converter/bgcode.py print.gcode [print.bgcode]
```

## Details

- **Converter:** written in pure Python from Prusa's [libbgcode](https://github.com/prusa3d/libbgcode) specification and source (AGPL-3.0). Its MeatPack encoding follows libbgcode closely, to stay byte-compatible with PrusaSlicer. On a print exported from both slicers, the G-code, metadata and thumbnails came out identical to PrusaSlicer's `.bgcode`; only the compressed bytes and the timestamp differ. `converter/verify.py` and `converter/compare.py` run those checks.
- **Ejecting:** the stick is ejected silently through the same Windows call as "Safely Remove Hardware". On USB ports that Windows doesn't treat as removable, the drive is locked, dismounted and its media ejected instead, as Explorer does. If something still has the stick open, the window says it's in use and keeps retrying until it's free.
- **Plugin API:** learned from the examples in [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) `v2.5.0-beta` (`sandboxes/`).
- **Not in the repository:** the libbgcode reference sources and the G-code test files are kept locally only (see `.gitignore`).
