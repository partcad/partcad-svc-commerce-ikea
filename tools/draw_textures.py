#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Draw the floor texture the sample rooms are laid with: light oak planks.

Drawn rather than photographed, so that it is this package's to license, and
from a fixed seed, so that running this again writes the same picture.

    python tools/draw_textures.py      # needs Pillow: pip install pillow
"""

import math
import os
import random

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "textures")

SIZE = 1024  # pixels; partcad.yaml says how many millimetres one copy covers
PLANKS = 8  # across the image, so a plank is 1/8 of what one copy covers
SEAM = (122, 98, 70)


def oak_floor(seed=7):
    rng = random.Random(seed)
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = image.load()
    height = SIZE // PLANKS
    for plank in range(PLANKS):
        top = plank * height
        # A butt joint somewhere along every plank, staggered from its neighbours'.
        joint = int(SIZE * ((plank * 0.37 + rng.random() * 0.2) % 1.0))
        for piece in (0, 1):
            # One plank lighter or darker than the next, never another hue.
            tone = rng.randint(-16, 12)
            base = [c + tone for c in (214, 184, 140)]
            phase = rng.random() * math.tau
            rings = rng.uniform(0.03, 0.06)
            for y in range(top, top + height):
                for x in range(SIZE):
                    if (x >= joint) != bool(piece):
                        continue
                    if y in (top, top + height - 1) or abs(x - joint) < 2:
                        pixels[x, y] = SEAM
                        continue
                    # Periodic across the image, so that copies laid side by side meet.
                    wave = math.sin(math.tau * 3 * x / SIZE + phase) * 2.5
                    grain = math.sin((y - top) * rings * 9 + wave + phase)
                    shade = 9 * grain + 3 * math.sin(math.tau * 37 * x / SIZE + y * 0.9)
                    pixels[x, y] = tuple(max(0, min(255, int(c + shade))) for c in base)
    return image.filter(ImageFilter.SMOOTH)


if __name__ == "__main__":
    path = os.path.join(OUT, "oak-floor.png")
    oak_floor().save(path, optimize=True)
    print(path)
