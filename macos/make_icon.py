"""Generate a simple PubMate app icon without external image dependencies."""

from __future__ import annotations

from pathlib import Path
import math
import struct
import sys
import zlib


ICNS_TYPES = {
    16: b"icp4",
    32: b"icp5",
    64: b"icp6",
    128: b"ic07",
    256: b"ic08",
    512: b"ic09",
    1024: b"ic10",
}


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    output = Path(args[0]) if args else Path("macos/assets/PubMate.icns")
    output.parent.mkdir(parents=True, exist_ok=True)

    _write_icns(output)
    print(output)
    return 0


def _draw_icon(size: int) -> list[tuple[int, int, int, int]]:
    pixels: list[tuple[int, int, int, int]] = []
    radius = size * 0.19
    for y in range(size):
        for x in range(size):
            alpha = _rounded_rect_alpha(x, y, size, size, radius)
            if alpha == 0:
                pixels.append((0, 0, 0, 0))
                continue
            top = (21, 126, 145)
            bottom = (33, 92, 174)
            t = y / max(1, size - 1)
            base = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
            pixels.append((*base, alpha))

    def rect(x0: float, y0: float, x1: float, y1: float, color: tuple[int, int, int, int]) -> None:
        ix0, iy0 = round(size * x0), round(size * y0)
        ix1, iy1 = round(size * x1), round(size * y1)
        for yy in range(max(0, iy0), min(size, iy1)):
            for xx in range(max(0, ix0), min(size, ix1)):
                _blend(pixels, size, xx, yy, color)

    def rounded_rect(
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        r: float,
        color: tuple[int, int, int, int],
    ) -> None:
        ix0, iy0 = round(size * x0), round(size * y0)
        ix1, iy1 = round(size * x1), round(size * y1)
        rr = size * r
        for yy in range(max(0, iy0), min(size, iy1)):
            for xx in range(max(0, ix0), min(size, ix1)):
                local_alpha = _rounded_rect_alpha(xx - ix0, yy - iy0, ix1 - ix0, iy1 - iy0, rr)
                if local_alpha:
                    _blend(pixels, size, xx, yy, (*color[:3], min(color[3], local_alpha)))

    # Document sheet.
    rounded_rect(0.24, 0.16, 0.76, 0.84, 0.055, (255, 255, 255, 238))
    rect(0.57, 0.16, 0.76, 0.35, (210, 237, 243, 255))
    for y0 in (0.38, 0.48, 0.58):
        rect(0.33, y0, 0.67, y0 + 0.035, (27, 103, 122, 210))

    # Temporary-citation braces.
    rect(0.30, 0.67, 0.36, 0.72, (27, 103, 122, 230))
    rect(0.28, 0.71, 0.34, 0.77, (27, 103, 122, 230))
    rect(0.64, 0.67, 0.70, 0.72, (27, 103, 122, 230))
    rect(0.66, 0.71, 0.72, 0.77, (27, 103, 122, 230))
    rect(0.39, 0.70, 0.61, 0.735, (27, 103, 122, 210))
    return pixels


def _rounded_rect_alpha(x: int, y: int, width: int, height: int, radius: float) -> int:
    cx = min(max(x, radius), width - radius - 1)
    cy = min(max(y, radius), height - radius - 1)
    distance = math.hypot(x - cx, y - cy)
    if distance <= radius - 1:
        return 255
    if distance >= radius + 1:
        return 0
    return round(255 * (radius + 1 - distance) / 2)


def _blend(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    index = y * width + x
    dst = pixels[index]
    alpha = color[3] / 255
    out = tuple(round(color[i] * alpha + dst[i] * (1 - alpha)) for i in range(3))
    out_alpha = min(255, round(color[3] + dst[3] * (1 - alpha)))
    pixels[index] = (*out, out_alpha)


def _write_png(
    path: Path,
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
) -> None:
    path.write_bytes(_png_bytes(pixels, width, height))


def _png_bytes(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels[y * width + x])
    payload = b"".join(
        [
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(bytes(raw), level=9)),
            _chunk(b"IEND", b""),
        ]
    )
    return b"\x89PNG\r\n\x1a\n" + payload


def _write_icns(path: Path) -> None:
    chunks: list[bytes] = []
    for size, icon_type in ICNS_TYPES.items():
        png = _png_bytes(_draw_icon(size), size, size)
        chunks.append(icon_type + struct.pack(">I", len(png) + 8) + png)
    total_length = 8 + sum(len(chunk) for chunk in chunks)
    path.write_bytes(b"icns" + struct.pack(">I", total_length) + b"".join(chunks))


def _chunk(name: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(name)
    checksum = zlib.crc32(data, checksum)
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", checksum & 0xFFFFFFFF)


if __name__ == "__main__":
    raise SystemExit(main())
