# Orca Slicer → Prusa binary G-code and USB

An Orca Slicer plugin for Prusa printers (made for the CORE One) on Windows. It gives Orca what PrusaSlicer has for these printers:

- **Binary G-code.** Orca can't write Prusa's binary G-code (`.bgcode`), which is smaller and loads faster on the printer. The plugin converts after slicing, with the same result PrusaSlicer would produce.
- **Print straight to a USB stick.** You can plug in a USB stick before or after clicking Print. The helper writes the file, ejects the stick, and tells you when it's safe to take it out. It can also send the file over PrusaLink and start the print.
- **Camera (optional).** Adds a live camera view to PrusaLink's page in Orca's Device tab, and optionally on other devices on your network.

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
   | `share_to_local_network` | optional, default `false`: `true` to open the printer page with the camera from other devices too, at `http://<this PC's address>:51201`. **There's no password:** the helper logs in for every visitor, so anyone on your local network can start, stop and delete prints, bypassing the PrusaLink password. Windows asks once whether to allow Python on the network. |

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
