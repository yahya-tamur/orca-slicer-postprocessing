"""Checks bgcode.py against a real PrusaSlicer .bgcode and round-trips our own output.

    python verify.py reference.bgcode            encoder vs PrusaSlicer's blocks
    python verify.py --roundtrip in.gcode         convert, then decode and compare
"""
import re
import sys
import time

import bgcode as bg


def against_reference(path, max_blocks=60):
    buf = open(path, "rb").read()
    blocks = [b for b in bg.read_blocks(buf) if b["type"] == bg.BLOCK_GCODE][:max_blocks]
    mp_same = hs_ok = 0
    ours_c = theirs_c = 0
    for b in blocks:
        raw = bg._decompress(b["data"], b["compression"], b["usize"])  # Prusa's MeatPack bytes
        text = bg.meatpack_decode(raw)
        if bg.meatpack_encode(text) == raw:
            mp_same += 1
        comp = bg.heatshrink_compress(raw, 12, 4)
        if bg.heatshrink_decompress(comp, 12, 4) == raw:
            hs_ok += 1
        ours_c += len(comp)
        theirs_c += b["csize"]
    n = len(blocks)
    print(f"MeatPack re-encode identical to PrusaSlicer: {mp_same}/{n} blocks")
    print(f"heatshrink round-trip exact:                {hs_ok}/{n} blocks")
    print(f"heatshrink size ours/PrusaSlicer:           {ours_c}/{theirs_c} = {ours_c / theirs_c:.3f}")


def _normalise(text):
    """What MeatPackComments keeps: full-line comments verbatim, other lines without inline
    comments, G<digit> lines without spaces and upper-cased e/x/g, no blank lines."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.lstrip(" \t")
        if s.startswith(";"):
            out.append(line)
            continue
        if not s:
            continue
        code = line.split(";", 1)[0].strip(" \t")
        if not code:
            continue
        g = code.find("G")
        if g != -1 and g + 1 < len(code) and code[g + 1].isdigit():
            code = code.replace("e", "E").replace("x", "X").replace("g", "G").replace(" ", "")
        out.append(code)
    return out


def roundtrip(path):
    text = open(path, encoding="latin-1").read()
    t0 = time.time()
    data = bg.convert(text)
    t1 = time.time()
    crc_ok = all(b["crc_ok"] for b in bg.read_blocks(data))
    decoded = "".join(bg.decode_block_payload(b) for b in bg.read_blocks(data) if b["type"] == bg.BLOCK_GCODE)
    _, _, _, body, _ = bg._split_ascii(text.replace("\r\n", "\n"))
    want = _normalise(body)
    got = [l.replace(" ", "") if re.match(r"^G\d", l) else l for l in decoded.split("\n") if l != ""]
    want = [l for l in want if l != ""]
    same = got == want
    print(f"{path}: {len(text)} -> {len(data)} bytes ({len(data) / len(text):.1%}) in {t1 - t0:.1f}s, "
          f"CRCs ok={crc_ok}, G-code identical after normalisation={same}")
    if not same:
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                print("first difference at line", i, repr(a), repr(b))
                break
        print("line counts", len(want), len(got))
    for b in bg.read_blocks(data):
        if b["type"] in (bg.BLOCK_PRINTER_META,):
            print(bg.decode_block_payload(b))
        if b["type"] == bg.BLOCK_THUMBNAIL:
            print("thumbnail", b["params"].hex(), b["usize"], "bytes")
    return same and crc_ok


if __name__ == "__main__":
    if sys.argv[1] == "--roundtrip":
        sys.exit(0 if roundtrip(sys.argv[2]) else 1)
    against_reference(sys.argv[1])
