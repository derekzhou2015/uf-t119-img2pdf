#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline census of img2pdf behaviour across PNG encoding variants.

Run with the project venv:
    .venv\\Scripts\\python.exe -I tools\\census.py
"""
import sys
import os
import io
import struct
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image  # noqa: E402
import img2pdf  # noqa: E402
from pngbuild import build_png  # noqa: E402

W = H = 8


def direct_pixels(channels, depth):
    maxv = (1 << depth) - 1
    px = []
    for y in range(H):
        for x in range(W):
            base = [
                (x * maxv) // (W - 1),
                (y * maxv) // (H - 1),
                ((x + y) * maxv) // (W + H - 2),
            ]
            if channels == 1:
                px.append(base[0])
            elif channels == 2:
                px.extend([base[0], maxv if (x + y) % 3 else maxv // 4])
            elif channels == 3:
                px.extend(base)
            elif channels == 4:
                px.extend(base + [maxv if (x + y) % 3 else maxv // 4])
    return px


def indexed_pixels(n):
    return [(x + y) % n for y in range(H) for x in range(W)]


def make_case(name, data):
    print("=" * 78)
    print("CASE:", name)
    print("  IHDR: depth=%d colortype=%d interlace=%d" % (data[24], data[25], data[28]))
    # what does PIL make of it?
    im = Image.open(io.BytesIO(data))
    im.load()
    print(
        "  PIL: mode=%s size=%s info.keys=%s"
        % (im.mode, im.size, sorted(im.info.keys()))
    )
    if "transparency" in im.info:
        print("  PIL: transparency=%r" % (im.info["transparency"],))
    pil_mode = im.mode
    im.close()
    # what does img2pdf do?
    try:
        frames = img2pdf.read_images(data, None)
        for i, fr in enumerate(frames):
            (
                color,
                ndpi,
                imgformat,
                imgdata,
                smask,
                w,
                h,
                palette,
                inverted,
                depth,
                rotation,
                iccp,
            ) = fr
            print(
                "  img2pdf frame %d: color=%s fmt=%s depth=%s smask=%s "
                "palette_bytes=%d"
                % (i, color.name, imgformat.name, depth,
                   "yes" if smask is not None else "no", len(palette))
            )
        print("  RESULT: ACCEPT")
    except Exception as e:
        print("  RESULT: REJECT %s: %s" % (type(e).__name__, e))
    print()


def main():
    cases = []

    # --- direct colour types, every legal depth, both interlace variants ---
    for colortype, channels, depths in [
        (0, 1, (1, 2, 4, 8, 16)),
        (2, 3, (8, 16)),
        (4, 2, (8, 16)),
        (6, 4, (8, 16)),
    ]:
        for depth in depths:
            px = direct_pixels(channels, depth)
            for interlace in (0, 1):
                tag = "ct%d-d%d-i%d" % (colortype, depth, interlace)
                data = build_png(W, H, colortype, depth, px, interlace=interlace)
                cases.append(("direct " + tag, data))

    # --- indexed colour (type 3) ---
    for depth in (1, 2, 4, 8):
        n = 1 << depth
        palette = [
            (
                (i * 255) // (n - 1),
                255 - (i * 255) // (n - 1),
                (i * 127) // (n - 1),
            )
            for i in range(n)
        ]
        px = indexed_pixels(n)
        for interlace in (0, 1):
            data = build_png(
                W, H, 3, depth, px, interlace=interlace, palette=palette
            )
            cases.append(("indexed ct3-d%d-i%d" % (depth, interlace), data))
        # with tRNS (first palette entry fully transparent)
        trns = bytes([0] + [255] * (n - 1))
        for interlace in (0, 1):
            data = build_png(
                W, H, 3, depth, px, interlace=interlace,
                palette=palette, trns=trns,
            )
            cases.append(
                ("indexed+trns ct3-d%d-i%d" % (depth, interlace), data)
            )

    # --- tRNS on grayscale (type 0) and RGB (type 2), depth 8 and 16 ---
    trns_cases = [
        (0, 1, 8, struct.pack(">H", 7)),
        (0, 1, 16, struct.pack(">H", 1000)),
        (2, 3, 8, struct.pack(">HHH", 7, 11, 13)),
        (2, 3, 16, struct.pack(">HHH", 1000, 2000, 3000)),
    ]
    for colortype, channels, depth, trns in trns_cases:
        px = direct_pixels(channels, depth)
        for interlace in (0, 1):
            data = build_png(
                W, H, colortype, depth, px, interlace=interlace, trns=trns
            )
            cases.append(
                ("trns ct%d-d%d-i%d" % (colortype, depth, interlace), data)
            )

    for name, data in cases:
        try:
            make_case(name, data)
        except Exception:
            print("CASE ERRORED:", name)
            traceback.print_exc()


if __name__ == "__main__":
    main()
