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
The bundle includes its dependencies: d3-array, d3-drag, d3-scale, d3-scale-chromatic, d3-selection and d3-zoom (ISC,
Mike Bostock), d3-force-3d, kapsule, accessor-fn, index-array-by, float-tooltip and canvas-color-tracker (MIT, Vasco
Asturiano), bezier-js (MIT), @tweenjs/tween.js (MIT) and lodash-es (MIT).

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
