# LineChainer — QGIS Plugin

![QGIS](https://img.shields.io/badge/QGIS-3.16%2B-green) ![License](https://img.shields.io/badge/license-GPL--2.0-blue) ![Version](https://img.shields.io/badge/version-0.1.0-orange)

**LineChainer** is a QGIS plugin that lets you interactively merge multiple touching polylines into a single, continuous polyline. It is designed for use cases such as building hiking routes, cycling itineraries, or any linear network where segments need to be chained together into one unified geometry.

Rather than relying on automated algorithms that may produce unexpected results, LineChainer puts you in control: you click the starting segment, confirm the direction, and the plugin guides you step by step — automatically advancing when the path is unambiguous, and asking you to choose when multiple options exist.

---

## Installation

### From a ZIP file (manual install)

1. Download or clone this repository and zip the `line_chainer/` folder.
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the ZIP file and click **Install Plugin**.
4. Enable **LineChainer** in the plugin list.

### From the QGIS Plugin Repository *(coming soon)*

Once published, LineChainer will be available directly from **Plugins → Manage and Install Plugins** by searching for `LineChainer`.

### Requirements

- QGIS **3.16** or later (tested on LTR 3.28 and 3.34)
- No additional Python dependencies required

---

## Usage

LineChainer opens as a **dock panel** on the right side of the QGIS window. Access it via **Vector → LineChainer → Chain Polylines** or the toolbar button.

### 1. Select a source layer

Choose a polyline layer from the dropdown. Only line layers from the current project are listed.

### 2. Set the offset (optional)

If your polylines do not connect perfectly (small gaps between segments), set an **offset value** (in map units). The plugin will search for the next segment within a buffer of that size around the current endpoint, and will automatically add a straight **bridge segment** to fill the gap.

Leave the offset at `0` for perfectly connected networks.

### 3. Start the chain

Click **▶ Start**, then click the starting polyline on the map.

### 4. Choose the direction

After selecting the first segment, LineChainer detects which ends have neighbours:

- **Neighbours on one side only** → direction is set automatically
- **Neighbours on both sides** → candidate segments are highlighted in orange; click the one you want to follow to set the direction
- **No neighbours** → the segment is isolated; you can save it directly

### 5. Follow the chain

The plugin propagates the chain automatically:

- **One candidate** → the segment is added automatically
- **Multiple candidates** → all options are highlighted in orange on the map and listed in the panel; click the one you want to follow, either on the map or in the list

The current chain is displayed in **green** on the map as it grows.

### 6. Handle loops

If the endpoint of the current chain connects back to a segment already in the chain, LineChainer detects the loop and offers three options:

- **Click a red segment** to continue manually in reverse, segment by segment
- **🔁 Return to start** to automatically traverse all remaining segments back to the original starting point
- **✔ Finish** or **🗺 Export GPX** to save the chain as-is without returning

Already-visited segments are highlighted in **red** to distinguish them from new candidates (orange).

### 7. Undo

At any point, click **↩ Undo last** to remove the last added segment and go back one step.

### 8. Calculate length

Click **📏 Calculate length** at any time to compute the total geodesic length of the current chain. The result is displayed in kilometres in the panel and stored in the output layer.

### 9. Save the result

Two options are available once your itinerary is complete:

- **✔ Finish** — saves the merged polyline as a **temporary memory layer** (`LineChainer_result`) added to the current QGIS project. The layer includes three attributes: `chain_length` (number of source segments), `source_layer` (name of the input layer), and `length_km` (total length in kilometres).
- **🗺 Export GPX** — reprojects the geometry to WGS84 and exports it as a **GPX file** via a save dialog. The resulting GPX layer is automatically loaded into the project.

Both buttons are available at any point during the chain construction, not only at the end.

### 10. Reset

Click **✖ Reset** to clear everything and start over.

---

## Bilingual interface (FR / EN)

LineChainer supports French and English. The interface language follows your QGIS locale setting (**Settings → Options → General → User interface translation**). Compile the `.ts` file in `i18n/` with `lrelease` to activate the French translation.

---

## License

This plugin is distributed under the terms of the [GNU General Public License v2.0](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html).
