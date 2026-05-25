# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LineChainer — line_chainer_dialog.py

 Business logic:
   - Layer selection + offset parameter
   - Interactive polyline chaining via MapToolSelectLine
   - Automatic propagation when only one candidate exists
   - Manual disambiguation when multiple candidates exist
   - Loop detection with user confirmation
   - Direction normalisation (flip lines so they connect head-to-tail)
   - Result written to a memory layer
 ***************************************************************************/
"""

from __future__ import annotations

import math
from typing import List, Optional, Set, Tuple

from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton,
    QMessageBox, QListWidget, QListWidgetItem,
    QGroupBox, QSizePolicy, QFileDialog,
)
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsFields,
    QgsField,
    QgsMemoryProviderUtils,
    QgsFeatureRequest,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsUnitTypes,
    QgsVectorFileWriter,
)
from qgis.gui import QgsRubberBand, QgsMapCanvas

from .map_tool_select_line import MapToolSelectLine


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------

def _endpoints(geom: QgsGeometry) -> Tuple[QgsPointXY, QgsPointXY]:
    """Return (start, end) points of the first part of a (multi)polyline."""
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        coords = parts[0]
    else:
        coords = geom.asPolyline()
    return QgsPointXY(coords[0]), QgsPointXY(coords[-1])


def _point_distance(a: QgsPointXY, b: QgsPointXY) -> float:
    dx = a.x() - b.x()
    dy = a.y() - b.y()
    return math.sqrt(dx * dx + dy * dy)


def _flip_geometry(geom: QgsGeometry) -> QgsGeometry:
    """Return a new geometry with vertex order reversed."""
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        flipped = [list(reversed(part)) for part in reversed(parts)]
        return QgsGeometry.fromMultiPolylineXY(flipped)
    else:
        coords = list(reversed(geom.asPolyline()))
        return QgsGeometry.fromPolylineXY(coords)


def _normalize_and_append(
    chain_geom: QgsGeometry,
    next_geom: QgsGeometry,
    offset: float,
) -> QgsGeometry:
    """
    Orient *next_geom* so that its start connects to the end of *chain_geom*,
    then return a single merged LineString.

    - If the lines touch exactly (offset=0 or gap < epsilon): deduplicate the
      shared junction vertex so it appears only once.
    - If there is a real gap (offset > 0 and lines don't touch): keep both
      endpoints as-is, which automatically creates a straight bridge segment
      between the two polylines.
    """
    EPSILON = 1e-8

    chain_end = _endpoints(chain_geom)[1]
    ns, ne = _endpoints(next_geom)

    dist_s = _point_distance(chain_end, ns)
    dist_e = _point_distance(chain_end, ne)

    # Flip next_geom if its END is closer to chain_end than its START
    if dist_e < dist_s:
        next_geom = _flip_geometry(next_geom)
        # Recompute start point after flip
        ns = _endpoints(next_geom)[0]
        dist_s = _point_distance(chain_end, ns)

    # Flatten both geometries into simple coordinate lists
    if chain_geom.isMultipart():
        all_coords = []
        for part in chain_geom.asMultiPolyline():
            all_coords.extend(part)
    else:
        all_coords = list(chain_geom.asPolyline())

    if next_geom.isMultipart():
        next_coords = []
        for part in next_geom.asMultiPolyline():
            next_coords.extend(part)
    else:
        next_coords = list(next_geom.asPolyline())

    # Junction strategy:
    #   - exact touch (dist < epsilon): drop the duplicate start vertex of next_coords
    #     so the shared point appears only once → clean single polyline
    #   - real gap (dist >= epsilon): keep both endpoints → straight bridge segment
    #     is implicitly created between all_coords[-1] and next_coords[0]
    if dist_s < EPSILON:
        merged_coords = all_coords + next_coords[1:]
    else:
        merged_coords = all_coords + next_coords

    return QgsGeometry.fromPolylineXY(merged_coords)


# ---------------------------------------------------------------------------
# Main dialog widget (embedded in the dock)
# ---------------------------------------------------------------------------

class LineChainerDialog(QWidget):
    """Main UI + controller for the LineChainer plugin."""

    # Rubber-band colours
    COLOR_CHAIN = QColor(0, 180, 0, 200)        # green  — confirmed chain
    COLOR_CANDIDATE = QColor(255, 165, 0, 180)  # orange — candidate lines

    def __init__(self, iface, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.iface = iface
        self.canvas: QgsMapCanvas = iface.mapCanvas()

        # State
        self._layer: Optional[QgsVectorLayer] = None
        self._map_tool: Optional[MapToolSelectLine] = None
        self._previous_map_tool = None

        self._chain_fids: List[int] = []          # ordered feature IDs in chain
        self._chain_geom: Optional[QgsGeometry] = None  # merged geometry so far
        self._visited_fids: Set[int] = set()       # anti-loop guard
        self._offset: float = 0.0
        self._waiting_for: str = "start"           # "start" | "next" | "loop"
        self._candidates: List[int] = []           # fids shown for manual pick

        # Rubber bands
        self._rb_chain = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self._rb_chain.setColor(self.COLOR_CHAIN)
        self._rb_chain.setWidth(4)

        self._rb_candidates: List[QgsRubberBand] = []

        self._build_ui()
        self._populate_layers()
        QgsProject.instance().layersAdded.connect(self._populate_layers)
        QgsProject.instance().layersRemoved.connect(self._populate_layers)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        tr = self.tr
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # --- Layer selection ---
        grp_layer = QGroupBox(tr("Source layer"))
        lay_layer = QVBoxLayout(grp_layer)
        self.combo_layer = QComboBox()
        self.combo_layer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo_layer.currentIndexChanged.connect(self._on_layer_changed)
        lay_layer.addWidget(self.combo_layer)
        main_layout.addWidget(grp_layer)

        # --- Offset ---
        grp_offset = QGroupBox(tr("Offset (search buffer)"))
        lay_offset = QHBoxLayout(grp_offset)
        lay_offset.addWidget(QLabel(tr("Offset (map units):")))
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setMinimum(0.0)
        self.spin_offset.setMaximum(100000.0)
        self.spin_offset.setDecimals(4)
        self.spin_offset.setValue(0.0)
        self.spin_offset.setSingleStep(0.1)
        self.spin_offset.valueChanged.connect(self._on_offset_changed)
        lay_offset.addWidget(self.spin_offset)
        main_layout.addWidget(grp_offset)

        # --- Status ---
        grp_status = QGroupBox(tr("Status"))
        lay_status = QVBoxLayout(grp_status)
        self.lbl_status = QLabel(tr("Select a layer and click « Start »."))
        self.lbl_status.setWordWrap(True)
        lay_status.addWidget(self.lbl_status)
        main_layout.addWidget(grp_status)

        # --- Candidate list (hidden until needed) ---
        grp_candidates = QGroupBox(tr("Choose next line"))
        self.grp_candidates = grp_candidates
        lay_cand = QVBoxLayout(grp_candidates)
        self.lbl_candidates = QLabel(
            tr("Multiple lines touch the endpoint.\nClick on the map OR select below:")
        )
        self.lbl_candidates.setWordWrap(True)
        lay_cand.addWidget(self.lbl_candidates)
        self.list_candidates = QListWidget()
        self.list_candidates.itemClicked.connect(self._on_candidate_item_clicked)
        lay_cand.addWidget(self.list_candidates)
        grp_candidates.setVisible(False)
        main_layout.addWidget(grp_candidates)

        # --- Length display ---
        grp_length = QGroupBox(tr("Route length"))
        lay_length = QHBoxLayout(grp_length)
        self.lbl_length = QLabel(tr("—"))
        self.lbl_length.setWordWrap(True)
        lay_length.addWidget(self.lbl_length)
        self.btn_calc_length = QPushButton(tr("📏  Calculate length"))
        self.btn_calc_length.setEnabled(False)
        self.btn_calc_length.clicked.connect(self._on_calc_length)
        lay_length.addWidget(self.btn_calc_length)
        main_layout.addWidget(grp_length)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton(tr("▶  Start"))
        self.btn_start.clicked.connect(self._on_start)
        self.btn_undo = QPushButton(tr("↩  Undo last"))
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self._on_undo)
        self.btn_finish = QPushButton(tr("✔  Finish"))
        self.btn_finish.setEnabled(False)
        self.btn_finish.clicked.connect(self._on_finish)
        self.btn_export_gpx = QPushButton(tr("🗺  Export GPX…"))
        self.btn_export_gpx.setEnabled(False)
        self.btn_export_gpx.clicked.connect(self._on_export_gpx)
        self.btn_return_to_start = QPushButton(tr("🔁  Return to start"))
        self.btn_return_to_start.setEnabled(False)
        self.btn_return_to_start.setToolTip(tr(
            "Continue in reverse along the already-visited segments back to the starting point"
        ))
        self.btn_return_to_start.clicked.connect(self._on_return_to_start)
        self.btn_reset = QPushButton(tr("✖  Reset"))
        self.btn_reset.clicked.connect(self._on_reset)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_undo)
        main_layout.addLayout(btn_layout)

        btn_layout2 = QHBoxLayout()
        btn_layout2.addWidget(self.btn_finish)
        btn_layout2.addWidget(self.btn_export_gpx)
        main_layout.addLayout(btn_layout2)

        btn_layout3 = QHBoxLayout()
        btn_layout3.addWidget(self.btn_return_to_start)
        btn_layout3.addWidget(self.btn_reset)
        main_layout.addLayout(btn_layout3)

        main_layout.addStretch()

    # ------------------------------------------------------------------
    # Translation helper
    # ------------------------------------------------------------------

    def tr(self, message: str) -> str:
        return QCoreApplication.translate("LineChainerDialog", message)

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------

    def _populate_layers(self, *args):
        """Fill the combo with line layers from the current project."""
        self.combo_layer.blockSignals(True)
        prev_id = self.combo_layer.currentData()
        self.combo_layer.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (
                isinstance(layer, QgsVectorLayer)
                and layer.geometryType() == QgsWkbTypes.LineGeometry
            ):
                self.combo_layer.addItem(layer.name(), layer.id())
        # Restore previous selection if still present
        idx = self.combo_layer.findData(prev_id)
        if idx >= 0:
            self.combo_layer.setCurrentIndex(idx)
        self.combo_layer.blockSignals(False)
        self._on_layer_changed()

    def _on_layer_changed(self, *args):
        layer_id = self.combo_layer.currentData()
        self._layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def _on_offset_changed(self, value: float):
        self._offset = value

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_start(self):
        if self._layer is None:
            self._set_status(self.tr("⚠ Please select a line layer first."))
            return
        self._reset_state()
        self._waiting_for = "start"
        self._set_status(self.tr("Click on the starting polyline in the map."))
        self._activate_map_tool()
        self.btn_finish.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.btn_calc_length.setEnabled(False)
        self.btn_export_gpx.setEnabled(False)
        self.btn_return_to_start.setEnabled(False)

    def _on_undo(self):
        if len(self._chain_fids) < 2:
            return
        # Remove last feature from chain
        removed_fid = self._chain_fids.pop()
        self._visited_fids.discard(removed_fid)

        # Rebuild chain geometry from scratch
        self._chain_geom = None
        if self._chain_fids:
            self._rebuild_chain_geometry()

        self._update_chain_rubber_band()
        self._set_status(self.tr("Last line removed. Click the next line to continue."))
        self._waiting_for = "next"
        self._activate_map_tool()

        if len(self._chain_fids) <= 1:
            self.btn_undo.setEnabled(False)

    def _on_finish(self):
        if self._chain_geom is None or self._chain_geom.isEmpty():
            self._set_status(self.tr("⚠ Nothing to save yet."))
            return
        self._save_to_memory_layer()
        self._reset_state()
        self._set_status(self.tr("✔ Result saved to memory layer « LineChainer_result »."))

    def _on_reset(self):
        self._reset_state()
        self._set_status(self.tr("Reset. Select a layer and click « Start »."))

    def _on_return_to_start(self):
        """
        Automatic full reverse: append the remaining segments back to the
        original starting point. Works whether or not the user has already
        done some manual reverse steps.
        """
        if not self._chain_fids:
            return

        self.btn_return_to_start.setEnabled(False)
        self.btn_export_gpx.setEnabled(False)
        self._clear_candidates()
        self.canvas.unsetMapTool(self._map_tool)
        self._set_status(self.tr("⏳ Building return path…"))

        # _original_chain_fids is the forward chain at the moment the loop
        # was detected. We need to go back from the current chain end to fid_0.
        # Find where we currently are in the original chain by matching the
        # current end point against the original segments.
        current_end = _endpoints(self._chain_geom)[1]
        original = self._original_chain_fids  # [fid_0, fid_1, ..., fid_n]

        # Find the index in the original chain whose endpoint matches current_end
        tol = self._offset if self._offset > 0 else 1e-8
        current_idx = 0  # fallback: start from the beginning
        for i, fid in enumerate(original):
            feat = self._get_feature(fid)
            if feat is None:
                continue
            s, e = _endpoints(feat.geometry())
            if _point_distance(current_end, s) <= tol or _point_distance(current_end, e) <= tol:
                current_idx = i
                break

        # Reverse from current_idx down to 0 (inclusive)
        return_fids = list(reversed(original[:current_idx + 1]))
        # Skip the very first one — it's the segment we are already sitting on
        if return_fids:
            return_fids = return_fids[1:]

        for fid in return_fids:
            feat = self._get_feature(fid)
            if feat is None:
                continue
            flipped = _flip_geometry(feat.geometry())
            self._chain_geom = _normalize_and_append(
                self._chain_geom, flipped, self._offset
            )
            self._chain_fids.append(fid)

        self._in_reverse = False
        self._update_chain_rubber_band()
        self.btn_export_gpx.setEnabled(True)
        self._set_status(
            self.tr(
                "✔ Returned to start. "
                "Click « Finish » to save as memory layer or « Export GPX »."
            )
        )

    def _on_calc_length(self):
        """Compute the geodesic length of the current chain and display it."""
        if self._chain_geom is None or self._chain_geom.isEmpty():
            self._set_status(self.tr("⚠ No chain to measure yet."))
            return

        da = QgsDistanceArea()
        da.setSourceCrs(self._layer.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

        length_m = da.measureLength(self._chain_geom)
        length_km = length_m / 1000.0

        # Also update the field on the result layer if it already exists
        result_layer = self._find_result_layer()
        if result_layer is not None:
            result_layer.startEditing()
            for feat in result_layer.getFeatures():
                result_layer.changeAttributeValue(
                    feat.id(),
                    result_layer.fields().indexOf("length_km"),
                    round(length_km, 3),
                )
            result_layer.commitChanges()

        self.lbl_length.setText(self.tr(f"{length_km:.3f} km  ({len(self._chain_fids)} segments)"))

    def _on_export_gpx(self):
        """Save the chain as a GPX file via a file dialog, then load it in QGIS."""
        from qgis.core import QgsCoordinateTransform, QgsMessageLog, Qgis

        if self._chain_geom is None or self._chain_geom.isEmpty():
            self._set_status(self.tr("⚠ Nothing to export yet."))
            return

        # Ask user for file path
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export route as GPX"),
            "",
            self.tr("GPS Exchange Format (*.gpx)"),
        )
        if not path:
            return  # user cancelled
        if not path.lower().endswith(".gpx"):
            path += ".gpx"

        # GPX requires WGS84 — reproject the geometry before building the layer
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        src_crs = self._layer.crs()
        geom_to_export = QgsGeometry(self._chain_geom)  # work on a copy

        if src_crs != wgs84:
            transform = QgsCoordinateTransform(
                src_crs,
                wgs84,
                QgsProject.instance().transformContext(),
            )
            geom_to_export.transform(transform)

        # Build a WGS84 memory layer with geometry only (GPX schema allows no custom fields)
        mem_layer = QgsMemoryProviderUtils.createMemoryLayer(
            "LineChainer_gpx_tmp",
            QgsFields(),
            QgsWkbTypes.LineString,
            wgs84,
        )
        mem_layer.startEditing()
        feat = QgsFeature()
        feat.setGeometry(geom_to_export)
        mem_layer.addFeature(feat)
        mem_layer.commitChanges()

        # Write to GPX — no reprojection needed since layer is already WGS84
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPX"
        options.fileEncoding = "UTF-8"
        options.layerName = "routes"

        error, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer,
            path,
            QgsProject.instance().transformContext(),
            options,
        )

        if error == QgsVectorFileWriter.NoError:
            self._set_status(self.tr(f"✔ Exported to {path}"))
            gpx_layer = QgsVectorLayer(f"{path}|layername=routes", "LineChainer_GPX", "ogr")
            if gpx_layer.isValid():
                QgsProject.instance().addMapLayer(gpx_layer)
        else:
            self._set_status(self.tr(f"⚠ Export failed: {msg}"))
            QgsMessageLog.logMessage(f"GPX export error: {msg}", "LineChainer", Qgis.Critical)

    # ------------------------------------------------------------------
    # Map tool wiring
    # ------------------------------------------------------------------

    def _activate_map_tool(self):
        if self._map_tool is not None:
            self._map_tool.line_selected.disconnect()
            self._map_tool.cancelled.disconnect()

        self._map_tool = MapToolSelectLine(self.canvas, self._layer)
        self._map_tool.line_selected.connect(self._on_line_selected_from_map)
        self._map_tool.cancelled.connect(self._on_reset)
        self._previous_map_tool = self.canvas.mapTool()
        self.canvas.setMapTool(self._map_tool)

    def deactivate(self):
        """Called when the dock is closed — restore previous map tool."""
        self._clear_rubber_bands()
        if self._previous_map_tool is not None:
            self.canvas.setMapTool(self._previous_map_tool)
        elif self._map_tool is not None:
            self.canvas.unsetMapTool(self._map_tool)

    # ------------------------------------------------------------------
    # Core chaining logic
    # ------------------------------------------------------------------

    def _on_line_selected_from_map(self, fid: int):
        """Handle a click on the map (feature fid selected)."""

        if self._waiting_for == "start":
            self._handle_start(fid)

        elif self._waiting_for == "direction":
            if self._candidates and fid not in self._candidates:
                self._set_status(
                    self.tr("⚠ Please click one of the highlighted candidate lines.")
                )
                return
            self._handle_direction_choice(fid)

        elif self._waiting_for == "next":
            # If candidates are shown, the clicked line must be one of them
            if self._candidates and fid not in self._candidates:
                self._set_status(
                    self.tr("⚠ Please click one of the highlighted candidate lines.")
                )
                return
            self._handle_next(fid)

    def _handle_start(self, fid: int):
        feat = self._get_feature(fid)
        if feat is None:
            return

        self._chain_fids = [fid]
        self._visited_fids = {fid}
        self._chain_geom = QgsGeometry(feat.geometry())
        self._update_chain_rubber_band()

        self.btn_undo.setEnabled(False)
        self.btn_finish.setEnabled(True)
        self.btn_calc_length.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)

        # --- Direction choice ---
        start_pt, end_pt = _endpoints(self._chain_geom)
        cands_at_start = self._find_touching(start_pt, only_visited=False, exclude_fid=fid)
        cands_at_end   = self._find_touching(end_pt,   only_visited=False, exclude_fid=fid)

        both_sides = bool(cands_at_start) and bool(cands_at_end)
        neither    = not cands_at_start and not cands_at_end

        if neither:
            # Isolated segment — nothing to chain
            self.canvas.unsetMapTool(self._map_tool)
            self.btn_export_gpx.setEnabled(True)
            self._set_status(self.tr(
                "✔ This line has no neighbours. "
                "Click « Finish » or « Export GPX »."
            ))

        elif both_sides:
            # Neighbours on both ends → show all candidates and ask user to click
            # the next segment they want, which will implicitly fix the direction
            all_candidates = cands_at_start + cands_at_end
            self._pending_direction_start = start_pt
            self._pending_direction_end   = end_pt
            self._waiting_for = "direction"
            self._show_candidates(all_candidates)
            self._activate_map_tool()
            self._set_status(self.tr(
                "Choose direction: click the next segment "
                "you want to follow (highlighted in orange)."
            ))

        elif cands_at_end:
            # Only the end side has neighbours → natural direction, proceed normally
            self._proceed_from_end()

        else:
            # Only the start side has neighbours → flip the starting segment
            self._chain_geom = _flip_geometry(self._chain_geom)
            self._update_chain_rubber_band()
            self._proceed_from_end()

    def _handle_direction_choice(self, fid: int):
        """
        The user clicked a neighbour to indicate the desired direction.
        Flip the starting segment if necessary so that its END connects
        to the chosen neighbour, then enter the normal chaining loop.
        """
        self._clear_candidates()
        self._waiting_for = "next"

        chosen_feat = self._get_feature(fid)
        if chosen_feat is None:
            return

        chosen_geom = chosen_feat.geometry()
        cs, ce = _endpoints(chosen_geom)

        # The starting segment's current end point
        _, cur_end = _endpoints(self._chain_geom)

        # Check whether the chosen neighbour connects to the current end
        tol = self._offset if self._offset > 0 else 1e-8
        end_connects = (
            _point_distance(cur_end, cs) <= tol or
            _point_distance(cur_end, ce) <= tol
        )

        if not end_connects:
            # The chosen neighbour is on the start side → flip the starting segment
            self._chain_geom = _flip_geometry(self._chain_geom)
            self._update_chain_rubber_band()

        # Now append the chosen segment normally
        self._handle_next(fid)

    def _handle_next(self, fid: int):
        """Append fid to the chain and continue."""

        # --- Intentional reverse revisit (lasso return path) ---
        if self._in_reverse and fid in self._visited_fids:
            feat = self._get_feature(fid)
            if feat is None:
                return
            # Append the segment flipped so we travel it in the reverse direction
            flipped = _flip_geometry(feat.geometry())
            self._chain_geom = _normalize_and_append(self._chain_geom, flipped, self._offset)
            self._chain_fids.append(fid)
            # Do NOT add to _visited_fids — intentional revisit
            self._clear_candidates()
            self._update_chain_rubber_band()
            self.btn_undo.setEnabled(len(self._chain_fids) >= 2)
            self._proceed_from_end_reverse()
            return

        # --- Normal forward case ---
        # If we were in reverse mode but the user picked a new unvisited segment,
        # they chose to go forward again — exit reverse mode.
        if self._in_reverse and fid not in self._visited_fids:
            self._in_reverse = False
            self.btn_return_to_start.setEnabled(False)
        # Loop detection for unintentional revisits
        if fid in self._visited_fids:
            reply = QMessageBox.question(
                self,
                self.tr("Loop detected"),
                self.tr(
                    "This line is already part of the chain.\n"
                    "Do you want to stop here and keep the current chain?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._clear_candidates()
                self.btn_export_gpx.setEnabled(True)
                self._set_status(self.tr(
                    "Chain stopped (loop). Click « Finish » to save as memory layer "
                    "or « Export GPX » to export as GPX file."
                ))
                self.canvas.unsetMapTool(self._map_tool)
                return
            else:
                self._set_status(self.tr("Pick another line."))
                return

        feat = self._get_feature(fid)
        if feat is None:
            return

        next_geom = feat.geometry()
        self._chain_geom = _normalize_and_append(self._chain_geom, next_geom, self._offset)
        self._chain_fids.append(fid)
        self._visited_fids.add(fid)
        self._clear_candidates()
        self._update_chain_rubber_band()

        self.btn_undo.setEnabled(len(self._chain_fids) >= 2)
        self._proceed_from_end()

    def _proceed_from_end(self):
        """Find candidates touching the current chain end and react."""
        end_pt = _endpoints(self._chain_geom)[1]

        # The immediately preceding segment is always touching the current end
        # (it's where we just came from) — exclude it from both searches to
        # avoid treating it as a candidate or a false loop signal.
        last_fid = self._chain_fids[-1] if self._chain_fids else None

        new_candidates    = self._find_touching(end_pt, only_visited=False,  exclude_fid=last_fid)
        looped_candidates = self._find_touching(end_pt, only_visited=True,   exclude_fid=last_fid)

        if not new_candidates and not looped_candidates:
            # True end — no neighbours at all
            self._clear_candidates()
            self.canvas.unsetMapTool(self._map_tool)
            self.btn_export_gpx.setEnabled(True)
            self._set_status(
                self.tr(
                    "✔ No more connecting lines found. "
                    "Click « Finish » to save as memory layer, "
                    "« Export GPX » to save as GPX file, "
                    "or « Undo last » to go back."
                )
            )

        elif not new_candidates and looped_candidates:
            # Only already-visited segments touch this end → closed loop.
            # Memorise the original chain at this point so "Return to start"
            # always knows the full forward path regardless of manual steps taken.
            self._original_chain_fids = list(self._chain_fids)
            self.btn_return_to_start.setEnabled(True)
            self._in_reverse = True  # flag: revisits are now intentional
            self._show_candidates(looped_candidates, visited_fids=set(looped_candidates))
            self._waiting_for = "next"
            self._activate_map_tool()
            self._set_status(
                self.tr(
                    "🔁 Loop closed. Click a segment (in red) to continue manually in reverse, "
                    "or click « Return to start » for automatic reverse, "
                    "or « Finish » / « Export GPX » to save as-is."
                )
            )

        elif len(new_candidates) == 1 and not looped_candidates:
            # Single new candidate, no loop ambiguity → auto-advance
            self._set_status(self.tr("Auto-connecting the only touching line…"))
            self._handle_next(new_candidates[0])

        else:
            # Multiple new candidates, OR new candidates + a loop candidate
            # → show everything and let the user choose
            all_candidates = new_candidates + looped_candidates
            if looped_candidates:
                # There are already-visited segments in the mix — this could be
                # a lasso junction, so offer "Return to start" right away
                self._original_chain_fids = list(self._chain_fids)
                self._in_reverse = True
                self.btn_return_to_start.setEnabled(True)
            self._show_candidates(all_candidates, visited_fids=set(looped_candidates))
            self._waiting_for = "next"
            self._activate_map_tool()

    def _proceed_from_end_reverse(self):
        """
        In reverse mode: find visited segments touching the current end
        and always present them for manual selection (no auto-advance).
        Stop when we reach the original starting point (no more visited neighbours).
        """
        end_pt = _endpoints(self._chain_geom)[1]
        last_fid = self._chain_fids[-1] if self._chain_fids else None

        looped_candidates = self._find_touching(end_pt, only_visited=True,  exclude_fid=last_fid)
        new_candidates    = self._find_touching(end_pt, only_visited=False, exclude_fid=last_fid)

        if not looped_candidates and not new_candidates:
            # No more neighbours → we've reached the starting point
            self._in_reverse = False
            self._clear_candidates()
            self.canvas.unsetMapTool(self._map_tool)
            self.btn_export_gpx.setEnabled(True)
            self.btn_return_to_start.setEnabled(False)
            self._set_status(
                self.tr(
                    "✔ Returned to start. "
                    "Click « Finish » to save as memory layer or « Export GPX »."
                )
            )
        else:
            # Always show candidates manually in reverse — never auto-advance.
            # Keep "Return to start" active so the user can switch to automatic
            # at any point during the manual reverse.
            all_candidates = new_candidates + looped_candidates
            self.btn_return_to_start.setEnabled(True)
            self._show_candidates(all_candidates, visited_fids=set(looped_candidates))
            self._waiting_for = "next"
            self._activate_map_tool()
            if new_candidates:
                self._set_status(self.tr(
                    "Choose next segment: orange = new direction, "
                    "red = continue reverse. « Return to start » to finish automatically."
                ))
            else:
                self._set_status(self.tr(
                    "🔁 Click the next segment to follow back (red), "
                    "or « Return to start » to finish automatically."
                ))

    def _find_touching(self, point: QgsPointXY, only_visited: bool, exclude_fid: int = None) -> List[int]:
        """
        Return fids of features whose start or end point is within *offset*
        (or exactly equal when offset == 0) of *point*.

        only_visited=False  → return only segments NOT yet in the chain
        only_visited=True   → return only segments already in the chain
        exclude_fid         → always skip this fid (used to ignore the last segment)
        """
        tol = self._offset if self._offset > 0 else 1e-8
        rect = QgsRectangle(
            point.x() - tol, point.y() - tol,
            point.x() + tol, point.y() + tol,
        )
        request = QgsFeatureRequest().setFilterRect(rect)
        result = []
        for feat in self._layer.getFeatures(request):
            fid = feat.id()
            if fid == exclude_fid:
                continue
            is_visited = fid in self._visited_fids
            if only_visited != is_visited:
                continue
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            s, e = _endpoints(geom)
            if _point_distance(point, s) <= tol or _point_distance(point, e) <= tol:
                result.append(fid)
        return result

    # ------------------------------------------------------------------
    # Candidate UI
    # ------------------------------------------------------------------

    def _show_candidates(self, fids: List[int], visited_fids: set = None):
        if visited_fids is None:
            visited_fids = set()
        self._candidates = fids
        self._clear_candidate_rubber_bands()

        self.list_candidates.clear()
        for fid in fids:
            feat = self._get_feature(fid)
            if feat is None:
                continue
            is_loop = fid in visited_fids
            # Orange for new segments, red for already-visited (loop) segments
            color = QColor(220, 50, 50, 200) if is_loop else QColor(255, 165, 0, 180)
            rb = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
            rb.setColor(color)
            rb.setWidth(4)
            rb.setToGeometry(feat.geometry(), self._layer)
            self._rb_candidates.append(rb)

            label = (
                self.tr(f"Line (id={fid}) ⚠ already in chain")
                if is_loop
                else self.tr(f"Line (id={fid})")
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, fid)
            self.list_candidates.addItem(item)

        self.grp_candidates.setVisible(True)
        self._set_status(
            self.tr(
                "Multiple touching lines found. "
                "Click one on the map or select it in the list."
            )
        )

    def _on_candidate_item_clicked(self, item: QListWidgetItem):
        fid = item.data(Qt.UserRole)
        self._handle_next(fid)

    def _clear_candidates(self):
        self._candidates = []
        self._clear_candidate_rubber_bands()
        self.list_candidates.clear()
        self.grp_candidates.setVisible(False)

    def _clear_candidate_rubber_bands(self):
        for rb in self._rb_candidates:
            rb.reset(QgsWkbTypes.LineGeometry)
        self._rb_candidates = []

    # ------------------------------------------------------------------
    # Rubber band update
    # ------------------------------------------------------------------

    def _update_chain_rubber_band(self):
        self._rb_chain.reset(QgsWkbTypes.LineGeometry)
        if self._chain_geom and not self._chain_geom.isEmpty():
            self._rb_chain.setToGeometry(self._chain_geom, self._layer)

    def _clear_rubber_bands(self):
        self._rb_chain.reset(QgsWkbTypes.LineGeometry)
        self._clear_candidate_rubber_bands()

    # ------------------------------------------------------------------
    # Memory layer output
    # ------------------------------------------------------------------

    def _build_result_memory_layer(self) -> QgsVectorLayer:
        """Build and return a memory layer with the current chain (not added to project)."""
        crs: QgsCoordinateReferenceSystem = self._layer.crs()
        crs_str = crs.authid()

        # Compute length
        da = QgsDistanceArea()
        da.setSourceCrs(crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        length_km = round(da.measureLength(self._chain_geom) / 1000.0, 3) if self._chain_geom else 0.0

        # Build layer via URI — avoids QgsField constructor entirely
        uri = f"LineString?crs={crs_str}&field=chain_length:integer&field=source_layer:string&field=length_km:double"
        mem_layer = QgsVectorLayer(uri, "LineChainer_result", "memory")

        mem_layer.startEditing()
        feat = QgsFeature(mem_layer.fields())
        feat.setGeometry(self._chain_geom)
        feat.setAttribute("chain_length", len(self._chain_fids))
        feat.setAttribute("source_layer", self._layer.name())
        feat.setAttribute("length_km", length_km)
        mem_layer.addFeature(feat)
        mem_layer.commitChanges()
        return mem_layer

    def _save_to_memory_layer(self):
        mem_layer = self._build_result_memory_layer()
        QgsProject.instance().addMapLayer(mem_layer)
        self._result_layer = mem_layer

    def _find_result_layer(self) -> Optional[QgsVectorLayer]:
        """Return the last result memory layer added to the project, if any."""
        return getattr(self, "_result_layer", None)

    # ------------------------------------------------------------------
    # Geometry rebuild (for undo)
    # ------------------------------------------------------------------

    def _rebuild_chain_geometry(self):
        """Re-merge all features in self._chain_fids from scratch."""
        if not self._chain_fids:
            self._chain_geom = None
            return
        first_feat = self._get_feature(self._chain_fids[0])
        if first_feat is None:
            self._chain_geom = None
            return
        self._chain_geom = QgsGeometry(first_feat.geometry())
        for fid in self._chain_fids[1:]:
            feat = self._get_feature(fid)
            if feat is None:
                continue
            self._chain_geom = _normalize_and_append(
                self._chain_geom, feat.geometry(), self._offset
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_feature(self, fid: int) -> Optional[QgsFeature]:
        feats = list(self._layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)))
        return feats[0] if feats else None

    def _set_status(self, msg: str):
        self.lbl_status.setText(msg)

    def _reset_state(self):
        self._clear_rubber_bands()
        self._clear_candidates()
        if self._map_tool is not None:
            self.canvas.unsetMapTool(self._map_tool)
            self._map_tool = None
        self._chain_fids = []
        self._visited_fids = set()
        self._chain_geom = None
        self._waiting_for = "start"
        self._in_reverse = False
        self._original_chain_fids = []
        self._pending_direction_start = None
        self._pending_direction_end = None
        self.btn_finish.setEnabled(False)
        self.btn_undo.setEnabled(False)