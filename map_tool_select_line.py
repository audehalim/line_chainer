# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LineChainer — map_tool_select_line.py
 Custom QgsMapTool: click on the canvas to identify the nearest polyline
 feature in the active layer within a pixel tolerance.
 ***************************************************************************/
"""

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QCursor, QColor
from qgis.core import (
    QgsPointXY,
    QgsRectangle,
    QgsFeatureRequest,
    QgsWkbTypes,
    QgsGeometry,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsMapCanvas
from qgis.core import QgsMessageLog, Qgis

def log(msg, level=Qgis.Info):
    QgsMessageLog.logMessage(str(msg), "LineChainer", level)


class MapToolSelectLine(QgsMapTool):
    """Map tool that emits the clicked feature ID for a given vector layer.

    Signals
    -------
    line_selected(int)
        Emitted with the feature ID when the user clicks near a polyline.
    cancelled()
        Emitted when the user presses Escape.
    """

    line_selected = pyqtSignal(int)
    cancelled = pyqtSignal()

    # Pixel radius used to search for features around the click point
    PIXEL_TOLERANCE = 10

    def __init__(self, canvas: QgsMapCanvas, layer):
        super().__init__(canvas)
        self.layer = layer
        self.canvas = canvas

        # Highlight rubber band for hover feedback (optional, lightweight)
        self._hover_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._hover_band.setColor(QColor(255, 165, 0, 180))  # orange
        self._hover_band.setWidth(3)

        self.setCursor(QCursor(Qt.CrossCursor))

    # ------------------------------------------------------------------
    # QgsMapTool overrides
    # ------------------------------------------------------------------

    def canvasReleaseEvent(self, event):
        """Identify the feature closest to the click and emit line_selected."""
        if event.button() != Qt.LeftButton:
            return

        point = self.toLayerCoordinates(self.layer, event.pos())
        fid = self._find_feature_at(point)
        if fid is not None:
            self.line_selected.emit(fid)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._clear_hover()
            self.cancelled.emit()

    def deactivate(self):
        self._clear_hover()
        super().deactivate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_feature_at(self, point: QgsPointXY):
        """Return the fid of the feature nearest to *point*, or None."""
        # Build a search rectangle in layer CRS from pixel tolerance
        tol = self.PIXEL_TOLERANCE * self._map_units_per_pixel()
        rect = QgsRectangle(
            point.x() - tol,
            point.y() - tol,
            point.x() + tol,
            point.y() + tol,
        )

        request = QgsFeatureRequest().setFilterRect(rect).setNoAttributes()
        best_fid = None
        best_dist = float("inf")

        for feat in self.layer.getFeatures(request):
            geom = feat.geometry()
            log(QgsGeometry.fromPointXY(QgsPointXY(point.x(), point.y())))
            log(geom)

            if geom is None or geom.isEmpty():
                log("oui")
                continue
            dist = geom.distance(QgsGeometry.fromPointXY(QgsPointXY(point.x(), point.y())))
            #if False  # placeholder — use closestSegmentWithContext below
            #else log("false") # geom.closestSegmentWithContext(point)[0]
                #closestSegmentWithContext returns (sqDist, minDistPoint, nextVertexIndex, leftOf)
                #index [0] is the squared distance — we compare directly
            #)
            # Re-do properly: closestSegmentWithContext gives squared distance
            sq_dist = geom.closestSegmentWithContext(point)[0]
            if sq_dist < best_dist:
                best_dist = sq_dist
                best_fid = feat.id()

        return best_fid

    def _map_units_per_pixel(self) -> float:
        """Return the current map-units-per-pixel ratio."""
        extent = self.canvas.extent()
        width = self.canvas.width()
        if width == 0:
            return 1.0
        return extent.width() / width

    def _clear_hover(self):
        self._hover_band.reset(QgsWkbTypes.LineGeometry)
