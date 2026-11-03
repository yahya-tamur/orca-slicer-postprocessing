# Orca Slicer → Prusa binary G-code and USB

An Orca Slicer plugin for Prusa printers (made for the CORE One) on Windows. It gives Orca what PrusaSlicer has for these printers:

- **Binary G-code.** Orca can't write Prusa's binary G-code (`.bgcode`), which is smaller and loads faster on the printer. The plugin converts after slicing, with the same result PrusaSlicer would produce.
- **Print straight to a USB stick.** Orca's Print button hands the file to a small helper on your PC, which writes it to a USB stick and ejects it, then tells you when to unplug. It can also send the file to the printer over the network (PrusaLink) and start the print.
- **Camera (optional).** Adds a live camera view to PrusaLink's page in Orca's Device tab.

To get plain G-code for a while, untick **Export as bgcode** under Process → Others → Slicing Pipeline Plugin. It's ticked again when you open a project or change the process preset.

## Installing

You need Windows, Orca Slicer 2.5 or newer, Python 3 (standard library only), and ffmpeg for the camera.

1. **Get the code.** Clone this repository somewhere permanent.
2. **Install the plugin.** In Orca's **Plugins** dialog, click the arrow next to **Install plugin**, choose **Install local plugin**, and pick `orca_plugin/usb_bgcode_export.py` from the repository. Then tick **Activate** for **USB bgcode export**.
3. **Fill in its settings.** With **USB bgcode export** selected in the Plugins dialog, choose **Config** in the menu at the bottom, set these values in the JSON, and click **Save**. Write paths with `/` or `\\`.

   | Setting | Value |
   |---|---|
   | `camera` | optional: the camera's RTSP address (step 6) |
   | `ffmpeg` | optional: the path to `ffmpeg.exe`, if it isn't on the PATH |
   | `printer` | the printer's address, for example `http://192.168.1.40` (on the printer under Settings → Network). USB still works without this setting. |
   | `printer_preset` | your printer's name in Orca's printer list |
   | `project` | the folder you cloned into, for example `C:/Users/you/orca_slicer_to_prusa_usb` |
   | `pythonw` | your `pythonw.exe` (variables like `%LOCALAPPDATA%` work) |

4. **Restart Orca** and click **Allow** when it asks to run the helper and the converter.
5. **Connect the printer to the helper.** Click **Connection** above the printer preset to open the **Physical Printer** dialog, and set:

   | Field | Value |
   |---|---|
   | **Host Type** | `Octo/Klipper` |
   | **Hostname, IP or URL** | `http://127.0.0.1:51200` |
   | **Device UI** | `http://127.0.0.1:51201` |
   | **API Key / Password** | the printer's PrusaLink password (on the printer under Settings → Network → PrusaLink) |

6. **Camera (optional).** For a Prusa Buddy3D camera: turn on its RTSP stream (in the Prusa app or on its Prusa Connect page), set its resolution to **HD** (Full HD sends no RTSP video), and set `camera` in the plugin's settings to its address, for example `rtsp://192.168.1.50/live`.
7. **Name files `.bgcode`.** Change the **Filename format** under **Others** in your process presets to end with `.bgcode`.

Restart Orca after changing the plugin's settings or the Physical Printer dialog. If something doesn't work, the helper's log is `%LOCALAPPDATA%\OrcaUsbBgcode\helper.log`.

## License

AGPL-3.0 (see `LICENSE`). The converter follows Prusa's [libbgcode](https://github.com/prusa3d/libbgcode), which has the same license.
