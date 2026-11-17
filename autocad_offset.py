# -*- coding: utf-8 -*-
import math
import os

from qgis.PyQt.QtCore import Qt, QVariant, QTimer
from qgis.PyQt.QtWidgets import (
    QMessageBox, QInputDialog, QDoubleSpinBox, QDialog,
    QVBoxLayout, QLabel, QDialogButtonBox
)
try:
    # QAction moved from QtWidgets to QtGui in Qt 6.
    from qgis.PyQt.QtGui import QAction, QIcon
except ImportError:  # QGIS 3 / Qt 5
    from qgis.PyQt.QtWidgets import QAction
    from qgis.PyQt.QtGui import QIcon
from qgis.gui import QgsMapTool, QgsSnapIndicator
from qgis.core import (
    Qgis, QgsProject, QgsPointXY, QgsGeometry, QgsFeature, QgsField,
    QgsVectorLayer, QgsWkbTypes, QgsCoordinateTransform, QgsMapLayerType,
    QgsUnitTypes, QgsSnappingConfig, QgsTolerance
)


def _enum(owner, scoped_name, legacy_name):
    """Return a Qt/QGIS enum member across Qt 5/6 and QGIS 3/4."""
    scoped = getattr(owner, scoped_name, None)
    if scoped is not None and hasattr(scoped, legacy_name):
        return getattr(scoped, legacy_name)
    return getattr(owner, legacy_name)


QT_CROSS_CURSOR = _enum(Qt, "CursorShape", "CrossCursor")
QT_LEFT_BUTTON = _enum(Qt, "MouseButton", "LeftButton")
QT_KEY_ESCAPE = _enum(Qt, "Key", "Key_Escape")
QT_KEY_BACKSPACE = _enum(Qt, "Key", "Key_Backspace")
QT_KEY_DELETE = _enum(Qt, "Key", "Key_Delete")
QT_KEY_RETURN = _enum(Qt, "Key", "Key_Return")
QT_KEY_ENTER = _enum(Qt, "Key", "Key_Enter")
DIALOG_ACCEPTED = _enum(QDialog, "DialogCode", "Accepted")
BUTTON_OK = _enum(QDialogButtonBox, "StandardButton", "Ok")
BUTTON_CANCEL = _enum(QDialogButtonBox, "StandardButton", "Cancel")


def _dialog_exec(dialog):
    """Use the dialog execution method available in PyQt5 and PyQt6."""
    execute = getattr(dialog, "exec", None)
    if execute is None:
        execute = dialog.exec_
    return execute()


def _message_level_info():
    return _enum(Qgis, "MessageLevel", "Info")


