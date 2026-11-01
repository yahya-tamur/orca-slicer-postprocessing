"""Prusa binary G-code (.bgcode) writer and reader, pure Python (stdlib only).

Format: libbgcode doc/specifications.md (github.com/prusa3d/libbgcode). Encodings mirror
libbgcode's binarize.cpp / meatpack.cpp and PrusaSlicer 2.9's defaults:
G-code blocks = MeatPack (comments kept) + Heatshrink 12/4, metadata = INI, checksum = CRC32.

    python bgcode.py in.gcode [out.bgcode]     convert
    python bgcode.py --dump file.bgcode        list blocks / metadata
    python bgcode.py --decode file.bgcode out.gcode
"""
import re
import struct
import sys
import zlib
from datetime import datetime, timezone

# ------------------------------------------------------------------ constants
MAGIC = b"GCDE"
VERSION = 1
CHECKSUM_CRC32 = 1

BLOCK_FILE_META, BLOCK_GCODE, BLOCK_SLICER_META, BLOCK_PRINTER_META, BLOCK_PRINT_META, BLOCK_THUMBNAIL = range(6)
COMP_NONE, COMP_DEFLATE, COMP_HS_11_4, COMP_HS_12_4 = range(4)
ENC_NONE, ENC_MEATPACK, ENC_MEATPACK_COMMENTS = range(3)
THUMB_PNG, THUMB_JPG, THUMB_QOI = range(3)

GCODE_BLOCK_SIZE = 65536  # libbgcode Binarizer::m_gcode_cache_size

# ------------------------------------------------------------------ heatshrink
def heatshrink_decompress(data, window_bits, lookahead_bits):
    out = bytearray()
    bit_pos, total_bits = 0, len(data) * 8

    def bits(n):
        nonlocal bit_pos
        if bit_pos + n > total_bits:
            return None
        v = 0
        for _ in range(n):
            v = (v << 1) | ((data[bit_pos >> 3] >> (7 - (bit_pos & 7))) & 1)
            bit_pos += 1
        return v

    while True:
        tag = bits(1)
        if tag is None:
            break
        if tag:
            b = bits(8)
            if b is None:
                break
            out.append(b)
        else:
            index = bits(window_bits)
            count = bits(lookahead_bits)
            if index is None or count is None:
                break
            offset, count = index + 1, count + 1
            # heatshrink's window starts zero-filled, so references before the data read zeros.
            for _ in range(count):
                pos = len(out) - offset
                out.append(out[pos] if pos >= 0 else 0)
    return bytes(out)


