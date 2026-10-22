# Third-Party Notices and Licenses

This file documents third-party open-source components, models, shaders, libraries, and assets adapted or included in J.A.R.V.I.S., along with their respective licenses and attribution requirements.

---

## 1. God's Eye View

* **Project**: God's Eye View (https://github.com/bilawalsidhu/gods-eye-view)
* **Author**: Bilawal Sidhu
* **License**: MIT License
* **Copyright**: Copyright (c) 2026 Bilawal Sidhu

### Full License Text

```text
MIT License

Copyright (c) 2026 Bilawal Sidhu

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

### Components Adapted in JARVIS

1. **Screen Frame Scope Mask & Altitude Fade** (`frontend/app.html`):
   * **What was adapted**: The circular keyhole aperture geometry and altitude-adaptive edge terminus.
     * Center: `50% / 50%`.
     * Keyhole Radius: $R = 0.525 \times \text{height}$ (derived from upstream `KEYHOLE_OUTER_RADIUS = 1.05` in `src/celestialRing.js`).
     * Feather Band: $11\%$ of $R$ ($0.945 R \to 1.055 R$, derived from upstream `SCOPE_FEATHER_RATIO_DEFAULT = 0.11` in `src/scopeMask.js`).
     * Outside Background Color: `#050508` (`rgb(5, 5, 8)`, matching upstream `SCOPE_OUTSIDE_COLOR`).
     * Altitude Fade: Smoothstep easing ramp between $10\,\text{Mm}$ ($0.94$ alpha, preserving faint space stars) and $7\,\text{Mm}$ ($1.00$ alpha, solid black at working altitudes), quantized to $0.005$ steps (`src/scopeMask.js`).
   * **Determination**: Derived by direct audit of `src/scopeMask.js` and `src/celestialRing.js`, matching mathematical constants, color hex values, and altitude thresholds.

2. **Unsharp-Mask Post-Process Filter Stage** (`frontend/app.html:L20249-20286`):
   * **What was adapted**: GLSL convolution fragment shader `SHARPEN_SHADER` registered as `Cesium.PostProcessStage` named `godsEyeView_sharpen` with uniform `amount = 1.08`.
   * **Determination**: Identical shader algorithm (3x3 box blur neighborhood subtracted from center sample), identical uniform names (`colorTextureDimensions`, `texel`, `amount`), identical stage identifier `godsEyeView_sharpen`, and identical amount value $1.08$ (derived from upstream formula `0.1 + intensity * 2.0` at baseline intensity `0.49` in `src/ui/visualPresets.js:L122-146` and `src/ui/visualEffects.js:L101-105, L159`).

3. **Cesium Atmosphere and Lighting Parameters** (`frontend/app.html:L20236-20244, L21189-21196`):
   * **What was adapted**: `viewer.scene.skyAtmosphere.atmosphereLightIntensity = 18`, `saturationShift = -0.12`, `brightnessShift = -0.08`, and `scene.globe.baseColor = '#020611'`.
   * **Determination**: Exact numerical match with upstream initialization parameters in `src/app/viewer.js:L133-136`.

4. **Cesium Render Governor Architecture**:
   * **What was adapted**: Demand rendering pattern (`scene.requestRenderMode = true`, `maximumRenderTimeChange = 1.0`, `maximumScreenSpaceError = 3.5`, `tileCacheSize = 50`) reflecting upstream's performance governor in `src/renderGovernor.js`.

---

## 2. Bundled 3D Models (`frontend/models/`)

The following 3D model files in `frontend/models/` match files in upstream `gods-eye-view/public/models/`. SHA-256 hashes were verified by automated byte comparison:

