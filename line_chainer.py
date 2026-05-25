# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LineChainer — line_chainer.py
 Plugin entry point: menu registration, toolbar, lifecycle.
 ***************************************************************************/
"""

import os
from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget
from qgis.core import QgsApplication

from .line_chainer_dialog import LineChainerDialog
from . import resources  # noqa: F401  — compiled resources (icon, etc.)


class LineChainer:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: A QGIS interface instance.
        :type iface: QgsInterface
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # --- i18n --------------------------------------------------------
        locale = QSettings().value("locale/userLocale", "en")[0:2]
        locale_path = os.path.join(
            self.plugin_dir, "i18n", f"line_chainer_{locale}.qm"
        )
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&LineChainer")
        self.dock_widget = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def tr(self, message):
        return QCoreApplication.translate("LineChainer", message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.iface.addToolBarIcon(action)
        if add_to_menu:
            self.iface.addPluginToVectorMenu(self.menu, action)

        self.actions.append(action)
        return action

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(
            icon_path,
            text=self.tr("Chain Polylines"),
            callback=self.run,
            parent=self.iface.mainWindow(),
            status_tip=self.tr("Chain multiple touching polylines into one"),
        )

    def unload(self):
        """Remove the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)

        # Close and remove the dock widget if open
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """Show (or bring to front) the LineChainer dock panel."""
        if self.dock_widget is None:
            self.dock_widget = QDockWidget(self.tr("LineChainer"), self.iface.mainWindow())
            self.dock_widget.setObjectName("LineChainerDock")
            self.dock_widget.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

            self.dialog = LineChainerDialog(self.iface, self.dock_widget)
            self.dock_widget.setWidget(self.dialog)

            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
            self.dock_widget.visibilityChanged.connect(self._on_dock_visibility_changed)
        else:
            self.dock_widget.show()
            self.dock_widget.raise_()

    def _on_dock_visibility_changed(self, visible):
        """Clean up the map tool when the dock is closed."""
        if not visible and self.dock_widget is not None:
            self.dialog.deactivate()
