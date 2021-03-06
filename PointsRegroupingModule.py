# -*- coding: utf-8 -*-

import math
from functools import partial

from PyQt4.QtCore import *
from PyQt4.QtGui import *

from qgis.core import *
from qgis.gui import *

from random import random


class PointsRegroupingProcessor(QgsMapTool):

    _name = "Points Regrouping Processor"

    def __init__(self, canvas, action, kind):
        self.canvas = canvas
        self.active = False
        QgsMapTool.__init__(self, self.canvas)
        self.setAction(action)
        self.rubberBand = QgsRubberBand(self.canvas, QGis.Polygon)
        mFillColor = QColor(254, 178, 76, 63)

        self.rubberBand.setColor(mFillColor)
        self.rubberBand.setWidth(1)

        self.kind = kind
        self.reset()

    def info(self, msg):
        QMessageBox.information(None, "DEBUG:", str(msg))

    def warning(self, msg):
        QMessageBox.warning(None, self._name, str(msg))

    def generate_points(self, point, polygon_feature):
        # WARNING IS POTENTIAL BUG PLACE
        processed_flag = '/processed'
        try:
            count = point['access']
            processed = processed_flag in count
            count = int(count.split(processed_flag)[0])
        except (UnicodeEncodeError, ValueError):
            count = None

        if not count:
            self.warning('No valid access parameter, must be digit > 0.')
            return

        if not processed:
            point['access'] += processed_flag
            for key in 'room', 'L_room', 'all_area', 'NL_area', 'CP_area', 'parcel_are':
                try:
                    if key in ('room', 'L_room', ):
                        _type = partial(round, ndigits=2)
                    else:
                        _type = float
                    point[key] = str(_type(float(point[key]) / count))
                except (TypeError, ValueError):
                    pass

        polygon = polygon_feature.geometry()
        return {'random': self.random_points, 'linear': self.linear_points}[self.kind](point, polygon, count)

    def qgisdist(self, point1, point2):
        return math.sqrt(point1.sqrDist(point2))

    def linear_points(self, point, polygon, count):
        polygon = polygon.asPolygon()[0]
        dist = 0
        A = B = None
        cx = cy = 0
        for indx, pnt in enumerate(polygon[1:], start=1):
            _dist = self.qgisdist(pnt, polygon[indx - 1])
            if _dist > dist:
                dist = _dist
                A, B = pnt, polygon[indx - 1]
            cx += pnt[0]
            cy += pnt[1]

        cx /= len(polygon) - 1
        cy /= len(polygon) - 1

        step = dist / (count)
        stepx = step / 2.

        A, B = (QgsPoint(item) for item in sorted([A, B], key=lambda item: item[0]))

        def K(a, b):
            k = (b.y() - a.y()) / (b.x() - a.x()) if b.x() != a.x() else 0
            return math.atan(k), k

        k, _k = K(A, B)

        def xy(x0, y0, step, k=k):
            return x0 + math.cos(k) * step, y0 + math.sin(k) * step

        def _xy(ax, ay, cx, cy, k=_k):
            if not k:
                x = cx
            else:
                x = (ax / k + ay + k * cx - cy) / (k + 1 / k)
            y = k * (x - cx) + cy
            return x, y

        x0, y0 = xy(A.x(), A.y(), stepx)
        x0, y0 = _xy(x0, y0, cx, cy)

        features = []
        for i in xrange(count):
            feature = QgsFeature()
            feature.setAttributes(point.attributes())
            feature.setGeometry(QgsGeometry.fromPoint(QgsPoint(x0, y0)))
            features.append(feature)
            x0, y0 = xy(x0, y0, step)
        return features

    def random_points(self, point, polygon, count):
        rect = polygon.boundingBox()
        maxx, maxy, minx, miny = [getattr(rect, key)() for key in ('xMaximum', 'yMaximum', 'xMinimum', 'yMinimum')]

        dis = polygon.length() / (3. * count)
        maxiterations = count * 100
        iteration = 0

        points = []
        while len(points) < count:
            iteration += 1
            random_point = QgsPoint(
                minx + (random() * (maxx - minx)), miny + (random() * (maxy - miny)))
            if not polygon.contains(random_point) or any(filter(
                    lambda item: iteration < maxiterations and self.qgisdist(item, random_point) < dis, points)):
                continue
            points.append(random_point)

        features = []
        for geom in points:
            feature = QgsFeature()
            feature.setAttributes(point.attributes())
            feature.setGeometry(QgsGeometry.fromPoint(geom))
            features.append(feature)
        return features

    def remove_points(self, layer, polygon_feature):
        index = QgsSpatialIndex()
        for feature in layer.getFeatures():
            index.insertFeature(feature)

        polygon = polygon_feature.geometry()
        candidats = index.intersects(polygon.boundingBox())

        request = QgsFeatureRequest()
        request.setFilterFids(candidats)

        for feature in layer.getFeatures(request):
            if polygon.contains(feature.geometry().asPoint()):
                yield feature.id()
        for feature in layer.selectedFeatures():
            yield feature.id()

    def do_points(self):
        point_layer = polygon = None
        for layer in self.canvas.layers():
            if layer.name() == 'home':
                point_layer = layer
            elif layer.name() == 'building-polygon':
                polygon = layer

        if point_layer is None or not len(point_layer.selectedFeatures()):
            QMessageBox.warning(None, self._name, 'No home point selected')
            return

        if polygon is None or not len(polygon.selectedFeatures()):
            QMessageBox.warning(None, self._name, 'No building polygon selected')
            return

        if len(polygon.selectedFeatures()) > 1:
            QMessageBox.warning(None, self._name, 'Please select only one polygon')
            return

        point_feature = point_layer.selectedFeatures()[0]
        polygon_feature = polygon.selectedFeatures()[0]

        # get work with point layer
        provider = point_layer.dataProvider()
        point_layer.startEditing()

        points = self.generate_points(point_feature, polygon_feature)
        if points is not None:
            map(point_layer.deleteFeature, self.remove_points(point_layer, polygon_feature))
            provider.addFeatures(points)
            point_layer.commitChanges()
            point_layer.updateExtents()

    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBand.reset(QGis.Polygon)

    def canvasPressEvent(self, e):
        self.startPoint = self.toMapCoordinates(e.pos())
        self.endPoint = self.startPoint
        self.isEmittingPoint = True
        self.showRect(self.startPoint, self.endPoint)

    def clearSelection(self):
        for layer in self.canvas.layers():
            layer.removeSelection()

    def canvasReleaseEvent(self, e):
        self.clearSelection()
        self.isEmittingPoint = False
        r = self.rectangle()
        layers = self.canvas.layers()
        for layer in layers:
            if layer is None or layer.type() == QgsMapLayer.RasterLayer:
                continue
            if r is not None:
                lRect = self.canvas.mapSettings().mapToLayerCoordinates(layer, r)
                layer.select(lRect, False)

        self.rubberBand.hide()

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self.do_points()
        QApplication.restoreOverrideCursor()

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return
        self.endPoint = self.toMapCoordinates(e.pos())
        self.showRect(self.startPoint, self.endPoint)

    def showRect(self, startPoint, endPoint):
        self.rubberBand.reset(QGis.Polygon)
        if startPoint.x() == endPoint.x() or startPoint.y() == endPoint.y():
            return

        self.rubberBand.addPoint(QgsPoint(startPoint.x(), startPoint.y()), False)
        self.rubberBand.addPoint(QgsPoint(startPoint.x(), endPoint.y()), False)
        self.rubberBand.addPoint(QgsPoint(endPoint.x(), endPoint.y()), False)
        self.rubberBand.addPoint(QgsPoint(endPoint.x(), startPoint.y()), True)    # true to update canvas
        self.rubberBand.show()

    def rectangle(self):
        if self.startPoint is None or self.endPoint is None:
            return None
        elif self.startPoint.x() == self.endPoint.x() or self.startPoint.y() == self.endPoint.y():
            return None

        return QgsRectangle(self.startPoint, self.endPoint)

    def deactivate(self):
        self.rubberBand.hide()
        QgsMapTool.deactivate(self)

    def activate(self):
        QgsMapTool.activate(self)