def heatshrink_compress(data, window_bits=12, lookahead_bits=4):
    """Greedy LZSS in heatshrink's bitstream format (MSB-first, zero-padded last byte)."""
    window = 1 << window_bits
    max_len = 1 << lookahead_bits
    # A back-reference costs 1+W+L bits; only worth it when it beats `n` literals (9 bits each).
    min_len = (1 + window_bits + lookahead_bits) // 9 + 1
    n = len(data)
    chains = {}  # 3-byte prefix -> recent positions (newest last)
    acc, acc_bits = 0, 0
    out = bytearray()
    i = 0
    while i < n:
        best_len, best_off = 0, 0
        if i + min_len <= n:
            key = data[i:i + 3]
            cand = chains.get(key)
            if cand:
                limit = min(max_len, n - i)
                for p in reversed(cand[-16:]):
                    off = i - p
                    if off > window:
                        break
                    length = 3
                    while length < limit and data[p + length] == data[i + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_off = length, off
                        if length == limit:
                            break
        if best_len >= min_len:
            acc = (acc << (1 + window_bits + lookahead_bits)) | ((best_off - 1) << lookahead_bits) | (best_len - 1)
            acc_bits += 1 + window_bits + lookahead_bits
            step = best_len
        else:
            acc = (acc << 9) | 0x100 | data[i]
            acc_bits += 9
            step = 1
        while acc_bits >= 8:
            acc_bits -= 8
            out.append((acc >> acc_bits) & 0xFF)
        acc &= (1 << acc_bits) - 1
        for j in range(i, min(i + step, n - 2)):
            k = data[j:j + 3]
            lst = chains.get(k)
            if lst is None:
                chains[k] = [j]
            else:
                lst.append(j)
                if len(lst) > 64:
                    del lst[:32]
        i += step
    if acc_bits:
        out.append((acc << (8 - acc_bits)) & 0xFF)
    return bytes(out)

# ------------------------------------------------------------------ MeatPack
_MP_SIGNAL = 0xFF
_MP_ENABLE, _MP_DISABLE, _MP_RESET, _MP_NOSPACES_ON, _MP_NOSPACES_OFF = 251, 250, 249, 247, 246
_MP_CODES = {c: i for i, c in enumerate("0123456789. \nGX")}
_MP_PACK = dict(_MP_CODES)  # no-spaces mode: 'E' takes the space code and ' ' is not packable
del _MP_PACK[" "]
_MP_PACK["E"] = _MP_CODES[" "]
_MP_FULL = 0b1111
_G_DIGIT = re.compile(r"G[0-9]")


def _mp_unify(line):
    """libbgcode's unified_method with OmitWhitespaces: only for lines with G<digit>."""
    g = line.find("G")
    if g == -1 or g + 1 >= len(line) or not line[g + 1].isdigit():
        return line
    result = line.replace("e", "E").replace("x", "X").replace("g", "G").replace(" ", "")
    if "*" in result:
        result = result.replace("*", "")
        cs = 0
        for ch in result.encode("latin-1"):
            cs ^= ch
        result += "*" + str(cs)
    return result + "\n"


def meatpack_encode(text):
    """MeatPackComments encoding of one G-code block (str with '\n' line ends)."""
    dst = bytearray((_MP_SIGNAL, _MP_SIGNAL, _MP_ENABLE, _MP_SIGNAL, _MP_SIGNAL, _MP_NOSPACES_ON))
    binarizing = True
    pack = _MP_PACK
    for line in text.splitlines(keepends=True):
        if not line:
            continue
        stripped = line.lstrip(" \t")
        if stripped.startswith(";"):
            if binarizing:
                dst += bytes((_MP_SIGNAL, _MP_SIGNAL, _MP_DISABLE))
                binarizing = False
            dst += line.encode("latin-1", "replace")
            continue
        if not stripped or stripped[0] in "\n\r" or len(line) < 2:
            continue
        semi = line.find(";")
        mod = (line if semi == -1 else line[:semi]).strip(" \t")
        if not mod:
            continue
        mod = _mp_unify(mod)
        if not mod.endswith("\n"):
            mod += "\n"
        tmp = bytearray()
        L = len(mod)
        for idx in range(0, L, 2):
            c1 = mod[idx]
            c2 = "\n" if idx == L - 1 else mod[idx + 1]
            v1, v2 = pack.get(c1), pack.get(c2)
            if v1 is not None:
                if v2 is not None:
                    tmp.append((v2 << 4) | v1)
                else:
                    tmp.append((_MP_FULL << 4) | v1)
                    tmp += c2.encode("latin-1", "replace")
            elif v2 is not None:
                tmp.append((v2 << 4) | _MP_FULL)
                tmp += c1.encode("latin-1", "replace")
            else:
                tmp.append(0xFF)
                tmp += (c1 + c2).encode("latin-1", "replace")
        if not binarizing and tmp:
            dst += bytes((_MP_SIGNAL, _MP_SIGNAL, _MP_ENABLE))
            binarizing = True
        dst += tmp
    return bytes(dst)


_GLINE_PARAMS = set("XYZEFIJRSGPWHCA")


def meatpack_decode(src):
    """Port of libbgcode MeatPack::unbinarize (re-inserts spaces into G lines)."""
    out = []
    unbinarizing = nospace = cmd_active = False
    cmd_count = 0
    char_buf = 0
    full_queue = 0
    pending = []

    def get_char(v):
        if v == 0b1011:
            return "E" if nospace else " "
        return "0123456789. \nGX"[v] if v < 15 else "\0"

    def rx(c):
        nonlocal full_queue, char_buf
        if not unbinarizing:
            pending.append(chr(c))
            return
        if full_queue > 0:
            pending.append(chr(c))
            if char_buf:
                pending.append(char_buf)
                char_buf = 0
            full_queue -= 1
            return
        lo, hi = c & 0xF, (c >> 4) & 0xF
        first_full, second_full = lo == 0xF, hi == 0xF
        if first_full:
            full_queue += 1
            if second_full:
                full_queue += 1
            else:
                char_buf = get_char(hi)
        else:
            ch = get_char(lo)
            pending.append(ch)
            if ch != "\n":
                if second_full:
                    full_queue += 1
                else:
                    pending.append(get_char(hi))

    add_space = False
    for c in src:
        if c == _MP_SIGNAL:
            if cmd_count > 0:
                cmd_active, cmd_count = True, 0
            else:
                cmd_count += 1
        else:
            if cmd_active:
                if c == _MP_ENABLE:
                    unbinarizing = True
                elif c in (_MP_DISABLE, _MP_RESET):
                    unbinarizing = False
                elif c == _MP_NOSPACES_ON:
                    nospace = True
                elif c == _MP_NOSPACES_OFF:
                    nospace = False
                cmd_active = False
            else:
                if cmd_count > 0:
                    rx(_MP_SIGNAL)
                    cmd_count = 0
                rx(c)
        for ch in pending:
            new_line = False
            prev = out[-1] if out else ""
            if ch == "G" and (not out or prev == "\n"):
                add_space = new_line = True
            elif ch == "\n":
                add_space = False
            if not new_line and add_space and prev != " " and ch in _GLINE_PARAMS:
                out.append(" ")
                prev = " "
            if ch != "\n" or not out or out[-1] != "\n":
                out.append(ch)
        pending.clear()
    return "".join(out)

# ------------------------------------------------------------------ block I/O
def _block(block_type, params, payload, compression=COMP_NONE, uncompressed_size=None):
    if compression == COMP_NONE:
        header = struct.pack("<HHI", block_type, compression, len(payload))
    else:
        header = struct.pack("<HHII", block_type, compression, uncompressed_size, len(payload))
    body = header + params + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def _compress(raw, compression):
    if compression == COMP_NONE:
        return raw
    if compression == COMP_DEFLATE:
        return zlib.compress(raw)
    if compression == COMP_HS_11_4:
        return heatshrink_compress(raw, 11, 4)
    if compression == COMP_HS_12_4:
        return heatshrink_compress(raw, 12, 4)
    raise ValueError(compression)


def _decompress(data, compression, size):
    if compression == COMP_NONE:
        return data
    if compression == COMP_DEFLATE:
        return zlib.decompress(data)
    if compression in (COMP_HS_11_4, COMP_HS_12_4):
        out = heatshrink_decompress(data, 11 if compression == COMP_HS_11_4 else 12, 4)
        return out[:size]
    raise ValueError(compression)


def metadata_block(block_type, pairs, compression=COMP_NONE):
    raw = "".join(f"{k}={v}\n" for k, v in pairs).encode("utf-8")
    return _block(block_type, struct.pack("<H", 0), _compress(raw, compression), compression, len(raw))


def thumbnail_block(fmt, width, height, data):
    return _block(BLOCK_THUMBNAIL, struct.pack("<HHH", fmt, width, height), data)


def gcode_block(text, compression=COMP_HS_12_4, encoding=ENC_MEATPACK_COMMENTS):
    raw = meatpack_encode(text) if encoding != ENC_NONE else text.encode("latin-1", "replace")
    return _block(BLOCK_GCODE, struct.pack("<H", encoding), _compress(raw, compression), compression, len(raw))


def read_blocks(buf):
    """Yield dicts for each block; verifies CRC32."""
    if buf[:4] != MAGIC:
        raise ValueError("not a bgcode file")
    version, checksum = struct.unpack_from("<IH", buf, 4)
    pos = 10
    while pos < len(buf):
        start = pos
        btype, comp, usize = struct.unpack_from("<HHI", buf, pos)
        pos += 8
        csize = usize
        if comp != COMP_NONE:
            (csize,) = struct.unpack_from("<I", buf, pos)
            pos += 4
        plen = 6 if btype == BLOCK_THUMBNAIL else 2
        params = buf[pos:pos + plen]
        pos += plen
        data = buf[pos:pos + csize]
        pos += csize
        crc_ok = None
        if checksum == CHECKSUM_CRC32:
            (crc,) = struct.unpack_from("<I", buf, pos)
            crc_ok = (zlib.crc32(buf[start:pos]) & 0xFFFFFFFF) == crc
            pos += 4
        yield {"type": btype, "compression": comp, "usize": usize, "csize": csize,
               "params": params, "data": data, "crc_ok": crc_ok}


def decode_block_payload(b):
    raw = _decompress(b["data"], b["compression"], b["usize"])
    if b["type"] == BLOCK_THUMBNAIL:
        return raw
    (enc,) = struct.unpack("<H", b["params"][:2])
    if b["type"] == BLOCK_GCODE:
        return meatpack_decode(raw) if enc in (ENC_MEATPACK, ENC_MEATPACK_COMMENTS) else raw.decode("latin-1")
    return raw.decode("latin-1")

# ------------------------------------------------------------------ ASCII G-code -> bgcode
_THUMB_BEGIN = re.compile(r"^; (thumbnail(?:_(QOI|JPG|PNG))?) begin (\d+)x(\d+) (\d+)")
_KV = re.compile(r"^; ([^=]+?) = (.*)$")
_STAT_KEYS = ("filament used [mm]", "filament used [cm3]", "filament used [g]", "filament cost",
              "total filament used [g]", "total filament cost", "total filament used for wipe tower [g]",
              "estimated printing time (normal mode)", "estimated first layer printing time (normal mode)",
              "estimated printing time (silent mode)", "estimated first layer printing time (silent mode)",
              "total layers count", "total toolchanges")
_PRINTER_STAT_KEYS = ("filament used [mm]", "filament used [cm3]", "filament used [g]", "filament cost",
                      "estimated printing time (normal mode)", "estimated printing time (silent mode)",
                      "total filament used for wipe tower [g]")
_PLATE_TEMP_KEY = {"cool plate": "cool_plate_temp", "engineering plate": "eng_plate_temp",
                   "high temp plate": "hot_plate_temp", "smooth pei plate": "hot_plate_temp",
                   "textured pei plate": "textured_plate_temp", "textured cool plate": "textured_cool_plate_temp",
                   "supertack plate": "supertack_plate_temp"}


def _split_ascii(text):
    """Separate thumbnails, config blocks, statistics and objects_info from the G-code body.

    Like PrusaSlicer's binary export, the body loses the "generated by" line and empty ";" lines."""
    thumbs, config, stats, body = [], {}, {}, []
    extra = {"json_config": None, "objects_info": None, "max_layer_z": None}
    lines = text.split("\n")
    i, n = 0, len(lines)
    in_config = False
    while i < n:
        line = lines[i]
        m = _THUMB_BEGIN.match(line)
        if m:
            fmt = {"QOI": THUMB_QOI, "JPG": THUMB_JPG}.get(m.group(2), THUMB_PNG)
            w, h = int(m.group(3)), int(m.group(4))
            b64 = []
            i += 1
            while i < n and not lines[i].startswith("; " + m.group(1) + " end"):
                b64.append(lines[i][2:] if lines[i].startswith("; ") else lines[i].lstrip(";"))
                i += 1
            import base64
            thumbs.append((fmt, w, h, base64.b64decode("".join(b64))))
            i += 1
            continue
        if line.startswith("; prusaslicer_json_config = begin"):
            js = []
            i += 1
            while i < n and not lines[i].startswith("; prusaslicer_json_config = end"):
                js.append(lines[i][2:] if lines[i].startswith("; ") else lines[i].lstrip(";"))
                i += 1
            extra["json_config"] = "\n".join(js)
            i += 1
            continue
        if line.startswith("; CONFIG_BLOCK_START") or line.startswith("; prusaslicer_config = begin"):
            in_config = True
            i += 1
            continue
        if line.startswith("; CONFIG_BLOCK_END") or line.startswith("; prusaslicer_config = end"):
            in_config = False
            i += 1
            continue
        kv = _KV.match(line)
        if in_config:
            if kv:
                config[kv.group(1)] = kv.group(2)
            i += 1
            continue
        if kv and kv.group(1) in _STAT_KEYS:  # moved into the metadata blocks, as PrusaSlicer does
            stats[kv.group(1)] = kv.group(2)
            i += 1
            continue
        if kv and kv.group(1) in ("objects_info", "max_layer_z"):
            extra[kv.group(1)] = kv.group(2)
            i += 1
            continue
        if line.strip() in (";", "; THUMBNAIL_BLOCK_START", "; THUMBNAIL_BLOCK_END") or line.startswith("; generated by "):
            i += 1
            continue
        body.append(line)
        i += 1
    return thumbs, config, stats, "\n".join(body), extra


def _first(config, *keys, default=""):
    for k in keys:
        v = config.get(k)
        if v not in (None, ""):
            return v.split(";")[0].split(",")[0].strip().strip('"')
    return default


def _printer_metadata(config, stats, body, extra):
    """PrusaSlicer's printer-metadata keys, derived from Orca (or PrusaSlicer) config + G-code."""
    m = re.search(r'M862\.3 P\s*"?([A-Za-z0-9_]+)"?', body)
    model = m.group(1) if m else _first(config, "printer_model")
    m = re.search(r"M862\.1 P[\d.]+.*?F(\d)", body)
    high_flow = m.group(1) if m else ("1" if "HF_NOZZLE" in config.get("printer_notes", "") else "0")
    plate_key = _PLATE_TEMP_KEY.get(_first(config, "curr_bed_type").lower(), "hot_plate_temp")
    ironing = config.get("ironing", config.get("ironing_type", "0")).strip('"')
    # Top of the print, ignoring travel Z-hops: the end-gcode "; max_layer_z" line, Orca's header
    # value, else the last ;Z: layer marker.
    m = re.search(r"^; max_z_height: ([\d.]+)", body, re.M)
    zs = ([extra["max_layer_z"]] if extra.get("max_layer_z") else [m.group(1)] if m
          else re.findall(r"^;Z:([\d.]+)", body, re.M))
    pairs = [
        ("printer_model", model),
        ("filament_type", _first(config, "filament_type")),
        ("filament_abrasive", _first(config, "filament_abrasive", default="0")),
        ("nozzle_diameter", _first(config, "nozzle_diameter")),
        ("nozzle_high_flow", high_flow),
        ("bed_temperature", _first(config, "bed_temperature", plate_key, "hot_plate_temp")),
        ("brim_width", _first(config, "brim_width", default="0")),
        ("fill_density", _first(config, "fill_density", "sparse_infill_density")),
        ("layer_height", _first(config, "layer_height")),
        ("temperature", _first(config, "temperature", "nozzle_temperature")),
        ("ironing", "0" if ironing in ("0", "false", "no ironing", "") else "1"),
        ("support_material", _first(config, "support_material", "enable_support", default="0")),
    ]
    if zs:
        pairs.append(("max_layer_z", f"{max(float(z) for z in zs):.2f}".rstrip("0").rstrip(".")))
    pairs.append(("extruder_colour", _first(config, "extruder_colour", "filament_colour") or '""'))
    pairs += [(k, stats[k]) for k in _PRINTER_STAT_KEYS if k in stats]
    if extra.get("objects_info"):
        pairs.append(("objects_info", extra["objects_info"]))
    return pairs


def convert(text, producer="OrcaSlicer", progress=None):
    """ASCII G-code (str) -> bgcode bytes."""
    text = text.replace("\r\n", "\n")
    thumbs, config, stats, body, extra = _split_ascii(text)
    m = re.search(r"generated by (\S+ \S+)", text[:2000])
    if m:
        producer = m.group(1)
    out = bytearray(MAGIC + struct.pack("<IH", VERSION, CHECKSUM_CRC32))
    out += metadata_block(BLOCK_FILE_META, [
        ("Producer", producer + " (converted by bgcode.py)"),
        ("Produced on", datetime.now(timezone.utc).strftime("%Y-%m-%d at %H:%M:%S UTC")),
    ])
    if "binary_gcode" in config:
        config["binary_gcode"] = "1"
    out += metadata_block(BLOCK_PRINTER_META, _printer_metadata(config, stats, body, extra))
    for fmt, w, h, data in thumbs:
        out += thumbnail_block(fmt, w, h, data)
    out += metadata_block(BLOCK_PRINT_META, [(k, v) for k, v in stats.items()], COMP_DEFLATE)
    out += metadata_block(BLOCK_SLICER_META, sorted(config.items()), COMP_DEFLATE)
    if extra.get("json_config"):  # PrusaSlicer 3: second slicer block, JSON-encoded, uncompressed
        js = extra["json_config"].encode("latin-1", "replace")
        out += _block(BLOCK_SLICER_META, struct.pack("<H", 1), js, COMP_NONE, len(js))
    # G-code blocks: whole lines, at most GCODE_BLOCK_SIZE characters each (libbgcode Binarizer).
    if not body.endswith("\n"):
        body += "\n"
    chunk, size, total, done = [], 0, len(body), 0
    for line in body.splitlines(keepends=True):
        if size + len(line) > GCODE_BLOCK_SIZE and chunk:
            out += gcode_block("".join(chunk))
            done += size
            if progress:
                progress(done / total)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        out += gcode_block("".join(chunk))
    if progress:
        progress(1.0)
    return bytes(out)


# ------------------------------------------------------------------ CLI
def _dump(buf):
    names = ["file-meta", "gcode", "slicer-meta", "printer-meta", "print-meta", "thumbnail"]
    counts = {}
    for b in read_blocks(buf):
        counts[b["type"]] = counts.get(b["type"], 0) + 1
        if b["type"] == BLOCK_GCODE and counts[b["type"]] > 2:
            continue
        line = f"{names[b['type']]:12} comp={b['compression']} usize={b['usize']} csize={b['csize']} crc_ok={b['crc_ok']}"
        if b["type"] == BLOCK_THUMBNAIL:
            line += " fmt=%d %dx%d" % struct.unpack("<HHH", b["params"])
        elif b["type"] == BLOCK_GCODE:
            line += " enc=%d" % struct.unpack("<H", b["params"])
        print(line)
        if b["type"] in (BLOCK_FILE_META, BLOCK_PRINTER_META, BLOCK_PRINT_META):
            for kv in decode_block_payload(b).splitlines():
                print("    " + kv)
    print("block counts:", {names[k]: v for k, v in counts.items()})


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--dump":
        _dump(open(a[1], "rb").read())
    elif a and a[0] == "--decode":
        bufd = open(a[1], "rb").read()
        with open(a[2], "w", encoding="latin-1", newline="\n") as f:
            for b in read_blocks(bufd):
                if b["type"] == BLOCK_GCODE:
                    f.write(decode_block_payload(b))
    elif a:
        src = a[0]
        dst = a[1] if len(a) > 1 else re.sub(r"\.gcode$", "", src, flags=re.I) + ".bgcode"
        data = convert(open(src, encoding="latin-1").read())
        open(dst, "wb").write(data)
        print(f"wrote {dst} ({len(data)} bytes)")
    else:
        print(__doc__)
