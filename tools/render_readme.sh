#!/usr/bin/env bash
#
# partcad-svc-commerce-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
# Draw the pictures in the README into images/, as 'pc render' draws them:
# each room shaded, from the front right and above, as the IDE's 3D view shows
# it (the 'render:' sections of partcad.yaml say how), and its plan from
# straight above, every product named by its link in the scene.
#
#   tools/render_readme.sh            # with 'pc' on PATH
#   PC="poetry run pc" tools/render_readme.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PC=${PC:-pc}

for scene in studio student-room; do
  $PC --no-ansi render -t png -O . ":$scene"
  $PC --no-ansi render -t svg --view top --with-component-names --filter "scenes/$scene.plan.yaml" -O . ":$scene"
done
