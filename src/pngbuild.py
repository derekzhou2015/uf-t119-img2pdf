#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handcrafted PNG builder for offline behaviour census.

Supports all PNG colour types / bit depths relevant here, optional Adam7
interlace, tRNS chunks and iCCP-free plain output. Pure stdlib + Pillow is
only used for *reading* the produced files, never for producing them.
"""
import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# (start_x, start_y, x_step, y_step, block_w, block_h)
ADAM7 = (
    (0, 0, 8, 8, 8, 8),
    (4, 0, 8, 8, 4, 8),
    (0, 4, 4, 8, 4, 4),
    (2, 0, 4, 4, 2, 4),
    (0, 2, 2, 4, 2, 2),
    (1, 0, 2, 2, 1, 2),
    (0, 1, 1, 2, 1, 1),
)

# colour type -> (channels, allowed depths)
COLOUR_TYPES = {
    0: (1, (1, 2, 4, 8, 16)),      # grayscale
    2: (3, (8, 16)),               # RGB
    3: (1, (1, 2, 4, 8)),          # indexed
    4: (2, (8, 16)),               # grayscale + alpha
    6: (4, (8, 16)),               # RGBA
}


def _chunk(typ, data):
    return (
        struct.pack(">I", len(data))
        + typ
        + data
        + struct.pack(">I", zlib.crc32(typ + data))
    )


def _passes(width, height):
    """Yield (pass_index, sub_width, sub_height, x0, y0, dx, dy)."""
    for idx, (x0, y0, dx, dy, bw, bh) in enumerate(ADAM7):
        sw = (width - x0 + dx - 1) // dx if width > x0 else 0
        sh = (height - y0 + dy - 1) // dy if height > y0 else 0
        yield idx, max(sw, 0), max(sh, 0), x0, y0, dx, dy


def _sample(raw_pixels, width, x, y, channels, depth):
    """raw_pixels: flat list of int samples indexed [y*width+x*channels+c]."""
    base = (y * width + x) * channels
    return raw_pixels[base : base + channels]


def build_png(
    width,
    height,
    colortype,
    depth,
    pixels,
    interlace=0,
    palette=None,
    trns=None,
):
    """pixels:
    - direct colour types: flat list of integer samples (row-major), values
      already scaled to the target bit depth (e.g. 0..65535 for depth 16).
    - indexed (type 3): flat list of palette indices, one per pixel.
    palette: list of (r, g, b) tuples for colour type 3.
    trns:
      - type 0: 2-byte gray value
      - type 2: 6-byte r,g,b values
      - type 3: bytes of alpha entries
    """
    channels, allowed = COLOUR_TYPES[colortype]
    assert depth in allowed, (colortype, depth)

    def pack_row(samples, ncols):
        if colortype == 3:
            vals = samples  # already indices
        else:
            vals = samples
        if depth >= 8:
            if depth == 16:
                return b"".join(struct.pack(">H", v) for v in vals)
            return bytes(vals)
        # sub-byte packing (grayscale or indexed)
        out = bytearray()
        vals_per_byte = 8 // depth
        mask = (1 << depth) - 1
        for x in range(0, ncols, vals_per_byte):
            byte = 0
            for j in range(vals_per_byte):
                if x + j >= ncols:
                    break
                byte |= (vals[x + j] & mask) << (
                    (vals_per_byte - j - 1) * depth
                )
            out.append(byte)
        return bytes(out)

    if interlace == 1:
        raw = b""
        for idx, sw, sh, x0, y0, dx, dy in _passes(width, height):
            if sw == 0 or sh == 0:
                continue
            for sy in range(sh):
                row = []
                for sx in range(sw):
                    x = x0 + sx * dx
                    y = y0 + sy * dy
                    row.extend(_sample(pixels, width, x, y, channels, depth))
                raw += b"\x00" + pack_row(row, sw)
    else:
        raw = b""
        for y in range(height):
            row = []
            for x in range(width):
                row.extend(_sample(pixels, width, x, y, channels, depth))
            raw += b"\x00" + pack_row(row, width)

    ihdr = struct.pack(
        ">IIBBBBB", width, height, depth, colortype, 0, 0, interlace
    )
    out = PNG_SIG + _chunk(b"IHDR", ihdr)
    if palette is not None:
        plte = b"".join(struct.pack(">BBB", *rgb) for rgb in palette)
        out += _chunk(b"PLTE", plte)
    if trns is not None:
        out += _chunk(b"tRNS", trns)
    out += _chunk(b"IDAT", zlib.compress(raw, 9))
    out += _chunk(b"IEND", b"")
    return out