| File Name | SHA-256 Checksum | Creator and Original Work | Source | License | Upstream & JARVIS Modifications | Required Attribution |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `airplane.glb` | `6780e81a4ce48c04e4c36404bf8bdd2e647933b2995f8cc62a81f9b9d9caf91f` | zairiq-123 (“boeing 747”) | [Sketchfab](https://sketchfab.com/3d-models/boeing-747-9b16672038ba48f98e6d80a159044ed9) | CC BY 4.0 | Upstream: geometry/material simplification, transforms baked, glTF +Y-up, nose toward −X, bounding box centered at origin. JARVIS: byte-identical to upstream. | “boeing 747” by zairiq-123, licensed under CC BY 4.0. |
| `atr72.glb` | `4641e4cd87015dfcbd94d6ec2f1f0a1d6423d814413ce6df8e8f874cb96b1915` | Oyan3D (“ATR 72 - 600”) | [Sketchfab](https://sketchfab.com/3d-models/atr-72-600-1e1a7186f7444d288675262fcee44744) | CC BY 4.0 | Upstream: geometry/material simplification, textures removed with dominant material colors baked into PBR factors, orientation/scale baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “ATR 72 - 600” by Oyan3D, licensed under CC BY 4.0. |
| `b789.glb` | `182963a700f02102c8aa3a11e467a33df383cdf1c58418e561104439dfec61af` | Nobilis 2 (“Boeing 787-9”) | [Sketchfab](https://sketchfab.com/3d-models/boeing-787-9-b6711e2e698e4e469675c1154a50b7a3) | CC BY 4.0 | Upstream: geometry/material simplification, textures resized to 256 px WebP, orientation/scale vertex-baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “Boeing 787-9” by Nobilis 2, licensed under CC BY 4.0. |
| `bell206.glb` | `6a47c8b2c13106e40219483598ef772da35b63261688406f427f24c5a8f92027` | terran4627 (“Bell 206 JetRanger”) | [Sketchfab](https://sketchfab.com/3d-models/bell-206-jetranger-d2f7ba1d671549d4b26aaf834139a1dd) | CC BY 4.0 | Upstream: geometry/material simplification, textures resized to 256 px WebP, orientation/scale vertex-baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “Bell 206 JetRanger” by terran4627, licensed under CC BY 4.0. |
| `c172.glb` | `aba52b6b784534db63c1712b0189ce1b74a94edea586b41db9b795f9be3601d8` | e737 (“Cessna 172”) | [Sketchfab](https://sketchfab.com/3d-models/cessna-172-64cddaee5aff470682659a8c08525046) | CC BY 4.0 | Upstream: geometry/material simplification, textures resized to 256 px WebP, orientation/scale vertex-baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “Cessna 172” by e737, licensed under CC BY 4.0. |
| `citation2.glb` | `5c8a7a98cdbb24faac928370df7f3adc03da8388663a6bb856b59366d271af1e` | BlenderCommunityHead (“1990 Cessna Citation, Texture Detailed, Exterior”) | [Sketchfab](https://sketchfab.com/3d-models/1990-cessna-citation-texture-detailed-exterior-a78839624fe64900a8352cb23462350a) | CC BY 4.0 | Upstream: geometry/material simplification, textures resized to 256 px WebP, orientation/scale vertex-baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “1990 Cessna Citation, Texture Detailed, Exterior” by BlenderCommunityHead, licensed under CC BY 4.0. |
| `jet.glb` | `ac33dc352dc4ef17946680d0ada5cd530e7a4bed95c6ef4aaf2153c381d4bd1a` | Nick the Name (“Private Jet”) | [Sketchfab](https://sketchfab.com/3d-models/private-jet-cbdd1de6ced9461e950eafaa302cc82b) | CC BY 4.0 | Upstream: repackaged as glTF binary, transforms baked, meter scale, glTF +Y-up, nose −X, origin centered. JARVIS: byte-identical. | “Private Jet” by Nick the Name, licensed under CC BY 4.0. |
| `mq9.glb` | `18e8588f44ed4522f838b5d1ce1ead6ef4c08dc5536e21cf7f2d8e60b045ad35` | IProZenoN (“MQ-9”) | [Sketchfab](https://sketchfab.com/3d-models/mq-9-fabe963feb354c5584b51f9c470c3f7e) | CC BY 4.0 | Upstream: geometry/material simplification, textures resized to 256 px WebP, orientation/scale vertex-baked (Y-up, nose −X), origin centered. JARVIS: byte-identical. | “MQ-9” by IProZenoN, licensed under CC BY 4.0. |
| `ship.glb` | `48665716be710479635171b3169f3f056e4785ca1e979089e3d47a83bf480d9a` | Javier_Fernandez (“Low Poly Cargo Ship”) | [Sketchfab](https://sketchfab.com/3d-models/low-poly-cargo-ship-4c22cbaf01c1427f8ab60b3a07b1b32c) | CC BY 4.0 | Upstream: optimized and repackaged as glTF binary. JARVIS: byte-identical. | “Low Poly Cargo Ship” by Javier_Fernandez, licensed under CC BY 4.0. |

*Note: All 9 files in `frontend/models/` matched upstream files. There are no unmatched 3D models in `frontend/models/`.*

---

## 3. Textures and Static Visual Assets

| Asset File | SHA-256 Checksum | Upstream Match | Source & License |
| :--- | :--- | :--- | :--- |
| `frontend/assets/textures/earth-blue-marble.jpg` | `228deba2e4b600146bdcb6cfa359b8ead6aacc2b1c13550a29cd82824cfa1c01` | No match | Source and license unknown, owner to confirm (standard NASA Blue Marble derived texture). |
| `frontend/assets/textures/earth-night.jpg` | `355ab23dd1323315b393d7b91dd2d7ee223a1cbaaba2b48dc72ba90d371ced24` | No match | Source and license unknown, owner to confirm (standard NASA Black Marble / Earth at Night derived texture). |
| `frontend/assets/textures/earth-topology.png` | `839b12da2e4dd346b256cebae72e10c479a102c8980a22084c41275e4b9a0e12` | No match | Source and license unknown, owner to confirm (bathymetric/elevation topology map). |
| `frontend/world_outline.jpg` | `2faa400d1eade3bc44caf6750d1b772bd9bbb6d87362c02bc7c3304e78fa0a9d` | No match | Source and license unknown, owner to confirm (vector coastline outline map). |
| `frontend/jarvis-icon.png` | `6473180fa49c33959bc84b90448aa793ba377b8ce8f465a37172efbe713ad7a2` | No match | Project Hellhound original artwork. |

---

## 4. Vendored JavaScript Libraries

### CesiumJS
* **Path**: `frontend/cesium/Cesium.js`
* **Version**: 1.138.0
* **Copyright**: Copyright 2011-2022 Cesium Contributors
* **License**: Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
* **Apache-2.0 Notice Requirement**:
  > Cesium - https://github.com/CesiumGS/cesium
  > Copyright 2011-2022 Cesium Contributors
  > Licensed under the Apache License, Version 2.0.

### Three.js
* **Path**: `frontend/three.min.js`
* **Version**: r128
* **Copyright**: Copyright 2010-2021 Three.js Authors
* **License**: MIT License (SPDX-License-Identifier: MIT)

### UnrealBloomPass Bundle
* **Path**: `frontend/UnrealBloomPass.bundle.js`
* **Components**: Three.js r128 postprocessing passes (`Pass.js`, `CopyShader.js`, `LuminosityHighPassShader.js`, `UnrealBloomPass.js`)
* **Copyright**: Copyright 2010-2021 Three.js Authors & contributors
* **License**: MIT License

### 3d-force-graph
* **Path**: `frontend/3d-force-graph.min.js`
* **Version**: 1.73.0
* **Author**: Vasco Asturiano (https://github.com/vasturiano/3d-force-graph)
* **License**: MIT License

### Marked
* **Source**: CDN runtime import (`https://cdn.jsdelivr.net/npm/marked/marked.min.js`)
* **Author**: Christopher Jeffrey and markedjs contributors (https://github.com/markedjs/marked)
* **License**: MIT License

### OpenFlights Airline Database
* **Path**: `data/airlines.dat`
* **Source**: OpenFlights.org (https://openflights.org/data.html)
* **License**: Open Database License (ODbL) v1.0 / Database Contents License (DbCL) v1.0
* **Attribution**: “OpenFlights airport and airline data, licensed under ODbL v1.0.”

---

## 5. Non-Commercial Upstream Data Verification

**No non-commercial upstream datasets are bundled in the JARVIS repository.**

Specifically:
1. **TeleGeography Submarine Cable Map** (`telegeography_submarine_cables/`, CC BY-NC-SA 3.0): **NOT present** in JARVIS.
2. **Bhote Koshi River Centerline & Event Data** (`bhote-koshi-2026/`, `bhoteKoshiFloodPath.js`, CC BY-NC 4.0): **NOT present** in JARVIS.

An exhaustive scan across all directories and files confirmed zero instances of these non-commercial datasets in the repository.
