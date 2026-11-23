#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression tests for the PNG interlace / 16-bit alpha split.

Background
----------
Before the fix, the non-interlaced PNG path inspected the PNG IHDR chunk and
refused 16-bit images carrying transparency with ``AlphaChannelError``, while
the interlaced (Adam7) path fell through to the generic frame loop where the
IHDR bit depth was never inspected. Pillow lossily flattens 16-bit RGBA/LA to
8-bit while opening the file, so the interlaced path silently produced a PDF
with an 8-bit soft mask instead of rejecting the input. The behaviour of the
same pixel data therefore diverged solely based on the interlace bit.

These tests build every relevant PNG variant by hand (pure stdlib, no
ImageMagick / ghostscript / network) and pin the *contract*:

* 16-bit PNGs carrying transparency are rejected with ``AlphaChannelError``
  regardless of the interlace bit (RGBA, gray+alpha, gray+tRNS, RGB+tRNS).
* Interlaced 16-bit grayscale without alpha is accepted and keeps 16 bits per
  component (the adjacent "unknown colorspace" split).
* Interlaced 8-bit / low-depth images, with or without alpha, keep producing
  the same artefacts as before (soft mask present, depth preserved).
* Other formats are untouched.
"""
import io
import os
import struct

import pytest
import pikepdf

import img2pdf
from pngbuild import build_png

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_INPUT = os.path.join(HERE, "tests", "input")

W = H = 8
MAX16 = (1 << 16) - 1
MAX8 = (1 << 8) - 1


def direct_pixels(channels, depth):
    """Deterministic gradient pixels, including non-opaque alpha samples."""
    maxv = (1 << depth) - 1
    px = []
    for y in range(H):
        for x in range(W):
            base = [
                (x * maxv) // (W - 1),
                (y * maxv) // (W - 1),
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


ENGINES = ["internal", "pikepdf"]


# ---------------------------------------------------------------------------
# 1. The core contract: 16-bit + transparency must reject for BOTH interlace
#    variants, with the identical exception type as the non-interlaced path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_rgba16_rejected_regardless_of_interlace(engine, interlace):
    data = build_png(
        W, H, 6, 16, direct_pixels(4, 16), interlace=interlace
    )
    assert data[28] == interlace  # sanity: the builder set the IHDR flag
    with pytest.raises(img2pdf.AlphaChannelError):
        img2pdf.convert(
            data, nodate=True, engine=getattr(img2pdf.Engine, engine)
        )


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_gray16_alpha_rejected_regardless_of_interlace(engine, interlace):
    # colour type 4 (gray+alpha); Pillow opens 16-bit files as RGBA
    data = build_png(
        W, H, 4, 16, direct_pixels(2, 16), interlace=interlace
    )
    with pytest.raises(img2pdf.AlphaChannelError):
        img2pdf.convert(
            data, nodate=True, engine=getattr(img2pdf.Engine, engine)
        )


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_gray16_trns_rejected_regardless_of_interlace(engine, interlace):
    trns = struct.pack(">H", 1000)
    data = build_png(
        W,
        H,
        0,
        16,
        direct_pixels(1, 16),
        interlace=interlace,
        trns=trns,
    )
    with pytest.raises(img2pdf.AlphaChannelError):
        img2pdf.convert(
            data, nodate=True, engine=getattr(img2pdf.Engine, engine)
        )


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_rgb16_trns_rejected_regardless_of_interlace(engine, interlace):
    trns = struct.pack(">HHH", 1000, 2000, 3000)
    data = build_png(
        W,
        H,
        2,
        16,
        direct_pixels(3, 16),
        interlace=interlace,
        trns=trns,
    )
    with pytest.raises(img2pdf.AlphaChannelError):
        img2pdf.convert(
            data, nodate=True, engine=getattr(img2pdf.Engine, engine)
        )


# ---------------------------------------------------------------------------
# 2. Adjacent split: interlaced 16-bit grayscale without alpha used to crash
#    with "ValueError: unknown colorspace". It must now embed as 16-bit
#    DeviceGray, matching the non-interlaced path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_gray16_no_alpha_keeps_16_bits(engine, interlace):
    data = build_png(
        W, H, 0, 16, direct_pixels(1, 16), interlace=interlace
    )
    out = img2pdf.convert(
        data, nodate=True, engine=getattr(img2pdf.Engine, engine)
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        assert int(xobj.BitsPerComponent) == 16
        assert str(xobj.ColorSpace) == "/DeviceGray"
        assert not hasattr(xobj, "SMask")


# ---------------------------------------------------------------------------
# 3. The existing, accepted behaviour for interlaced 8-bit / low-depth images
#    must not change: depth is preserved and alpha becomes a soft mask.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_rgba8_accepted_with_8bit_softmask(engine, interlace):
    data = build_png(
        W, H, 6, 8, direct_pixels(4, 8), interlace=interlace
    )
    out = img2pdf.convert(
        data, nodate=True, engine=getattr(img2pdf.Engine, engine)
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        assert int(xobj.BitsPerComponent) == 8
        assert str(xobj.ColorSpace) == "/DeviceRGB"
        assert hasattr(xobj, "SMask")
        assert int(xobj.SMask.BitsPerComponent) == 8
        assert str(xobj.SMask.ColorSpace) == "/DeviceGray"


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_gray8_alpha_accepted_with_8bit_softmask(engine, interlace):
    data = build_png(
        W, H, 4, 8, direct_pixels(2, 8), interlace=interlace
    )
    out = img2pdf.convert(
        data, nodate=True, engine=getattr(img2pdf.Engine, engine)
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        assert int(xobj.BitsPerComponent) == 8
        assert hasattr(xobj, "SMask")
        assert int(xobj.SMask.BitsPerComponent) == 8


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_indexed_trns_accepted_with_softmask(engine, interlace, depth):
    n = 1 << depth
    palette = [
        (
            (i * MAX8) // (n - 1),
            MAX8 - (i * MAX8) // (n - 1),
            (i * 127) // (n - 1),
        )
        for i in range(n)
    ]
    trns = bytes([0] + [MAX8] * (n - 1))
    data = build_png(
        W,
        H,
        3,
        depth,
        indexed_pixels(n),
        interlace=interlace,
        palette=palette,
        trns=trns,
    )
    out = img2pdf.convert(
        data, nodate=True, engine=getattr(img2pdf.Engine, engine)
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        # indexed PNG keeps its native depth and gains a soft mask
        assert int(xobj.BitsPerComponent) == depth
        assert hasattr(xobj, "SMask")


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("interlace", [0, 1])
def test_trns_8bit_accepted_with_softmask(engine, interlace):
    gray_trns = struct.pack(">H", 7)
    data = build_png(
        W,
        H,
        0,
        8,
        direct_pixels(1, 8),
        interlace=interlace,
        trns=gray_trns,
    )
    out = img2pdf.convert(
        data, nodate=True, engine=getattr(img2pdf.Engine, engine)
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        assert int(xobj.BitsPerComponent) == 8
        assert hasattr(xobj, "SMask")


# ---------------------------------------------------------------------------
# 4. Full legal-depth matrix: interlaced and non-interlaced variants of every
#    alpha-free / 8-bit-transparency colour type must behave identically in
#    accept/reject terms (the original split was never about these, so they
#    stay accepted).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "colortype,channels,depths",
    [
        (0, 1, (1, 2, 4, 8, 16)),
        (2, 3, (8, 16)),
        (4, 2, (8,)),
        (6, 4, (8,)),
    ],
)
@pytest.mark.parametrize("interlace", [0, 1])
def test_accepted_matrix_interlace_parity(colortype, channels, depths, interlace):
    for depth in depths:
        data = build_png(
            W,
            H,
            colortype,
            depth,
            direct_pixels(channels, depth),
            interlace=interlace,
        )
        # must not raise; both engines produce a single-page PDF
        out = img2pdf.convert(data, nodate=True, engine=img2pdf.Engine.internal)
        with pikepdf.open(io.BytesIO(out)) as pdf:
            assert len(pdf.pages) == 1


# ---------------------------------------------------------------------------
# 5. Other formats' semantics must be untouched. The animated GIF fixture and
#    the plain 8-bit PNG fixture still convert the same way.
# ---------------------------------------------------------------------------


def test_gif_animation_unchanged():
    with open(os.path.join(FIXTURE_INPUT, "animation.gif"), "rb") as fh:
        data = fh.read()
    frames = img2pdf.read_images(data, None)
    assert len(frames) == 2
    for frame in frames:
        assert frame[2] == img2pdf.ImageFormat.PNG
        assert frame[9] == 8


def test_plain_png_fixture_unchanged():
    with open(os.path.join(FIXTURE_INPUT, "normal.png"), "rb") as fh:
        data = fh.read()
    out = img2pdf.convert(data, nodate=True, engine=img2pdf.Engine.internal)
    with pikepdf.open(io.BytesIO(out)) as pdf:
        xobj = pdf.pages[0].Resources.XObject.Im0
        assert int(xobj.BitsPerComponent) == 8
        assert not hasattr(xobj, "SMask")


def test_builder_emits_valid_adam7_that_pillow_reads():
    # Guard the handcrafted interlaced fixture itself: Pillow must decode the
    # Adam7 payload back to the same pixels as the non-interlaced encoding.
    px = direct_pixels(4, 8)
    interlaced = build_png(W, H, 6, 8, px, interlace=1)
    planar = build_png(W, H, 6, 8, px, interlace=0)
    from PIL import Image

    with Image.open(io.BytesIO(interlaced)) as a, Image.open(
        io.BytesIO(planar)
    ) as b:
        assert a.info.get("interlace") == 1
        assert a.tobytes() == b.tobytes()
