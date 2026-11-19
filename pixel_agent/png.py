"""Tiny PNG encoder/decoder (stdlib only).

The driver captures 32-bit BGRA rows from GDI; models want PNG. Decoding is
only needed by tests and the driver self-test, so it supports just what the
encoder writes (8-bit RGB / RGBA, no interlace) plus the five row filters.
"""
import struct
import zlib


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def bgra_to_rgb(buf, width, height):
    """GDI top-down BGRA bytes -> packed RGB bytes."""
    n = width * height
    out = bytearray(n * 3)
    mv = memoryview(buf)
    out[0::3] = mv[2::4]
    out[1::3] = mv[1::4]
    out[2::3] = mv[0::4]
    return bytes(out)


def encode_rgb(rgb, width, height, level=6):
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b""))


def encode_bgra(buf, width, height, level=6):
    return encode_rgb(bgra_to_rgb(buf, width, height), width, height, level)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data):
    """PNG bytes -> (width, height, channels, pixel bytes). 8-bit RGB/RGBA only."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError("unsupported PNG variant")
            channels = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    i = 0
    for y in range(height):
        ftype = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ftype == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                up_left = prev[x - channels] if x >= channels else 0
                line[x] = (line[x] + _paeth(left, prev[x], up_left)) & 0xFF
        elif ftype != 0:
            raise ValueError(f"bad filter {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


def size_of(data):
    """(width, height) from PNG header without decoding."""
    return struct.unpack(">II", data[16:24])
