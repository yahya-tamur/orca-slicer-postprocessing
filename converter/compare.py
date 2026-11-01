"""Compare our conversion of an ASCII G-code with PrusaSlicer's own .bgcode of the same print.

    python compare.py prusaslicer.bgcode same_print.gcode

Reports block layout, metadata differences, thumbnails, and whether the decoded G-code matches.
Compressed bytes are not compared: two valid Heatshrink encoders can produce different bytes.
"""
import difflib
import struct
import sys

import bgcode as bg

NAMES = ["file-meta", "gcode", "slicer-meta", "printer-meta", "print-meta", "thumbnail"]


def summary(buf):
    blocks = list(bg.read_blocks(buf))
    meta, thumbs, gcode = {}, [], []
    for b in blocks:
        t = b["type"]
        if t == bg.BLOCK_GCODE:
            gcode.append(bg.decode_block_payload(b))
        elif t == bg.BLOCK_THUMBNAIL:
            thumbs.append((struct.unpack("<HHH", b["params"]), bg.decode_block_payload(b)))
        elif t == bg.BLOCK_SLICER_META and struct.unpack("<H", b["params"])[0] == 1:
            meta["json"] = bg.decode_block_payload(b)  # PrusaSlicer 3 JSON slicer block
        else:
            pairs = [l.split("=", 1) for l in bg.decode_block_payload(b).splitlines() if "=" in l]
            meta[t] = {"pairs": pairs, "compression": b["compression"]}
    layout = []
    for b in blocks:
        name = NAMES[b["type"]]
        if layout and layout[-1][0] == name and name == "gcode":
            layout[-1][1] += 1
        else:
            layout.append([name, 1])
    return {"blocks": blocks, "meta": meta, "thumbs": thumbs, "gcode": "".join(gcode), "layout": layout,
            "crc_ok": all(b["crc_ok"] for b in blocks)}


def gcode_lines(text):
    """Decoded G-code, ignoring blank lines and spacing inside G lines."""
    out = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        out.append(line.replace(" ", "") if line[:1] == "G" and line[1:2].isdigit() else line)
    return out


def main(ref_path, gcode_path):
    ref = summary(open(ref_path, "rb").read())
    ours_bytes = bg.convert(open(gcode_path, encoding="latin-1").read())
    ours = summary(ours_bytes)
    print(f"size: PrusaSlicer {len(open(ref_path, 'rb').read())} bytes, ours {len(ours_bytes)} bytes")
    print("CRCs ok:", ref["crc_ok"], ours["crc_ok"])
    print("layout PrusaSlicer:", ref["layout"])
    print("layout ours:       ", ours["layout"])

    for t in (bg.BLOCK_FILE_META, bg.BLOCK_PRINTER_META, bg.BLOCK_PRINT_META, bg.BLOCK_SLICER_META):
        r, o = ref["meta"].get(t), ours["meta"].get(t)
        print(f"\n== {NAMES[t]} (compression {r and r['compression']} vs {o and o['compression']})")
        if not r or not o:
            print("   missing in", "PrusaSlicer" if not r else "ours")
            continue
        rd, od = dict(r["pairs"]), dict(o["pairs"])
        if t == bg.BLOCK_SLICER_META:
            same = sum(1 for k in rd if od.get(k) == rd[k])
            print(f"   {len(rd)} keys vs {len(od)}; identical values: {same}")
            diff = [k for k in rd if k in od and od[k] != rd[k]]
            print("   differing:", diff[:15], "..." if len(diff) > 15 else "")
            print("   only PrusaSlicer:", sorted(set(rd) - set(od))[:15])
            print("   only ours:", sorted(set(od) - set(rd))[:15])
            continue
        print("   key order same:", [k for k, _ in r["pairs"]] == [k for k, _ in o["pairs"]])
        for k in dict.fromkeys(list(rd) + list(od)):
            a, b = rd.get(k, "<missing>"), od.get(k, "<missing>")
            mark = "  " if a == b else "!!"
            if k == "objects_info":
                a, b = a[:60] + "...", b[:60] + "..."
            print(f"   {mark} {k}: {a}" + ("" if a == b else f"   | ours: {b}"))

    if "json" in ref["meta"] or "json" in ours["meta"]:
        import json
        rj, oj = ref["meta"].get("json"), ours["meta"].get("json")
        same = rj is not None and oj is not None and json.loads(rj).get("config") == json.loads(oj).get("config")
        print(f"\n== slicer-meta JSON: present {rj is not None}/{oj is not None}, same config: {same}")

    print("\n== thumbnails")
    print("   PrusaSlicer:", [p for p, _ in ref["thumbs"]])
    print("   ours:       ", [p for p, _ in ours["thumbs"]])
    print("   identical image data:", [d1 == d2 for (_, d1), (_, d2) in zip(ref["thumbs"], ours["thumbs"])])

    rl, ol = gcode_lines(ref["gcode"]), gcode_lines(ours["gcode"])
    print(f"\n== G-code: {len(rl)} lines vs {len(ol)} lines, identical: {rl == ol}")
    if rl != ol:
        diff = list(difflib.unified_diff(rl, ol, "PrusaSlicer", "ours", n=1, lineterm=""))
        print(f"   {sum(1 for d in diff if d[:1] in '+-' and d[:3] not in ('---', '+++'))} differing lines; first hunks:")
        for d in diff[:60]:
            print("   " + d)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
