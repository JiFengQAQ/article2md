import struct

from images import _is_content_image_dimensions, _parse_image_dimensions
from models import IMAGE_ASPECT_RATIO_MAX, IMAGE_DIMENSION_MIN_SIDE


def _png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def _gif_bytes(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"


def _webp_vp8x_bytes(width: int, height: int) -> bytes:
    data = bytearray(30)
    data[0:4] = b"RIFF"
    data[8:12] = b"WEBP"
    data[12:16] = b"VP8X"
    data[24:27] = (width - 1).to_bytes(3, "little")
    data[27:30] = (height - 1).to_bytes(3, "little")
    return bytes(data)


def _jpeg_bytes(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x10"
        + b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )


def test_parse_dimensions_png():
    assert _parse_image_dimensions(_png_bytes(900, 450)) == (900, 450)


def test_parse_dimensions_gif():
    assert _parse_image_dimensions(_gif_bytes(800, 700)) == (800, 700)


def test_parse_dimensions_webp_vp8x():
    assert _parse_image_dimensions(_webp_vp8x_bytes(1200, 800)) == (1200, 800)


def test_parse_dimensions_jpeg():
    assert _parse_image_dimensions(_jpeg_bytes(1024, 768)) == (1024, 768)


def test_content_image_dimension_rule():
    min_side = IMAGE_DIMENSION_MIN_SIDE
    max_aspect = IMAGE_ASPECT_RATIO_MAX

    # – keep: at least one side ≥ 480, landscape ratio ≤ 5, not square
    assert _is_content_image_dimensions((480, 360), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((640, 427), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((660, 310), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((660, 372), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((3050, 744), min_side=min_side, max_landscape_aspect=max_aspect)

    # – keep: portrait, h ≥ 480, NO aspect ratio limit
    assert _is_content_image_dimensions((744, 3050), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((400, 800), min_side=min_side, max_landscape_aspect=max_aspect)
    assert _is_content_image_dimensions((300, 5000), min_side=min_side, max_landscape_aspect=max_aspect)

    # – reject: both sides < 480
    assert not _is_content_image_dimensions((479, 300), min_side=min_side, max_landscape_aspect=max_aspect)
    assert not _is_content_image_dimensions((300, 479), min_side=min_side, max_landscape_aspect=max_aspect)

    # – reject: landscape ratio > 5
    assert not _is_content_image_dimensions((3000, 500), min_side=min_side, max_landscape_aspect=max_aspect)

    # – reject: square
    assert not _is_content_image_dimensions((700, 700), min_side=min_side, max_landscape_aspect=max_aspect)
    assert not _is_content_image_dimensions((500, 500), min_side=min_side, max_landscape_aspect=max_aspect)


def test_default_image_threshold_constants():
    assert IMAGE_DIMENSION_MIN_SIDE == 480
    assert IMAGE_ASPECT_RATIO_MAX == 5.0
