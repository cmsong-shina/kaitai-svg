"""Contrast-constrained, perceptually spaced colors for type connections.

The renderer uses fixed sRGB values for portable, deterministic SVGs. Reproduce
the palette and its quality measurements with ``python -m kaitai_svg.palette``.
No color-science dependency or palette search is needed during rendering.

Oklab (D65) matrices: https://bottosson.github.io/posts/oklab/
WCAG relative luminance: https://www.w3.org/TR/WCAG22/#dfn-relative-luminance
"""

from itertools import combinations, product
from math import dist, sqrt


TYPE_EDGE_COLORS = (
    "#1768bd",
    "#f82818",
    "#009000",
    "#c840d8",
    "#903058",
    "#a07028",
    "#7800c0",
    "#7858ff",
    "#b06890",
    "#089088",
    "#206838",
    "#e00880",
)

# Canvas, white cards and file-group fill composited over the canvas at .55.
EDGE_BACKGROUNDS = ("#f4f7fb", "#ffffff", "#eef3f9")


def _linear_rgb(color: str) -> tuple[float, float, float]:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    return tuple(value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                 for value in channels)


def _oklab(color: str) -> tuple[float, float, float]:
    red, green, blue = _linear_rgb(color)
    ell = (.4122214708 * red + .5363325363 * green + .0514459929 * blue) ** (1 / 3)
    mid = (.2119034982 * red + .6806995451 * green + .1073969566 * blue) ** (1 / 3)
    short = (.0883024619 * red + .2817188376 * green + .6299787005 * blue) ** (1 / 3)
    return (.2104542553 * ell + .7936177850 * mid - .0040720468 * short,
            1.9779984951 * ell - 2.4285922050 * mid + .4505937099 * short,
            .0259040371 * ell + .7827717662 * mid - .8086757660 * short)


def _contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        return sum(channel * weight for channel, weight in
                   zip(_linear_rgb(color), (.2126, .7152, .0722)))

    dark, light = sorted((luminance(first), luminance(second)))
    return (light + .05) / (dark + .05)


def _generate_palette() -> tuple[str, ...]:
    """Greedy maximin Oklab sampling, seeded with a readable reference blue.

    Keep lightness .46–.67 and chroma .10–.24 to avoid near-black, pastel and
    neutral colors. Require 3.5:1 contrast after 8-bit sRGB quantization (above
    WCAG's 3:1 non-text threshold). Farthest-first ordering spreads even small
    prefixes across perceptual space. This optimizes normal trichromatic vision;
    it does not guarantee twelve distinguishable colors for color deficiencies.
    """
    candidates = []
    channels = (*range(0, 256, 8), 255)
    for rgb in product(channels, repeat=3):
        color = "#{:02x}{:02x}{:02x}".format(*rgb)
        lab = _oklab(color)
        if (.46 <= lab[0] <= .67 and .10 <= sqrt(lab[1] ** 2 + lab[2] ** 2) <= .24
                and min(_contrast(color, bg) for bg in EDGE_BACKGROUNDS) >= 3.5):
            candidates.append((color, lab))
    selected = ["#1768bd"]
    labs = [_oklab(selected[0])]
    while len(selected) < 12:
        color, lab = max(candidates, key=lambda item: min(dist(item[1], other) for other in labs))
        selected.append(color)
        labs.append(lab)
    return tuple(selected)


if __name__ == "__main__":
    palette = _generate_palette()
    print("TYPE_EDGE_COLORS = (")
    for color in palette:
        print(f'    "{color}",')
    print(")")
    print("Minimum background contrast:", min(_contrast(color, bg) for color in palette for bg in EDGE_BACKGROUNDS))
    print("Minimum Oklab distance:", min(dist(_oklab(a), _oklab(b)) for a, b in combinations(palette, 2)))
