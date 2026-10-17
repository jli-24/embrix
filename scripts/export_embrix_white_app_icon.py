"""Export the white-background Windows app icon from the existing brand mark."""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "frontend" / "public" / "brand"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def export_icon(source_path: Path, output_dir: Path) -> None:
    source = Image.open(source_path).convert("RGB")
    difference = ImageChops.difference(source, Image.new("RGB", source.size, "white"))
    bounds = difference.convert("L").point(lambda value: 255 if value > 12 else 0).getbbox()
    if bounds is None:
        raise ValueError("brand mark is empty")
    left, top, right, bottom = bounds
    side = math.ceil(max(right - left, bottom - top) / 0.88)
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    crop_left = center_x - side // 2
    crop_top = center_y - side // 2
    if crop_left < 0 or crop_top < 0 or crop_left + side > source.width or crop_top + side > source.height:
        raise ValueError("brand mark does not fit the icon canvas")
    mark = source.crop((crop_left, crop_top, crop_left + side, crop_top + side))

    output_dir.mkdir(parents=True, exist_ok=True)
    mark.resize((256, 256), Image.Resampling.LANCZOS).save(
        output_dir / "embrix-app-icon-white.png", optimize=True
    )

    encoded: list[bytes] = []
    for size in SIZES:
        image = mark.resize((size, size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        encoded.append(buffer.getvalue())
    offset = 6 + 16 * len(SIZES)
    with (output_dir / "embrix-app-icon-white.ico").open("wb") as icon:
        icon.write(struct.pack("<HHH", 0, 1, len(SIZES)))
        for size, payload in zip(SIZES, encoded, strict=True):
            icon.write(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
            offset += len(payload)
        for payload in encoded:
            icon.write(payload)


if __name__ == "__main__":
    export_icon(BRAND / "embrix-mark-review-white-hd.png", BRAND)
