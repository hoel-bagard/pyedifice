from ..base_components import CustomWidget
from .._component import Component, register_props

from ..qt import QT_VERSION
if QT_VERSION == "PyQt5":
    from PyQt5 import QtWidgets
    from PyQt5 import QtCore
else:
    from PySide2 import QtCore, QtWidgets

try:
    MATPLOTLIB_LOADED = True
    from matplotlib.backends.backend_qt5agg import FigureCanvas
    from matplotlib.figure import Figure as MatplotlibFigure
except:
    MATPLOTLIB_LOADED = False


class Figure(CustomWidget):

    @register_props
    def __init__(self, plot_fun):
        super().__init__()
        self.figure_added = False
        self.figure_canvas = None
        self.subplots = None

    def create_widget(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.figure_added = False
        return widget

    def paint(self, widget, newprops):
        if "plot_fun" in newprops:
            if self.figure_added:
                self.subplots.clear()
                self.props.plot_fun(self.subplots)
                self.figure_canvas.draw()
            else:
                self.figure_canvas = FigureCanvas(MatplotlibFigure(figsize=(5, 3)))
                self.subplots = self.figure_canvas.figure.subplots()
                self.props.plot_fun(self.subplots)
                widget.layout().addWidget(self.figure_canvas)
                self.figure_added = True


if not MATPLOTLIB_LOADED:
    def Figure(*args, **kwargs):
        raise ValueError("To use the Figure component, you must install matplotlib, e.g. by `pip install matplotlib`")
