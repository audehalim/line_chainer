# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LineChainer
                                 A QGIS Plugin
 Chain multiple touching polylines into a single polyline
                             -------------------
        begin                : 2024-01-01
        copyright            : (C) 2024 by Your Name
        email                : your@email.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""


def classFactory(iface):
    """Load LineChainer class from file line_chainer.py

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .line_chainer import LineChainer
    return LineChainer(iface)