class OffsetMapTool(QgsMapTool):
    def __init__(self, canvas, plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.plugin = plugin
        self.setCursor(QT_CROSS_CURSOR)
        self.indicator = QgsSnapIndicator(canvas)

    def canvasMoveEvent(self, event):
        try:
            match = self.canvas.snappingUtils().snapToMap(event.pos())
            self.indicator.setMatch(match if match.isValid() else None)
        except Exception:
            self.indicator.setMatch(None)

    def canvasReleaseEvent(self, event):
        if event.button() != QT_LEFT_BUTTON:
            return
        try:
            match = self.canvas.snappingUtils().snapToMap(event.pos())
            point = QgsPointXY(match.point()) if match.isValid() else QgsPointXY(
                self.toMapCoordinates(event.pos()))
            self.plugin.capture_baseline_point(point, match.isValid())
        except Exception as exc:
            QMessageBox.critical(self.plugin.iface.mainWindow(), "AutoCAD OFFSET",
                                 "Point capture failed:\n{}".format(exc))

    def keyPressEvent(self, event):
        if event.key() == QT_KEY_ESCAPE:
            self.plugin.cancel()
        elif event.key() in (QT_KEY_BACKSPACE, QT_KEY_DELETE):
            self.plugin.undo_point()
        elif event.key() in (QT_KEY_RETURN, QT_KEY_ENTER):
            self.plugin.repeat_last()

    def clear_indicator(self):
        try:
            self.indicator.setMatch(None)
        except Exception:
            pass


class DistanceDialog(QDialog):
    def __init__(self, parent, units, last_values=None):
        super().__init__(parent)
        self.setWindowTitle("AutoCAD OFFSET - Cutting / Offset / Prolong")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "CUTTING DISTANCE: along First Point → Second Point.\n"
            "Positive = forward; negative = backward."))
        self.cutting = QDoubleSpinBox()
        self.cutting.setDecimals(0)
        self.cutting.setRange(-1e12, 1e12)
        self.cutting.setValue(last_values[0] if last_values else 0.0)
        self.cutting.setSuffix(" Links")
        layout.addWidget(self.cutting)

        layout.addWidget(QLabel(
            "OFFSET DISTANCE: positive = RIGHT; negative = LEFT."))
        self.offset = QDoubleSpinBox()
        self.offset.setDecimals(0)
        self.offset.setRange(-1e12, 1e12)
        self.offset.setValue(last_values[1] if last_values else 0.0)
        self.offset.setSuffix(" Links")
        layout.addWidget(self.offset)

        layout.addWidget(QLabel(
            "PROLONG: extra length beyond Second Point. "
            "Example: baseline 100 m + prolong 20 m = forward range 120 m."))
        self.prolong = QDoubleSpinBox()
        self.prolong.setDecimals(0)
        self.prolong.setRange(0.0, 1e12)
        self.prolong.setValue(last_values[2] if last_values else 0.0)
        self.prolong.setSuffix(" Links")
        layout.addWidget(self.prolong)

        layout.addWidget(QLabel(
             "MEASUREMENT MULTIPLIER (Into): default 0.2 map units per Link. "
            "Cutting / Offset / Prolong entries are whole-number Links."))
        self.multiplier = QDoubleSpinBox()
        self.multiplier.setDecimals(4)
        self.multiplier.setRange(0.0001, 1e6)
        self.multiplier.setValue(last_values[3] if last_values else 0.2)
        layout.addWidget(self.multiplier)

        buttons = QDialogButtonBox(BUTTON_OK | BUTTON_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (self.cutting.value(), self.offset.value(),
                self.prolong.value(), self.multiplier.value())


class AutoCADOffsetPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.tool = None
        self.old_tool = None
        self.points = []
        self.layer = None
        self.last_values = None
        self.output = None
        self.prolong_output = None
        self.previous_snapping_config = None

    def initGui(self):
        self.action = QAction(QIcon(os.path.join(os.path.dirname(__file__), "icon.png")),
                             "AutoCAD OFFSET", self.iface.mainWindow())
        self.action.setToolTip(
            "First Point > Second Point > Cutting Distance / Offset Distance / Prolong")
        self.action.triggered.connect(self.start)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&AutoCAD OFFSET", self.action)

    def unload(self):
        self.restore_tool()
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&AutoCAD OFFSET", self.action)

    def start(self):
        active = self.iface.activeLayer()
        if (active is None or not active.isValid()
                or active.type() != QgsMapLayerType.VectorLayer
                or QgsWkbTypes.geometryType(active.wkbType()) != QgsWkbTypes.PointGeometry):
            QMessageBox.warning(
                self.iface.mainWindow(), "AutoCAD OFFSET",
                "Munduga QGIS lo target Point Layer select cheyyandi.\n"
                "OFFSET points aa selected Point Layer lone add avuthayi.")
            return

        self.layer = active
        if self.layer.crs().isGeographic():
            QMessageBox.warning(
                self.iface.mainWindow(), "AutoCAD OFFSET",
                "Selected Point Layer uses degrees. Reproject it to a projected CRS (metres/feet) first.")
            return

        self.points = []
        self.last_values = None
        self.output = self.layer

        # Enable snapping on visible line layers (Vertex + Segment) so clicks
        # can snap anywhere on line edges, while also allowing point snapping.
        project = QgsProject.instance()
        self.previous_snapping_config = project.snappingConfig()
        snap_config = project.snappingConfig()
        snap_config.setEnabled(True)
        snap_config.setMode(QgsSnappingConfig.AdvancedConfiguration)

        root = project.layerTreeRoot()
        for lyr in project.mapLayers().values():
            if not lyr.isValid() or lyr.type() != QgsMapLayerType.VectorLayer:
                continue
            node = root.findLayer(lyr.id())
            if node is None or not node.isVisible():
                continue
            geom_type = QgsWkbTypes.geometryType(lyr.wkbType())
            if geom_type == QgsWkbTypes.LineGeometry:
                snap_type = QgsSnappingConfig.VertexAndSegment
            elif geom_type == QgsWkbTypes.PointGeometry:
                snap_type = QgsSnappingConfig.Vertex
            else:
                continue
            layer_settings = QgsSnappingConfig.IndividualLayerSettings(
                True, snap_type, 12, QgsTolerance.Pixels)
            snap_config.setIndividualLayerSettings(lyr, layer_settings)
        project.setSnappingConfig(snap_config)

        self.old_tool = self.iface.mapCanvas().mapTool()
        self.tool = OffsetMapTool(self.iface.mapCanvas(), self)
        self.iface.mapCanvas().setMapTool(self.tool)
        self.prompt("Selected output Point Layer: {}. Click FIRST POINT; line edges support Vertex + Segment snap."
                    .format(self.layer.name()))

    def capture_baseline_point(self, canvas_point, snapped):
        if self.layer is None:
            return
        # Once two source points are fixed, map clicks do not change the baseline.
        if len(self.points) >= 2:
            return
        tr = QgsCoordinateTransform(
            self.iface.mapCanvas().mapSettings().destinationCrs(),
            self.layer.crs(), QgsProject.instance())
        point = QgsPointXY(tr.transform(canvas_point))
        self.points.append(point)

        if len(self.points) == 1:
            self.prompt("2/2 SECOND POINT: click the end of the baseline." +
                        (" [SNAPPED]" if snapped else ""))
            return

        if len(self.points) >= 2:
            p1, p2 = self.points[:2]
            length = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
            if length <= 1e-12:
                self.points = []
                self.prompt("First and Second Points must be different. Click FIRST POINT again.")
                return
            self.prompt("Baseline set: {:.4f} {}. Enter Cutting Distance, Offset Distance and Prolong."
                        .format(length, QgsUnitTypes.toString(self.layer.crs().mapUnits())))
            self.make_offset()

    def make_offset(self):
        if len(self.points) < 2:
            return
        p1, p2 = self.points[:2]
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return

        units = QgsUnitTypes.toString(self.layer.crs().mapUnits())
        dlg = DistanceDialog(self.iface.mainWindow(), units, self.last_values)
        if _dialog_exec(dlg) != DIALOG_ACCEPTED:
            self.cancel()
            return

        cutting_input, offset_input, prolong_input, multiplier = dlg.values()
        # "Into" is a measurement multiplier. Apply it to all three entered values.
        cutting_dist = cutting_input * multiplier
        offset_dist = offset_input * multiplier
        prolong_dist = prolong_input * multiplier

        # The usable forward range is baseline length plus the entered prolong.
        # Negative cutting distances are allowed backwards from First Point.
        if cutting_dist > length + prolong_dist + 1e-9:
            QMessageBox.warning(
                self.iface.mainWindow(), "AutoCAD OFFSET",
                "Cutting Distance exceeds the baseline + Prolong range.\n"
                "Baseline: {:.4f} {}\nProlong: {:.4f} {}\nMaximum forward distance: {:.4f} {}"
                .format(length, units, prolong_dist, units, length + prolong_dist, units))
            self.last_values = (cutting_input, offset_input, prolong_input, multiplier)
            return

        ux, uy = dx / length, dy / length
        right_x, right_y = uy, -ux
        base_point = QgsPointXY(p1.x() + ux * cutting_dist,
                                p1.y() + uy * cutting_dist)
        out_point = QgsPointXY(base_point.x() + right_x * offset_dist,
                               base_point.y() + right_y * offset_dist)

        self.ensure_output_layer()
        if self.output is None:
            return
        if not self.output.isEditable() and not self.output.startEditing():
            raise RuntimeError("Could not start output point layer editing.")

        feat = QgsFeature(self.output.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(out_point))
        # Preserve the selected layer schema; new attributes are left NULL.
        feat.setAttributes([None] * len(self.output.fields()))
        side = "RIGHT" if offset_dist >= 0 else "LEFT"
        if not self.output.addFeature(feat):
            raise RuntimeError("Could not create offset point.")

        self.output.updateExtents()
        # Prolong is only an allowed extra distance for placing points.
        # No separate line layer is created; all results remain in the point layer.

        self.last_values = (cutting_input, offset_input, prolong_input, multiplier)
        self.iface.mapCanvas().refresh()
        self.prompt(
            "Point created. Next Cutting Distance and Offset Distance are ready. "
            "First and Second Source Points remain fixed; press ESC/Cancel to finish.")
        # Keep the same baseline active and automatically open the next measurement entry.
        self.points = [p1, p2]
        QTimer.singleShot(150, self._safe_make_offset)

    def _safe_make_offset(self):
        try:
            if self.tool is None or self.layer is None:
                return
            self.make_offset()
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(), "AutoCAD OFFSET",
                "Could not create the point.\n{}\n\n"
                "Check that the selected Point Layer is editable and has a valid projected CRS."
                .format(exc))
            self.cancel()

    def ensure_output_layer(self):
        # Output is always the point layer selected by the user.
        self.output = self.layer
        if self.output is None or not self.output.isValid():
            raise RuntimeError("Select a valid target Point Layer first.")
        if QgsWkbTypes.geometryType(self.output.wkbType()) != QgsWkbTypes.PointGeometry:
            raise RuntimeError("The selected output layer must be a Point Layer.")
        if not self.output.isEditable():
            if not self.output.startEditing():
                raise RuntimeError(
                    "Could not start editing the selected Point Layer. "
                    "Check layer permissions and provider.")

    def add_or_update_prolong_line(self, p1, p2, distance):
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return
        ux, uy = dx / length, dy / length
        end = QgsPointXY(p2.x() + ux * distance, p2.y() + uy * distance)

        if not self.prolong_output or not self.prolong_output.isValid():
            self.prolong_output = QgsVectorLayer(
                "LineString?crs={}".format(self.layer.crs().authid()),
                "AutoCAD_PROLONG", "memory")
            self.prolong_output.dataProvider().addAttributes([
                QgsField("distance", QVariant.Double)])
            self.prolong_output.updateFields()
            QgsProject.instance().addMapLayer(self.prolong_output)

        # Replace the previous helper prolong geometry with the current one.
        ids = [f.id() for f in self.prolong_output.getFeatures()]
        if ids:
            self.prolong_output.dataProvider().deleteFeatures(ids)
        f = QgsFeature(self.prolong_output.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(p2), end]))
        f.setAttributes([distance])
        self.prolong_output.dataProvider().addFeature(f)
        self.prolong_output.updateExtents()

    def repeat_last(self):
        if len(self.points) < 2:
            self.prompt("Pick First Point and Second Point before entering distances.")
            return
        self.make_offset()

    def undo_point(self):
        if self.points:
            self.points.pop()
            self.prompt("Last baseline point removed. Start again from FIRST POINT.")
        else:
            self.prompt("No point to undo.")

    def prompt(self, message):
        self.iface.messageBar().pushMessage(
            "AutoCAD OFFSET", message, level=_message_level_info(), duration=0)

    def cancel(self):
        self.prompt("OFFSET command finished.")
        self.restore_tool()

    def restore_tool(self):
        if self.tool:
            self.tool.clear_indicator()
        canvas = self.iface.mapCanvas()
        if self.old_tool is not None:
            canvas.setMapTool(self.old_tool)
        elif self.tool is not None:
            canvas.unsetMapTool(self.tool)
        self.tool = None
        self.old_tool = None
        if self.previous_snapping_config is not None:
            try:
                QgsProject.instance().setSnappingConfig(self.previous_snapping_config)
            except Exception:
                pass
            self.previous_snapping_config = None
