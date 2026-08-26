"""Рисует иконку приложения в PNG без внешних библиотек (только numpy + zlib)."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

SIZE = 1024


def _rounded_mask(size: int, radius: float, supersample: int = 4) -> np.ndarray:
    n = size * supersample
    r = radius * supersample
    y, x = np.mgrid[0:n, 0:n].astype(np.float32)
    # расстояние до скруглённого квадрата (signed distance)
    cx = np.abs(x - (n - 1) / 2) - (n / 2 - r)
    cy = np.abs(y - (n - 1) / 2) - (n / 2 - r)
    cx = np.maximum(cx, 0)
    cy = np.maximum(cy, 0)
    dist = np.sqrt(cx ** 2 + cy ** 2)
    mask = (dist <= r).astype(np.float32)
    return mask.reshape(size, supersample, size, supersample).mean(axis=(1, 3))


def _rect(canvas_alpha: np.ndarray, x0: float, y0: float, x1: float, y1: float,
          radius: float = 0.0) -> None:
    """Заливает скруглённый прямоугольник в альфа-канале (значения 0..1)."""
    size = canvas_alpha.shape[0]
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    w, h = (x1 - x0) / 2, (y1 - y0) / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = min(radius, w, h)
    dx = np.maximum(np.abs(xs - cx) - (w - r), 0)
    dy = np.maximum(np.abs(ys - cy) - (h - r), 0)
    dist = np.sqrt(dx ** 2 + dy ** 2)
    shape = np.clip(r + 0.8 - dist, 0, 1) if r > 0 else \
        ((np.abs(xs - cx) <= w) & (np.abs(ys - cy) <= h)).astype(np.float32)
    np.maximum(canvas_alpha, shape, out=canvas_alpha)


def render(size: int = SIZE) -> np.ndarray:
    y = np.linspace(0, 1, size, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, size, dtype=np.float32)[None, :]
    t = np.clip(0.35 * x + 0.65 * y, 0, 1)

    top = np.array([0x4F, 0x7A, 0xFF], dtype=np.float32)     # индиго
    bottom = np.array([0x27, 0x3D, 0xC4], dtype=np.float32)  # тёмно-синий
    rgb = top[None, None, :] * (1 - t)[..., None] + bottom[None, None, :] * t[..., None]

    # Столбики звуковой волны
    ink = np.zeros((size, size), dtype=np.float32)
    heights = [0.24, 0.46, 0.72, 1.0, 0.66, 0.38, 0.55, 0.28]
    bar_w = size * 0.052
    gap = size * 0.031
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    start_x = (size - total) / 2
    mid_y = size * 0.44
    max_h = size * 0.23
    for i, hh in enumerate(heights):
        bx = start_x + i * (bar_w + gap)
        bh = max(max_h * hh, bar_w * 0.6)
        _rect(ink, bx, mid_y - bh, bx + bar_w, mid_y + bh, radius=bar_w / 2)

    # Строки «текста» под волной
    line_x0, line_w = size * 0.24, size * 0.52
    for i, frac in enumerate([1.0, 1.0, 0.62]):
        ly = size * 0.735 + i * size * 0.072
        _rect(ink, line_x0, ly, line_x0 + line_w * frac, ly + size * 0.032,
              radius=size * 0.016)

    rgb = rgb * (1 - ink[..., None]) + 255.0 * ink[..., None]
    alpha = _rounded_mask(size, radius=size * 0.225) * 255.0
    return np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)


def write_png(path: Path, image: np.ndarray) -> None:
    h, w, _ = image.shape
    raw = b"".join(b"\x00" + image[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, render())
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
