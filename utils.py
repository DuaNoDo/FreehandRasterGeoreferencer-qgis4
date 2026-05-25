"""
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os.path

from qgis.core import QgsMessageLog, Qgis, QgsProject

# constants for saving data inside QGS
SETTINGS_KEY = "FreehandRasterGeoreferencer"
SETTING_BROWSER_RASTER_DIR = "browseRasterDir"


def debug_log(msg):
    QgsMessageLog.logMessage(
        str(msg), "FreehandRasterGeoreferencer", Qgis.MessageLevel.Info
    )


def toRelativeToQGS(imagePath):
    qgsPath = QgsProject.instance().fileName()
    if qgsPath and os.path.isabs(imagePath):
        # Make it relative to current project if image below QGS
        imageFolder, imageName = os.path.split(imagePath)
        qgsFolder, _ = os.path.split(qgsPath)
        imageFolder = os.path.abspath(imageFolder)
        qgsFolder = os.path.abspath(qgsFolder)

        if imageFolder.startswith(qgsFolder):
            # relative
            imageFolderRelPath = os.path.relpath(imageFolder, qgsFolder)
            imagePath = os.path.join(imageFolderRelPath, imageName)
            debug_log(imagePath)

    return imagePath


def tryfloat(strF):
    try:
        f = float(strF)
        return f
    except ValueError:
        return None


def imageFormat(path):
    _, extension = os.path.splitext(path)
    extension = extension.lstrip(".").lower()
    if extension == "tiff":
        extension = "tif"
    return extension
