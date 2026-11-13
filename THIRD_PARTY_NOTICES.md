# Third-party notices

The console's layout, palette and component styles (`src/agent/control/ui.css`, `ui.html`, `ui.js`) are adapted
from Hermes Agent (https://github.com/NousResearch/hermes-agent) and its design system `@nous-research/ui`. No
code, fonts, names or logos of theirs are included.

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## force-graph 1.52.0

`src/agent/control/force-graph.min.js` is force-graph 1.52.0 (https://github.com/vasturiano/force-graph), the
unmodified `dist/force-graph.min.js` from the npm tarball, whose sha512 matched the registry's integrity value. Its
SHA-256 is f59db59ba92ff5a76ed93d7a4d77a703b176260e5d9cd29f4f8eb4f6c369a629, pinned by `tests/test_console_force_graph.py`.
The bundle includes its dependency tree. Licenses as each package declares on npm (checked 2026-11-12), all
permissive and compatible with this project's Apache 2.0:

| License | Packages |
|---|---|
| ISC (Mike Bostock) | d3-array, d3-color, d3-dispatch, d3-drag, d3-format, d3-interpolate, d3-quadtree, d3-scale, d3-scale-chromatic, d3-selection, d3-time, d3-time-format, d3-timer, d3-transition, d3-zoom, internmap |
| BSD-3-Clause (Mike Bostock; easing equations Robert Penner) | d3-ease |
| MIT (Vasco Asturiano) | force-graph, d3-force-3d, d3-binarytree, d3-octree, kapsule, accessor-fn, index-array-by, float-tooltip, canvas-color-tracker |
| MIT (others) | preact (Jason Miller), tinycolor2 (Brian Grinstead), bezier-js (Mike Kamermans), @tweenjs/tween.js (tween.js authors), lodash-es (OpenJS Foundation) |

ISC and MIT ask only that the copyright and permission notice travel with the code; BSD-3-Clause adds that the
authors' names are not used to endorse this project. The force-graph license follows; the others are the same MIT or
ISC terms with their own copyright lines above.

```
MIT License

Copyright (c) 2018 Vasco Asturiano

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
