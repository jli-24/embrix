from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


def cut(source: Image.Image, box: tuple[int, int, int, int], output: Path, *, white: bool = False) -> None:
    image = source.crop(box).convert("RGBA")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if r > 238 and g > 238 and b > 238:
                pixels[x, y] = (255, 255, 255, 0)
            elif white:
                pixels[x, y] = (255, 255, 255, a)
    image.save(output, optimize=True)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: export_embrix_brand.py SOURCE OUTPUT_DIR")
    source_path, output_dir = Path(sys.argv[1]), Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Image.open(source_path)
    cut(source, (470, 35, 780, 385), output_dir / "embrix-mark-light.png")
    cut(source, (470, 35, 780, 385), output_dir / "embrix-mark-dark.png", white=True)
    cut(source, (320, 20, 900, 565), output_dir / "embrix-lockup-light.png")
    cut(source, (320, 20, 900, 565), output_dir / "embrix-lockup-dark.png", white=True)
    mark = Image.open(output_dir / "embrix-mark-light.png")
    mark.thumbnail((256, 256), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (10, 15, 30, 255))
    canvas.alpha_composite(mark, ((256 - mark.width) // 2, (256 - mark.height) // 2))
    canvas.save(output_dir / "embrix-app-icon.png", optimize=True)
    canvas.save(output_dir / "embrix-app-icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    canvas.resize((32, 32), Image.Resampling.LANCZOS).save(output_dir / "favicon.png", optimize=True)


if __name__ == "__main__":
    main()
