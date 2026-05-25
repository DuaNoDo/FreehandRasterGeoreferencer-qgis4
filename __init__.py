# -*- coding: utf-8 -*-
"""
/***************************************************************************
 FreehandRasterGeoreferencer
                                 A QGIS plugin
 Interactive georeferencing of rasters
                             -------------------
        copyright            : (C) 2018 Guilhem Vellut
                             : (C) 2026 Taeyeon Won
        email                : teadone@konkuk.ac.kr
        original author      : Guilhem Vellut (g@vellut.com)
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
    from .freehandrastergeoreferencer import FreehandRasterGeoreferencer
    return FreehandRasterGeoreferencer(iface)
