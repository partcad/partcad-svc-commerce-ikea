#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Write the floor plans the sample scenes stand their walls on, as DXF files.

Each plan is one closed outline of straight lines on the XY plane, in
millimetres, which is what PartCAD's 'walls' scenes take (see "Rooms" in
PartCAD's documentation). The outlines are written here rather than drawn in a
CAD application so that a change to a room is a change to a list of corners.

    python tools/draw_floorplans.py      # needs ezdxf: pip install ezdxf
"""

import os

import ezdxf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "floorplans")

PLANS = {
    # An 18 m2 studio: 5.8 x 3.6 m, less the 1.2 x 1.0 m bathroom block in its
    # back left corner, whose wall the BILLY bookcase stands against.
    "studio": [(0, 0), (5800, 0), (5800, 3600), (1200, 3600), (1200, 2600), (0, 2600)],
    # A 10.8 m2 student room: 3.0 x 3.6 m.
    "student-room": [(0, 0), (3000, 0), (3000, 3600), (0, 3600)],
}


def write(name, corners):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.modelspace().add_lwpolyline(corners, close=True)
    path = os.path.join(OUT, name + ".dxf")
    doc.saveas(path)
    return path


if __name__ == "__main__":
    for name, corners in PLANS.items():
        print(write(name, corners))
