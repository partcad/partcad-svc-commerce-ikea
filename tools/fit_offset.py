#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Work out the 'offset' that puts a model where this package puts every product.

The convention (see catalog.yaml): millimetres, Z up, the width along X, the
middle of the back edge at the origin on the floor, the front facing -Y.
PartCAD applies a reference's 'offset' - a rotation about the origin, then a
translation, in the source's own units - and then its 'scale', so the
translation is worked out in the model's units.

Give it the model's bounding box as PartCAD reports it ('pc info' of the
collection's part), the turns that stand it up and face it forward, in order,
and the scale:

    python tools/fit_offset.py --min -63.8 0 0 --max -5 58.8 15.6 --turn x90 --scale 25
    {"offset": [[34.4, 0, 0], [1.0, 0, 0], 90.0], "size_mm": [1470.0, 390.0, 1470.0]}

Then check it with 'pc info' of the product: its bounding box should run from
[-width/2, -depth, 0] to [width/2, 0, height].
"""

import math


def _matrix(axis, degrees):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / n, y / n, z / n
    return [
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, p):
    return [sum(m[i][k] * p[k] for k in range(3)) for i in range(3)]


def _axis_angle(m):
    trace = m[0][0] + m[1][1] + m[2][2]
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1) / 2))))
    if abs(angle) < 1e-9:
        return [0, 0, 1], 0
    if abs(angle - 180) < 1e-6:
        # The axis is the column of (M + I) with the largest norm.
        cols = [[m[i][j] + (1 if i == j else 0) for i in range(3)] for j in range(3)]
        col = max(cols, key=lambda c: sum(v * v for v in c))
        n = math.sqrt(sum(v * v for v in col))
        return [round(v / n, 6) for v in col], 180
    s = 2 * math.sin(math.radians(angle))
    axis = [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    return [round(v, 6) for v in axis], round(angle, 6)


def offset(box_min, box_max, rotations=(), scale=1.0):
    m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for axis, degrees in rotations:
        m = _mul(_matrix(axis, degrees), m)
    corners = [
        _apply(m, [x, y, z])
        for x in (box_min[0], box_max[0])
        for y in (box_min[1], box_max[1])
        for z in (box_min[2], box_max[2])
    ]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    t = [-(lo[0] + hi[0]) / 2, -hi[1], -lo[2]]
    axis, angle = _axis_angle(m)
    size = [round((hi[i] - lo[i]) * scale, 1) for i in range(3)]

    def clean(v):
        v = round(v, 4)
        return 0 if v == 0 else v

    return {"offset": [[clean(v) for v in t], [clean(v) for v in axis], clean(angle)], "size_mm": size}


def _turn(text):
    """'x90', 'z-90': a turn about an axis, in degrees."""
    axis = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[text[0].lower()]
    return axis, float(text[1:])


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=float, nargs=3, required=True, help="the bounding box's least corner")
    parser.add_argument("--max", type=float, nargs=3, required=True, help="and its greatest")
    parser.add_argument("--turn", action="append", default=[], help="x90, y-90, z180, ...: in the order applied")
    parser.add_argument("--scale", type=float, default=1.0, help="what the model is multiplied by after the offset")
    arguments = parser.parse_args()
    print(json.dumps(offset(arguments.min, arguments.max, [_turn(t) for t in arguments.turn], arguments.scale)))
