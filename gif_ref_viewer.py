"""
ReView - Reference Viewer
v14.1 - Blend mode + opacity hover panel
"""

import sys
import os
import json
import math
import atexit
try:
    import psutil as _psutil
    _PSUTIL_PROC = _psutil.Process(os.getpid())
except ImportError:
    _psutil = None
    _PSUTIL_PROC = None
import zipfile
import shutil
import tempfile
import subprocess
import ctypes
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMenu, QScrollArea, QLineEdit,
    QSlider, QSpinBox, QTextEdit, QPlainTextEdit, QDialog, QDialogButtonBox,
    QColorDialog, QStyle, QStyleOptionSlider, QFileDialog, QMessageBox,
    QGraphicsOpacityEffect, QFrame
)
from PyQt5.QtCore import (
    Qt, QTimer, QPoint, QSize, QRect, QRectF, QPointF,
    pyqtSignal, pyqtSlot, QUrl, QByteArray, QEvent, QObject,
    QPropertyAnimation, QThread
)
from PyQt5.QtGui import (
    QMovie, QPainter, QColor, QCursor, QPixmap, QPalette, QRegion,
    QIcon, QImage, QFont, QPen, QLinearGradient, QPainterPath,
    QFontMetrics, QBrush, QPolygonF
)

try:
    import cv2
    import numpy as np
    os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads=1')
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    from PIL import Image as _PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_config.json")
SAVE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saves')
_main_window = None

# 블렌드 모드 목록 (이름, QPainter.CompositionMode)
_BLEND_MODES = [
    ('Normal',      QPainter.CompositionMode_SourceOver),
    ('Screen',      QPainter.CompositionMode_Screen),
    ('Multiply',    QPainter.CompositionMode_Multiply),
    ('Overlay',     QPainter.CompositionMode_Overlay),
    ('Darken',      QPainter.CompositionMode_Darken),
    ('Lighten',     QPainter.CompositionMode_Lighten),
    ('Color Dodge', QPainter.CompositionMode_ColorDodge),
    ('Color Burn',  QPainter.CompositionMode_ColorBurn),
    ('Hard Light',  QPainter.CompositionMode_HardLight),
    ('Soft Light',  QPainter.CompositionMode_SoftLight),
    ('Difference',  QPainter.CompositionMode_Difference),
    ('Exclusion',   QPainter.CompositionMode_Exclusion),
    ('Plus',        QPainter.CompositionMode_Plus),
]
_BLEND_MODE_NAMES = {mode: name for name, mode in _BLEND_MODES}
PADDING = 16


class _UndoStack:
    """Unified undo/redo stack. Each entry is (undo_fn, redo_fn)."""
    _MAXLEN = 100

    def __init__(self):
        self._undo = []
        self._redo = []

    def push(self, undo_fn, redo_fn):
        self._undo.append((undo_fn, redo_fn))
        if len(self._undo) > self._MAXLEN:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        undo_fn, redo_fn = self._undo.pop()
        try:
            undo_fn()
        except Exception as ex:
            print(f'[undo] {ex}')
        self._redo.append((undo_fn, redo_fn))
        return True

    def redo(self):
        if not self._redo:
            return False
        undo_fn, redo_fn = self._redo.pop()
        try:
            redo_fn()
        except Exception as ex:
            print(f'[redo] {ex}')
        self._undo.append((undo_fn, redo_fn))
        return True

    def clear(self):
        self._undo.clear()
        self._redo.clear()


def _record_undo(undo_fn, redo_fn):
    """Push an undo/redo pair to the global main-window undo stack."""
    if _main_window and hasattr(_main_window, '_undo'):
        _main_window._undo.push(undo_fn, redo_fn)
MENU_STYLE = 'QMenu{background:#2d2d2d;color:#fff;border:1px solid #555;}QMenu::item:selected{background:#0078d4;}'


def save_on_exit():
    global _main_window
    if _main_window:
        _main_window.saveState()


def _hq_scale(pix, tw, th):
    """High-quality downscale: iteratively halve until within 2x of target,
    then do a final SmoothTransformation. Gives Lanczos-class sharpness."""
    sw, sh = pix.width(), pix.height()
    if tw <= 0 or th <= 0:
        return pix
    # Only apply multi-step for significant downscaling
    while sw > tw * 2 or sh > th * 2:
        sw = max(tw, sw // 2)
        sh = max(th, sh // 2)
        pix = pix.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return pix.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def get_ui_font(size=10, bold=False):
    candidates = ['Noto Sans KR', '본고딕', 'Apple SD Gothic Neo',
                  'Malgun Gothic', 'Noto Sans', 'Segoe UI', 'Arial']
    for name in candidates:
        f = QFont(name, size)
        f.setHintingPreference(QFont.PreferFullHinting)
        if QFontMetrics(f).horizontalAdvance('A') > 0:
            f.setWeight(QFont.Bold if bold else QFont.Light)
            return f
    f = QFont()
    f.setPointSize(size)
    f.setWeight(QFont.Bold if bold else QFont.Light)
    return f


# ── Custom Color Picker ────────────────────────────────────────────────────────

class _SBPicker(QWidget):
    """Saturation/Brightness square for current hue."""
    changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(170)
        self.setMinimumWidth(180)
        self._hue = 0
        self._sat = 1.0
        self._val = 1.0
        self.setCursor(Qt.CrossCursor)

    def setHue(self, h):
        self._hue = max(0, min(359, h))
        self.update()

    def setSV(self, s, v):
        self._sat = max(0.0, min(1.0, s))
        self._val = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        hue_color = QColor.fromHsv(self._hue, 255, 255)

        # White → hue gradient (horizontal)
        grad_h = QLinearGradient(0, 0, w, 0)
        grad_h.setColorAt(0, QColor(255, 255, 255))
        grad_h.setColorAt(1, hue_color)
        p.fillRect(0, 0, w, h, grad_h)

        # Transparent → black gradient (vertical)
        grad_v = QLinearGradient(0, 0, 0, h)
        grad_v.setColorAt(0, QColor(0, 0, 0, 0))
        grad_v.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(0, 0, w, h, grad_v)

        # Crosshair
        cx = int(self._sat * w)
        cy = int((1.0 - self._val) * h)
        p.setPen(QPen(Qt.white, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPoint(cx, cy), 7, 7)
        p.setPen(QPen(hue_color, 1))
        p.drawEllipse(QPoint(cx, cy), 5, 5)

    def _fromPos(self, pos):
        w, h = max(1, self.width()), max(1, self.height())
        s = max(0.0, min(1.0, pos.x() / w))
        v = max(0.0, min(1.0, 1.0 - pos.y() / h))
        self.setSV(s, v)
        self.changed.emit(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._fromPos(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._fromPos(e.pos())


class _HueBar(QWidget):
    """Horizontal rainbow hue slider."""
    changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setMinimumWidth(100)
        self._hue = 0
        self.setCursor(Qt.PointingHandCursor)

    def setHue(self, h):
        self._hue = max(0, min(359, h))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, w, 0)
        for deg in range(0, 360, 60):
            grad.setColorAt(deg / 359.0, QColor.fromHsv(deg, 255, 255))
        grad.setColorAt(1.0, QColor.fromHsv(359, 255, 255))
        p.fillRect(0, 0, w, h, grad)
        # Position indicator
        x = int(self._hue / 359.0 * w)
        p.setPen(QPen(Qt.white, 2))
        p.drawLine(x, 0, x, h)

    def _fromPos(self, pos):
        w = max(1, self.width())
        hue = int(max(0.0, min(1.0, pos.x() / w)) * 359)
        self.setHue(hue)
        self.changed.emit(hue)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._fromPos(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._fromPos(e.pos())


class _AlphaBar(QWidget):
    """Alpha slider with checkerboard background."""
    changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setMinimumWidth(100)
        self._alpha = 255
        self._rgb = QColor(0, 0, 0)
        self.setCursor(Qt.PointingHandCursor)

    def setAlpha(self, a):
        self._alpha = max(0, min(255, a))
        self.update()

    def setRGB(self, c):
        self._rgb = QColor(c.red(), c.green(), c.blue())
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # Checkerboard
        cell = 7
        for row in range(0, h, cell):
            for col in range(0, w, cell):
                c = QColor(230, 230, 230) if (row // cell + col // cell) % 2 == 0 else QColor(160, 160, 160)
                p.fillRect(col, row, cell, cell, c)
        # Gradient transparent → color
        grad = QLinearGradient(0, 0, w, 0)
        t = QColor(self._rgb)
        t.setAlpha(0)
        grad.setColorAt(0, t)
        end = QColor(self._rgb)
        end.setAlpha(255)
        grad.setColorAt(1, end)
        p.fillRect(0, 0, w, h, grad)
        # Indicator
        x = int(self._alpha / 255.0 * w)
        p.setPen(QPen(Qt.white, 2))
        p.drawLine(x, 0, x, h)

    def _fromPos(self, pos):
        w = max(1, self.width())
        a = int(max(0.0, min(1.0, pos.x() / w)) * 255)
        self.setAlpha(a)
        self.changed.emit(a)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._fromPos(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._fromPos(e.pos())


class ColorPickerDialog(QDialog):
    """Modern HSV color picker dialog."""
    colorChanged = pyqtSignal(QColor)

    def __init__(self, initial=None, parent=None, show_alpha=False):
        super().__init__(parent)
        self.setFixedWidth(260)
        self.setModal(True)
        self._show_alpha = show_alpha
        self._alpha_val = 255
        self.setStyleSheet("""
            QDialog{background:#252525;}
            QLabel{color:#ccc;background:transparent;}
            QLineEdit{background:#333;color:#fff;border:1px solid #555;padding:2px;border-radius:2px;}
            QPushButton{background:#333;color:#fff;border:none;padding:4px 10px;border-radius:2px;}
            QPushButton:hover{background:#444;}
        """)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 10, 10, 10)

        self._sb = _SBPicker(self)
        lay.addWidget(self._sb)

        hrow = QHBoxLayout()
        hrow.addWidget(QLabel('H:'))
        self._hbar = _HueBar(self)
        hrow.addWidget(self._hbar)
        lay.addLayout(hrow)

        if show_alpha:
            arow = QHBoxLayout()
            arow.addWidget(QLabel('A:'))
            self._abar = _AlphaBar(self)
            arow.addWidget(self._abar)
            lay.addLayout(arow)
        else:
            self._abar = None

        hexrow = QHBoxLayout()
        self._hex = QLineEdit()
        self._hex.setMaxLength(8)
        self._prev = QLabel()
        self._prev.setFixedSize(82, 32)
        hexrow.addWidget(self._hex)
        hexrow.addWidget(self._prev)
        lay.addLayout(hexrow)

        btnrow = QHBoxLayout()
        self._ok = QPushButton('확인')
        self._cancel = QPushButton('취소')
        btnrow.addWidget(self._ok)
        btnrow.addWidget(self._cancel)
        lay.addLayout(btnrow)

        self._sb.changed.connect(self._onSBChange)
        self._hbar.changed.connect(self._onHueChange)
        if self._abar:
            self._abar.changed.connect(self._onAlphaChange)
        self._hex.textEdited.connect(self._onHexEdit)
        self._ok.clicked.connect(self.accept)
        self._cancel.clicked.connect(self.reject)

        self._color = initial if (initial and initial.isValid()) else QColor(255, 255, 255)
        self._load(self._color)
        self.setWindowTitle('색상 선택')

    def _load(self, c):
        h = c.hsvHue()
        if h < 0:
            h = 0
        self._hbar.setHue(h)
        self._sb.setHue(h)
        self._sb.setSV(c.hsvSaturationF(), c.valueF())
        if self._abar:
            self._alpha_val = c.alpha()
            self._abar.setAlpha(c.alpha())
            self._abar.setRGB(c)
        self._updateHex(c)
        self._updatePreview(c)
        self._color = c

    def _curColor(self):
        h = self._hbar._hue
        s = int(self._sb._sat * 255)
        v = int(self._sb._val * 255)
        a = self._alpha_val if self._show_alpha else 255
        return QColor.fromHsv(h, s, v, a)

    def _updateHex(self, c):
        if self._show_alpha:
            text = f'{c.alpha():02X}{c.red():02X}{c.green():02X}{c.blue():02X}'
        else:
            text = f'{c.red():02X}{c.green():02X}{c.blue():02X}'
        self._hex.setText(text.upper())

    def _updatePreview(self, c):
        self._prev.setStyleSheet(
            f'background:{c.name(QColor.HexArgb)};border:1px solid #555;border-radius:2px;')

    def _onSBChange(self, _):
        c = self._curColor()
        self._updateHex(c)
        self._updatePreview(c)
        if self._abar:
            self._abar.setRGB(c)
        self.colorChanged.emit(c)
        self._color = c

    def _onHueChange(self, h):
        self._sb.setHue(h)
        self._onSBChange(None)

    def _onAlphaChange(self, a):
        self._alpha_val = a
        c = self._curColor()
        self._updateHex(c)
        self._updatePreview(c)
        self.colorChanged.emit(c)
        self._color = c

    def _onHexEdit(self):
        t = self._hex.text().strip()
        try:
            if len(t) == 6:
                c = QColor(f'#{t}')
                if c.isValid():
                    self._load(c)
            elif len(t) == 8:
                a = int(t[0:2], 16)
                c = QColor(f'#{t[2:]}')
                if c.isValid():
                    c.setAlpha(a)
                    self._load(c)
        except Exception:
            pass

    def selectedColor(self):
        return self._color

    @staticmethod
    def getColor(initial=None, parent=None, title='', show_alpha=False, on_change=None):
        dlg = ColorPickerDialog(initial, parent, show_alpha)
        if title:
            dlg.setWindowTitle(title)
        if on_change:
            dlg.colorChanged.connect(on_change)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.selectedColor()
        return QColor()


# ── Custom Media Controls ──────────────────────────────────────────────────────

class _PlayBtn(QWidget):
    """Custom-drawn play/pause icon button."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background:transparent;border:none;')
        self.setFocusPolicy(Qt.NoFocus)
        self._playing = True
        self._hovered = False
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)

    def setText(self, t):
        self._playing = (t == '||')
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() // 2, self.height() // 2
        # Outer circle
        p.setPen(Qt.NoPen)
        if self._hovered:
            p.setBrush(QColor(255, 255, 255, 55))
        else:
            p.setBrush(QColor(255, 255, 255, 18))
        p.drawEllipse(QPoint(cx, cy), 13, 13)
        # Icon
        p.setBrush(QColor(230, 235, 255))
        if self._playing:
            # Pause bars
            p.drawRoundedRect(cx - 5, cy - 5, 3, 10, 1, 1)
            p.drawRoundedRect(cx + 2, cy - 5, 3, 10, 1, 1)
        else:
            # Play triangle
            path = QPainterPath()
            path.moveTo(cx - 4, cy - 6)
            path.lineTo(cx + 8, cy)
            path.lineTo(cx - 4, cy + 6)
            path.closeSubpath()
            p.drawPath(path)

    def enterEvent(self, e):
        self._hovered = True
        self.update()

    def leaveEvent(self, e):
        self._hovered = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class _ControlSlider(QWidget):
    """Thin custom progress slider for media control bars."""
    sliderMoved = pyqtSignal(int)
    sliderPressed = pyqtSignal()
    sliderReleased = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setMinimumWidth(60)
        self._min = 0
        self._max = 100
        self._val = 0
        self._hover = False
        self._pressed = False
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)

    def setMinimum(self, v): self._min = v
    def setMaximum(self, v): self._max = v
    def setValue(self, v): self._val = max(self._min, min(self._max, v)); self.update()
    def value(self): return self._val
    def minimum(self): return self._min
    def maximum(self): return self._max
    def blockSignals(self, b): super().blockSignals(b)

    def _ratio(self):
        span = max(1, self._max - self._min)
        return (self._val - self._min) / span

    def _posToVal(self, x):
        pad = 7
        w = max(1, self.width() - 2 * pad)
        ratio = max(0.0, min(1.0, (x - pad) / w))
        return max(self._min, min(self._max, int(ratio * (self._max - self._min) + self._min)))

    def event(self, e):
        if e.type() == QEvent.HoverEnter:
            self._hover = True
            self.update()
        elif e.type() == QEvent.HoverLeave:
            self._hover = False
            self.update()
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = True
            self._val = self._posToVal(e.pos().x())
            self.sliderPressed.emit()
            if not self.signalsBlocked():
                self.sliderMoved.emit(self._val)
            self.update()

    def mouseMoveEvent(self, e):
        if self._pressed and e.buttons() & Qt.LeftButton:
            self._val = self._posToVal(e.pos().x())
            if not self.signalsBlocked():
                self.sliderMoved.emit(self._val)
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = False
            self.sliderReleased.emit()
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pad = 8
        tw = self.width() - 2 * pad
        ty = self.height() // 2
        r = self._ratio()
        p.setPen(Qt.NoPen)
        # Track background
        p.setBrush(QColor(70, 70, 75, 180))
        p.drawRoundedRect(pad, ty - 2, tw, 4, 2, 2)
        # Progress fill
        if r > 0:
            grad = QLinearGradient(pad, 0, pad + int(tw * r), 0)
            grad.setColorAt(0, QColor(60, 160, 255, 230))
            grad.setColorAt(1, QColor(100, 210, 255, 230))
            p.setBrush(grad)
            p.drawRoundedRect(pad, ty - 2, int(tw * r), 4, 2, 2)
        # Handle
        cx = pad + int(tw * r)
        active = self._hover or self._pressed
        rad = 6 if active else 5
        # Shadow
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QPoint(cx + 1, ty + 2), rad, rad)
        # Knob
        p.setBrush(QColor(220, 230, 255) if active else QColor(200, 210, 240))
        p.drawEllipse(QPoint(cx, ty), rad, rad)


# ── _SpeedPopup ────────────────────────────────────────────────────────────────

class _SpeedPopup(QWidget):
    """말풍선 형태 재생속도 선택 팝업."""
    speed_selected = pyqtSignal(float)
    SPEEDS = [('0.25×', 0.25), ('0.5×', 0.5), ('0.75×', 0.75), ('1.0×', 1.0),
              ('1.025×', 1.025), ('1.25×', 1.25), ('1.5×', 1.5), ('1.75×', 1.75), ('2.0×', 2.0)]
    _ARROW_H = 9
    _ARROW_W = 16
    _R = 8

    def __init__(self):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._active = 1.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8 + self._ARROW_H)
        lay.setSpacing(4)
        self._btns = {}
        for label, val in self.SPEEDS:
            btn = QPushButton(label, self)
            btn.setFixedSize(36, 28)
            btn.setFocusPolicy(Qt.NoFocus)
            self._btns[val] = btn
            btn.clicked.connect(lambda checked=False, v=val: self._select(v))
            lay.addWidget(btn)
        self._refreshStyles()

    def _refreshStyles(self):
        for val, btn in self._btns.items():
            if val == self._active:
                btn.setStyleSheet(
                    'QPushButton{background:rgba(0,150,255,200);color:#fff;'
                    'border:none;border-radius:4px;font-size:9px;}')
            else:
                btn.setStyleSheet(
                    'QPushButton{background:rgba(255,255,255,12);color:rgba(180,200,225,220);'
                    'border:none;border-radius:4px;font-size:9px;}'
                    'QPushButton:hover{background:rgba(255,255,255,30);color:#fff;}')

    def _select(self, speed):
        self._active = speed
        self._refreshStyles()
        self.speed_selected.emit(speed)
        self.hide()

    def setActiveSpeed(self, speed):
        self._active = speed
        self._refreshStyles()

    def showAbove(self, ref_widget):
        self.adjustSize()
        gp = ref_widget.mapToGlobal(QPoint(ref_widget.width() // 2, 0))
        self.move(gp.x() - self.width() // 2, gp.y() - self.height())
        self.show()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        body_h = h - self._ARROW_H
        cx = w // 2
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, body_h), self._R, self._R)
        tip = QPainterPath()
        tip.moveTo(cx - self._ARROW_W // 2, body_h)
        tip.lineTo(cx, h - 1)
        tip.lineTo(cx + self._ARROW_W // 2, body_h)
        tip.closeSubpath()
        path = path.united(tip)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(22, 25, 38, 235))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(80, 105, 140, 150), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


# ── GifControlBar ──────────────────────────────────────────────────────────────

class GifControlBar(QWidget):
    speed_changed = pyqtSignal(float)
    _GAP = 5  # transparent gap above the bar (spacing from image)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(62 + self._GAP)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, self._GAP + 4, 10, 4)
        outer.setSpacing(3)

        # ── Row 1: slider (full width) ──────────────────────────────────────
        self.slider = _ControlSlider(self)
        outer.addWidget(self.slider)

        # ── Row 2: controls ─────────────────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.setAlignment(Qt.AlignVCenter)

        self.play_btn = _PlayBtn(self)
        row2.addWidget(self.play_btn, 0, Qt.AlignVCenter)

        _step_ss = ('QPushButton{background:rgba(255,255,255,10);color:rgba(180,195,215,220);'
                    'border:none;border-radius:4px;font-size:11px;padding:2px 6px;}'
                    'QPushButton:hover{background:rgba(255,255,255,25);color:#fff;}')
        self.prev_btn = QPushButton('◀', self)
        self.prev_btn.setFixedSize(26, 26)
        self.prev_btn.setStyleSheet(_step_ss)
        self.prev_btn.setFocusPolicy(Qt.NoFocus)
        row2.addWidget(self.prev_btn, 0, Qt.AlignVCenter)

        self.next_btn = QPushButton('▶', self)
        self.next_btn.setFixedSize(26, 26)
        self.next_btn.setStyleSheet(_step_ss)
        self.next_btn.setFocusPolicy(Qt.NoFocus)
        row2.addWidget(self.next_btn, 0, Qt.AlignVCenter)

        self.frame_label = QLabel('0 / 0', self)
        self.frame_label.setFixedHeight(26)
        self.frame_label.setAlignment(Qt.AlignCenter)
        self.frame_label.setFont(get_ui_font(9))
        self.frame_label.setStyleSheet(
            'color:rgba(160,175,195,220);background:transparent;letter-spacing:1px;')
        row2.addWidget(self.frame_label, 1, Qt.AlignVCenter)

        # ── 속도 버튼 (단일 버튼 → 팝업) ────────────────────────────────────
        self.speed_btn = QPushButton('1.0×', self)
        self.speed_btn.setFixedSize(38, 22)
        self.speed_btn.setFocusPolicy(Qt.NoFocus)
        self.speed_btn.setStyleSheet(
            'QPushButton{background:rgba(0,150,255,130);color:#fff;'
            'border:none;border-radius:4px;font-size:9px;}'
            'QPushButton:hover{background:rgba(0,150,255,200);}')
        row2.addWidget(self.speed_btn, 0, Qt.AlignVCenter)

        self._popup = _SpeedPopup()
        self._popup.speed_selected.connect(self._onPopupSpeed)
        self.speed_btn.clicked.connect(self._showPopup)

        outer.addLayout(row2)

    def _showPopup(self):
        self._popup.showAbove(self.speed_btn)

    _SPEED_LABELS = {0.25: '0.25×', 0.5: '0.5×', 0.75: '0.75×', 1.0: '1.0×',
                     1.025: '1.025×', 1.25: '1.25×', 1.5: '1.5×', 1.75: '1.75×', 2.0: '2.0×'}

    def _onPopupSpeed(self, speed):
        self.speed_btn.setText(self._SPEED_LABELS.get(speed, f'{speed}×'))
        self.speed_changed.emit(speed)

    def setActiveSpeed(self, speed):
        self.speed_btn.setText(self._SPEED_LABELS.get(speed, f'{speed}×'))
        self._popup.setActiveSpeed(speed)

    def showEvent(self, e):
        super().showEvent(e)
        self.raise_()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        # Draw bar background below the gap
        rect = self.rect().adjusted(0, self._GAP, 0, 0)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0, QColor(22, 22, 28, 220))
        grad.setColorAt(1, QColor(14, 14, 18, 230))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 7, 7)


# ── _ContentLabel ──────────────────────────────────────────────────────────────

class _ContentLabel(QLabel):
    """QLabel that draws resize handles on top when item is selected."""
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self.setMouseTracking(True)

    def paintEvent(self, e):
        pix = self.pixmap()
        if pix and not pix.isNull():
            painter = QPainter(self)
            item = self._item
            blend = getattr(item, '_blend_mode', QPainter.CompositionMode_SourceOver)
            opacity = getattr(item, '_opacity', 1.0)
            if opacity < 1.0:
                painter.setOpacity(opacity)
            if blend != QPainter.CompositionMode_SourceOver:
                painter.setCompositionMode(blend)
            flip_h = getattr(self._item, '_flip_h', False)
            flip_v = getattr(self._item, '_flip_v', False)
            rotation = getattr(self._item, '_rotation', 0)
            crop = getattr(self._item, '_crop', None)
            # 크롭 적용: 서브 픽스맵 추출
            cw_frac, ch_frac = 1.0, 1.0
            if crop and crop != [0.0, 0.0, 1.0, 1.0]:
                cw_frac = max(0.01, crop[2] - crop[0])
                ch_frac = max(0.01, crop[3] - crop[1])
                cx = int(crop[0] * pix.width())
                cy = int(crop[1] * pix.height())
                cw = max(1, int(cw_frac * pix.width()))
                ch = max(1, int(ch_frac * pix.height()))
                pix = pix.copy(QRect(cx, cy, cw, ch))
            if getattr(self._item, '_invert', False):
                _img = pix.toImage()
                _img.invertPixels(QImage.InvertRgb)
                pix = QPixmap.fromImage(_img)
            if flip_h or flip_v or rotation:
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                ow = max(1, int(self._item.original_size.width() * cw_frac * self._item.current_scale))
                oh = max(1, int(self._item.original_size.height() * ch_frac * self._item.current_scale))
                painter.save()
                painter.translate(self.width() / 2.0, self.height() / 2.0)
                if rotation:
                    painter.rotate(rotation)
                painter.scale(-1.0 if flip_h else 1.0, -1.0 if flip_v else 1.0)
                painter.drawPixmap(QRect(-ow // 2, -oh // 2, ow, oh), pix)
                painter.restore()
            elif pix.size() == self.size():
                # 표시 크기와 일치 → 빠른 1:1 복사 (GIF/Video pre-scaled 경로)
                painter.drawPixmap(0, 0, pix)
            else:
                # 원본 해상도 저장 → smooth scale (ImageItem 경로, 거의 재렌더 없음)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                painter.drawPixmap(self.rect(), pix)
            painter.end()
        else:
            super().paintEvent(e)

    def mouseMoveEvent(self, e):
        if self._item.is_selected:
            hit = self._item._rm_hit(e.pos())
            self.setCursor(self._item._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._item._rm_hover(e.pos()):
                self._item.update()
        e.ignore()

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._item._rm_clear_hover():
            self._item.update()


# ── ResizeMixin ────────────────────────────────────────────────────────────────

class ResizeMixin:
    """8-handle proportional resize mixin."""
    _RM_S = 14
    _HPAD = 0   # padding around image label; override to 8 in image items
    _ANCHOR_FRAC = {
        'tl': (1.0, 1.0), 'tc': (0.5, 1.0), 'tr': (0.0, 1.0),
        'ml': (1.0, 0.5),                    'mr': (0.0, 0.5),
        'bl': (1.0, 0.0), 'bc': (0.5, 0.0), 'br': (0.0, 0.0),
    }
    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'tc': Qt.SizeVerCursor,  'tr': Qt.SizeBDiagCursor,
        'ml': Qt.SizeHorCursor,                              'mr': Qt.SizeHorCursor,
        'bl': Qt.SizeBDiagCursor, 'bc': Qt.SizeVerCursor,  'br': Qt.SizeFDiagCursor,
    }

    def _rm_init(self):
        self._rz_handle = None
        self._rz_start_scale = 1.0
        self._rz_anchor_parent = QPoint(0, 0)
        self._rz_anchor_global = QPoint(0, 0)
        self._rz_start_dist = 0.0
        self._rz_dir = (1.0, 0.0)
        self._rz_fx = 0.0
        self._rz_fy = 0.0
        self._hover_handle = None

    def _rm_hover(self, pos):
        """Update hovered handle. Returns True if changed (needs repaint)."""
        new = self._rm_hit(pos) if self.is_selected else None
        if new != self._hover_handle:
            self._hover_handle = new
            return True
        return False

    def _rm_clear_hover(self):
        if self._hover_handle is not None:
            self._hover_handle = None
            return True
        return False

    def _rm_csize(self):
        w = max(1, int(self.original_size.width() * self.current_scale))
        h = max(1, int(self.original_size.height() * self.current_scale))
        return QSize(w, h)

    def _rm_handle_pts(self):
        cs = self._rm_csize()
        w, h = cs.width(), cs.height()
        return {
            'tl': QPoint(0,     0),       'tc': QPoint(w // 2, 0),     'tr': QPoint(w,     0),
            'ml': QPoint(0,     h // 2),                                 'mr': QPoint(w,     h // 2),
            'bl': QPoint(0,     h),       'bc': QPoint(w // 2, h),      'br': QPoint(w,     h),
        }

    def _rm_hit(self, lpos):
        half = self._RM_S // 2 + 4
        for k, pt in self._rm_handle_pts().items():
            if abs(lpos.x() - pt.x()) <= half and abs(lpos.y() - pt.y()) <= half:
                return k
        return None

    def _rm_press(self, e):
        canvas = self.parent()
        if canvas and hasattr(canvas, 'main_window'):
            mw = canvas.main_window
            if mw and hasattr(mw, 'layer_panel'):
                if id(self) in mw.layer_panel._locked:
                    return False
        if e.button() == Qt.LeftButton and self.is_selected:
            HPAD = self._HPAD
            x_off = getattr(self, '_content_x_offset', 0)
            label_pos = e.pos() - QPoint(HPAD + x_off, HPAD)
            handle = self._rm_hit(label_pos)
            if handle:
                self._rz_handle = handle
                self._rz_start_scale = self.current_scale
                self._rz_start_pos = self.pos()
                fx, fy = self._ANCHOR_FRAC[handle]
                cs = self._rm_csize()
                # anchor_local in widget-space (label origin is at HPAD+x_off, HPAD)
                anchor_local = QPoint(int(cs.width() * fx) + HPAD + x_off, int(cs.height() * fy) + HPAD)
                self._rz_anchor_parent = self.pos() + anchor_local
                self._rz_anchor_global = self.mapToGlobal(anchor_local)
                dx = float(e.globalPos().x() - self._rz_anchor_global.x())
                dy = float(e.globalPos().y() - self._rz_anchor_global.y())
                self._rz_start_dist = max(1.0, math.sqrt(dx * dx + dy * dy))
                # Normalized direction vector from anchor to handle press
                self._rz_dir = (dx / self._rz_start_dist, dy / self._rz_start_dist)
                self.setCursor(self._CURSORS[handle])
                return True
        return False

    def _rm_move(self, e):
        if self._rz_handle and e.buttons() & Qt.LeftButton:
            gp = e.globalPos()
            dx = float(gp.x() - self._rz_anchor_global.x())
            dy = float(gp.y() - self._rz_anchor_global.y())
            # Signed projection onto original drag direction
            proj = dx * self._rz_dir[0] + dy * self._rz_dir[1]
            new_scale = max(0.01, min(10.0, self._rz_start_scale * proj / self._rz_start_dist))
            self.setScale(new_scale)
            cs = self._rm_csize()
            fx, fy = self._ANCHOR_FRAC[self._rz_handle]
            HPAD = self._HPAD
            x_off = getattr(self, '_content_x_offset', 0)
            anchor_in_new = QPoint(int(cs.width() * fx) + HPAD + x_off, int(cs.height() * fy) + HPAD)
            self.move(self._rz_anchor_parent - anchor_in_new)
            return True
        return False

    def _rm_release(self, e):
        if self._rz_handle:
            old_scale = self._rz_start_scale
            old_pos = getattr(self, '_rz_start_pos', self.pos())
            new_scale = self.current_scale
            new_pos = self.pos()
            if abs(old_scale - new_scale) > 1e-4:
                item = self
                def _undo():
                    try:
                        item.setScale(old_scale); item.move(old_pos)
                    except RuntimeError:
                        pass
                def _redo():
                    try:
                        item.setScale(new_scale); item.move(new_pos)
                    except RuntimeError:
                        pass
                _record_undo(_undo, _redo)
            self._rz_handle = None
            self.setCursor(Qt.ArrowCursor)
            return True
        return False

    def _rm_paint(self, p):
        """Draw selection handles. Caller should translate painter to label origin."""
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        cs = self._rm_csize()
        w, h = cs.width() - 1, cs.height() - 1

        # Check lock state
        is_locked = False
        canvas = self.parent()
        if canvas and hasattr(canvas, 'main_window'):
            mw = canvas.main_window
            if mw and hasattr(mw, 'layer_panel'):
                is_locked = id(self) in mw.layer_panel._locked

        if is_locked:
            # ── Orange dashed border OUTSIDE the image (in HPAD margin) ──
            pen = QPen(QColor(255, 150, 20, 255), 2.5, Qt.CustomDashLine)
            pen.setDashPattern([5, 3])
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            # Draw 3px outside label boundary so it's not hidden under the image
            p.drawRect(QRectF(-3, -3, w + 6, h + 6))
        else:
            # ── Thin selection border at image edge ───────────────────────
            p.setPen(QPen(QColor(80, 160, 255, 200), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(0, 0, w, h)

            # ── Corner L-bracket arms — WRAP outside image corner ─────────
            GAP = 2   # how far the corner point sits outside the image
            ARM = 9   # length of each arm running along the image edge
            THICK = 2.2
            # Each tuple: end-of-arm1, corner-point, end-of-arm2
            # Corner point is outside; arms run inward along image edges
            corners = [
                ((ARM, -GAP), (-GAP, -GAP), (-GAP, ARM)),              # TL
                ((w-ARM, -GAP), (w+GAP, -GAP), (w+GAP, ARM)),          # TR
                ((ARM, h+GAP), (-GAP, h+GAP), (-GAP, h-ARM)),          # BL
                ((w-ARM, h+GAP), (w+GAP, h+GAP), (w+GAP, h-ARM)),      # BR
            ]
            pen_corner = QPen(QColor(255, 255, 255, 245), THICK,
                              Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen_corner)
            for (a1, corner, a2) in corners:
                path = QPainterPath()
                path.moveTo(QPointF(*a1))
                path.lineTo(QPointF(*corner))
                path.lineTo(QPointF(*a2))
                p.drawPath(path)

            # ── Edge midpoint handles — centered on image border ──────────
            MID_H = 4
            mid_pts = [
                (w // 2, 0,  'tc'),
                (w // 2, h,  'bc'),
                (0,  h // 2, 'ml'),
                (w,  h // 2, 'mr'),
            ]
            for (mx, my, key) in mid_pts:
                hov = (key == self._hover_handle)
                fill = QColor(255, 255, 255, 255) if hov else QColor(240, 240, 245, 230)
                border_c = QColor(200, 200, 210) if hov else QColor(190, 190, 200, 200)
                sz = MID_H + (2 if hov else 0)
                p.setBrush(fill)
                p.setPen(QPen(border_c, 1.5))
                p.drawRect(mx - sz, my - sz, sz * 2, sz * 2)

        p.restore()


# ── SelectableMixin ────────────────────────────────────────────────────────────

class SelectableMixin:
    """Drag and multi-select mixin."""

    def _sm_init(self):
        self.drag_start = None
        self.drag_start_positions = {}
        self.is_selected = False

    def _sm_press(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        canvas = self.parent()
        if e.button() == Qt.LeftButton:
            shift = bool(e.modifiers() & Qt.ShiftModifier)
            ctrl = bool(e.modifiers() & Qt.ControlModifier)
            additive = shift or ctrl
            if ctrl and self.is_selected:
                self.deselect()
                if hasattr(canvas, 'selected_items') and self in canvas.selected_items:
                    canvas.selected_items.remove(self)
                self.drag_start = None
                return
            # 이미 선택된 아이템 클릭 시 다중 선택 유지 (멀티 드래그)
            already_in_multi = (self.is_selected and
                                hasattr(canvas, 'selected_items') and
                                len(canvas.selected_items) > 1)
            if not additive and not already_in_multi and hasattr(canvas, 'deselectAll'):
                canvas.deselectAll()
            self.select(additive=additive or already_in_multi)
            self.raise_()
            if hasattr(canvas, '_lower_all_groups'):
                canvas._lower_all_groups()
            # Check lock — allow selection but block drag
            is_locked = False
            if canvas and hasattr(canvas, 'main_window'):
                mw = canvas.main_window
                if mw and hasattr(mw, 'layer_panel'):
                    is_locked = id(self) in mw.layer_panel._locked
            if not is_locked:
                self.drag_start = e.globalPos()
                all_sel = canvas.selected_items if hasattr(canvas, 'selected_items') else [self]
                self.drag_start_positions = {item: QPoint(item.pos()) for item in all_sel}
        elif e.button() == Qt.RightButton:
            e.ignore()

    def _sm_move(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore()
            return
        if self.drag_start and e.buttons() & Qt.LeftButton:
            delta = e.globalPos() - self.drag_start
            for item, start in self.drag_start_positions.items():
                item.move(start + delta)

    def _sm_release(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        if e.button() == Qt.LeftButton and self.drag_start:
            canvas = self.parent()
            # Record undo for any items that actually moved
            moved = {it: (start, it.pos())
                     for it, start in self.drag_start_positions.items()
                     if it.pos() != start}
            if moved:
                old_pos = {it: s for it, (s, _) in moved.items()}
                new_pos = {it: n for it, (_, n) in moved.items()}
                def _undo():
                    for it, p in old_pos.items():
                        try: it.move(p)
                        except RuntimeError: pass
                def _redo():
                    for it, p in new_pos.items():
                        try: it.move(p)
                        except RuntimeError: pass
                _record_undo(_undo, _redo)
            if hasattr(canvas, 'groups'):
                for g in canvas.groups:
                    for item in list(self.drag_start_positions.keys()):
                        center = item.pos() + QPoint(item.width() // 2, item.height() // 2)
                        if QRect(g.pos(), g.size()).contains(center):
                            if item not in g.member_items:
                                g.member_items.append(item)
                        else:
                            if item in g.member_items:
                                g.member_items.remove(item)
            self.drag_start = None
            self.drag_start_positions = {}


# ── CropDialog ─────────────────────────────────────────────────────────────────

class CropDialog(QDialog):
    """크롭(자르기) 설정 다이얼로그 - 상하좌우 비율 설정."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle('자르기')
        self.setModal(True)
        self.setFixedWidth(360)
        self._item = item
        crop = list(getattr(item, '_crop', None) or [0.0, 0.0, 1.0, 1.0])
        self._orig_crop = list(crop)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 12)

        info = QLabel('표시할 영역의 시작/끝 비율을 설정합니다.')
        info.setStyleSheet('color:#aaa; font-size:10px;')
        lay.addWidget(info)

        self._spins = {}
        rows = [
            ('왼쪽 시작', 0, int(crop[0] * 100)),
            ('위 시작',   1, int(crop[1] * 100)),
            ('오른쪽 끝', 2, int(crop[2] * 100)),
            ('아래 끝',   3, int(crop[3] * 100)),
        ]
        for label_text, idx, val in rows:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(70)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(val)
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setValue(val)
            spin.setSuffix('%')
            spin.setFixedWidth(58)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            slider.valueChanged.connect(lambda v, i=idx: self._on_change())
            row.addWidget(lbl)
            row.addWidget(slider)
            row.addWidget(spin)
            self._spins[idx] = (slider, spin)
            lay.addLayout(row)

        btns = QHBoxLayout()
        reset_btn = QPushButton('초기화')
        reset_btn.clicked.connect(self._reset)
        ok_btn = QPushButton('확인')
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton('취소')
        cancel_btn.clicked.connect(self._cancel)
        btns.addWidget(reset_btn)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

    def _get_values(self):
        return [self._spins[i][1].value() / 100.0 for i in range(4)]

    def _on_change(self):
        crop = self._get_values()
        self._item._crop = crop
        self._item.updateSize()
        self._item._label.update()

    def _reset(self):
        for i, (slider, spin) in self._spins.items():
            slider.blockSignals(True)
            spin.blockSignals(True)
            slider.setValue(0 if i < 2 else 100)
            spin.setValue(0 if i < 2 else 100)
            slider.blockSignals(False)
            spin.blockSignals(False)
        self._on_change()

    def _cancel(self):
        self._item._crop = self._orig_crop
        self._item.updateSize()
        self._item._label.update()
        self.reject()

    def get_crop(self):
        v = self._get_values()
        l, t, r, b = v
        if r <= l:
            r = min(1.0, l + 0.01)
        if b <= t:
            b = min(1.0, t + 0.01)
        return [l, t, r, b]


# ── TrimDialog ─────────────────────────────────────────────────────────────────

class TrimDialog(QDialog):
    """구간(트림) 설정 다이얼로그 - 시작/끝 프레임 설정."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle('구간 설정')
        self.setModal(True)
        self.setFixedWidth(400)
        self._item = item
        fc = item.frame_count
        fps = getattr(item, 'fps', 25)
        ts = getattr(item, '_trim_start', 0)
        te = getattr(item, '_trim_end', fc - 1)
        self._orig_start = ts
        self._orig_end = te

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 12)

        info = QLabel(f'전체 {fc} 프레임  ({fc / fps:.2f}초)')
        info.setStyleSheet('color:#aaa; font-size:10px;')
        lay.addWidget(info)

        # 시작 프레임
        row1 = QHBoxLayout()
        lbl1 = QLabel('시작 프레임')
        lbl1.setFixedWidth(75)
        self._start_slider = QSlider(Qt.Horizontal)
        self._start_slider.setRange(0, fc - 1)
        self._start_slider.setValue(ts)
        self._start_spin = QSpinBox()
        self._start_spin.setRange(0, fc - 1)
        self._start_spin.setValue(ts)
        self._start_spin.setFixedWidth(65)
        self._start_slider.valueChanged.connect(self._start_spin.setValue)
        self._start_spin.valueChanged.connect(self._start_slider.setValue)
        self._start_spin.valueChanged.connect(lambda _: self._update_info())
        row1.addWidget(lbl1)
        row1.addWidget(self._start_slider)
        row1.addWidget(self._start_spin)
        lay.addLayout(row1)

        # 끝 프레임
        row2 = QHBoxLayout()
        lbl2 = QLabel('끝 프레임')
        lbl2.setFixedWidth(75)
        self._end_slider = QSlider(Qt.Horizontal)
        self._end_slider.setRange(0, fc - 1)
        self._end_slider.setValue(te)
        self._end_spin = QSpinBox()
        self._end_spin.setRange(0, fc - 1)
        self._end_spin.setValue(te)
        self._end_spin.setFixedWidth(65)
        self._end_slider.valueChanged.connect(self._end_spin.setValue)
        self._end_spin.valueChanged.connect(self._end_slider.setValue)
        self._end_spin.valueChanged.connect(lambda _: self._update_info())
        row2.addWidget(lbl2)
        row2.addWidget(self._end_slider)
        row2.addWidget(self._end_spin)
        lay.addLayout(row2)

        self._info_lbl = QLabel()
        self._info_lbl.setStyleSheet('color:#8cf; font-size:10px;')
        self._update_info()
        lay.addWidget(self._info_lbl)

        btns = QHBoxLayout()
        reset_btn = QPushButton('초기화')
        reset_btn.clicked.connect(self._reset)
        ok_btn = QPushButton('확인')
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton('취소')
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(reset_btn)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

    def _update_info(self):
        fps = getattr(self._item, 'fps', 25)
        s = self._start_spin.value()
        e = self._end_spin.value()
        start_s = s / fps
        end_s = (e + 1) / fps
        dur = end_s - start_s
        frames = max(0, e - s + 1)
        self._info_lbl.setText(f'{start_s:.2f}s ~ {end_s:.2f}s  (길이: {dur:.2f}s,  {frames} 프레임)')

    def _reset(self):
        self._start_slider.setValue(0)
        self._end_slider.setValue(self._item.frame_count - 1)

    def get_trim(self):
        s = self._start_spin.value()
        e = self._end_spin.value()
        if s > e:
            s, e = e, s
        return s, e


# ── _CropOverlay ───────────────────────────────────────────────────────────────

class _CropOverlay(QWidget):
    """빨간 핸들 오버레이로 직접 드래그해서 자르기 영역을 설정하는 위젯."""
    _HS = 7       # 핸들 사각형 반폭
    _BTN_H = 70   # 하단 버튼 스트립 높이 (진행바 12px + 버튼행1 26px + 버튼행2 16px + 여백)
    _BAR_H = 12   # 재생 진행바 높이

    def __init__(self, item, canvas):
        super().__init__(canvas)
        self._item = item
        self._canvas = canvas
        self._orig_crop = list(getattr(item, '_crop', [0.0, 0.0, 1.0, 1.0]))

        # 전체 이미지가 보이도록 크롭 임시 해제
        item._crop = [0.0, 0.0, 1.0, 1.0]
        item.updateSize()

        self._lw = item._label.width()
        self._lh = item._label.height()

        # 초기 크롭 사각형: 이전 크롭이 없으면 70% 중앙, 있으면 이전 값 복원
        c = self._orig_crop
        if c == [0.0, 0.0, 1.0, 1.0]:
            px = int(self._lw * 0.15)
            py = int(self._lh * 0.15)
            self._r = QRect(px, py, max(20, self._lw - px * 2), max(20, self._lh - py * 2))
        else:
            self._r = QRect(
                int(c[0] * self._lw), int(c[1] * self._lh),
                max(20, int((c[2] - c[0]) * self._lw)),
                max(20, int((c[3] - c[1]) * self._lh)),
            )

        # 컨트롤바 표시 (GIF/비디오 재생 & 프레임바 유지)
        self._had_control_bar_hidden = False
        # 컨트롤바·호버바 숨김 (편집창이 그 자리를 차지)
        item._editing_overlay_active = True
        if hasattr(item, 'control_bar'):
            self._had_control_bar_hidden = item.control_bar.isVisible()
            item._hide_control_bar()
        if hasattr(item, '_hover_bar'):
            item._hover_bar.hide()
        if hasattr(item, '_blend_bar'):
            item._blend_bar.hide()

        self._drag_handle = None
        self._drag_start_pos = QPoint()
        self._drag_start_r = QRect()
        self._seeking = False   # 진행바 드래그 시킹 중
        self._item_dragging = False  # 아이템 이동 드래그 중

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # ── 버튼 ──────────────────────────────────────────────────────────────
        ok_style = ('QPushButton{background:#c0392b;color:#fff;border:1px solid #e74c3c;'
                    'border-radius:4px;padding:4px 18px;font-size:11px;font-weight:bold;}'
                    'QPushButton:hover{background:#e74c3c;}')
        cancel_style = ('QPushButton{background:#2d2d2d;color:#ddd;border:1px solid #555;'
                        'border-radius:4px;padding:4px 18px;font-size:11px;}'
                        'QPushButton:hover{background:#3d3d3d;}')
        reset_style = ('QPushButton{background:transparent;color:#aaa;border:none;'
                       'font-size:10px;}'
                       'QPushButton:hover{color:#fff;}')

        self._ok_btn = QPushButton('자르기', self)
        self._ok_btn.setStyleSheet(ok_style)
        self._ok_btn.clicked.connect(self._confirm)

        self._cancel_btn = QPushButton('취소', self)
        self._cancel_btn.setStyleSheet(cancel_style)
        self._cancel_btn.clicked.connect(self._cancel_crop)

        self._reset_btn = QPushButton('초기화', self)
        self._reset_btn.setStyleSheet(reset_style)
        self._reset_btn.clicked.connect(self._reset_rect)

        # 위치 동기화 타이머
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._reposition)
        self._sync_timer.start(30)

        self._reposition()
        self.raise_()
        self.show()

    # ── 위치 동기화 ───────────────────────────────────────────────────────────
    def _reposition(self):
        lbl = self._item._label
        if not lbl.isVisible():
            return

        # ── 줌/리사이즈 대응: 라벨 크기 변화를 감지해 크롭 사각형도 비례 조정 ──
        new_lw, new_lh = lbl.width(), lbl.height()
        if (new_lw != self._lw or new_lh != self._lh) and self._lw > 0 and self._lh > 0 and new_lw > 0 and new_lh > 0:
            sx = new_lw / self._lw
            sy = new_lh / self._lh
            self._r = QRect(
                int(self._r.left()   * sx),
                int(self._r.top()    * sy),
                max(20, int(self._r.width()  * sx)),
                max(20, int(self._r.height() * sy)),
            )
            # 드래그 중이면 _drag_start_r 도 함께 스케일 → 좌표 불일치 방지
            if self._drag_handle is not None:
                self._drag_start_r = QRect(
                    int(self._drag_start_r.left()  * sx),
                    int(self._drag_start_r.top()   * sy),
                    max(20, int(self._drag_start_r.width()  * sx)),
                    max(20, int(self._drag_start_r.height() * sy)),
                )
                self._drag_start_pos = QPoint(
                    int(self._drag_start_pos.x() * sx),
                    int(self._drag_start_pos.y() * sy),
                )
            self._lw = new_lw
            self._lh = new_lh
        self._clamp_r()

        gp = lbl.mapToGlobal(QPoint(0, 0))
        pos = self._canvas.mapFromGlobal(gp)
        w = self._lw
        h = self._lh + self._BTN_H
        self.setGeometry(pos.x(), pos.y(), w, h)

        # 버튼 배치: 진행바(12px) + 여백 아래에 2행 배치
        # 행1: 자르기·취소 (적응형 너비, 좁을 때 좌측 정렬 / 넓을 때 중앙 정렬)
        # 행2: 초기화 (좌측 소형)
        bh = 26
        bw = min(76, max(30, (w - 22) // 2))
        mid = w // 2
        ok_x = max(8, mid - bw - 3)
        by = self._lh + self._BAR_H + 7
        self._ok_btn.setGeometry(ok_x, by, bw, bh)
        self._cancel_btn.setGeometry(ok_x + bw + 6, by, bw, bh)
        self._reset_btn.setGeometry(8, by + bh + 4, 52, 16)

        # 컨트롤바 슬라이더 실시간 동기화 (GIF/Video)
        fc = getattr(self._item, 'frame_count', 0)
        if fc > 1 and hasattr(self._item, 'control_bar'):
            if hasattr(self._item, 'movie'):
                cf = self._item.movie.currentFrameNumber()
            else:
                cf = getattr(self._item, 'current_frame', 0)
            try:
                self._item.control_bar.slider.blockSignals(True)
                self._item.control_bar.slider.setValue(cf)
                self._item.control_bar.slider.blockSignals(False)
            except RuntimeError:
                pass

        self.raise_()  # setGeometry 후 Windows HWND Z-순서 리셋 방지
        self.update()  # 진행바 실시간 갱신

    # ── 경계 보정 ─────────────────────────────────────────────────────────────
    def _clamp_r(self):
        """self._r 를 [0, lw] x [0, lh] 안에 유지, 최소 크기 20×20 보장."""
        lw, lh = self._lw, self._lh
        if lw <= 0 or lh <= 0:
            return
        r = self._r
        w = max(20, min(r.width(),  lw))
        h = max(20, min(r.height(), lh))
        x = max(0, min(lw - w, r.left()))
        y = max(0, min(lh - h, r.top()))
        self._r = QRect(x, y, w, h)

    # ── 핸들 위치 계산 ────────────────────────────────────────────────────────
    def _handle_rects(self):
        r = self._r
        cx, cy = r.center().x(), r.center().y()
        hs = self._HS
        pts = {
            'tl': (r.left(),  r.top()),    'tc': (cx,        r.top()),
            'tr': (r.right(), r.top()),    'ml': (r.left(),  cy),
            'mr': (r.right(), cy),         'bl': (r.left(),  r.bottom()),
            'bc': (cx,        r.bottom()), 'br': (r.right(), r.bottom()),
        }
        return {k: QRect(x - hs, y - hs, hs * 2, hs * 2) for k, (x, y) in pts.items()}

    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'tc': Qt.SizeVerCursor,  'tr': Qt.SizeBDiagCursor,
        'ml': Qt.SizeHorCursor,                              'mr': Qt.SizeHorCursor,
        'bl': Qt.SizeBDiagCursor, 'bc': Qt.SizeVerCursor,  'br': Qt.SizeFDiagCursor,
        'move': Qt.SizeAllCursor,
    }

    def _hit(self, pos):
        if pos.y() >= self._lh:   # 버튼 영역
            return None
        for k, rect in self._handle_rects().items():
            if rect.contains(pos):
                return k
        if self._r.contains(pos):
            return 'move'
        return None

    # ── 페인트 ────────────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        lw, lh = self._lw, self._lh
        r = self._r

        # 어두운 마스크 (잘려나갈 영역)
        dark = QColor(0, 0, 0, 155)
        p.setBrush(dark)
        p.setPen(Qt.NoPen)
        if r.top() > 0:
            p.drawRect(0, 0, lw, r.top())
        if r.bottom() < lh - 1:
            p.drawRect(0, r.bottom() + 1, lw, lh - r.bottom() - 1)
        if r.left() > 0:
            p.drawRect(0, r.top(), r.left(), r.height())
        if r.right() < lw - 1:
            p.drawRect(r.right() + 1, r.top(), lw - r.right() - 1, r.height())

        # 빨간 테두리
        p.setPen(QPen(QColor(210, 35, 35), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        # 코너 L자 브라켓 (두꺼운 빨간선)
        arm = max(8, min(18, r.width() // 4, r.height() // 4))
        pen_brk = QPen(QColor(255, 55, 55), 3.0, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        p.setPen(pen_brk)
        for x, y, dx1, dy1, dx2, dy2 in [
            (r.left(),  r.top(),    arm,  0,    0,   arm),
            (r.right(), r.top(),   -arm,  0,    0,   arm),
            (r.left(),  r.bottom(), arm,  0,    0,  -arm),
            (r.right(), r.bottom(),-arm,  0,    0,  -arm),
        ]:
            p.drawLine(x, y, x + dx1, y + dy1)
            p.drawLine(x, y, x + dx2, y + dy2)

        # 삼등분선 (rule of thirds)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        for i in (1, 2):
            gx = r.left() + r.width() * i // 3
            gy = r.top() + r.height() * i // 3
            p.drawLine(gx, r.top() + 1, gx, r.bottom() - 1)
            p.drawLine(r.left() + 1, gy, r.right() - 1, gy)

        # 핸들 (흰 사각형, 빨간 테두리)
        p.setPen(QPen(QColor(180, 30, 30), 1.2))
        p.setBrush(QColor(255, 255, 255, 220))
        for hr in self._handle_rects().values():
            p.drawRect(hr.adjusted(2, 2, -2, -2))

        # 버튼 스트립 배경
        p.setBrush(QColor(18, 18, 18, 240))
        p.setPen(QPen(QColor(70, 70, 70), 1))
        p.drawRect(QRectF(0, lh, lw, self._BTN_H))

        # ── 재생 진행바 (GIF/Video) ── 스트립 최상단에 크게 표시
        fc = getattr(self._item, 'frame_count', 0)
        BH = self._BAR_H
        bar_x, bar_w = 8, lw - 16
        bar_y = lh + 4
        # 트랙 (라운드 사각형)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(55, 55, 55))
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, BH), BH / 2, BH / 2)
        if fc > 1:
            if hasattr(self._item, 'movie'):
                cf = self._item.movie.currentFrameNumber()
            else:
                cf = getattr(self._item, 'current_frame', 0)
            frac = max(0.0, min(1.0, cf / (fc - 1)))
            fill_w = max(BH, bar_w * frac)
            # 채워진 부분 (밝은 빨간 → 주황 그라데이션)
            grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
            grad.setColorAt(0.0, QColor(220, 60, 60))
            grad.setColorAt(1.0, QColor(240, 110, 50))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, BH), BH / 2, BH / 2)
            # 재생헤드 원
            head_x = bar_x + fill_w
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(200, 80, 80), 1.5))
            p.drawEllipse(QRectF(head_x - BH / 2, bar_y - 1, BH + 2, BH + 2))
            # 프레임 번호 텍스트
            p.setPen(QColor(180, 180, 180))
            p.setFont(QFont('Arial', 8))
            p.drawText(QRectF(bar_x, bar_y + BH + 2, bar_w, 12),
                       Qt.AlignRight, f'{cf + 1} / {fc}')
        elif fc == 0:
            # 이미지 아이템: 진행바 불필요, 아무것도 그리지 않음
            pass
        p.end()

    # ── 진행바 헬퍼 ───────────────────────────────────────────────────────────
    def _in_bar(self, pos):
        """클릭 위치가 진행바 영역인지 확인."""
        BH = self._BAR_H
        bar_y = self._lh + 4
        return (8 <= pos.x() <= self._lw - 8 and
                bar_y - 5 <= pos.y() <= bar_y + BH + 5)

    def _seek_bar(self, x):
        """진행바 x 좌표에 해당하는 프레임으로 이동."""
        fc = getattr(self._item, 'frame_count', 0)
        if fc < 2:
            return
        frac = max(0.0, min(1.0, (x - 8) / max(1, self._lw - 16)))
        frame = int(frac * (fc - 1))
        self._pending_bar_frame = frame
        if not getattr(self, '_bar_seek_pending', False):
            self._bar_seek_pending = True
            QTimer.singleShot(0, self._flush_bar_seek)
        self.update()

    def _flush_bar_seek(self):
        self._bar_seek_pending = False
        frame = getattr(self, '_pending_bar_frame', None)
        if frame is None:
            return
        if hasattr(self._item, 'showFrame'):
            self._item.showFrame(frame)
        elif hasattr(self._item, 'movie'):
            self._item.movie.jumpToFrame(frame)
        self.update()

    # ── 마우스 이벤트 ─────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if e.pos().y() >= self._lh:
                # 버튼 스트립 영역: 진행바 클릭 시 시킹 시작
                if self._in_bar(e.pos()):
                    self._seeking = True
                    # 진행 중인 재생을 일시정지하지 않고 시킹만 함
                    self._seek_bar(e.pos().x())
                e.accept()
                return
            h = self._hit(e.pos())
            if h:
                self._drag_handle = h
                self._drag_start_pos = QPoint(e.pos())
                self._drag_start_r = QRect(self._r)
            else:  # 콘텐츠 영역 빈 곳 - 아이템 이동
                self._item_dragging = True
                self._item._sm_press(e)
                self.raise_()  # item.raise_() 로 오버레이가 뒤로 밀리는 것 복원
            e.accept()

    def mouseMoveEvent(self, e):
        if self._seeking:
            if e.buttons() & Qt.LeftButton:
                self._seek_bar(e.pos().x())
            return
        if self._item_dragging:
            if e.buttons() & Qt.LeftButton:
                self._item._sm_move(e)
            return
        if not (e.buttons() & Qt.LeftButton) or self._drag_handle is None:
            cursor = self._CURSORS.get(self._hit(e.pos()), Qt.ArrowCursor)
            if e.pos().y() < self._lh and self._hit(e.pos()) is None:
                cursor = Qt.SizeAllCursor
            self.setCursor(cursor)
            return
        delta = e.pos() - self._drag_start_pos
        r = QRect(self._drag_start_r)
        h = self._drag_handle
        lw, lh = self._lw, self._lh
        MIN = 20
        if h == 'move':
            r.translate(delta)
            r.moveLeft(max(0, min(lw - r.width(),  r.left())))
            r.moveTop( max(0, min(lh - r.height(), r.top())))
        else:
            if 'l' in h:
                r.setLeft(max(0, min(r.right() - MIN, self._drag_start_r.left() + delta.x())))
            if 'r' in h:
                r.setRight(max(r.left() + MIN, min(lw, self._drag_start_r.right() + delta.x())))
            if 't' in h:
                r.setTop(max(0, min(r.bottom() - MIN, self._drag_start_r.top() + delta.y())))
            if 'b' in h:
                r.setBottom(max(r.top() + MIN, min(lh, self._drag_start_r.bottom() + delta.y())))
        self._r = r
        self._clamp_r()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._item_dragging:
                self._item._sm_release(e)
                self._item_dragging = False
            self._seeking = False
            self._drag_handle = None
            self._clamp_r()

    def mouseDoubleClickEvent(self, e):
        """더블클릭 → 아이템에 재생/정지 전달."""
        if e.button() == Qt.LeftButton:
            if hasattr(self._item, 'togglePlay'):
                self._item.togglePlay()
        e.accept()

    def keyPressEvent(self, e):
        key = e.key()
        step = 10 if (e.modifiers() & Qt.ShiftModifier) else 1
        r = QRect(self._r)
        lw, lh = self._lw, self._lh
        if key == Qt.Key_Left:
            r.moveLeft(max(0, r.left() - step))
        elif key == Qt.Key_Right:
            r.moveLeft(min(max(0, lw - r.width()), r.left() + step))
        elif key == Qt.Key_Up:
            r.moveTop(max(0, r.top() - step))
        elif key == Qt.Key_Down:
            r.moveTop(min(max(0, lh - r.height()), r.top() + step))
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm(); return
        elif key == Qt.Key_Escape:
            self._cancel_crop(); return
        elif key == Qt.Key_Space:
            # 스페이스바 → 재생/정지
            if hasattr(self._item, 'togglePlay'):
                self._item.togglePlay()
            return
        else:
            # 미처리 키는 메인 윈도우로 전달
            if _main_window:
                QApplication.sendEvent(_main_window, e)
            return
        self._r = r
        self._clamp_r()
        self.update()

    # ── 확인 / 취소 / 초기화 ─────────────────────────────────────────────────
    def _reset_rect(self):
        self._r = QRect(0, 0, self._lw, self._lh)
        self.update()

    def _confirm(self):
        r = self._r
        lw, lh = self._lw, self._lh
        crop = [
            max(0.0, min(1.0, r.left()   / lw)),
            max(0.0, min(1.0, r.top()    / lh)),
            max(0.0, min(1.0, r.right()  / lw)),
            max(0.0, min(1.0, r.bottom() / lh)),
        ]
        if crop[2] <= crop[0]: crop[2] = min(1.0, crop[0] + 0.01)
        if crop[3] <= crop[1]: crop[3] = min(1.0, crop[1] + 0.01)
        self._item.setCrop(crop)
        self._cleanup()

    def _cancel_crop(self):
        self._item._crop = self._orig_crop
        self._item.updateSize()
        self._item._label.update()
        self._cleanup()

    def _cleanup(self):
        self._sync_timer.stop()
        # 편집 전 컨트롤바가 보였으면 선택 상태일 때 복원
        try:
            self._item._editing_overlay_active = False
            hb = getattr(self._item, '_hover_bar', None)
            if hb:
                hb._crop_editing = False
                hb._refresh_edit_btns()
            if self._had_control_bar_hidden and hasattr(self._item, 'control_bar'):
                if getattr(self._item, 'is_selected', False):
                    self._item._show_control_bar()
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()


# ── _TrimOverlay ───────────────────────────────────────────────────────────────

class _TrimOverlay(QWidget):
    """타임라인 핸들로 구간(Trim)을 직관적으로 설정하는 오버레이."""
    _HS  = 10   # 핸들 히트 반폭(px)
    _TH  = 64   # 타임라인 바 높이
    _BTH = 60   # 버튼 스트립 높이 (버튼행1 26px + 버튼행2 16px + 여백)
    _PAD = 16   # 좌우 패딩

    def __init__(self, item, canvas):
        super().__init__(canvas)
        self._item   = item
        self._canvas = canvas
        self._fc     = max(2, item.frame_count)
        self._orig_start = item._trim_start
        self._orig_end   = item._trim_end
        self._start  = item._trim_start
        self._end    = item._trim_end
        self._drag   = None  # 'start' | 'end' | 'seek' | None

        self._lw = item._label.width()
        self._lh = item._label.height()
        self._item_dragging = False

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # ── 버튼 ──────────────────────────────────────────────────────────────
        ok_s  = ('QPushButton{background:#1a5fc8;color:#fff;border:1px solid #3a80e8;'
                 'border-radius:4px;padding:4px 18px;font-size:11px;font-weight:bold;}'
                 'QPushButton:hover{background:#3a80e8;}')
        cn_s  = ('QPushButton{background:#2d2d2d;color:#ddd;border:1px solid #555;'
                 'border-radius:4px;padding:4px 18px;font-size:11px;}'
                 'QPushButton:hover{background:#3d3d3d;}')
        rs_s  = ('QPushButton{background:transparent;color:#aaa;border:none;font-size:10px;}'
                 'QPushButton:hover{color:#fff;}')

        self._ok_btn     = QPushButton('적용',   self)
        self._cancel_btn = QPushButton('취소',   self)
        self._reset_btn  = QPushButton('초기화', self)
        self._ok_btn    .setStyleSheet(ok_s)
        self._cancel_btn.setStyleSheet(cn_s)
        self._reset_btn .setStyleSheet(rs_s)
        self._ok_btn    .clicked.connect(self._confirm)
        self._cancel_btn.clicked.connect(self._cancel_trim)
        self._reset_btn .clicked.connect(self._reset)

        # 컨트롤바·호버바 숨김 (편집창이 그 자리를 차지)
        item._editing_overlay_active = True
        self._ctrl_hidden = False
        if hasattr(item, 'control_bar'):
            self._ctrl_hidden = item.control_bar.isVisible()
            item._hide_control_bar()
        if hasattr(item, '_hover_bar'):
            item._hover_bar.hide()
        if hasattr(item, '_blend_bar'):
            item._blend_bar.hide()

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._reposition)
        self._sync_timer.start(30)

        self._reposition()
        self.raise_()
        self.show()

    # ── 좌표 변환 ──────────────────────────────────────────────────────────────
    def _fx(self, frame):
        """프레임 → 타임라인 x 좌표."""
        bw = max(1, self._lw - 2 * self._PAD)
        return int(self._PAD + bw * frame / max(1, self._fc - 1))

    def _xf(self, x):
        """타임라인 x 좌표 → 프레임 번호."""
        bw = max(1, self._lw - 2 * self._PAD)
        frac = max(0.0, min(1.0, (x - self._PAD) / bw))
        return max(0, min(self._fc - 1, int(round(frac * (self._fc - 1)))))

    def _fmt(self, frame):
        """프레임 번호를 시간 문자열로."""
        fps = getattr(self._item, 'fps', 0)
        if fps and fps > 0:
            secs = frame / fps
            m = int(secs) // 60; s = secs - m * 60
            return f'{m}:{s:04.1f}'
        return str(frame)

    # ── 위치 동기화 ────────────────────────────────────────────────────────────
    def _reposition(self):
        lbl = self._item._label
        if not lbl.isVisible():
            return
        new_lw, new_lh = lbl.width(), lbl.height()
        if new_lw != self._lw or new_lh != self._lh:
            self._lw = new_lw
            self._lh = new_lh

        gp  = lbl.mapToGlobal(QPoint(0, 0))
        pos = self._canvas.mapFromGlobal(gp)
        w   = self._lw
        h   = self._lh + self._TH + self._BTH
        self.setGeometry(pos.x(), pos.y(), w, h)

        # 버튼 위치: 2행 배치 (행1: 적용·취소, 행2: 초기화)
        bh = 26
        bw = min(76, max(30, (w - 22) // 2))
        mid = w // 2
        ok_x = max(8, mid - bw - 3)
        by  = self._lh + self._TH + 6
        self._ok_btn    .setGeometry(ok_x,           by, bw, bh)
        self._cancel_btn.setGeometry(ok_x + bw + 6,  by, bw, bh)
        self._reset_btn .setGeometry(8, by + bh + 4, 52, 16)

        self.raise_()  # setGeometry 후 Windows HWND Z-순서 리셋 방지
        # 드래그 중에는 mouseMoveEvent에서 update()가 이미 호출되므로 생략
        if self._drag is None and not getattr(self, '_item_dragging', False):
            self.update()

    # ── 페인트 ─────────────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p   = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        lw, lh = self._lw, self._lh
        TH, BTH, PAD = self._TH, self._BTH, self._PAD

        # 콘텐츠 영역 - 옅은 딤 + 하단 경계선
        p.setBrush(QColor(0, 0, 0, 30))
        p.setPen(Qt.NoPen)
        p.drawRect(0, 0, lw, lh)
        p.setPen(QPen(QColor(60, 140, 255, 180), 2))
        p.drawLine(0, lh, lw, lh)

        # ── 타임라인 스트립 배경 ──────────────────────────────────────────────
        p.setBrush(QColor(16, 16, 16, 250))
        p.setPen(QPen(QColor(65, 65, 65), 1))
        p.drawRect(QRectF(0, lh, lw, TH))

        # ── 트랙 ────────────────────────────────────────────────────────────
        TRK_Y = lh + TH // 2 - 7
        TRK_H = 14
        sx = self._fx(self._start)
        ex = self._fx(self._end)

        # 전체 트랙 배경 (어두운 회색)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(40, 40, 40))
        p.drawRoundedRect(QRectF(PAD, TRK_Y, lw - 2*PAD, TRK_H), TRK_H/2, TRK_H/2)

        # 선택 구간 (파란 그라데이션)
        if ex > sx:
            grad = QLinearGradient(sx, 0, ex, 0)
            grad.setColorAt(0.0, QColor(30, 100, 220))
            grad.setColorAt(1.0, QColor(80, 180, 255))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(sx, TRK_Y, ex - sx, TRK_H), TRK_H/2, TRK_H/2)

        # 제외 구간 어둡게 (오버레이)
        p.setBrush(QColor(0, 0, 0, 120))
        if sx > PAD:
            p.drawRoundedRect(QRectF(PAD, TRK_Y, sx - PAD, TRK_H), TRK_H/2, TRK_H/2)
        if ex < lw - PAD:
            p.drawRoundedRect(QRectF(ex, TRK_Y, lw - PAD - ex, TRK_H), TRK_H/2, TRK_H/2)

        # ── 핸들 (시작 / 끝) ────────────────────────────────────────────────
        for x, col, lbl_txt in [(sx, QColor(60, 150, 255), self._fmt(self._start)),
                                 (ex, QColor(130, 210, 255), self._fmt(self._end))]:
            # 수직 막대
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x - 4, lh + 4, 8, TH - 8), 3, 3)
            # 위쪽 화살표
            tri_top = QPolygonF([QPointF(x, lh + 2),
                                  QPointF(x - 7, lh + 10),
                                  QPointF(x + 7, lh + 10)])
            p.drawPolygon(tri_top)
            # 아래쪽 화살표
            tri_bot = QPolygonF([QPointF(x, lh + TH - 2),
                                  QPointF(x - 7, lh + TH - 10),
                                  QPointF(x + 7, lh + TH - 10)])
            p.drawPolygon(tri_bot)
            # 시간 라벨
            p.setPen(QColor(160, 210, 255))
            p.setFont(QFont('Arial', 8, QFont.Bold))
            align = Qt.AlignLeft if lbl_txt == self._fmt(self._start) else Qt.AlignRight
            tx = x + 6 if lbl_txt == self._fmt(self._start) else x - 6 - 60
            p.drawText(QRectF(tx, lh + TH - 18, 60, 14), align, lbl_txt)

        # ── 재생헤드 ────────────────────────────────────────────────────────
        if hasattr(self._item, 'movie'):
            cf = self._item.movie.currentFrameNumber()
        else:
            cf = getattr(self._item, 'current_frame', 0)
        ph = self._fx(cf)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2))
        p.drawLine(ph, lh + 6, ph, lh + TH - 6)
        p.setBrush(QColor(255, 255, 255))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(ph, lh + TH // 2), 5, 5)

        # ── 구간 정보 텍스트 ────────────────────────────────────────────────
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont('Arial', 8))
        info = f'구간: {self._fmt(self._start)} ~ {self._fmt(self._end)}  ({self._end - self._start + 1}f)'
        p.drawText(QRectF(PAD, lh + 4, lw - 2*PAD, 14), Qt.AlignHCenter, info)

        # ── 버튼 스트립 배경 ─────────────────────────────────────────────────
        p.setBrush(QColor(16, 16, 16, 250))
        p.setPen(QPen(QColor(65, 65, 65), 1))
        p.drawRect(QRectF(0, lh + TH, lw, BTH))
        p.end()

    # ── 마우스 이벤트 ─────────────────────────────────────────────────────────
    def _in_bar(self, pos):
        return self._lh <= pos.y() <= self._lh + self._TH

    def _hit_handle(self, x):
        if abs(x - self._fx(self._start)) <= self._HS:
            return 'start'
        if abs(x - self._fx(self._end)) <= self._HS:
            return 'end'
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._in_bar(e.pos()):
                h = self._hit_handle(e.pos().x())
                self._drag = h if h else 'seek'
                if self._drag == 'seek':
                    self._seek(e.pos().x())
            elif e.pos().y() < self._lh:  # 콘텐츠 영역 - 아이템 이동
                self._item_dragging = True
                self._item._sm_press(e)
                self.raise_()  # item.raise_() 로 오버레이가 뒤로 밀리는 것 복원
            e.accept()

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.LeftButton):
            if self._in_bar(e.pos()):
                h = self._hit_handle(e.pos().x())
                self.setCursor(Qt.SizeHorCursor if h else Qt.PointingHandCursor)
            elif e.pos().y() < self._lh:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        if self._drag == 'start':
            f = self._xf(e.pos().x())
            self._start = max(0, min(self._end - 1, f))
            self._item._trim_start = self._start
            self._seek_to(self._start)
            self.update()
        elif self._drag == 'end':
            f = self._xf(e.pos().x())
            self._end = max(self._start + 1, min(self._fc - 1, f))
            self._item._trim_end = self._end
            self._seek_to(self._end)
            self.update()
        elif self._drag == 'seek':
            self._seek(e.pos().x())
        elif getattr(self, '_item_dragging', False):
            self._item._sm_move(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if getattr(self, '_item_dragging', False):
                self._item._sm_release(e)
                self._item_dragging = False
            self._flush_seek()  # 마지막 seek 위치 반영
            self._drag = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and hasattr(self._item, 'togglePlay'):
            self._item.togglePlay()
        e.accept()

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm()
        elif key == Qt.Key_Escape:
            self._cancel_trim()
        elif key == Qt.Key_Space:
            if hasattr(self._item, 'togglePlay'):
                self._item.togglePlay()
        else:
            if _main_window:
                QApplication.sendEvent(_main_window, e)

    def _seek(self, x):
        import time
        frame = self._xf(x)
        self._pending_seek_frame = frame
        now = time.monotonic()
        if now - getattr(self, '_last_seek_t', 0) >= 0.033:  # ~30fps 제한
            self._flush_seek()

    def _flush_seek(self):
        import time
        frame = getattr(self, '_pending_seek_frame', None)
        if frame is None:
            return
        self._pending_seek_frame = None
        self._last_seek_t = time.monotonic()
        self._seek_to(frame)

    def _seek_to(self, frame):
        if hasattr(self._item, 'showFrame'):
            self._item.showFrame(frame)
        elif hasattr(self._item, 'movie'):
            self._item.movie.jumpToFrame(frame)
        self.update()

    # ── 확인 / 취소 / 초기화 ─────────────────────────────────────────────────
    def _reset(self):
        self._start = 0
        self._end   = self._fc - 1
        self._item._trim_start = self._start
        self._item._trim_end   = self._end
        self.update()

    def _confirm(self):
        self._item.setTrim(self._start, self._end)
        self._cleanup()

    def _cancel_trim(self):
        self._item._trim_start = self._orig_start
        self._item._trim_end   = self._orig_end
        self._cleanup()

    def _cleanup(self):
        self._sync_timer.stop()
        try:
            self._item._editing_overlay_active = False
            hb = getattr(self._item, '_hover_bar', None)
            if hb:
                hb._trim_editing = False
                hb._refresh_edit_btns()
            if self._ctrl_hidden and hasattr(self._item, 'control_bar'):
                if getattr(self._item, 'is_selected', False):
                    self._item._show_control_bar()
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()


# ── _TextEditPanel ─────────────────────────────────────────────────────────────

class _TextEditPanel(QWidget):
    """Semi-transparent floating toolbar panel background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(18, 18, 25, 210))
        p.setPen(QPen(QColor(60, 60, 70), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)


# ── _TextBgPanel ───────────────────────────────────────────────────────────────

class _TextBgPanel(QWidget):
    """Background panel for TextItem that paints rgba + border-radius."""
    def __init__(self, text_item, parent=None):
        super().__init__(parent)
        self._ti = text_item
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._ti._bg_color
        p.setBrush(QBrush(c))
        if self._ti.is_selected:
            p.setPen(QPen(QColor('#00aaff'), 2))
        else:
            p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)

    def mouseMoveEvent(self, e):
        if self._ti.is_selected:
            hit = self._ti._rm_hit(e.pos())
            self.setCursor(self._ti._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._ti._rm_hover(e.pos()) and self._ti._overlay:
                self._ti._overlay.update()
        e.ignore()

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._ti._rm_clear_hover() and self._ti._overlay:
            self._ti._overlay.update()


# ── _HandleOverlay ─────────────────────────────────────────────────────────────

class _HandleOverlay(QWidget):
    """Transparent overlay that draws resize handles."""
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, e):
        if not self._item.is_selected:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width() - 1, self.height() - 1
        # Thin selection border at edge
        p.setPen(QPen(QColor(60, 150, 240, 180), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(0, 0, w, h)
        # Corner bracket arms
        ARM = 10
        corners = [
            (0, 0,  ARM, 0,   0,  ARM),
            (w, 0, -ARM, 0,   0,  ARM),
            (0, h,  ARM, 0,   0, -ARM),
            (w, h, -ARM, 0,   0, -ARM),
        ]
        p.setPen(QPen(QColor(255, 255, 255, 240), 2.5, Qt.SolidLine, Qt.SquareCap))
        for (x, y, dx1, dy1, dx2, dy2) in corners:
            p.drawLine(QPointF(x, y), QPointF(x + dx1, y + dy1))
            p.drawLine(QPointF(x, y), QPointF(x + dx2, y + dy2))
        # Edge midpoint handles
        MID_H = 5
        mid_pts = [(w // 2, 0, 'tc'), (w // 2, h, 'bc'),
                   (0, h // 2, 'ml'), (w, h // 2, 'mr')]
        for (mx, my, key) in mid_pts:
            hov = (key == self._item._hover_handle)
            fill = QColor(255, 255, 255, 255) if hov else QColor(220, 235, 255, 230)
            sz = MID_H + (2 if hov else 0)
            p.setBrush(fill)
            p.setPen(QPen(QColor(60, 140, 240) if hov else QColor(80, 160, 255, 200), 1.5))
            p.drawRect(mx - sz, my - sz, sz * 2, sz * 2)


# ── TextItem ───────────────────────────────────────────────────────────────────

class TextItem(QWidget, ResizeMixin, SelectableMixin):
    """Editable text canvas item."""
    selected = pyqtSignal(object)
    _RM_S = 14
    _ANCHOR_FRAC = ResizeMixin._ANCHOR_FRAC
    _CURSORS = ResizeMixin._CURSORS

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sm_init()
        self._rm_init()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._bg_color = QColor(60, 60, 60, 220)
        self._font_size = 14
        self._text_color = QColor(255, 255, 255)
        self._text_align = Qt.AlignLeft
        self._user_resized = False
        self._inline_toolbar = None
        self._inline_edit = None
        self._rz_press_pos = None
        self._rz_press_size = None
        self._rz_press_tl = None
        self._opacity = 1.0
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        # opacity is handled in _ContentLabel.paintEvent — no widget-level effect

        self._outer = _TextBgPanel(self, self)
        outer_lay = QVBoxLayout(self._outer)
        outer_lay.setContentsMargins(12, 10, 12, 10)
        self._label = QLabel(self._outer)
        self._label.setWordWrap(False)
        self._label.setTextInteractionFlags(Qt.NoTextInteraction)
        self._label.setMouseTracking(True)
        outer_lay.addWidget(self._label)

        self._overlay = _HandleOverlay(self, self)
        self._overlay.raise_()
        self._label.setText('텍스트를 입력하세요')
        self._applyStyle()
        self.adjustSize()

    def getState(self):
        return {
            'type': 'text',
            'x': self.pos().x(), 'y': self.pos().y(),
            'w': self.width(), 'h': self.height(),
            'user_resized': self._user_resized,
            'text': self._label.text(),
            'bg_color': self._bg_color.name(QColor.HexArgb),
            'text_color': self._text_color.name(QColor.HexArgb),
            'font_size': self._font_size,
            'align': int(self._text_align),
            'opacity': self._opacity,
        }

    def setItemOpacity(self, value):
        self._opacity = max(0.0, min(1.0, value))
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(self._opacity)
        self._label.update()
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            self._blend_bar.refresh()

    def applyState(self, s):
        self._bg_color = QColor(s.get('bg_color', '#dc3c3c3c'))
        self._text_color = QColor(s.get('text_color', '#ffffff'))
        self._font_size = s.get('font_size', 14)
        self._text_align = Qt.Alignment(s.get('align', int(Qt.AlignLeft)))
        self._user_resized = s.get('user_resized', False)
        self._label.setText(s.get('text', ''))
        self._applyStyle()
        if self._user_resized:
            self.setFixedSize(s.get('w', 200), s.get('h', 80))

    def _applyStyle(self):
        self._outer.update()
        canvas = self.parent()
        scale = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else CanvasWidget.DEFAULT_SCALE
        font = get_ui_font(max(1, round(self._font_size * scale / CanvasWidget.DEFAULT_SCALE)))
        self._label.setFont(font)
        self._label.setAlignment(self._text_align | Qt.AlignTop)
        self._label.setStyleSheet(
            f'color:{self._text_color.name()};background:transparent;border:none;')
        if not self._user_resized:
            fm = QFontMetrics(font)
            lines = self._label.text().split('\n') if self._label.text() else ['']
            max_w = max((fm.horizontalAdvance(l) for l in lines), default=80)
            w = max(60, max_w + 48)
            h = max(30, fm.height() * len(lines) + 20)
            self._label.setMinimumWidth(0)
            self._label.setMaximumWidth(16777215)
            self.setFixedSize(w, h)
        self._outer.setGeometry(0, 0, self.width(), self.height())
        if self._overlay:
            self._overlay.setGeometry(0, 0, self.width(), self.height())

    def _refreshBorder(self):
        self._outer.update()
        if self._overlay:
            self._overlay.update()

    def select(self, additive=False):
        canvas = self.parent()
        if not additive and hasattr(canvas, 'deselectAll'):
            canvas.deselectAll()
        self.is_selected = True
        self._refreshBorder()
        if hasattr(canvas, 'addToSelection'):
            canvas.addToSelection(self)
        self.selected.emit(self)

    def deselect(self):
        self.is_selected = False
        self._refreshBorder()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._outer.setGeometry(0, 0, self.width(), self.height())
        if self._overlay:
            self._overlay.setGeometry(0, 0, self.width(), self.height())

    def _rm_handle_pts(self):
        w, h = self.width(), self.height()
        s = self._RM_S // 2
        return {
            'tl': QPoint(s, s),         'tc': QPoint(w // 2, s),     'tr': QPoint(w - s, s),
            'ml': QPoint(s, h // 2),                                   'mr': QPoint(w - s, h // 2),
            'bl': QPoint(s, h - s),     'bc': QPoint(w // 2, h - s), 'br': QPoint(w - s, h - s),
        }

    def _rm_hit(self, pos):
        half = self._RM_S // 2 + 4
        for k, pt in self._rm_handle_pts().items():
            if abs(pos.x() - pt.x()) <= half and abs(pos.y() - pt.y()) <= half:
                return k
        return None

    def _do_resize(self, delta):
        h = self._rz_handle
        pw, ph = self._rz_press_size.width(), self._rz_press_size.height()
        px, py = self._rz_press_tl.x(), self._rz_press_tl.y()
        dx, dy = delta.x(), delta.y()
        nx, ny, nw, nh = px, py, pw, ph
        if 'l' in h:
            nw = max(60, pw - dx)
            nx = px + (pw - nw)
        if 'r' in h:
            nw = max(60, pw + dx)
        if 't' in h:
            nh = max(30, ph - dy)
            ny = py + (ph - nh)
        if 'b' in h:
            nh = max(30, ph + dy)
        self._user_resized = True
        self.setFixedSize(nw, nh)
        self.move(nx, ny)
        self._outer.setGeometry(0, 0, nw, nh)
        if self._overlay:
            self._overlay.setGeometry(0, 0, nw, nh)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        if e.button() == Qt.LeftButton and self.is_selected:
            hit = self._rm_hit(e.pos())
            if hit:
                self._rz_handle = hit
                self._rz_press_pos = e.globalPos()
                self._rz_press_size = QSize(self.width(), self.height())
                self._rz_press_tl = QPoint(self.pos())
                self.setCursor(self._CURSORS[hit])
                return
        self._sm_press(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore()
            return
        if self._rz_handle and e.buttons() & Qt.LeftButton:
            self._do_resize(e.globalPos() - self._rz_press_pos)
            self._sync_tb()
            return
        if self.is_selected:
            hit = self._rm_hit(e.pos())
            self.setCursor(self._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._rm_hover(e.pos()) and self._overlay:
                self._overlay.update()
        self._sm_move(e)

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._rm_clear_hover() and self._overlay:
            self._overlay.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        if self._rz_handle and e.button() == Qt.LeftButton:
            self._rz_handle = None
            self.setCursor(Qt.ArrowCursor)
            return
        self._sm_release(e)

    def contextMenuEvent(self, e):
        self._showContextMenu(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._startInlineEdit()

    def _tbPos(self):
        x = self.pos().x()
        y = self.pos().y() - 48
        if y < 0:
            y = self.pos().y() + self.height() + 6
        return x, y

    def _sync_tb(self):
        if self._inline_toolbar:
            x, y = self._tbPos()
            self._inline_toolbar.move(x, y)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._sync_tb()

    def _startInlineEdit(self):
        if self._inline_edit:
            return
        canvas = self.parent()
        self._label.hide()

        # Create inline text editor
        canvas = self.parent()
        _scale = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else CanvasWidget.DEFAULT_SCALE
        _scaled_fs = max(1, round(self._font_size * _scale / CanvasWidget.DEFAULT_SCALE))
        self._inline_edit = QTextEdit(self._outer)
        self._inline_edit.setLineWrapMode(QTextEdit.NoWrap)
        self._inline_edit.setPlainText(self._label.text())
        self._inline_edit.setFont(get_ui_font(_scaled_fs))
        self._inline_edit.setGeometry(4, 4, self._outer.width() - 8, self._outer.height() - 8)
        tc = self._text_color.name()
        self._inline_edit.setStyleSheet(
            f'QTextEdit{{background:transparent;color:{tc};border:none;padding:2px 6px;}}'
            f'QTextEdit QScrollBar:vertical{{width:3px;background:transparent;}}'
            f'QTextEdit QScrollBar::handle:vertical{{background:rgba(255,255,255,60);border-radius:1px;}}')
        self._inline_edit.show()
        cursor = self._inline_edit.textCursor()
        cursor.movePosition(cursor.End)
        self._inline_edit.setTextCursor(cursor)

        # Floating toolbar
        if canvas:
            self._inline_toolbar = _TextEditPanel(canvas)
            self._inline_toolbar.setMinimumHeight(38)
            tb_lay = QHBoxLayout(self._inline_toolbar)
            tb_lay.setContentsMargins(10, 5, 10, 5)
            tb_lay.setSpacing(8)

            bg_btn = QPushButton('배경색')
            tc_btn = QPushButton('글자색')
            fs_slider = QSlider(Qt.Horizontal)
            fs_slider.setRange(6, 72)
            fs_slider.setValue(self._font_size)
            fs_slider.setFixedWidth(110)
            fs_slider.setStyleSheet(
                'QSlider::groove:horizontal{height:4px;background:rgba(255,255,255,40);border-radius:2px;}'
                'QSlider::sub-page:horizontal{background:rgba(0,170,255,160);border-radius:2px;}'
                'QSlider::handle:horizontal{width:13px;height:13px;margin:-5px 0;'
                'background:rgba(220,220,220,230);border-radius:6px;}')
            fs_label = QLabel(str(self._font_size))
            fs_label.setStyleSheet('color:rgba(200,200,200,220);background:transparent;font-size:11px;')
            fs_label.setFixedWidth(26)
            btn_style = ('QPushButton{background:rgba(255,255,255,14);color:#bbb;border:none;'
                         'border-radius:4px;font-size:11px;padding:5px 12px;}'
                         'QPushButton:hover{background:rgba(255,255,255,28);color:#fff;}')
            bg_btn.setStyleSheet(btn_style)
            tc_btn.setStyleSheet(btn_style)

            for w in [bg_btn, tc_btn, fs_slider, fs_label]:
                tb_lay.addWidget(w)
            self._inline_toolbar.adjustSize()
            x, y = self._tbPos()
            self._inline_toolbar.move(x, y)
            self._inline_toolbar.show()
            self._inline_toolbar.raise_()

            def _update_fs(v):
                self._font_size = v
                fs_label.setText(str(v))
                _sc = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else CanvasWidget.DEFAULT_SCALE
                _sfs = max(1, round(v * _sc / CanvasWidget.DEFAULT_SCALE))
                self._inline_edit.setFont(get_ui_font(_sfs))

            def _pick_bg():
                def _ap(c):
                    self._bg_color = c
                    border = '2px solid #00aaff' if self.is_selected else 'none'
                    self._outer.setStyleSheet(
                        f'QWidget{{background:{c.name(QColor.HexArgb)};border-radius:6px;border:{border};}}')
                ColorPickerDialog.getColor(self._bg_color, canvas, '배경색', show_alpha=True, on_change=_ap)

            def _pick_tc():
                def _ap(c):
                    self._text_color = c
                    self._inline_edit.setStyleSheet(
                        f'QTextEdit{{background:transparent;color:{c.name()};border:none;padding:2px 6px;}}')
                ColorPickerDialog.getColor(self._text_color, canvas, '텍스트 색', show_alpha=False, on_change=_ap)

            fs_slider.valueChanged.connect(_update_fs)
            bg_btn.clicked.connect(_pick_bg)
            tc_btn.clicked.connect(_pick_tc)

        def _cleanup(save=True):
            if self._inline_edit is None:
                return
            if save:
                self._label.setText(self._inline_edit.toPlainText())
            self._label.show()
            self._inline_edit.deleteLater()
            self._inline_edit = None
            if self._inline_toolbar:
                self._inline_toolbar.deleteLater()
                self._inline_toolbar = None
            self._applyStyle()

        def _kpe(ev):
            if ev.key() == Qt.Key_Escape:
                _cleanup(False)
            else:
                QTextEdit.keyPressEvent(self._inline_edit, ev)

        def _foe(ev):
            QTextEdit.focusOutEvent(self._inline_edit, ev)
            def _check():
                if self._inline_edit and not self._inline_edit.hasFocus():
                    focused = QApplication.focusWidget()
                    if self._inline_toolbar and self._inline_toolbar.isAncestorOf(focused):
                        return
                    _cleanup(True)
            QTimer.singleShot(60, _check)

        def _on_text_changed():
            if self._inline_edit is None or self._user_resized:
                return
            doc = self._inline_edit.document()
            new_w = max(60, int(doc.idealWidth()) + 48)
            new_h = max(30, int(doc.size().height()) + 20)
            self.setFixedSize(new_w, new_h)
            self._outer.setGeometry(0, 0, new_w, new_h)
            self._inline_edit.setGeometry(4, 4, new_w - 8, new_h - 8)
            if self._overlay:
                self._overlay.setGeometry(0, 0, new_w, new_h)

        self._inline_edit.textChanged.connect(_on_text_changed)
        self._inline_edit.keyPressEvent = _kpe
        self._inline_edit.focusOutEvent = _foe
        self._inline_edit.setFocus()

    def _showContextMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('복사', self._copyItem)
        menu.addSeparator()
        menu.addAction('편집...', self._startInlineEdit)
        menu.addAction('삭제', self.cleanup)
        menu.exec_(pos)

    def _copyItem(self):
        canvas = self.parent()
        if hasattr(canvas, '_item_clipboard'):
            canvas._item_clipboard = [self.getState()]
            canvas._paste_offset = 0

    def cleanup(self):
        _deregister_canvas_item(self)
        if self._inline_toolbar:
            self._inline_toolbar.deleteLater()
        self.deleteLater()


def _apply_crop_and_save(item, dest):
    """크롭+트림이 적용된 상태로 item의 파일을 dest에 저장한다."""
    global HAS_PIL, _PILImage

    src = getattr(item, 'file_path', None)
    if not src or not os.path.isfile(src):
        return False

    crop = list(getattr(item, '_crop', [0.0, 0.0, 1.0, 1.0]))
    is_cropped = (crop[0] > 0.001 or crop[1] > 0.001 or
                  crop[2] < 0.999 or crop[3] < 0.999)

    fc = getattr(item, 'frame_count', 1)
    trim_start = getattr(item, '_trim_start', 0)
    trim_end   = getattr(item, '_trim_end',   fc - 1)
    is_trimmed = (trim_start > 0 or trim_end < fc - 1)

    if not is_cropped and not is_trimmed:
        shutil.copy(src, dest)
        return True

    # ── 정적 이미지 (트림 해당 없음, 크롭만) ────────────────────
    if isinstance(item, ImageItem):
        if not is_cropped:
            shutil.copy(src, dest)
            return True
        try:
            img = cv2.imread(src, cv2.IMREAD_UNCHANGED)
            if img is None:
                raise ValueError('cv2.imread 실패')
            h, w = img.shape[:2]
            x1 = int(crop[0] * w); y1 = int(crop[1] * h)
            x2 = max(x1 + 1, int(crop[2] * w))
            y2 = max(y1 + 1, int(crop[3] * h))
            ok = cv2.imwrite(dest, img[y1:y2, x1:x2])
            if not ok:
                raise ValueError('cv2.imwrite 실패')
            return True
        except Exception as ex:
            print(f'[image crop save] {ex}')
            shutil.copy(src, dest)
            return True

    # ── GIF (크롭 + 트림) ───────────────────────────────────────
    if isinstance(item, GifItem):
        if not HAS_PIL:
            try:
                import subprocess as _sp
                _sp.check_call(
                    [sys.executable, '-m', 'pip', 'install', 'Pillow', '-q',
                     '--no-warn-script-location'],
                    creationflags=getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000))
                from PIL import Image as _PI
                _PILImage = _PI
                HAS_PIL = True
            except Exception:
                pass

        if not HAS_PIL:
            shutil.copy(src, dest)
            if _main_window:
                _main_window._show_toast('GIF 저장 실패: Pillow 설치 필요')
            return True

        try:
            img = _PILImage.open(src)
            n = getattr(img, 'n_frames', 1)
            t_end = min(trim_end + 1, n)
            frames, durations = [], []
            for i in range(trim_start, t_end):
                try:
                    img.seek(i)
                except EOFError:
                    break
                f = img.convert('RGBA')
                if is_cropped:
                    fw, fh = f.size
                    x1 = int(crop[0] * fw); y1 = int(crop[1] * fh)
                    x2 = max(x1 + 1, int(crop[2] * fw))
                    y2 = max(y1 + 1, int(crop[3] * fh))
                    f = f.crop((x1, y1, x2, y2))
                frames.append(f)
                durations.append(img.info.get('duration', 100))
            if frames:
                frames[0].save(dest, save_all=True,
                               append_images=frames[1:],
                               loop=0, duration=durations, disposal=2)
        except Exception as ex:
            print(f'[gif save] {ex}')
            shutil.copy(src, dest)
        return True

    # ── 비디오 (크롭 + 트림) ────────────────────────────────────
    if isinstance(item, VideoItem) and HAS_OPENCV:
        try:
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                raise ValueError('VideoCapture 열기 실패')
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            ow  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            oh  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            if is_cropped:
                x1 = int(crop[0] * ow); y1 = int(crop[1] * oh)
                x2 = max(x1 + 1, int(crop[2] * ow))
                y2 = max(y1 + 1, int(crop[3] * oh))
            else:
                x1, y1, x2, y2 = 0, 0, ow, oh
            cw, ch = x2 - x1, y2 - y1

            ext_low = os.path.splitext(dest)[1].lower()
            codecs = ['mp4v', 'avc1', 'XVID'] if ext_low in ('.mp4', '.m4v') else ['XVID', 'mp4v']
            out = None
            for cc in codecs:
                fourcc = cv2.VideoWriter_fourcc(*cc)
                out = cv2.VideoWriter(dest, fourcc, fps, (cw, ch))
                if out.isOpened():
                    break
                out.release()
                out = None

            if out is None or not out.isOpened():
                raise ValueError('VideoWriter 열기 실패 (코덱 없음)')

            if trim_start > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, trim_start)

            frame_num = trim_start
            while frame_num <= trim_end:
                ret, frame = cap.read()
                if not ret:
                    break
                out.write(frame[y1:y2, x1:x2])
                frame_num += 1

            cap.release()
            out.release()
        except Exception as ex:
            print(f'[video save] {ex}')
            if _main_window:
                _main_window._show_toast(f'비디오 저장 실패: {ex}')
            shutil.copy(src, dest)
        return True

    shutil.copy(src, dest)
    return True


def _quick_save_to_dir(item):
    """아이템의 파일을 SAVE_DIR에 복사한다 (크롭 적용). 같은 이름이 있으면 번호를 붙인다."""
    src = getattr(item, 'file_path', None)
    if not src or not os.path.isfile(src):
        return None
    os.makedirs(SAVE_DIR, exist_ok=True)
    name = os.path.basename(src)
    base, ext = os.path.splitext(name)
    dest = os.path.join(SAVE_DIR, name)
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(SAVE_DIR, f'{base}_{i}{ext}')
        i += 1
    _apply_crop_and_save(item, dest)
    return dest


def _show_save_toast():
    """메인 윈도우에 잠깐 표시되는 저장 완료 알림."""
    if _main_window and hasattr(_main_window, '_show_toast'):
        _main_window._show_toast(
            'Saved  ·  Open Folder →', duration=4000,
            on_click=lambda: subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)])
        )


def _save_item_or_selection(item):
    """item이 멀티 선택에 포함돼 있으면 전체 선택 저장, 아니면 해당 아이템만 저장."""
    canvas = item.parent()
    sel = getattr(canvas, 'selected_items', [])
    targets = [i for i in sel if hasattr(i, 'file_path') and getattr(i, 'file_path', None)] \
              if item in sel and len(sel) > 1 else []
    if not targets:
        targets = [item] if getattr(item, 'file_path', None) else []
    count = sum(1 for t in targets if _quick_save_to_dir(t) is not None)
    if _main_window and hasattr(_main_window, '_show_toast'):
        label = f'Saved ({count})  ·  Open Folder →' if count > 1 else 'Saved  ·  Open Folder →'
        _main_window._show_toast(
            label, duration=4000,
            on_click=lambda: subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)])
        )


def _toggle_invert(item):
    """아이템의 색상 반전 토글."""
    item._invert = not getattr(item, '_invert', False)
    item._label.update()


def _set_blend_mode(item, mode):
    """아이템의 블렌드 모드를 변경한다. opacity는 paintEvent에서 처리."""
    old_mode = item._blend_mode
    if old_mode == mode:
        return
    item._blend_mode = mode
    item._label.update()
    if hasattr(item, '_hover_bar'):
        item._hover_bar.refresh()
    if hasattr(item, '_blend_bar'):
        item._blend_bar.refresh()
    def _refresh(m):
        try:
            item._blend_mode = m
            item._label.update()
            if hasattr(item, '_hover_bar'): item._hover_bar.refresh()
            if hasattr(item, '_blend_bar'): item._blend_bar.refresh()
        except RuntimeError:
            pass
    _record_undo(lambda: _refresh(old_mode), lambda: _refresh(mode))


def _apply_item_state(item, s):
    """아이템에 저장된 상태(crop/trim/flip/rotation/opacity/blend)를 적용한다."""
    if 'rotation' in s:
        item._rotation = s['rotation']
    if 'flip_h' in s:
        item._flip_h = s['flip_h']
    if 'flip_v' in s:
        item._flip_v = s['flip_v']
    if 'crop' in s:
        item._crop = list(s['crop'])
    if 'trim_start' in s and hasattr(item, '_trim_start'):
        item._trim_start = s['trim_start']
    if 'trim_end' in s and hasattr(item, '_trim_end'):
        item._trim_end = s['trim_end']
    if hasattr(item, '_sig_trim') and ('trim_start' in s or 'trim_end' in s):
        item._sig_trim.emit(item._trim_start, item._trim_end)
    if 'invert' in s and hasattr(item, '_invert'):
        item._invert = s['invert']
    if 'z_always_on_top' in s and hasattr(item, '_z_always_on_top'):
        item._z_always_on_top = s['z_always_on_top']
    if 'blend_mode' in s:
        item._blend_mode = s['blend_mode']
    opacity = s.get('opacity', 1.0)
    if opacity != 1.0:
        item.setItemOpacity(opacity)
    item.updateSize()
    if hasattr(item, '_label'):
        item._label.update()


def _z_bring_to_front(item):
    """아이템을 최상위 레이어로 이동한다."""
    canvas = item.parent()
    if not canvas or not hasattr(canvas, 'items') or item not in canvas.items:
        return
    canvas.items.remove(item)
    canvas.items.append(item)
    item.raise_()
    canvas._lower_all_groups()


def _z_send_to_back(item):
    """아이템을 최하위 레이어로 이동한다."""
    canvas = item.parent()
    if not canvas or not hasattr(canvas, 'items') or item not in canvas.items:
        return
    canvas.items.remove(item)
    canvas.items.insert(0, item)
    for it in canvas.items:
        it.raise_()
    canvas._lower_all_groups()


def _z_bring_forward(item):
    """아이템을 한 단계 위 레이어로 이동한다."""
    canvas = item.parent()
    if not canvas or not hasattr(canvas, 'items') or item not in canvas.items:
        return
    idx = canvas.items.index(item)
    if idx < len(canvas.items) - 1:
        canvas.items[idx], canvas.items[idx + 1] = canvas.items[idx + 1], canvas.items[idx]
        canvas.items[idx + 1].raise_()
        canvas._lower_all_groups()


def _z_send_backward(item):
    """아이템을 한 단계 아래 레이어로 이동한다."""
    canvas = item.parent()
    if not canvas or not hasattr(canvas, 'items') or item not in canvas.items:
        return
    idx = canvas.items.index(item)
    if idx > 0:
        canvas.items[idx], canvas.items[idx - 1] = canvas.items[idx - 1], canvas.items[idx]
        for it in canvas.items[idx - 1:]:
            it.raise_()
        canvas._lower_all_groups()


def _z_toggle_always_on_top(item):
    """아이템의 '항상 위' 상태를 토글한다."""
    item._z_always_on_top = not getattr(item, '_z_always_on_top', False)
    canvas = item.parent()
    if canvas:
        if item._z_always_on_top:
            item.raise_()
        canvas._lower_all_groups()


def _calc_bar_positions(item):
    """호버바·블렌드바 위치 계산. 겹치면 2행으로 배치, 호버바는 콘텐츠 중앙."""
    cs = item._rm_csize()
    item_pos = item.pos()
    x_off = getattr(item, '_content_x_offset', 0)
    HPAD = item._HPAD
    content_left = item_pos.x() + HPAD + x_off
    content_cx   = content_left + cs.width() // 2

    hbar = item._hover_bar
    bbar = item._blend_bar
    bar_h  = max(hbar.height(), bbar.height(), 1)
    base_y = item_pos.y() + HPAD - bar_h - 4

    # 호버바: 콘텐츠 중앙 정렬
    hover_x    = content_cx - hbar.width() // 2
    blend_right = content_left + bbar.width()

    if blend_right + 4 <= hover_x:
        # 단일 행: 블렌드바 왼쪽, 호버바 가운데
        blend_x = content_left
        hover_y = blend_y = base_y
    else:
        # 2행: 두 바 모두 가운데 정렬, 블렌드바를 위쪽 행에
        hover_y = base_y
        blend_x = content_cx - bbar.width() // 2
        blend_y = base_y - bar_h - 2

    return (max(0, hover_x), max(0, hover_y), max(0, blend_x), max(0, blend_y))


def _deregister_canvas_item(item):
    """Canvas의 추적 목록에서 아이템을 제거한다 (deleteLater 호출 전 사용)."""
    canvas = item.parent()
    if canvas is None:
        return
    for attr in ('items', 'selected_items'):
        lst = getattr(canvas, attr, None)
        if lst is not None:
            try: lst.remove(item)
            except ValueError: pass
    if getattr(canvas, 'selected_item', None) is item:
        canvas.selected_item = None
    for g in getattr(canvas, 'groups', []):
        try: g.member_items.remove(item)
        except ValueError: pass


# ── GroupItem ──────────────────────────────────────────────────────────────────

class GroupItem(QWidget):
    """Named group box with header and auto-membership tracking."""
    selected = pyqtSignal(object)
    HEADER_H = 28
    RESIZE_MARGIN = 8

    _last_color = QColor(80, 140, 200, 160)

    def __init__(self, name='Group', parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.group_name = name
        self.member_items = []
        self.is_selected = False
        self.hover_highlight = False
        self._color = QColor(GroupItem._last_color)
        self._text_color = QColor(255, 255, 255)
        self._font_size = 10
        self._inline_toolbar = None
        self._name_edit = None
        self._group_edit_cleanup = None
        self.drag_start = None
        self.drag_start_pos = QPoint(0, 0)
        self.member_start_positions = {}
        self.resize_dir = None
        self.resize_start = None
        self.resize_geo = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        hh = self._hdr_h()
        body_color = QColor(self._color)
        body_color.setAlpha(max(30, self._color.alpha() // 3))
        if self.is_selected:
            body_color = body_color.lighter(130)

        # Body
        body_path = QPainterPath()
        body_path.addRoundedRect(QRectF(0, 0, w, h), 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(body_color)
        p.drawPath(body_path)

        # Header — flat, tight around text
        hdr_color = QColor(self._color)
        hdr_color.setAlpha(min(210, self._color.alpha()))
        hdr_path = QPainterPath()
        hdr_path.addRoundedRect(QRectF(0, 0, w, hh), 6, 6)
        rect_path = QPainterPath()
        rect_path.addRect(QRectF(0, hh // 2, w, hh - hh // 2))
        hdr_path = hdr_path.united(rect_path)
        p.setPen(Qt.NoPen)
        p.setBrush(hdr_color)
        p.drawPath(hdr_path)

        # Border
        if self.is_selected:
            p.setPen(QPen(QColor('#00aaff'), 2))
        elif self.hover_highlight:
            p.setPen(QPen(QColor(255, 200, 50, 200), 2))
        else:
            border_c = QColor(self._color)
            border_c.setAlpha(120)
            p.setPen(QPen(border_c, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 5, 5)

        # Title (only if not inline editing)
        if self._name_edit is None:
            canvas = self.parent()
            scale = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else 1.0
            pad_top = max(1, round(self._HDR_PAD_TOP * scale))
            font = get_ui_font(self._scaled_fs(), bold=True)
            font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
            fm2 = QFontMetrics(font)
            text = self.group_name if self.group_name else 'Ag'
            br = fm2.tightBoundingRect(text)
            baseline_y = pad_top - br.y()
            p.setPen(self._text_color)
            p.setFont(font)
            # QRectF 기반 drawText — 서브픽셀 정렬로 더 선명하게
            text_rect = QRectF(14, 0, w - 18, hh)
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine,
                       self.group_name)

    def getState(self):
        canvas = self.parent()
        indices = []
        if canvas and hasattr(canvas, 'items'):
            for m in self.member_items:
                try:
                    indices.append(canvas.items.index(m))
                except ValueError:
                    pass
        return {
            'type': 'group',
            'name': self.group_name,
            'x': self.pos().x(), 'y': self.pos().y(),
            'w': self.width(), 'h': self.height(),
            'color': self._color.name(QColor.HexArgb),
            'text_color': self._text_color.name(QColor.HexArgb),
            'font_size': self._font_size,
            'member_indices': indices,
        }

    def select(self, additive=False):
        canvas = self.parent()
        if not additive and hasattr(canvas, 'deselectAll'):
            canvas.deselectAll()
        self.is_selected = True
        self.update()
        if hasattr(canvas, 'addToSelection'):
            canvas.addToSelection(self)
        self.selected.emit(self)

    def deselect(self):
        self.is_selected = False
        self.hover_highlight = False
        self.update()

    def _scaled_fs(self):
        canvas = self.parent()
        scale = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else CanvasWidget.DEFAULT_SCALE
        return max(1, round(self._font_size * scale / CanvasWidget.DEFAULT_SCALE))

    _HDR_PAD_TOP = 100
    _HDR_PAD_BOT = 90

    def _hdr_h(self):
        canvas = self.parent()
        scale = canvas.canvas_scale if hasattr(canvas, 'canvas_scale') else 1.0
        font = get_ui_font(self._scaled_fs(), bold=True)
        fm = QFontMetrics(font)
        text = self.group_name if self.group_name else 'Ag'
        br = fm.tightBoundingRect(text)
        pad_top = max(1, round(self._HDR_PAD_TOP * scale))
        pad_bot = max(1, round(self._HDR_PAD_BOT * scale))
        return br.height() + pad_top + pad_bot

    def _getEdge(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        m = self.RESIZE_MARGIN
        left   = x < m
        right  = x > w - m
        top    = y < m
        bottom = y > h - m
        if top    and left:  return 'topleft'
        if top    and right: return 'topright'
        if bottom and left:  return 'bottomleft'
        if bottom and right: return 'bottomright'
        if left:   return 'left'
        if right:  return 'right'
        if top:    return 'top'
        if bottom: return 'bottom'
        return None

    def _edgeCursor(self, edge):
        cursors = {
            'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
            'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
            'topleft': Qt.SizeFDiagCursor,  'topright': Qt.SizeBDiagCursor,
            'bottomleft': Qt.SizeBDiagCursor, 'bottomright': Qt.SizeFDiagCursor,
        }
        return cursors.get(edge, Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        if e.button() == Qt.LeftButton:
            shift = bool(e.modifiers() & Qt.ShiftModifier)
            ctrl = bool(e.modifiers() & Qt.ControlModifier)
            edge = self._getEdge(e.pos())
            if edge:
                self.resize_dir = edge
                self.resize_start = e.globalPos()
                self.resize_geo = QRect(self.pos(), self.size())
                self.select(additive=(shift or ctrl))
                return
            self.drag_start = e.globalPos()
            self.drag_start_pos = QPoint(self.pos())
            self.member_start_positions = {m: QPoint(m.pos()) for m in self.member_items}
            self.select(additive=(shift or ctrl))
            canvas = self.parent()
            if hasattr(canvas, '_lower_all_groups'):
                canvas._lower_all_groups()
        elif e.button() == Qt.RightButton:
            e.ignore()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore()
            return
        if self.resize_dir and e.buttons() & Qt.LeftButton:
            self._doResize(e.globalPos())
            return
        if self.drag_start and e.buttons() & Qt.LeftButton:
            delta = e.globalPos() - self.drag_start
            self.move(self.drag_start_pos + delta)
            for m, sp in self.member_start_positions.items():
                if m.isVisible():
                    m.move(sp + delta)
            self._sync_tb()
            return
        edge = self._getEdge(e.pos())
        self.setCursor(self._edgeCursor(edge) if edge else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore()
            return
        was_resizing = self.resize_dir is not None
        self.drag_start = None
        self.member_start_positions = {}
        self.resize_dir = None
        self.resize_start = None
        self.resize_geo = None
        self.setCursor(Qt.ArrowCursor)
        if was_resizing:
            self._syncMembership()

    def contextMenuEvent(self, e):
        self._showContextMenu(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().y() < self._hdr_h():
            self._startGroupEdit()

    def _doResize(self, gp):
        diff = gp - self.resize_start
        geo = QRect(self.resize_geo)
        if 'left' in self.resize_dir:
            new_left = geo.left() + diff.x()
            if geo.right() - new_left >= 150:
                geo.setLeft(new_left)
        if 'right' in self.resize_dir:
            if geo.width() + diff.x() >= 150:
                geo.setWidth(geo.width() + diff.x())
        if 'top' in self.resize_dir:
            new_top = geo.top() + diff.y()
            if geo.bottom() - new_top >= 80:
                geo.setTop(new_top)
        if 'bottom' in self.resize_dir:
            if geo.height() + diff.y() >= 80:
                geo.setHeight(geo.height() + diff.y())
        self.setGeometry(geo)
        self._syncMembership()

    def _syncMembership(self):
        canvas = self.parent()
        if not canvas or not hasattr(canvas, 'items'):
            return
        group_rect = QRect(self.pos(), self.size())
        new_members = []
        for item in canvas.items:
            if isinstance(item, GroupItem):
                continue
            center = item.pos() + QPoint(item.width() // 2, item.height() // 2)
            if group_rect.contains(center):
                new_members.append(item)
        self.member_items = new_members

    def _tbPos(self):
        x = self.pos().x()
        y = self.pos().y() - 48
        if y < 0:
            y = self.pos().y() + self.height() + 6
        return x, y

    def _sync_tb(self):
        if self._inline_toolbar:
            x, y = self._tbPos()
            self._inline_toolbar.move(x, y)
        if self._name_edit:
            self._name_edit.setGeometry(14, 3, self.width() - 22, self._hdr_h() - 6)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._sync_tb()

    def _startGroupEdit(self):
        if self._name_edit:
            return
        canvas = self.parent()
        hh = self._hdr_h()

        # Inline name editor
        self._name_edit = QLineEdit(self)
        self._name_edit.setText(self.group_name)
        self._name_edit.setFont(get_ui_font(self._font_size, bold=True))
        self._name_edit.setGeometry(14, 3, self.width() - 22, hh - 6)
        self._name_edit.setStyleSheet(
            f'background:transparent;color:{self._text_color.name()};border:none;')
        self._name_edit.show()
        self._name_edit.setFocus()
        self._name_edit.selectAll()
        self.update()

        # Floating toolbar
        if canvas:
            self._inline_toolbar = _TextEditPanel(canvas)
            self._inline_toolbar.setMinimumHeight(38)
            tb_lay = QHBoxLayout(self._inline_toolbar)
            tb_lay.setContentsMargins(10, 5, 10, 5)
            tb_lay.setSpacing(8)
            bg_btn = QPushButton('배경색')
            tc_btn = QPushButton('글자색')
            fs_slider = QSlider(Qt.Horizontal)
            fs_slider.setRange(6, 72)
            fs_slider.setValue(self._font_size)
            fs_slider.setFixedWidth(110)
            fs_slider.setStyleSheet(
                'QSlider::groove:horizontal{height:4px;background:rgba(255,255,255,40);border-radius:2px;}'
                'QSlider::sub-page:horizontal{background:rgba(0,170,255,160);border-radius:2px;}'
                'QSlider::handle:horizontal{width:13px;height:13px;margin:-5px 0;'
                'background:rgba(220,220,220,230);border-radius:6px;}')
            fs_label = QLabel(str(self._font_size))
            fs_label.setStyleSheet('color:rgba(200,200,200,220);background:transparent;font-size:11px;')
            fs_label.setFixedWidth(26)
            btn_style = ('QPushButton{background:rgba(255,255,255,14);color:#bbb;border:none;'
                         'border-radius:4px;font-size:11px;padding:5px 12px;}'
                         'QPushButton:hover{background:rgba(255,255,255,28);color:#fff;}')
            bg_btn.setStyleSheet(btn_style)
            tc_btn.setStyleSheet(btn_style)
            for w in [bg_btn, tc_btn, fs_slider, fs_label]:
                tb_lay.addWidget(w)
            self._inline_toolbar.adjustSize()
            x, y = self._tbPos()
            self._inline_toolbar.move(x, y)
            self._inline_toolbar.show()
            self._inline_toolbar.raise_()

            def _update_fs(v):
                self._font_size = v
                fs_label.setText(str(v))
                if self._name_edit:
                    self._name_edit.setFont(get_ui_font(v, bold=True))
                    hh = self._hdr_h()
                    self._name_edit.setGeometry(14, 3, self.width() - 22, hh - 6)
                self.update()

            def _pick_bg():
                def _ap(c): self._color = c; GroupItem._last_color = QColor(c); self.update()
                ColorPickerDialog.getColor(self._color, canvas, '그룹 색상', show_alpha=True, on_change=_ap)

            def _pick_tc():
                def _ap(c):
                    self._text_color = c
                    if self._name_edit:
                        self._name_edit.setStyleSheet(
                            f'background:transparent;color:{c.name()};border:none;')
                    self.update()
                ColorPickerDialog.getColor(self._text_color, canvas, '텍스트 색', on_change=_ap)

            fs_slider.valueChanged.connect(_update_fs)
            bg_btn.clicked.connect(_pick_bg)
            tc_btn.clicked.connect(_pick_tc)

        def _cleanup(save=True):
            if save and self._name_edit:
                self.group_name = self._name_edit.text() or self.group_name
            if self._name_edit:
                self._name_edit.deleteLater()
                self._name_edit = None
            if self._inline_toolbar:
                self._inline_toolbar.deleteLater()
                self._inline_toolbar = None
            self._group_edit_cleanup = None
            self.update()

        self._group_edit_cleanup = _cleanup

        def _kpe(ev):
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                _cleanup(True)
            elif ev.key() == Qt.Key_Escape:
                _cleanup(False)
            else:
                QLineEdit.keyPressEvent(self._name_edit, ev)

        def _foe(ev):
            QLineEdit.focusOutEvent(self._name_edit, ev)
            def _check():
                if self._name_edit and not self._name_edit.hasFocus():
                    focused = QApplication.focusWidget()
                    if self._inline_toolbar and self._inline_toolbar.isAncestorOf(focused):
                        return
                    _cleanup(True)
            QTimer.singleShot(60, _check)

        self._name_edit.keyPressEvent = _kpe
        self._name_edit.focusOutEvent = _foe

    def _changeColor(self):
        canvas = self.parent()
        def _apply(c): self._color = c; GroupItem._last_color = QColor(c); self.update()
        ColorPickerDialog.getColor(self._color, canvas, '그룹 색상 및 투명도', show_alpha=True, on_change=_apply)

    def _showContextMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('복사', self._copyItem)
        menu.addSeparator()
        menu.addAction('이름 변경', self._startGroupEdit)
        menu.addAction('색상 변경', self._changeColor)
        menu.addSeparator()
        menu.addAction('그룹 해제', lambda: QTimer.singleShot(0, self._ungroup))
        menu.addAction('삭제 (멤버 포함)', lambda: QTimer.singleShot(0, self._deleteWithMembers))
        menu.addAction('삭제 (박스만)', lambda: QTimer.singleShot(0, self.cleanup))
        menu.exec_(pos)

    def _copyItem(self):
        canvas = self.parent()
        if hasattr(canvas, '_item_clipboard'):
            canvas._item_clipboard = [self.getState()]
            canvas._paste_offset = 0

    def _ungroup(self):
        self.member_items.clear()
        canvas = self.parent()
        if canvas and hasattr(canvas, 'groups') and self in canvas.groups:
            canvas.groups.remove(self)
        self.hide()
        QTimer.singleShot(0, self.deleteLater)

    def _deleteWithMembers(self):
        for m in list(self.member_items):
            if hasattr(m, 'cleanup'):
                m.cleanup()
            else:
                m.deleteLater()
        self.member_items.clear()
        self.cleanup()

    def cleanup(self):
        canvas = self.parent()
        if canvas:
            if hasattr(canvas, 'groups'):
                try: canvas.groups.remove(self)
                except ValueError: pass
            if hasattr(canvas, 'selected_items'):
                try: canvas.selected_items.remove(self)
                except ValueError: pass
            if getattr(canvas, 'selected_item', None) is self:
                canvas.selected_item = None
        if self._inline_toolbar:
            self._inline_toolbar.deleteLater()
        self.deleteLater()


# ── ImageItem ──────────────────────────────────────────────────────────────────

class ImageItem(QWidget, ResizeMixin, SelectableMixin):
    selected = pyqtSignal(object)
    _HPAD = 8   # padding around image to allow handles outside image bounds

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self._sm_init()
        self._rm_init()
        self.file_path = image_path
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._HPAD, self._HPAD, self._HPAD, self._HPAD)
        lay.setSpacing(0)
        self._label = _ContentLabel(self, self)
        lay.addWidget(self._label)
        self.pixmap = QPixmap(image_path)
        self.original_size = self.pixmap.size()
        _MAX = 240
        native_max = max(self.original_size.width(), self.original_size.height())
        self.current_scale = min(1.0, _MAX / native_max) if native_max > _MAX else 1.0
        self._label.setPixmap(self.pixmap)   # 원본 해상도 – paintEvent가 smooth scale
        self._opacity = 1.0
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        # opacity is handled in _ContentLabel.paintEvent — no widget-level effect
        self._flip_h = False
        self._flip_v = False
        self._rotation = 0
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self._blend_mode = QPainter.CompositionMode_SourceOver
        self._z_always_on_top = False
        self._invert = False
        self._hover_bar = _ItemHoverBar(self, self)
        self._hover_bar.hide()
        self._blend_bar = _ItemBlendBar(self, self)
        self._blend_bar.hide()
        self.setMouseTracking(True)
        self.updateSize()

    def getState(self):
        return {'type': 'image', 'path': self.file_path,
                'x': self.pos().x(), 'y': self.pos().y(), 'scale': self.current_scale,
                'opacity': self._opacity, 'flip_h': self._flip_h, 'flip_v': self._flip_v,
                'rotation': self._rotation, 'crop': list(self._crop),
                'blend_mode': self._blend_mode, 'invert': self._invert,
                'z_always_on_top': self._z_always_on_top}

    def _rm_csize(self):
        crop = self._crop or [0.0, 0.0, 1.0, 1.0]
        cw_frac = max(0.01, crop[2] - crop[0])
        ch_frac = max(0.01, crop[3] - crop[1])
        r = self._rotation
        if r in (90, 270):
            w = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
            h = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
        else:
            w = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
            h = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
        return QSize(w, h)

    def setCrop(self, crop, _push_undo=True):
        if _push_undo:
            _old, _new, _item = list(self._crop), list(crop), self
            _record_undo(
                lambda: _item.setCrop(_old, _push_undo=False),
                lambda: _item.setCrop(_new, _push_undo=False),
            )
        self._crop = list(crop)
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def resetCrop(self):
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def setItemOpacity(self, value):
        self._opacity = max(0.0, min(1.0, value))
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(self._opacity)
        self._label.update()
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            self._blend_bar.refresh()

    def rotateCW(self):
        self._rotation = (self._rotation + 90) % 360
        self.updateSize()
        self._label.update()

    def rotateCCW(self):
        self._rotation = (self._rotation - 90) % 360
        self.updateSize()
        self._label.update()

    def updateSize(self):
        cs = self._rm_csize()
        self._label.setFixedSize(cs)
        self.adjustSize()

    def setScale(self, s):
        self.current_scale = max(0.01, min(5.0, s))
        self.updateSize()
        if self.is_selected:
            self.update()

    def paintEvent(self, e):
        """Draw selection handles in the HPAD margin area (outside image)."""
        if self.is_selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.translate(self._HPAD, self._HPAD)
            self._rm_paint(p)
            p.end()

    def wheelEvent(self, e):
        e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if e.button() == Qt.LeftButton and self.is_selected and self._rm_press(e):
            return
        self._sm_press(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore(); return
        if self._rz_handle:
            self._rm_move(e); return
        if self.is_selected:
            label_pos = e.pos() - QPoint(self._HPAD, self._HPAD)
            hit = self._rm_hit(label_pos)
            self.setCursor(self._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._rm_hover(label_pos):
                self.update()
        self._sm_move(e)

    def _hover_bar_pos(self):
        hx, hy, _, _ = _calc_bar_positions(self)
        return hx, hy

    def _blend_bar_pos(self):
        _, _, bx, by = _calc_bar_positions(self)
        return bx, by

    def _show_hover_bar(self):
        if getattr(self, '_editing_overlay_active', False):
            return
        canvas = self.parent()
        if not canvas:
            return
        bb = self._blend_bar
        if bb.parent() is not canvas:
            bb.setParent(canvas)
        bb.refresh()
        bar = self._hover_bar
        if bar.parent() is not canvas:
            bar.setParent(canvas)
        bar.adjustSize()
        hx, hy, bx, by = _calc_bar_positions(self)
        bar.move(hx, hy)
        bar.raise_()
        bar.show()
        bb.move(bx, by)
        bb.raise_()
        bb.show()

    def _hide_hover_bar_if_away(self):
        cursor = QCursor.pos()
        item_global = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        bar = self._hover_bar
        if bar.isVisible():
            bar_global = QRect(bar.mapToGlobal(QPoint(0, 0)), bar.size())
            if not bar_global.contains(cursor) and not item_global.contains(cursor):
                bar.hide()
        bb = self._blend_bar
        if bb.isVisible():
            bb_global = QRect(bb.mapToGlobal(QPoint(0, 0)), bb.size())
            if not bb_global.contains(cursor) and not item_global.contains(cursor):
                bb.hide()

    def moveEvent(self, e):
        super().moveEvent(e)
        if hasattr(self, '_hover_bar') and self._hover_bar.isVisible():
            bx, by = self._hover_bar_pos()
            self._hover_bar.move(bx, by)
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            bbx, bby = self._blend_bar_pos()
            self._blend_bar.move(bbx, bby)
        if hasattr(self, 'control_bar') and self.control_bar.isVisible():
            cbx, cby = self._control_bar_pos()
            self.control_bar.move(cbx, cby)

    def enterEvent(self, e):
        pass

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._rm_clear_hover():
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if self._rm_release(e):
            return
        self._sm_release(e)

    def contextMenuEvent(self, e):
        self._showContextMenu(e.globalPos())

    def select(self, additive=False):
        canvas = self.parent()
        if not additive and hasattr(canvas, 'deselectAll'):
            canvas.deselectAll()
        self.is_selected = True
        self.update()
        self._show_hover_bar()
        if hasattr(canvas, 'addToSelection'):
            canvas.addToSelection(self)
        self.selected.emit(self)

    def deselect(self):
        self.is_selected = False
        self.update()
        self._hover_bar.hide()
        self._blend_bar.hide()

    def _showContextMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('삭제', self.cleanup)
        menu.addAction('크기 초기화', lambda: self.setScale(1.0))
        menu.addSeparator()
        z_menu = menu.addMenu('레이어 순서')
        z_menu.setStyleSheet(MENU_STYLE)
        z_menu.addAction('맨위로', lambda: _z_bring_to_front(self))
        z_menu.addAction('앞으로', lambda: _z_bring_forward(self))
        z_menu.addAction('뒤로', lambda: _z_send_backward(self))
        z_menu.addAction('맨뒤로', lambda: _z_send_to_back(self))
        z_menu.addSeparator()
        _aot = z_menu.addAction('항상 위')
        _aot.setCheckable(True)
        _aot.setChecked(getattr(self, '_z_always_on_top', False))
        _aot.triggered.connect(lambda: _z_toggle_always_on_top(self))
        menu.addSeparator()
        crop_menu = menu.addMenu('자르기')
        crop_menu.setStyleSheet(MENU_STYLE)
        crop_menu.addAction('자르기 설정...', self._openCropDialog)
        crop_menu.addAction('자르기 초기화', self.resetCrop)
        blend_menu = menu.addMenu('Blend Mode')
        blend_menu.setStyleSheet(MENU_STYLE)
        for _bname, _bmode in _BLEND_MODES:
            _ba = blend_menu.addAction(_bname)
            _ba.setCheckable(True)
            _ba.setChecked(self._blend_mode == _bmode)
            _ba.triggered.connect(lambda _, m=_bmode: _set_blend_mode(self, m))
        _inv_act = menu.addAction('색상 반전', lambda: _toggle_invert(self))
        _inv_act.setCheckable(True)
        _inv_act.setChecked(getattr(self, '_invert', False))
        flip_menu = menu.addMenu('뒤집기')
        flip_menu.setStyleSheet(MENU_STYLE)
        flip_menu.addAction('좌우 뒤집기', lambda: (setattr(self, '_flip_h', not self._flip_h), self._label.update()))
        flip_menu.addAction('상하 뒤집기', lambda: (setattr(self, '_flip_v', not self._flip_v), self._label.update()))
        rot_menu = menu.addMenu('회전')
        rot_menu.setStyleSheet(MENU_STYLE)
        rot_menu.addAction('시계 방향 90°', self.rotateCW)
        rot_menu.addAction('반시계 방향 90°', self.rotateCCW)
        rot_menu.addAction('180°', lambda: [self.rotateCW(), self.rotateCW()])
        menu.addSeparator()
        menu.addAction('저장', lambda: _save_item_or_selection(self))
        menu.addAction('저장 폴더 열기', lambda: (
            os.makedirs(SAVE_DIR, exist_ok=True),
            subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)])))
        menu.addAction('다른 이름으로 저장', self._saveAs)
        menu.addSeparator()
        menu.addAction('원본 위치 열기', self._revealInExplorer)
        menu.exec_(pos)

    def enterCropMode(self):
        canvas = self.parent()
        if canvas:
            for child in canvas.children():
                if isinstance(child, _CropOverlay) and child._item is self:
                    child._cleanup()
                    return
            for child in canvas.children():
                if isinstance(child, _CropOverlay) or isinstance(child, _TrimOverlay):
                    child._cleanup()
            overlay = _CropOverlay(self, canvas)
            overlay.setFocus()

    def _openCropDialog(self):
        self.enterCropMode()

    def _saveAs(self):
        src = getattr(self, 'file_path', None)
        if not src or not os.path.isfile(src):
            return
        ext = os.path.splitext(src)[1]
        dest, _ = QFileDialog.getSaveFileName(
            _main_window, '다른이름으로 저장', os.path.basename(src),
            f'Files (*{ext});;All Files (*)')
        if dest:
            _apply_crop_and_save(self, dest)
            dest_dir = os.path.dirname(os.path.abspath(dest))
            if _main_window:
                _main_window._show_toast(
                    'Saved  ·  Open Folder →', duration=4000,
                    on_click=lambda d=dest_dir: subprocess.Popen(
                        ['explorer', '/select,', os.path.normpath(dest)]))

    def _revealInExplorer(self):
        src = getattr(self, 'file_path', None)
        if src and os.path.isfile(src):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(src)])

    def cleanup(self):
        _deregister_canvas_item(self)
        if hasattr(self, '_hover_bar') and self._hover_bar:
            self._hover_bar.deleteLater()
        if hasattr(self, '_blend_bar') and self._blend_bar:
            self._blend_bar.hide()
            self._blend_bar.deleteLater()
        self.deleteLater()


# ── GifItem ────────────────────────────────────────────────────────────────────

class GifItem(QWidget, ResizeMixin, SelectableMixin):
    selected = pyqtSignal(object)
    _HPAD = 8   # padding around image to allow handles outside image bounds

    def __init__(self, gif_path, parent=None):
        super().__init__(parent)
        self._sm_init()
        self._rm_init()
        self.file_path = gif_path
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.ClickFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._HPAD, self._HPAD, self._HPAD, self._HPAD)
        lay.setSpacing(0)
        self._label = _ContentLabel(self, self)
        lay.addWidget(self._label)
        self.control_bar = GifControlBar(self)
        self.control_bar.hide()

        self.movie = QMovie(gif_path)
        file_size = os.path.getsize(gif_path) if os.path.exists(gif_path) else 0
        _GIF_CACHE_LIMIT = 4 * 1024 * 1024  # 4 MB
        if file_size < _GIF_CACHE_LIMIT:
            self.movie.setCacheMode(QMovie.CacheAll)
            self._gif_cached = True
        else:
            self.movie.setCacheMode(QMovie.CacheNone)
            self._gif_cached = False
        self.movie.jumpToFrame(0)
        self.original_size = self.movie.currentImage().size()
        if not self.original_size.isValid() or self.original_size.width() == 0:
            self.original_size = QSize(200, 200)
        _MAX = 240
        native_max = max(self.original_size.width(), self.original_size.height())
        self.current_scale = min(1.0, _MAX / native_max) if native_max > _MAX else 1.0
        self._opacity = 1.0
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        # opacity is handled in _ContentLabel.paintEvent — no widget-level effect
        self._flip_h = False
        self._flip_v = False
        self._rotation = 0
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self._blend_mode = QPainter.CompositionMode_SourceOver
        self._z_always_on_top = False
        self._invert = False
        self.movie.frameChanged.connect(self._onGifFrame)
        self.movie.start()
        self.frame_count = self.movie.frameCount()
        self._trim_start = 0
        self._trim_end = max(0, self.frame_count - 1)
        self._hover_bar = _ItemHoverBar(self, self)
        self._hover_bar.hide()
        self._blend_bar = _ItemBlendBar(self, self)
        self._blend_bar.hide()
        self.is_playing = True
        self._playback_speed = 1.025
        self.setMouseTracking(True)
        self.control_bar.play_btn.clicked.connect(self.togglePlay)
        self.control_bar.slider.sliderMoved.connect(self._onSlider)
        self.control_bar.slider.setMaximum(max(0, self.frame_count - 1))
        self.control_bar.speed_changed.connect(self.setSpeed)
        self._step_dir = 0
        self._step_hold_timer = QTimer(); self._step_hold_timer.setSingleShot(True)
        self._step_hold_timer.timeout.connect(self._startStepRepeat)
        self._step_repeat_timer = QTimer(); self._step_repeat_timer.setInterval(80)
        self._step_repeat_timer.timeout.connect(self._doStep)
        self.control_bar.prev_btn.pressed.connect(lambda: self._onStepPressed(-1))
        self.control_bar.prev_btn.released.connect(self._onStepReleased)
        self.control_bar.next_btn.pressed.connect(lambda: self._onStepPressed(1))
        self.control_bar.next_btn.released.connect(self._onStepReleased)
        self._pending_slider_val = 0
        self._slider_debounce = QTimer(self)
        self._slider_debounce.setSingleShot(True)
        self._slider_debounce.setInterval(0)
        self._slider_debounce.timeout.connect(self._do_slider_seek)
        self.updateSize()
        self.setSpeed(1.025)

    def getState(self):
        return {'type': 'gif', 'path': self.file_path,
                'x': self.pos().x(), 'y': self.pos().y(),
                'scale': self.current_scale, 'playing': self.is_playing,
                'opacity': self._opacity, 'flip_h': self._flip_h, 'flip_v': self._flip_v,
                'rotation': self._rotation, 'crop': list(self._crop),
                'blend_mode': self._blend_mode,
                'trim_start': self._trim_start, 'trim_end': self._trim_end,
                'invert': self._invert, 'z_always_on_top': self._z_always_on_top}

    def _rm_csize(self):
        crop = self._crop or [0.0, 0.0, 1.0, 1.0]
        cw_frac = max(0.01, crop[2] - crop[0])
        ch_frac = max(0.01, crop[3] - crop[1])
        r = self._rotation
        if r in (90, 270):
            w = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
            h = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
        else:
            w = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
            h = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
        return QSize(w, h)

    def setCrop(self, crop, _push_undo=True):
        if _push_undo:
            _old, _new, _item = list(self._crop), list(crop), self
            _record_undo(
                lambda: _item.setCrop(_old, _push_undo=False),
                lambda: _item.setCrop(_new, _push_undo=False),
            )
        self._crop = list(crop)
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def resetCrop(self):
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def setTrim(self, start, end):
        self._trim_start = max(0, min(start, self.frame_count - 1))
        self._trim_end = max(0, min(end, self.frame_count - 1))
        if self._trim_start > self._trim_end:
            self._trim_start, self._trim_end = self._trim_end, self._trim_start
        self.control_bar.slider.setMinimum(self._trim_start)
        self.control_bar.slider.setMaximum(self._trim_end)
        self.movie.jumpToFrame(self._trim_start)
        # jumpToFrame 후 movie 는 Paused 상태 — is_playing 동기화
        self.is_playing = False
        self.control_bar.play_btn.setText('>')
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def resetTrim(self):
        self._trim_start = 0
        self._trim_end = max(0, self.frame_count - 1)
        self.control_bar.slider.setMinimum(0)
        self.control_bar.slider.setMaximum(max(0, self.frame_count - 1))
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def setItemOpacity(self, value):
        self._opacity = max(0.0, min(1.0, value))
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(self._opacity)
        self._label.update()
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            self._blend_bar.refresh()

    def rotateCW(self):
        self._rotation = (self._rotation + 90) % 360
        self.updateSize()
        self._label.update()

    def rotateCCW(self):
        self._rotation = (self._rotation - 90) % 360
        self.updateSize()
        self._label.update()

    def _onGifFrame(self, _):
        pix = self.movie.currentPixmap()
        if not pix.isNull():
            self._label.setPixmap(pix)
        if not hasattr(self, 'is_playing'):
            return
        cur = self.movie.currentFrameNumber()
        if self.is_playing:
            if cur < self._trim_start:
                # trim 시작점 이전 → 앞으로 점프 (CacheNone도 전진은 동작)
                self.movie.jumpToFrame(self._trim_start)
                cur = self._trim_start
            elif cur > self._trim_end:
                if self._gif_cached:
                    # CacheAll: 바로 시작점으로 후진
                    self.movie.jumpToFrame(self._trim_start)
                    cur = self._trim_start
                # CacheNone: 후진 불가 → 끝까지 재생 후 frame 0에서 위의 cur < trim_start 처리로 점프
        rel = cur - self._trim_start + 1
        total = self._trim_end - self._trim_start + 1
        self.control_bar.frame_label.setText(f'{rel} / {total}')
        if not self.control_bar.slider._pressed:
            self.control_bar.slider.blockSignals(True)
            self.control_bar.slider.setValue(cur)
            self.control_bar.slider.blockSignals(False)

    def updateSize(self):
        cs = self._rm_csize()
        self._label.setFixedSize(cs)
        self.adjustSize()
        if hasattr(self, 'control_bar') and self.control_bar.isVisible():
            self.control_bar.setFixedWidth(max(150, cs.width()))
            bx, by = self._control_bar_pos()
            self.control_bar.move(bx, by)
        pix = self.movie.currentPixmap()
        if not pix.isNull():
            self._label.setPixmap(pix)

    def _control_bar_pos(self):
        cs = self._rm_csize()
        item_pos = self.pos()
        bar_w = self.control_bar.width()
        content_cx = item_pos.x() + self._HPAD + cs.width() // 2
        return content_cx - bar_w // 2, item_pos.y() + self._HPAD + cs.height() + 4

    def _show_control_bar(self):
        if getattr(self, '_editing_overlay_active', False):
            return
        canvas = self.parent()
        if not canvas:
            return
        bar = self.control_bar
        if bar.parent() is not canvas:
            bar.setParent(canvas)
        bar.setFixedWidth(max(150, self._rm_csize().width()))
        bx, by = self._control_bar_pos()
        bar.move(bx, by)
        bar.raise_()
        bar.show()

    def _hide_control_bar(self):
        self.control_bar.hide()

    def setScale(self, s):
        self.current_scale = max(0.01, min(5.0, s))
        self.updateSize()

    def paintEvent(self, e):
        if self.is_selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.translate(self._HPAD + getattr(self, '_content_x_offset', 0), self._HPAD)
            self._rm_paint(p)
            p.end()

    def wheelEvent(self, e):
        e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if e.button() == Qt.LeftButton and self.is_selected and self._rm_press(e):
            return
        self._sm_press(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore(); return
        if self._rz_handle:
            self._rm_move(e); return
        if self.is_selected:
            label_pos = e.pos() - QPoint(self._HPAD + getattr(self, '_content_x_offset', 0), self._HPAD)
            hit = self._rm_hit(label_pos)
            self.setCursor(self._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._rm_hover(label_pos):
                self.update()
        self._sm_move(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if self._rm_release(e):
            return
        self._sm_release(e)

    def contextMenuEvent(self, e):
        self._showContextMenu(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.togglePlay()

    def _hover_bar_pos(self):
        hx, hy, _, _ = _calc_bar_positions(self)
        return hx, hy

    def _blend_bar_pos(self):
        _, _, bx, by = _calc_bar_positions(self)
        return bx, by

    def _show_hover_bar(self):
        if getattr(self, '_editing_overlay_active', False):
            return
        canvas = self.parent()
        if not canvas:
            return
        bb = self._blend_bar
        if bb.parent() is not canvas:
            bb.setParent(canvas)
        bb.refresh()
        bar = self._hover_bar
        if bar.parent() is not canvas:
            bar.setParent(canvas)
        bar.adjustSize()
        hx, hy, bx, by = _calc_bar_positions(self)
        bar.move(hx, hy)
        bar.raise_()
        bar.show()
        bb.move(bx, by)
        bb.raise_()
        bb.show()

    def _hide_hover_bar_if_away(self):
        cursor = QCursor.pos()
        item_global = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        bar = self._hover_bar
        if bar.isVisible():
            bar_global = QRect(bar.mapToGlobal(QPoint(0, 0)), bar.size())
            if not bar_global.contains(cursor) and not item_global.contains(cursor):
                bar.hide()
        bb = self._blend_bar
        if bb.isVisible():
            bb_global = QRect(bb.mapToGlobal(QPoint(0, 0)), bb.size())
            if not bb_global.contains(cursor) and not item_global.contains(cursor):
                bb.hide()

    def moveEvent(self, e):
        super().moveEvent(e)
        if hasattr(self, '_hover_bar') and self._hover_bar.isVisible():
            bx, by = self._hover_bar_pos()
            self._hover_bar.move(bx, by)
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            bbx, bby = self._blend_bar_pos()
            self._blend_bar.move(bbx, bby)
        if hasattr(self, 'control_bar') and self.control_bar.isVisible():
            cbx, cby = self._control_bar_pos()
            self.control_bar.move(cbx, cby)

    def enterEvent(self, e):
        self._show_control_bar()

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._rm_clear_hover():
            self.update()
        if not self.is_selected:
            self._hide_control_bar()

    def select(self, additive=False):
        canvas = self.parent()
        if not additive and hasattr(canvas, 'deselectAll'):
            canvas.deselectAll()
        self.is_selected = True
        self.update()
        self._show_control_bar()
        self._show_hover_bar()
        if hasattr(canvas, 'addToSelection'):
            canvas.addToSelection(self)
        self.selected.emit(self)

    def deselect(self):
        self.is_selected = False
        self.update()
        self._hide_control_bar()
        self._hover_bar.hide()
        self._blend_bar.hide()

    def togglePlay(self):
        self.is_playing = not self.is_playing
        self.movie.setPaused(not self.is_playing)
        self.control_bar.play_btn.setText('||' if self.is_playing else '>')

    def setSpeed(self, speed):
        self._playback_speed = speed
        self.movie.setSpeed(int(speed * 100))
        self.control_bar.setActiveSpeed(speed)

    def _set_culled(self, culled: bool):
        if culled == getattr(self, '_culled', False):
            return
        self._culled = culled
        if culled:
            self.movie.setPaused(True)
        else:
            if self.is_playing:
                self.movie.setPaused(False)

    def _onStepPressed(self, direction):
        self.is_playing = False
        self.movie.setPaused(True)
        self.control_bar.play_btn.setText('>')
        self._step_dir = direction
        self._doStep()
        self._step_hold_timer.start(300)

    def _onStepReleased(self):
        self._step_hold_timer.stop()
        self._step_repeat_timer.stop()

    def _startStepRepeat(self):
        self._step_repeat_timer.start()

    def _doStep(self):
        cur = self.movie.currentFrameNumber()
        target = max(0, min(self.frame_count - 1, cur + self._step_dir))
        if not self._gif_cached and target < cur:
            # CacheNone: jumpToFrame 후진 불가 — 처음부터 다시 재생해 target까지 진행
            self.movie.stop()
            self.movie.start()
            self.movie.setPaused(True)
            while self.movie.currentFrameNumber() < target:
                self.movie.jumpToNextFrame()
        else:
            self.movie.jumpToFrame(target)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            direction = -1 if e.key() == Qt.Key_Left else 1
            canvas = self.parent()
            for item in getattr(canvas, 'selected_items', [self]):
                if hasattr(item, '_onStepPressed'):
                    item._onStepPressed(direction)
            e.accept()
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and e.isAutoRepeat():
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            canvas = self.parent()
            for item in getattr(canvas, 'selected_items', [self]):
                if hasattr(item, '_onStepReleased'):
                    item._onStepReleased()
            e.accept()
            return
        super().keyReleaseEvent(e)

    def _onSlider(self, value):
        # 마지막 값만 처리: 빠른 드래그 시 중간 프레임 생략
        self._pending_slider_val = value
        if not self._slider_debounce.isActive():
            self._slider_debounce.start()

    def _do_slider_seek(self):
        value = self._pending_slider_val
        if self.is_playing:
            self.is_playing = False
            self.movie.setPaused(True)
            self.control_bar.play_btn.setText('>')
        # Ensure movie is in a seekable state before jumping
        if self.movie.state() == QMovie.NotRunning:
            self.movie.start()
            self.movie.setPaused(True)
        if not self._gif_cached:
            cur = self.movie.currentFrameNumber()
            if value < cur:
                # 역방향: 처음부터 다시 재생
                self.movie.stop()
                self.movie.start()
                self.movie.setPaused(True)
            while self.movie.currentFrameNumber() < value:
                self.movie.jumpToNextFrame()
        else:
            self.movie.jumpToFrame(value)
        rel = value - self._trim_start + 1
        total = self._trim_end - self._trim_start + 1
        self.control_bar.frame_label.setText(f'{rel} / {total}')

    def _showContextMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('삭제', self.cleanup)
        menu.addAction('크기 초기화', lambda: self.setScale(1.0))
        menu.addSeparator()
        z_menu = menu.addMenu('레이어 순서')
        z_menu.setStyleSheet(MENU_STYLE)
        z_menu.addAction('맨위로', lambda: _z_bring_to_front(self))
        z_menu.addAction('앞으로', lambda: _z_bring_forward(self))
        z_menu.addAction('뒤로', lambda: _z_send_backward(self))
        z_menu.addAction('맨뒤로', lambda: _z_send_to_back(self))
        z_menu.addSeparator()
        _aot = z_menu.addAction('항상 위')
        _aot.setCheckable(True)
        _aot.setChecked(getattr(self, '_z_always_on_top', False))
        _aot.triggered.connect(lambda: _z_toggle_always_on_top(self))
        menu.addSeparator()
        crop_menu = menu.addMenu('자르기')
        crop_menu.setStyleSheet(MENU_STYLE)
        crop_menu.addAction('자르기 설정...', self._openCropDialog)
        crop_menu.addAction('자르기 초기화', self.resetCrop)
        trim_menu = menu.addMenu('구간 설정')
        trim_menu.setStyleSheet(MENU_STYLE)
        trim_menu.addAction('구간 설정...', self._openTrimDialog)
        trim_menu.addAction('구간 초기화', self.resetTrim)
        blend_menu = menu.addMenu('Blend Mode')
        blend_menu.setStyleSheet(MENU_STYLE)
        for _bname, _bmode in _BLEND_MODES:
            _ba = blend_menu.addAction(_bname)
            _ba.setCheckable(True)
            _ba.setChecked(self._blend_mode == _bmode)
            _ba.triggered.connect(lambda _, m=_bmode: _set_blend_mode(self, m))
        _inv_act = menu.addAction('색상 반전', lambda: _toggle_invert(self))
        _inv_act.setCheckable(True)
        _inv_act.setChecked(getattr(self, '_invert', False))
        flip_menu = menu.addMenu('뒤집기')
        flip_menu.setStyleSheet(MENU_STYLE)
        flip_menu.addAction('좌우 뒤집기', lambda: (setattr(self, '_flip_h', not self._flip_h), self._label.update()))
        flip_menu.addAction('상하 뒤집기', lambda: (setattr(self, '_flip_v', not self._flip_v), self._label.update()))
        rot_menu = menu.addMenu('회전')
        rot_menu.setStyleSheet(MENU_STYLE)
        rot_menu.addAction('시계 방향 90°', self.rotateCW)
        rot_menu.addAction('반시계 방향 90°', self.rotateCCW)
        rot_menu.addAction('180°', lambda: [self.rotateCW(), self.rotateCW()])
        menu.addSeparator()
        menu.addAction('저장', lambda: _save_item_or_selection(self))
        menu.addAction('저장 폴더 열기', lambda: (
            os.makedirs(SAVE_DIR, exist_ok=True),
            subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)])))
        menu.addAction('다른 이름으로 저장', self._saveAs)
        menu.addSeparator()
        menu.addAction('원본 위치 열기', self._revealInExplorer)
        menu.exec_(pos)

    def enterCropMode(self):
        canvas = self.parent()
        if canvas:
            for child in canvas.children():
                if isinstance(child, _CropOverlay) and child._item is self:
                    child._cleanup()
                    return
            for child in canvas.children():
                if isinstance(child, _CropOverlay) or isinstance(child, _TrimOverlay):
                    child._cleanup()
            overlay = _CropOverlay(self, canvas)
            overlay.setFocus()

    def _openCropDialog(self):
        self.enterCropMode()

    def enterTrimMode(self):
        canvas = self.parent()
        if canvas:
            for child in canvas.children():
                if isinstance(child, _TrimOverlay) and child._item is self:
                    child._cleanup()
                    return
            for child in canvas.children():
                if isinstance(child, _TrimOverlay) or isinstance(child, _CropOverlay):
                    child._cleanup()
            overlay = _TrimOverlay(self, canvas)
            overlay.setFocus()

    def _openTrimDialog(self):
        self.enterTrimMode()

    def _saveAs(self):
        src = getattr(self, 'file_path', None)
        if not src or not os.path.isfile(src):
            return
        ext = os.path.splitext(src)[1]
        dest, _ = QFileDialog.getSaveFileName(
            _main_window, '다른이름으로 저장', os.path.basename(src),
            f'Files (*{ext});;All Files (*)')
        if dest:
            _apply_crop_and_save(self, dest)
            dest_dir = os.path.dirname(os.path.abspath(dest))
            if _main_window:
                _main_window._show_toast(
                    'Saved  ·  Open Folder →', duration=4000,
                    on_click=lambda d=dest_dir: subprocess.Popen(
                        ['explorer', '/select,', os.path.normpath(dest)]))

    def _revealInExplorer(self):
        src = getattr(self, 'file_path', None)
        if src and os.path.isfile(src):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(src)])

    def cleanup(self):
        _deregister_canvas_item(self)
        self.movie.stop()
        if hasattr(self, '_hover_bar') and self._hover_bar:
            self._hover_bar.deleteLater()
        if hasattr(self, '_blend_bar') and self._blend_bar:
            self._blend_bar.hide()
            self._blend_bar.deleteLater()
        if hasattr(self, 'control_bar') and self.control_bar:
            self.control_bar.hide()
            self.control_bar.deleteLater()
        self.deleteLater()


# ── _VideoDecodeWorker ─────────────────────────────────────────────────────────

class _VideoDecodeWorker(QObject):
    frame_ready = pyqtSignal(object, int)  # QImage, frame_number
    seek_failed = pyqtSignal()             # 디코딩 실패 — VideoItem이 in-flight 플래그 해제용

    def __init__(self, video_path, fps, original_size):
        super().__init__()
        self.video_path = video_path
        self.cap = None          # thread 안에서 initialize() 시 생성
        self.fps = fps
        self.original_size = original_size
        self._current_scale = 1.0
        self._is_playing = True  # thread 시작 즉시 재생 시작
        self._trim_start = 0
        self._trim_end = 0
        self._current_frame = 0
        self._base_interval = max(16, int(1000 / fps))
        self._cleaning_up = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    @pyqtSlot()
    def initialize(self):
        """QThread.started에 연결 — worker thread 안에서 VideoCapture를 생성."""
        self.cap = cv2.VideoCapture(
            self.video_path, cv2.CAP_FFMPEG,
            [int(cv2.CAP_PROP_N_THREADS), 1]
        )
        if self._is_playing:
            self._timer.start(self._base_interval)

    @pyqtSlot()
    def start_playing(self):
        self._is_playing = True
        self._timer.start(self._timer.interval() if self._timer.interval() > 0 else self._base_interval)

    @pyqtSlot()
    def stop_playing(self):
        self._is_playing = False
        self._timer.stop()

    @pyqtSlot(float)
    def set_speed(self, speed):
        self._timer.setInterval(max(1, int(self._base_interval / speed)))

    @pyqtSlot(float)
    def set_scale(self, scale):
        self._current_scale = scale

    def _reopen_cap(self):
        """VideoCapture를 재시작한다. 후진 seek 시 FFmpeg 스레드 컨텍스트 오염 방지."""
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(
            self.video_path, cv2.CAP_FFMPEG,
            [int(cv2.CAP_PROP_N_THREADS), 1]
        )

    @pyqtSlot(int)
    def seek_to(self, frame_num):
        if self._cleaning_up or self.cap is None or not self.cap.isOpened():
            return
        was_active = self._timer.isActive()
        self._timer.stop()
        # 후진 seek: FFmpeg 내부 상태 오염 방지를 위해 cap 재생성
        if frame_num <= self._current_frame:
            self._reopen_cap()
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.cap.read()
        if not ret:
            # 실패 시 cap 재생성 후 재시도
            self._reopen_cap()
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = self.cap.read()
        if ret:
            self._current_frame = frame_num
            self._emit_frame(frame)
        else:
            self.seek_failed.emit()  # 디코딩 실패 → VideoItem in-flight 플래그 해제
        if was_active and not self._cleaning_up:
            self._timer.start(self._timer.interval())

    @pyqtSlot(int, int)
    def set_trim(self, start, end):
        self._trim_start = start
        self._trim_end = end

    def _tick(self):
        if self.cap is None or not self.cap.isOpened():
            return
        nxt = self._current_frame + 1
        if nxt > self._trim_end:
            nxt = self._trim_start
            self._reopen_cap()
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, nxt)
        ret, frame = self.cap.read()
        if ret:
            self._current_frame = nxt
            self._emit_frame(frame)
        else:
            self._reopen_cap()
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self._trim_start)
            self._current_frame = self._trim_start

    def _emit_frame(self, frame):
        sw = max(1, int(self.original_size.width() * self._current_scale))
        sh = max(1, int(self.original_size.height() * self._current_scale))
        if frame.shape[1] != sw or frame.shape[0] != sh:
            frame = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.tobytes(), w, h, ch * w, QImage.Format_RGB888).copy()
        self.frame_ready.emit(img, self._current_frame)

    @pyqtSlot()
    def cleanup_worker(self):
        self._cleaning_up = True
        self._timer.stop()
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()


# ── VideoItem ──────────────────────────────────────────────────────────────────

class VideoItem(QWidget, ResizeMixin, SelectableMixin):
    selected = pyqtSignal(object)
    _sig_start = pyqtSignal()
    _sig_stop = pyqtSignal()
    _sig_speed = pyqtSignal(float)
    _sig_seek = pyqtSignal(int)
    _sig_scale = pyqtSignal(float)
    _sig_trim = pyqtSignal(int, int)
    _sig_cleanup_worker = pyqtSignal()
    _HPAD = 8   # padding around image to allow handles outside image bounds

    def __init__(self, video_path, parent=None):
        super().__init__(parent)
        self._sm_init()
        self._rm_init()
        self.file_path = video_path
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.ClickFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._HPAD, self._HPAD, self._HPAD, self._HPAD)
        lay.setSpacing(0)
        self._label = _ContentLabel(self, self)
        self._label.setStyleSheet('background:#000;')
        lay.addWidget(self._label)
        self.control_bar = GifControlBar(self)
        self.control_bar.hide()

        # 메타데이터 읽기 전용 — 즉시 해제 (실제 디코딩은 worker thread에서 수행)
        _meta_cap = cv2.VideoCapture(video_path)
        self.fps = _meta_cap.get(cv2.CAP_PROP_FPS) or 30
        self.frame_count = int(_meta_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.current_frame = 0
        w = int(_meta_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(_meta_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        _meta_cap.release()
        self.original_size = QSize(w if w > 0 else 320, h if h > 0 else 180)
        _MAX = 240
        native_max = max(self.original_size.width(), self.original_size.height())
        self.current_scale = min(1.0, _MAX / native_max) if native_max > _MAX else 1.0
        self.is_playing = True
        self._slider_dragging = False
        self._playback_speed = 1.025
        self._opacity = 1.0
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        # opacity is handled in _ContentLabel.paintEvent — no widget-level effect
        self._flip_h = False
        self._flip_v = False
        self._rotation = 0
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self._blend_mode = QPainter.CompositionMode_SourceOver
        self._z_always_on_top = False
        self._invert = False
        self._trim_start = 0
        self._trim_end = max(0, self.frame_count - 1)
        self._hover_bar = _ItemHoverBar(self, self)
        self._hover_bar.hide()
        self._blend_bar = _ItemBlendBar(self, self)
        self._blend_bar.hide()

        self.control_bar.play_btn.clicked.connect(self.togglePlay)
        self.control_bar.slider.sliderPressed.connect(self._onSliderPressed)
        self.control_bar.slider.sliderReleased.connect(self._onSliderReleased)
        self.control_bar.slider.sliderMoved.connect(self.showFrame)
        self.control_bar.slider.setMaximum(max(0, self.frame_count - 1))
        self._step_dir = 0
        self._step_hold_timer = QTimer(); self._step_hold_timer.setSingleShot(True)
        self._step_hold_timer.timeout.connect(self._startStepRepeat)
        self._step_repeat_timer = QTimer(); self._step_repeat_timer.setInterval(80)
        self._step_repeat_timer.timeout.connect(self._doStep)
        self.control_bar.prev_btn.pressed.connect(lambda: self._onStepPressed(-1))
        self.control_bar.prev_btn.released.connect(self._onStepReleased)
        self.control_bar.next_btn.pressed.connect(lambda: self._onStepPressed(1))
        self.control_bar.next_btn.released.connect(self._onStepReleased)
        self.control_bar.speed_changed.connect(self.setSpeed)
        self._pending_seek = 0
        self._seek_in_flight = False

        self._worker = _VideoDecodeWorker(video_path, self.fps, self.original_size)
        self._worker._trim_end = self._trim_end
        self._worker._current_scale = self.current_scale
        self._decode_thread = QThread()
        self._worker.moveToThread(self._decode_thread)
        self._decode_thread.started.connect(self._worker.initialize)  # thread 안에서 cap 생성
        self._worker.frame_ready.connect(self._onFrameReady)
        self._worker.seek_failed.connect(self._onSeekFailed)
        self._sig_start.connect(self._worker.start_playing)
        self._sig_stop.connect(self._worker.stop_playing)
        self._sig_speed.connect(self._worker.set_speed)
        self._sig_seek.connect(self._worker.seek_to)
        self._sig_scale.connect(self._worker.set_scale)
        self._sig_trim.connect(self._worker.set_trim)
        self._sig_cleanup_worker.connect(self._worker.cleanup_worker)
        self._decode_thread.start()  # → initialize() 호출 → cap 생성 및 재생 시작

        self.setMouseTracking(True)
        self.updateSize()
        self.setSpeed(1.025)

    def getState(self):
        return {'type': 'video', 'path': self.file_path,
                'x': self.pos().x(), 'y': self.pos().y(),
                'scale': self.current_scale, 'playing': self.is_playing,
                'frame': self.current_frame,
                'opacity': self._opacity, 'flip_h': self._flip_h, 'flip_v': self._flip_v,
                'rotation': self._rotation, 'crop': list(self._crop),
                'blend_mode': self._blend_mode,
                'trim_start': self._trim_start, 'trim_end': self._trim_end,
                'invert': self._invert, 'z_always_on_top': self._z_always_on_top}

    def _rm_csize(self):
        crop = self._crop or [0.0, 0.0, 1.0, 1.0]
        cw_frac = max(0.01, crop[2] - crop[0])
        ch_frac = max(0.01, crop[3] - crop[1])
        r = self._rotation
        if r in (90, 270):
            w = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
            h = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
        else:
            w = max(1, int(self.original_size.width() * cw_frac * self.current_scale))
            h = max(1, int(self.original_size.height() * ch_frac * self.current_scale))
        return QSize(w, h)

    def setCrop(self, crop, _push_undo=True):
        if _push_undo:
            _old, _new, _item = list(self._crop), list(crop), self
            _record_undo(
                lambda: _item.setCrop(_old, _push_undo=False),
                lambda: _item.setCrop(_new, _push_undo=False),
            )
        self._crop = list(crop)
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def resetCrop(self):
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self.updateSize()
        self._label.update()
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def setTrim(self, start, end):
        self._trim_start = max(0, min(start, self.frame_count - 1))
        self._trim_end = max(0, min(end, self.frame_count - 1))
        if self._trim_start > self._trim_end:
            self._trim_start, self._trim_end = self._trim_end, self._trim_start
        self.control_bar.slider.setMinimum(self._trim_start)
        self.control_bar.slider.setMaximum(self._trim_end)
        self._sig_trim.emit(self._trim_start, self._trim_end)
        # 재생 중 seek storm 방지: worker 정지 후 seek
        # (재생 중 showFrame → tick 프레임마다 _onFrameReady 재dispatch → _reopen_cap 폭주)
        if self.is_playing:
            self.is_playing = False
            self.control_bar.play_btn.setText('>')
            self._sig_stop.emit()
        self._seek_in_flight = False  # 진행 중 in-flight 초기화
        self.showFrame(self._trim_start)
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def resetTrim(self):
        self._trim_start = 0
        self._trim_end = max(0, self.frame_count - 1)
        self.control_bar.slider.setMinimum(0)
        self.control_bar.slider.setMaximum(max(0, self.frame_count - 1))
        self._sig_trim.emit(0, max(0, self.frame_count - 1))
        if hasattr(self, '_hover_bar'):
            self._hover_bar.refresh()

    def setItemOpacity(self, value):
        self._opacity = max(0.0, min(1.0, value))
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(self._opacity)
        self._label.update()
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            self._blend_bar.refresh()

    def rotateCW(self):
        self._rotation = (self._rotation + 90) % 360
        self.updateSize()
        self._label.update()

    def rotateCCW(self):
        self._rotation = (self._rotation - 90) % 360
        self.updateSize()
        self._label.update()

    def _onFrameReady(self, img, frame_num):
        self.current_frame = frame_num
        self._label.setPixmap(QPixmap.fromImage(img))
        was_in_flight = self._seek_in_flight
        self._seek_in_flight = False
        # seek 중에 새 위치 요청이 들어왔으면 즉시 dispatch (일반 재생 중엔 발동 안 함)
        if was_in_flight and self._pending_seek != frame_num:
            self._seek_in_flight = True
            self._do_seek()
        if not self._slider_dragging:
            self._updateSlider()

    @pyqtSlot()
    def _onSeekFailed(self):
        """seek_to 디코딩 실패 시 in-flight 플래그 해제 — 이후 방향키/스크럽 정상 작동 보장"""
        self._seek_in_flight = False

    def showFrame(self, frame_num):
        self.current_frame = frame_num
        self._pending_seek = frame_num
        if not self._seek_in_flight:
            self._seek_in_flight = True
            self._do_seek()

    def _do_seek(self):
        self._sig_seek.emit(self._pending_seek)

    def _updateSlider(self):
        self.control_bar.slider.blockSignals(True)
        self.control_bar.slider.setValue(self.current_frame)
        self.control_bar.slider.blockSignals(False)
        rel_cur = self.current_frame - self._trim_start
        rel_total = self._trim_end - self._trim_start
        s = int(rel_cur / self.fps) if self.fps > 0 else 0
        t = int(rel_total / self.fps) if self.fps > 0 else 0
        self.control_bar.frame_label.setText(f'{s}s / {t}s')

    def updateSize(self):
        cs = self._rm_csize()
        self._label.setFixedSize(cs)
        self.adjustSize()
        if hasattr(self, 'control_bar') and self.control_bar.isVisible():
            self.control_bar.setFixedWidth(max(150, cs.width()))
            bx, by = self._control_bar_pos()
            self.control_bar.move(bx, by)
        if hasattr(self, '_sig_scale'):
            self._sig_scale.emit(self.current_scale)
        pix = self._label.pixmap()
        if pix is not None and not pix.isNull():
            self.showFrame(self.current_frame)

    def _control_bar_pos(self):
        cs = self._rm_csize()
        item_pos = self.pos()
        bar_w = self.control_bar.width()
        content_cx = item_pos.x() + self._HPAD + cs.width() // 2
        return content_cx - bar_w // 2, item_pos.y() + self._HPAD + cs.height() + 4

    def _show_control_bar(self):
        if getattr(self, '_editing_overlay_active', False):
            return
        canvas = self.parent()
        if not canvas:
            return
        bar = self.control_bar
        if bar.parent() is not canvas:
            bar.setParent(canvas)
        bar.setFixedWidth(max(150, self._rm_csize().width()))
        bx, by = self._control_bar_pos()
        bar.move(bx, by)
        bar.raise_()
        bar.show()

    def _hide_control_bar(self):
        self.control_bar.hide()

    def setScale(self, s):
        self.current_scale = max(0.01, min(5.0, s))
        self.updateSize()

    def paintEvent(self, e):
        if self.is_selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.translate(self._HPAD + getattr(self, '_content_x_offset', 0), self._HPAD)
            self._rm_paint(p)
            p.end()

    def wheelEvent(self, e):
        e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if e.button() == Qt.LeftButton and self.is_selected and self._rm_press(e):
            return
        self._sm_press(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MiddleButton:
            e.ignore(); return
        if self._rz_handle:
            self._rm_move(e); return
        if self.is_selected:
            label_pos = e.pos() - QPoint(self._HPAD + getattr(self, '_content_x_offset', 0), self._HPAD)
            hit = self._rm_hit(label_pos)
            self.setCursor(self._CURSORS[hit] if hit else Qt.ArrowCursor)
            if self._rm_hover(label_pos):
                self.update()
        self._sm_move(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            e.ignore(); return
        if self._rm_release(e):
            return
        self._sm_release(e)

    def contextMenuEvent(self, e):
        self._showContextMenu(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.togglePlay()

    def _hover_bar_pos(self):
        hx, hy, _, _ = _calc_bar_positions(self)
        return hx, hy

    def _blend_bar_pos(self):
        _, _, bx, by = _calc_bar_positions(self)
        return bx, by

    def _show_hover_bar(self):
        if getattr(self, '_editing_overlay_active', False):
            return
        canvas = self.parent()
        if not canvas:
            return
        bb = self._blend_bar
        if bb.parent() is not canvas:
            bb.setParent(canvas)
        bb.refresh()
        bar = self._hover_bar
        if bar.parent() is not canvas:
            bar.setParent(canvas)
        bar.adjustSize()
        hx, hy, bx, by = _calc_bar_positions(self)
        bar.move(hx, hy)
        bar.raise_()
        bar.show()
        bb.move(bx, by)
        bb.raise_()
        bb.show()

    def _hide_hover_bar_if_away(self):
        cursor = QCursor.pos()
        item_global = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        bar = self._hover_bar
        if bar.isVisible():
            bar_global = QRect(bar.mapToGlobal(QPoint(0, 0)), bar.size())
            if not bar_global.contains(cursor) and not item_global.contains(cursor):
                bar.hide()
        bb = self._blend_bar
        if bb.isVisible():
            bb_global = QRect(bb.mapToGlobal(QPoint(0, 0)), bb.size())
            if not bb_global.contains(cursor) and not item_global.contains(cursor):
                bb.hide()

    def moveEvent(self, e):
        super().moveEvent(e)
        if hasattr(self, '_hover_bar') and self._hover_bar.isVisible():
            bx, by = self._hover_bar_pos()
            self._hover_bar.move(bx, by)
        if hasattr(self, '_blend_bar') and self._blend_bar.isVisible():
            bbx, bby = self._blend_bar_pos()
            self._blend_bar.move(bbx, bby)
        if hasattr(self, 'control_bar') and self.control_bar.isVisible():
            cbx, cby = self._control_bar_pos()
            self.control_bar.move(cbx, cby)

    def enterEvent(self, e):
        self._show_control_bar()

    def leaveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self._rm_clear_hover():
            self.update()
        if not self.is_selected:
            self._hide_control_bar()

    def select(self, additive=False):
        canvas = self.parent()
        if not additive and hasattr(canvas, 'deselectAll'):
            canvas.deselectAll()
        self.is_selected = True
        self.update()
        self._show_control_bar()
        self._show_hover_bar()
        if hasattr(canvas, 'addToSelection'):
            canvas.addToSelection(self)
        self.selected.emit(self)

    def deselect(self):
        self.is_selected = False
        self.update()
        self._hide_control_bar()
        self._hover_bar.hide()
        self._blend_bar.hide()

    def togglePlay(self):
        self.is_playing = not self.is_playing
        self.control_bar.play_btn.setText('||' if self.is_playing else '>')
        if self.is_playing:
            self._sig_start.emit()
        else:
            self._sig_stop.emit()

    def setSpeed(self, speed):
        self._playback_speed = speed
        self._sig_speed.emit(speed)
        self.control_bar.setActiveSpeed(speed)

    def _set_culled(self, culled: bool):
        if culled == getattr(self, '_culled', False):
            return
        self._culled = culled
        if culled:
            self._sig_stop.emit()
        else:
            if self.is_playing:
                self._sig_start.emit()

    def _onSliderPressed(self):
        self._slider_dragging = True
        self._sig_stop.emit()

    def _onStepPressed(self, direction):
        self.is_playing = False
        self.control_bar.play_btn.setText('>')
        self._sig_stop.emit()
        self._step_dir = direction
        self._doStep()
        self._step_hold_timer.start(300)

    def _onStepReleased(self):
        self._step_hold_timer.stop()
        self._step_repeat_timer.stop()

    def _startStepRepeat(self):
        self._step_repeat_timer.start()

    def _doStep(self):
        if self._seek_in_flight:
            return  # 이전 seek 완료 전 — 큐 누적 방지
        target = max(0, min(self.frame_count - 1, self.current_frame + self._step_dir))
        if target == self.current_frame:
            return
        self._seek_in_flight = True
        self.current_frame = target
        self._pending_seek = target
        self._do_seek()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            direction = -1 if e.key() == Qt.Key_Left else 1
            canvas = self.parent()
            for item in getattr(canvas, 'selected_items', [self]):
                if hasattr(item, '_onStepPressed'):
                    item._onStepPressed(direction)
            e.accept()
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and e.isAutoRepeat():
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            canvas = self.parent()
            for item in getattr(canvas, 'selected_items', [self]):
                if hasattr(item, '_onStepReleased'):
                    item._onStepReleased()
            e.accept()
            return
        super().keyReleaseEvent(e)

    def _onSliderReleased(self):
        self._slider_dragging = False
        self.showFrame(self.control_bar.slider.value())
        if self.is_playing:
            self._sig_start.emit()

    def _showContextMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('삭제', self.cleanup)
        menu.addAction('크기 초기화', lambda: self.setScale(1.0))
        menu.addSeparator()
        z_menu = menu.addMenu('레이어 순서')
        z_menu.setStyleSheet(MENU_STYLE)
        z_menu.addAction('맨위로', lambda: _z_bring_to_front(self))
        z_menu.addAction('앞으로', lambda: _z_bring_forward(self))
        z_menu.addAction('뒤로', lambda: _z_send_backward(self))
        z_menu.addAction('맨뒤로', lambda: _z_send_to_back(self))
        z_menu.addSeparator()
        _aot = z_menu.addAction('항상 위')
        _aot.setCheckable(True)
        _aot.setChecked(getattr(self, '_z_always_on_top', False))
        _aot.triggered.connect(lambda: _z_toggle_always_on_top(self))
        menu.addSeparator()
        crop_menu = menu.addMenu('자르기')
        crop_menu.setStyleSheet(MENU_STYLE)
        crop_menu.addAction('자르기 설정...', self._openCropDialog)
        crop_menu.addAction('자르기 초기화', self.resetCrop)
        trim_menu = menu.addMenu('구간 설정')
        trim_menu.setStyleSheet(MENU_STYLE)
        trim_menu.addAction('구간 설정...', self._openTrimDialog)
        trim_menu.addAction('구간 초기화', self.resetTrim)
        blend_menu = menu.addMenu('Blend Mode')
        blend_menu.setStyleSheet(MENU_STYLE)
        for _bname, _bmode in _BLEND_MODES:
            _ba = blend_menu.addAction(_bname)
            _ba.setCheckable(True)
            _ba.setChecked(self._blend_mode == _bmode)
            _ba.triggered.connect(lambda _, m=_bmode: _set_blend_mode(self, m))
        _inv_act = menu.addAction('색상 반전', lambda: _toggle_invert(self))
        _inv_act.setCheckable(True)
        _inv_act.setChecked(getattr(self, '_invert', False))
        flip_menu = menu.addMenu('뒤집기')
        flip_menu.setStyleSheet(MENU_STYLE)
        flip_menu.addAction('좌우 뒤집기', lambda: (setattr(self, '_flip_h', not self._flip_h), self._label.update()))
        flip_menu.addAction('상하 뒤집기', lambda: (setattr(self, '_flip_v', not self._flip_v), self._label.update()))
        rot_menu = menu.addMenu('회전')
        rot_menu.setStyleSheet(MENU_STYLE)
        rot_menu.addAction('시계 방향 90°', self.rotateCW)
        rot_menu.addAction('반시계 방향 90°', self.rotateCCW)
        rot_menu.addAction('180°', lambda: [self.rotateCW(), self.rotateCW()])
        menu.addSeparator()
        menu.addAction('저장', lambda: _save_item_or_selection(self))
        menu.addAction('저장 폴더 열기', lambda: (
            os.makedirs(SAVE_DIR, exist_ok=True),
            subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)])))
        menu.addAction('다른 이름으로 저장', self._saveAs)
        menu.addSeparator()
        menu.addAction('원본 위치 열기', self._revealInExplorer)
        menu.exec_(pos)

    def enterCropMode(self):
        canvas = self.parent()
        if canvas:
            for child in canvas.children():
                if isinstance(child, _CropOverlay) and child._item is self:
                    child._cleanup()
                    return
            for child in canvas.children():
                if isinstance(child, _CropOverlay) or isinstance(child, _TrimOverlay):
                    child._cleanup()
            overlay = _CropOverlay(self, canvas)
            overlay.setFocus()

    def _openCropDialog(self):
        self.enterCropMode()

    def enterTrimMode(self):
        canvas = self.parent()
        if canvas:
            for child in canvas.children():
                if isinstance(child, _TrimOverlay) and child._item is self:
                    child._cleanup()
                    return
            for child in canvas.children():
                if isinstance(child, _TrimOverlay) or isinstance(child, _CropOverlay):
                    child._cleanup()
            overlay = _TrimOverlay(self, canvas)
            overlay.setFocus()

    def _openTrimDialog(self):
        self.enterTrimMode()

    def _saveAs(self):
        src = getattr(self, 'file_path', None)
        if not src or not os.path.isfile(src):
            return
        ext = os.path.splitext(src)[1]
        dest, _ = QFileDialog.getSaveFileName(
            _main_window, '다른이름으로 저장', os.path.basename(src),
            f'Files (*{ext});;All Files (*)')
        if dest:
            _apply_crop_and_save(self, dest)
            dest_dir = os.path.dirname(os.path.abspath(dest))
            if _main_window:
                _main_window._show_toast(
                    'Saved  ·  Open Folder →', duration=4000,
                    on_click=lambda d=dest_dir: subprocess.Popen(
                        ['explorer', '/select,', os.path.normpath(dest)]))

    def _revealInExplorer(self):
        src = getattr(self, 'file_path', None)
        if src and os.path.isfile(src):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(src)])

    def cleanup(self):
        _deregister_canvas_item(self)
        self._sig_cleanup_worker.emit()
        self._decode_thread.quit()
        self._decode_thread.wait(2000)
        if hasattr(self, '_hover_bar') and self._hover_bar:
            self._hover_bar.deleteLater()
        if hasattr(self, '_blend_bar') and self._blend_bar:
            self._blend_bar.hide()
            self._blend_bar.deleteLater()
        if hasattr(self, 'control_bar') and self.control_bar:
            self.control_bar.hide()
            self.control_bar.deleteLater()
        self.deleteLater()


# ── CanvasWidget ───────────────────────────────────────────────────────────────

class CanvasWidget(QWidget):
    DEFAULT_SCALE = 0.088  # 기준 100%

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setAcceptDrops(True)
        self.items = []
        self.groups = []
        self.selected_item = None
        self.selected_items = []
        self.setMinimumSize(400, 300)
        self.bg_color = QColor(30, 30, 30)
        self.setAutoFillBackground(True)
        self.setMouseTracking(True)
        self.canvas_drag_start = None
        self.pan_offset = QPoint(0, 0)
        self.pan_start = None
        self.pan_start_offset = None
        self.canvas_scale = self.DEFAULT_SCALE
        self._item_float_pos = {}
        self._pan_float = [0.0, 0.0]
        # Left-drag selection
        self.sel_start = None
        self.sel_rect = None
        self.sel_additive = False
        # Right-drag selection / pan
        self.rsel_start = None
        self.rsel_rect = None
        self.rpan_start = None
        self.rpan_start_offset = None
        self._rmove_win_start = None
        self._rmove_win_initial = None
        self.r_drag_mode = None
        self._r_dragged = False
        self.R_DRAG_THRESHOLD = 6
        self._item_clipboard = []   # copied TextItem / GroupItem states
        self._item_clipboard_time = 0.0  # 내부 복사 시각
        self._paste_offset = 0      # increments 20px per successive paste
        self._applyBg()
        self._cull_timer = QTimer()
        self._cull_timer.timeout.connect(self._update_culling)
        self._cull_timer.start(500)

    def _applyBg(self):
        p = self.palette()
        p.setColor(QPalette.Window, self.bg_color)
        self.setPalette(p)

    def setBackgroundColor(self, color):
        self.bg_color = color
        self._applyBg()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg_color)
        if self.sel_rect:
            p.setPen(QPen(QColor(0, 170, 255), 1))
            p.setBrush(QColor(0, 170, 255, 40))
            p.drawRect(self.sel_rect)
        if self.rsel_rect:
            p.setPen(QPen(QColor(255, 120, 0), 1))
            p.setBrush(QColor(255, 120, 0, 40))
            p.drawRect(self.rsel_rect)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                ext = url.toLocalFile().lower()
                if ext.endswith(('.gif', '.mp4', '.avi', '.mov', '.mkv', '.webm',
                                  '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif', '.tga')):
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            ext = path.lower()
            drop_pos = e.pos() - self.pan_offset
            try:
                if ext.endswith('.gif'):
                    self.addItem(GifItem(path, self), drop_pos)
                elif ext.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm')) and HAS_OPENCV:
                    self.addItem(VideoItem(path, self), drop_pos)
                elif ext.endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif', '.tga')):
                    self.addItem(ImageItem(path, self), drop_pos)
            except Exception as ex:
                print(f'[dropEvent] {ex}')
        e.acceptProposedAction()

    def _lower_all_groups(self):
        """그룹 박스를 항상 가장 아래 레이어로."""
        for g in self.groups:
            g.lower()
        for it in self.items:
            if getattr(it, '_z_always_on_top', False):
                it.raise_()
        # 오버레이 바는 항상 모든 아이템보다 위에 있어야 한다
        for it in self.items:
            for bar_attr in ('control_bar', '_hover_bar', '_blend_bar'):
                bar = getattr(it, bar_attr, None)
                if bar and bar.isVisible():
                    bar.raise_()

    def addItem(self, item, pos=None):
        item.selected.connect(self.onItemSelected)
        # Pre-reparent hover bar to canvas (avoids Windows window-activation on first hover)
        if hasattr(item, '_hover_bar') and item._hover_bar is not None:
            item._hover_bar.setParent(self)
            item._hover_bar.hide()
        if pos:
            item.move(pos + self.pan_offset)
        else:
            item.move(50 + len(self.items) * 20 + self.pan_offset.x(),
                      50 + len(self.items) * 20 + self.pan_offset.y())
        item.show()
        self.items.append(item)
        self._lower_all_groups()
        self.selectItem(item)

    def addGroup(self, name='Group', targets=None):
        targets = targets or list(self.selected_items)
        targets = [t for t in targets if not isinstance(t, GroupItem)]
        g = GroupItem(name, self)
        if targets:
            min_x = min(t.pos().x() for t in targets)
            min_y = min(t.pos().y() for t in targets)
            max_x = max(t.pos().x() + t.width() for t in targets)
            max_y = max(t.pos().y() + t.height() for t in targets)
            g.member_items = list(targets)
            g.setGeometry(min_x - PADDING, min_y - PADDING,
                          max_x - min_x + 2 * PADDING, max_y - min_y + 2 * PADDING)
        else:
            # No selection — create empty group at cursor/context-menu position
            pos = getattr(self, '_ctx_pos', None) or self.mapFromGlobal(QCursor.pos())
            g.setGeometry(pos.x() - 100, pos.y() - 60, 200, 120)
        g.selected.connect(self.onItemSelected)
        g.show()
        g.lower()
        self.groups.append(g)
        self.selectItem(g)

    def addTextItem(self):
        item = TextItem(self)
        pos = getattr(self, '_ctx_pos', None) or self.mapFromGlobal(QCursor.pos())
        item.show()
        item.move(pos.x() - item.width() // 2, pos.y() - item.height() // 2)
        item.selected.connect(self.onItemSelected)
        self.items.append(item)
        self.selectItem(item)

    def onItemSelected(self, item):
        self.selectItem(item)

    def selectItem(self, item):
        # 이미 멀티 선택에 포함된 아이템을 클릭하면 선택 목록을 유지한다
        if item in self.selected_items and len(self.selected_items) > 1:
            self.selected_item = item
            return
        if self.selected_item and self.selected_item != item:
            if hasattr(self.selected_item, 'deselect'):
                self.selected_item.deselect()
        self.selected_item = item
        self.selected_items = [item]

    def selectAll(self):
        self.deselectAll()
        for item in self.items + self.groups:
            item.is_selected = True
            if hasattr(item, '_label'):
                item._label.update()
            elif hasattr(item, 'update'):
                item.update()
            self.selected_items.append(item)
        if self.items or self.groups:
            self.selected_item = (self.items + self.groups)[-1]

    def deselectAll(self):
        for item in list(self.selected_items):
            if hasattr(item, 'deselect'):
                item.deselect()
        self.selected_items = []
        self.selected_item = None

    def addToSelection(self, item):
        if item not in self.selected_items:
            self.selected_items.append(item)
        self.selected_item = item

    def _applySelectionRect(self, rect, additive=False):
        if not additive:
            self.deselectAll()
        if rect and rect.width() > 5 and rect.height() > 5:
            for item in self.items:
                item_rect = QRect(item.pos(), item.size())
                if rect.intersects(item_rect):
                    # Set directly to avoid signal→onItemSelected→selectItem clearing the list
                    item.is_selected = True
                    if hasattr(item, '_refreshBorder'):
                        item._refreshBorder()
                    else:
                        item.update()
                    self.addToSelection(item)

    def _commit_active_text_edit(self):
        """활성 인라인 편집(텍스트/그룹)이 있으면 저장 후 종료."""
        for item in self.children():
            if hasattr(item, '_inline_edit') and item._inline_edit is not None:
                item._inline_edit.clearFocus()
                return
            if hasattr(item, '_name_edit') and item._name_edit is not None:
                if hasattr(item, '_group_edit_cleanup') and item._group_edit_cleanup:
                    item._group_edit_cleanup(True)
                else:
                    item._name_edit.clearFocus()
                return

    def mousePressEvent(self, e):
        self._commit_active_text_edit()
        if e.button() == Qt.MiddleButton:
            self.pan_start = e.pos()
            self.pan_start_offset = QPoint(self.pan_offset)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.RightButton:
            # 아이템(자식 위젯) 위에서 우클릭이면 창 이동 모드 진입하지 않음
            child = self.childAt(e.pos())
            if child and child != getattr(self.main_window, 'status_label', None):
                return
            self._rmove_win_start = e.globalPos()
            self._rmove_win_initial = self.main_window.pos()
            self.r_drag_mode = 'window'
            self._r_dragged = False
            self.setCursor(Qt.SizeAllCursor)
            return
        if e.button() == Qt.LeftButton:
            shift = bool(e.modifiers() & Qt.ShiftModifier)
            ctrl = bool(e.modifiers() & Qt.ControlModifier)
            if ctrl and not shift:
                self.sel_start = e.pos()
                self.sel_rect = QRect(self.sel_start, self.sel_start)
                self.sel_additive = True
                return
            child = self.childAt(e.pos())
            is_empty = not child or child == getattr(self.main_window, 'status_label', None)
            if is_empty:
                if not shift:
                    self.deselectAll()
                self.sel_start = e.pos()
                self.sel_rect = QRect(self.sel_start, self.sel_start)
                self.sel_additive = shift
                self.canvas_drag_start = e.globalPos() - self.main_window.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.pan_start and e.buttons() & Qt.MiddleButton:
            delta = e.pos() - self.pan_start
            new_offset = self.pan_start_offset + delta
            offset_delta = new_offset - self.pan_offset
            self.pan_offset = new_offset
            self._pan_float = [float(new_offset.x()), float(new_offset.y())]
            for item in self.items + self.groups:
                new_pos = item.pos() + offset_delta
                item.move(new_pos)
                key = id(item)
                if key in self._item_float_pos:
                    self._item_float_pos[key][0] += offset_delta.x()
                    self._item_float_pos[key][1] += offset_delta.y()
                else:
                    self._item_float_pos[key] = [float(new_pos.x()), float(new_pos.y())]
            self._update_culling()
            return
        if e.buttons() & Qt.RightButton and self._rmove_win_start:
            delta = e.globalPos() - self._rmove_win_start
            if delta.manhattanLength() > 4:
                self._r_dragged = True
            self.main_window.move(self._rmove_win_initial + delta)
            return
        if self.sel_start and e.buttons() & Qt.LeftButton:
            self.sel_rect = QRect(self.sel_start, e.pos()).normalized()
            self.update()
            return
        if self.canvas_drag_start and e.buttons() == Qt.LeftButton:
            self.main_window.move(e.globalPos() - self.canvas_drag_start)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.pan_start = None
            self.pan_start_offset = None
            self.setCursor(Qt.ArrowCursor)
            return
        if e.button() == Qt.RightButton:
            self.setCursor(Qt.ArrowCursor)
            self._rmove_win_start = None
            self.r_drag_mode = None
            return
        if e.button() == Qt.LeftButton:
            if self.sel_start:
                self._applySelectionRect(self.sel_rect, additive=self.sel_additive)
                self.sel_start = None
                self.sel_rect = None
                self.update()
            self.canvas_drag_start = None

    def contextMenuEvent(self, e):
        if self._r_dragged:
            self._r_dragged = False
            return
        child = self.childAt(e.pos())
        if child and child != getattr(self.main_window, 'status_label', None):
            return
        self._ctx_pos = e.pos()   # save click position for addTextItem / addGroup
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('텍스트 추가', self.addTextItem)
        menu.addAction('그룹 만들기', self.addGroup)
        menu.addSeparator()
        menu.addAction('배경 색상...', self._changeBg)
        menu.addSeparator()
        for name, color in [
            ('검정', '#1a1a1a'), ('어두운 회색', '#2d2d2d'), ('회색', '#505050'),
            ('흰색', '#ffffff'), ('어두운 녹색', '#1e3d1e'), ('어두운 파랑', '#1e2d3d'),
        ]:
            menu.addAction(f'  {name}', lambda c=color: self.setBackgroundColor(QColor(c)))
        menu.addSeparator()
        menu.addAction('뷰 초기화', self.resetView)
        menu.exec_(e.globalPos())

    def resetView(self):
        offset_delta = QPoint(0, 0) - self.pan_offset
        self.pan_offset = QPoint(0, 0)
        self._pan_float = [0.0, 0.0]
        self._item_float_pos.clear()
        for item in self.items + self.groups:
            item.move(item.pos() + offset_delta)
        if abs(self.canvas_scale - self.DEFAULT_SCALE) > 0.001:
            scale_factor = self.DEFAULT_SCALE / self.canvas_scale
            self.canvas_scale = self.DEFAULT_SCALE
            center = QPoint(self.width() // 2, self.height() // 2)
            for item in self.items + self.groups:
                if isinstance(item, GroupItem):
                    item.setGeometry(
                        round(item.pos().x() * scale_factor),
                        round(item.pos().y() * scale_factor),
                        round(item.width() * scale_factor),
                        round(item.height() * scale_factor))
                elif isinstance(item, TextItem):
                    if item._user_resized:
                        item.setFixedSize(
                            max(60, round(item.width() * scale_factor)),
                            max(30, round(item.height() * scale_factor)))
                    item.move(round(item.pos().x() * scale_factor),
                              round(item.pos().y() * scale_factor))
                else:
                    item.setScale(item.current_scale * scale_factor)
                    item.move(round(item.pos().x() * scale_factor),
                              round(item.pos().y() * scale_factor))

    def _get_item_float_pos(self, item):
        key = id(item)
        if key in self._item_float_pos:
            fx, fy = self._item_float_pos[key]
            if round(fx) == item.pos().x() and round(fy) == item.pos().y():
                return fx, fy
        fx, fy = float(item.pos().x()), float(item.pos().y())
        self._item_float_pos[key] = [fx, fy]
        return fx, fy

    def resetZoom(self):
        if abs(self.canvas_scale - self.DEFAULT_SCALE) < 0.001:
            return
        center = QPoint(self.width() // 2, self.height() // 2)
        self.zoomCanvas(self.DEFAULT_SCALE / self.canvas_scale, center)

    def center_on_item(self, item):
        """아이템이 뷰포트 중앙에 꽉 차도록 줌 + 패닝한다."""
        vp_w, vp_h = self.width(), self.height()
        it_w, it_h = item.width(), item.height()
        if it_w <= 0 or it_h <= 0 or vp_w <= 0 or vp_h <= 0:
            return

        MARGIN = 0.12
        factor_x = vp_w * (1.0 - MARGIN) / it_w
        factor_y = vp_h * (1.0 - MARGIN) / it_h
        factor   = min(factor_x, factor_y)

        # 범위 초과 방지
        new_scale = self.canvas_scale * factor
        new_scale = max(self.DEFAULT_SCALE * 0.10,
                        min(self.DEFAULT_SCALE * 4.00, new_scale))
        factor = new_scale / self.canvas_scale

        # 아이템 중심을 줌 피벗으로 사용 → 줌 후에도 중심이 같은 좌표에 고정됨
        item_cx = item.pos().x() + it_w / 2
        item_cy = item.pos().y() + it_h / 2
        self.zoomCanvas(factor, QPoint(round(item_cx), round(item_cy)))

        # 줌 후 아이템 중심 → 뷰포트 중앙으로 패닝
        delta = QPoint(round(vp_w / 2 - item_cx), round(vp_h / 2 - item_cy))
        for i in self.items + self.groups:
            new_pos = i.pos() + delta
            i.move(new_pos)
            key = id(i)
            if key in self._item_float_pos:
                self._item_float_pos[key][0] += delta.x()
                self._item_float_pos[key][1] += delta.y()
            else:
                self._item_float_pos[key] = [float(new_pos.x()), float(new_pos.y())]
        self.pan_offset += delta
        self._pan_float = [float(self.pan_offset.x()), float(self.pan_offset.y())]
        if hasattr(self.main_window, 'titlebar'):
            self.main_window.titlebar.zoom_label.setText(
                f'{round(self.canvas_scale / self.DEFAULT_SCALE * 100)}%')

    def zoom_to_fit_all(self):
        """H키: 모든 아이템이 뷰포트에 꽉 차도록 줌아웃/줌인."""
        all_items = self.items + self.groups
        if not all_items:
            return
        min_x = min(i.pos().x() for i in all_items)
        min_y = min(i.pos().y() for i in all_items)
        max_x = max(i.pos().x() + i.width()  for i in all_items)
        max_y = max(i.pos().y() + i.height() for i in all_items)
        content_w = max_x - min_x
        content_h = max_y - min_y
        if content_w <= 0 or content_h <= 0:
            return
        MARGIN = 0.08
        factor_x = self.width()  * (1 - MARGIN) / content_w
        factor_y = self.height() * (1 - MARGIN) / content_h
        factor   = min(factor_x, factor_y)
        new_scale = max(self.DEFAULT_SCALE * 0.10,
                        min(self.DEFAULT_SCALE * 4.00, self.canvas_scale * factor))
        factor = new_scale / self.canvas_scale
        # 콘텐츠 중심을 피벗으로 줌 → 중심 좌표 불변
        ccx = (min_x + max_x) / 2
        ccy = (min_y + max_y) / 2
        self.zoomCanvas(factor, QPoint(round(ccx), round(ccy)))
        # 콘텐츠 중심 → 뷰포트 중앙으로 패닝
        delta = QPoint(round(self.width() / 2 - ccx), round(self.height() / 2 - ccy))
        for i in self.items + self.groups:
            new_pos = i.pos() + delta
            i.move(new_pos)
            key = id(i)
            if key in self._item_float_pos:
                self._item_float_pos[key][0] += delta.x()
                self._item_float_pos[key][1] += delta.y()
            else:
                self._item_float_pos[key] = [float(new_pos.x()), float(new_pos.y())]
        self.pan_offset += delta
        self._pan_float = [float(self.pan_offset.x()), float(self.pan_offset.y())]
        if hasattr(self.main_window, 'titlebar'):
            self.main_window.titlebar.zoom_label.setText(
                f'{round(self.canvas_scale / self.DEFAULT_SCALE * 100)}%')

    def zoomCanvas(self, factor, center):
        new_scale = self.canvas_scale * factor
        if new_scale < self.DEFAULT_SCALE * 0.10 or new_scale > self.DEFAULT_SCALE * 4.00:
            return
        self.canvas_scale = new_scale
        mw = self.main_window
        if hasattr(mw, 'titlebar') and hasattr(mw.titlebar, 'zoom_label'):
            mw.titlebar.zoom_label.setText(
                f'{round(self.canvas_scale / self.DEFAULT_SCALE * 100)}%')
        cx, cy = float(center.x()), float(center.y())
        for item in self.items:
            if isinstance(item, TextItem):
                if item._user_resized:
                    new_w = max(1, round(item.width() * factor))
                    new_h = max(1, round(item.height() * factor))
                    item.setFixedSize(new_w, new_h)
                fx, fy = self._get_item_float_pos(item)
                new_fx = cx + (fx - cx) * factor
                new_fy = cy + (fy - cy) * factor
                self._item_float_pos[id(item)] = [new_fx, new_fy]
                item.move(round(new_fx), round(new_fy))
                item._applyStyle()
            else:
                item.setScale(item.current_scale * factor)
                fx, fy = self._get_item_float_pos(item)
                new_fx = cx + (fx - cx) * factor
                new_fy = cy + (fy - cy) * factor
                self._item_float_pos[id(item)] = [new_fx, new_fy]
                item.move(round(new_fx), round(new_fy))
        for g in self.groups:
            fx, fy = self._get_item_float_pos(g)
            new_fx = cx + (fx - cx) * factor
            new_fy = cy + (fy - cy) * factor
            self._item_float_pos[id(g)] = [new_fx, new_fy]
            g.setGeometry(round(new_fx), round(new_fy),
                          round(g.width() * factor), round(g.height() * factor))
        pan_fx = self._pan_float[0]
        pan_fy = self._pan_float[1]
        self._pan_float = [cx + (pan_fx - cx) * factor, cy + (pan_fy - cy) * factor]
        self.pan_offset = QPoint(round(self._pan_float[0]), round(self._pan_float[1]))
        self.update()
        mw = self.main_window
        if mw and hasattr(mw, '_zoom_hud'):
            mw._zoom_hud.set_zoom(round(self.canvas_scale / self.DEFAULT_SCALE * 100))
        self._update_culling()

    def wheelEvent(self, e):
        factor = 1.1 if e.angleDelta().y() > 0 else 0.9
        self.zoomCanvas(factor, e.pos())
        e.accept()

    def _changeBg(self):
        ColorPickerDialog.getColor(
            self.bg_color, self, '배경 색상',
            on_change=self.setBackgroundColor)

    def _update_culling(self):
        visible_rect = self.rect()
        for item in self.items:
            if isinstance(item, (GifItem, VideoItem)):
                item._set_culled(not item.geometry().intersects(visible_rect))


# ── PanelToggleButton ──────────────────────────────────────────────────────────

class PanelToggleButton(QPushButton):
    """Layer panel open/close icon button."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('레이어 패널 열기/닫기')
        self.setStyleSheet('background:transparent;border:none;')
        self._hovered = False
        self.setAttribute(Qt.WA_Hover)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() // 2, self.height() // 2
        checked = self.isChecked()
        if checked:
            bg = QColor(0, 120, 212, 180 if self._hovered else 130)
        else:
            bg = QColor(255, 255, 255, 50 if self._hovered else 20)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(3, 3, 24, 24, 4, 4)
        p.setPen(QPen(Qt.white, 1.5))
        for i, y in enumerate([9, 15, 21]):
            p.drawLine(8, y, 22, y)


# ── PinButton ──────────────────────────────────────────────────────────────────

class PinButton(QPushButton):
    """Custom pin/thumbtack icon button for always-on-top."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background:transparent;border:none;')
        self._hovered = False
        self.setAttribute(Qt.WA_Hover)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        checked = self.isChecked()

        # 배경
        if checked:
            bg = QColor(80, 160, 255, 175 if self._hovered else 135)
        else:
            bg = QColor(255, 255, 255, 40 if self._hovered else 18)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(2, 2, 20, 20, 3, 3)

        alpha = 245 if checked else (205 if self._hovered else 155)
        col = QColor(255, 255, 255, alpha)

        # ── 압정 실루엣 ──────────────────────────────────────────
        # 40° 기울임: 헤드=우상단, 바늘=좌하단
        p.save()
        p.translate(11.0, 11.0)
        p.rotate(40.0)
        p.setPen(Qt.NoPen)
        p.setBrush(col)

        # ① 헤드 (납작한 디스크 – 타원형)
        p.drawEllipse(QRectF(-3.2, -8.0, 6.4, 4.0))

        # ② 돔 몸통 (원형 – 헤드와 자연스럽게 이어짐)
        p.drawEllipse(QRectF(-3.6, -4.8, 7.2, 7.2))

        # ③ 바늘 (길고 날카로운 삼각형)
        needle = QPainterPath()
        needle.moveTo(-1.0,  2.2)
        needle.lineTo( 1.0,  2.2)
        needle.lineTo( 0.0, 10.0)
        needle.closeSubpath()
        p.fillPath(needle, col)

        p.restore()


# ── FolderButton ───────────────────────────────────────────────────────────────

class FolderButton(QPushButton):
    """Folder icon button — painted in monochrome (no emoji)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background:transparent;border:none;')
        self._hovered = False
        self.setAttribute(Qt.WA_Hover)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        alpha = 245 if self._hovered else 200
        col = QColor(255, 255, 255, alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        # 탭 (폴더 상단 왼쪽 돌출)
        p.drawRoundedRect(QRectF(2.5, 3.5, 7.0, 3.5), 1.5, 1.5)
        # 폴더 몸통 (탭과 겹쳐서 자연스럽게 이어짐)
        p.drawRoundedRect(QRectF(2.5, 5.5, 15.0, 9.0), 1.5, 1.5)


# ── CustomTitleBar ─────────────────────────────────────────────────────────────

class CustomTitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setMouseTracking(True)
        self.parent_window = parent
        self.drag_start = None
        self._color = QColor(45, 45, 45)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        self.title = QLabel('ReView')
        self.title.setStyleSheet('font-weight:bold;font-size:13px;color:#ffffff;background:transparent;')
        lay.addWidget(self.title)
        lay.addStretch()
        self.zoom_label = QLabel('100%')
        self.zoom_label.setStyleSheet('color:#aaaaaa;background:transparent;font-size:13px;')
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.zoom_label)
        self.zoom_reset_btn = QPushButton('↺')
        self.zoom_reset_btn.setFixedSize(30, 24)
        self.zoom_reset_btn.setStyleSheet(
            'QPushButton{background:transparent;color:#aaaaaa;border:none;font-size:18px;}'
            'QPushButton:hover{color:#ffffff;background:rgba(255,255,255,20);}')
        lay.addWidget(self.zoom_reset_btn)
        lay.addSpacing(8)

        _btn_base = (
            'QPushButton{{'
            'background:transparent;color:#aaa;'
            'border:1px solid rgba(255,255,255,22);border-radius:3px;'
            'font-family:"Segoe UI","Malgun Gothic",sans-serif;'
            'font-size:10px;padding:0px 8px;'
            '}}'
            'QPushButton:hover{{color:#fff;{hover_extra}border-color:rgba(255,255,255,55);}}'
        )
        file_ss = _btn_base.format(hover_extra='background:rgba(255,255,255,14);')
        proj_ss = _btn_base.format(hover_extra='background:rgba(80,140,220,28);border-color:rgba(90,150,235,70);')

        self.open_saves_btn = FolderButton(self)
        lay.addWidget(self.open_saves_btn)
        lay.addSpacing(4)

        self.save_btn = QPushButton('파일 저장')
        self.save_btn.setFixedHeight(18)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip('선택한 파일을 저장 폴더에 저장')
        self.save_btn.setStyleSheet(file_ss)
        lay.addWidget(self.save_btn)
        lay.addSpacing(4)

        self.project_save_btn = QPushButton('프로젝트 저장')
        self.project_save_btn.setFixedHeight(18)
        self.project_save_btn.setCursor(Qt.PointingHandCursor)
        self.project_save_btn.setToolTip('프로젝트 저장  Ctrl+S')
        self.project_save_btn.setStyleSheet(proj_ss)
        lay.addWidget(self.project_save_btn)
        lay.addSpacing(8)

        self.pin_btn = PinButton(self)
        lay.addWidget(self.pin_btn)
        lay.addSpacing(6)
        btn_ss = ('QPushButton{background:transparent;color:#cccccc;border:none;'
                  'font-size:11px;font-weight:bold;padding:0;}'
                  'QPushButton:hover{background:rgba(255,255,255,30);}')
        close_ss = ('QPushButton{background:transparent;color:#cccccc;border:none;'
                    'font-size:11px;font-weight:bold;padding:0;}'
                    'QPushButton:hover{background:#c42b1c;color:#ffffff;}')
        for text, ss, slot, w in [
            ('−', btn_ss, lambda: parent.showMinimized(), 42),
            ('□', btn_ss, self._toggleMax, 42),
            ('✕', close_ss, lambda: parent.close(), 42),
        ]:
            btn = QPushButton(text, self)
            btn.setFixedSize(w, 24)
            btn.setStyleSheet(ss)
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        self.pin_btn.clicked.connect(self._onPin)

    def paintEvent(self, e):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, self._color)
        grad.setColorAt(1, self._color.darker(125))
        p.fillRect(self.rect(), grad)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        mw = self.parent_window
        if mw:
            menu.addAction('새 프로젝트  Ctrl+N', mw.newProject)
            menu.addAction('열기...  Ctrl+O', mw.openProject)
            menu.addSeparator()
            menu.addAction('저장  Ctrl+S', mw.saveProject)
            menu.addAction('다른 이름으로 저장...  Ctrl+Shift+S', mw.saveProjectAs)
            menu.addSeparator()
        act = menu.addAction('헤더 색상 변경...')
        act.triggered.connect(self._pickColor)
        menu.exec_(e.globalPos())

    def _pickColor(self):
        dlg = ColorPickerDialog(self._color, self)
        if dlg.exec_() == QDialog.Accepted:
            self._color = dlg.selectedColor()
            self.update()

    def enterEvent(self, e):
        if hasattr(self.parent_window, 'titlebar_timer'):
            self.parent_window.titlebar_timer.stop()

    def leaveEvent(self, e):
        if hasattr(self.parent_window, 'titlebar_timer'):
            self.parent_window.titlebar_timer.start(500)

    def _updatePin(self, on):
        self.pin_btn.setChecked(on)
        self.pin_btn.update()

    def _onPin(self, checked):
        if self.parent_window:
            self.parent_window.setAlwaysOnTop(checked)

    def _toggleMax(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
        else:
            self.parent_window.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_start = e.globalPos() - self.parent_window.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag_start and e.buttons() == Qt.LeftButton:
            self.parent_window.move(e.globalPos() - self.drag_start)

    def mouseReleaseEvent(self, e):
        self.drag_start = None

    def mouseDoubleClickEvent(self, e):
        self._toggleMax()


# ── ResizeFrame ────────────────────────────────────────────────────────────────

class ResizeFrame(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setMouseTracking(True)
        self.resize_dir = None
        self.resize_start = None
        self.resize_geometry = None
        self.margin = 8

    def updateMask(self):
        w, h = self.width(), self.height()
        m = self.margin
        outer = QRegion(0, 0, w, h)
        inner = QRegion(m, m, w - 2 * m, h - 2 * m)
        self.setMask(outer.subtracted(inner))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.updateMask()

    def _edge(self, pos):
        w, h = self.width(), self.height()
        m = self.margin
        x, y = pos.x(), pos.y()
        left, right = x < m, x > w - m
        top, bottom = y < m, y > h - m
        if top and left:     return 'topleft'
        if top and right:    return 'topright'
        if bottom and left:  return 'bottomleft'
        if bottom and right: return 'bottomright'
        if left:   return 'left'
        if right:  return 'right'
        if top:    return 'top'
        if bottom: return 'bottom'
        return None

    def mouseMoveEvent(self, e):
        if self.resize_dir and self.resize_start:
            self._doResize(e.globalPos())
            return
        edge = self._edge(e.pos())
        if edge:
            cursors = {
                'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
                'top': Qt.SizeVerCursor, 'bottom': Qt.SizeVerCursor,
                'topleft': Qt.SizeFDiagCursor, 'bottomright': Qt.SizeFDiagCursor,
                'topright': Qt.SizeBDiagCursor, 'bottomleft': Qt.SizeBDiagCursor,
            }
            self.setCursor(cursors.get(edge, Qt.ArrowCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            edge = self._edge(e.pos())
            if edge:
                self.resize_dir = edge
                self.resize_start = e.globalPos()
                self.resize_geometry = self.main_window.geometry()

    def mouseReleaseEvent(self, e):
        self.resize_dir = None
        self.resize_start = None
        self.setCursor(Qt.ArrowCursor)

    def _doResize(self, gp):
        if not self.resize_dir or not self.resize_geometry:
            return
        diff = gp - self.resize_start
        geo = QRect(self.resize_geometry)
        mw, mh = 400, 300
        if 'left' in self.resize_dir:
            nl = geo.left() + diff.x()
            if geo.right() - nl >= mw:
                geo.setLeft(nl)
        if 'right' in self.resize_dir:
            if geo.width() + diff.x() >= mw:
                geo.setWidth(geo.width() + diff.x())
        if 'top' in self.resize_dir:
            nt = geo.top() + diff.y()
            if geo.bottom() - nt >= mh:
                geo.setTop(nt)
        if 'bottom' in self.resize_dir:
            if geo.height() + diff.y() >= mh:
                geo.setHeight(geo.height() + diff.y())
        self.main_window.setGeometry(geo)


# ── PanelDivider ───────────────────────────────────────────────────────────────

class PanelDivider(QWidget):
    """Draggable divider between canvas and layer panel."""
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setFixedWidth(10)
        self.setCursor(Qt.SizeHorCursor)
        self.setMouseTracking(True)
        self._drag_start_x = None
        self._start_width = None
        self._hovered = False
        self.setAttribute(Qt.WA_Hover)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 패널 → 캔버스 방향 그림자 그라디언트
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor(0, 0, 0, 90))
        grad.setColorAt(0.4, QColor(0, 0, 0, 30))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), grad)
        # 패널 경계 얇은 선
        alpha = 40 if self._hovered else 18
        p.setPen(QPen(QColor(255, 255, 255, alpha), 1))
        p.drawLine(0, 0, 0, h)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_start_x = e.globalPos().x()
            self._start_width = self._panel.width()

    def mouseMoveEvent(self, e):
        if self._drag_start_x is not None and e.buttons() & Qt.LeftButton:
            delta = e.globalPos().x() - self._drag_start_x
            new_w = max(100, min(450, self._start_width + delta))
            self._panel.setFixedWidth(new_w)
            if _main_window and hasattr(_main_window, '_reposition_overlay_panel'):
                _main_window._reposition_overlay_panel()

    def mouseReleaseEvent(self, e):
        self._drag_start_x = None
        self._start_width = None


# ── EyeButton ──────────────────────────────────────────────────────────────────

class EyeButton(QPushButton):
    """Visibility toggle (eye icon) for layer rows."""
    def __init__(self, item, panel, parent=None, is_group=False):
        super().__init__(parent)
        self._item = item
        self._panel = panel
        self._is_group = is_group
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background:transparent;border:none;')
        self.clicked.connect(self._toggle)

    def _is_visible(self):
        if self._is_group:
            return id(self._item) not in self._panel._eye_hidden_groups
        return id(self._item) not in self._panel._eye_hidden

    def _toggle(self):
        if self._is_group:
            self._panel.toggle_group_visibility(self._item)
        else:
            self._panel.toggle_item_visibility(self._item)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        visible = self._is_visible()
        cx, cy = self.width() / 2.0, self.height() / 2.0

        if visible:
            # ── 눈 뜬 상태 ────────────────────────────────────────────
            # ① 공막 아몬드 (얇은 외곽선 + 희미한 내부 채움)
            eye = QPainterPath()
            eye.moveTo(cx - 6, cy)
            eye.quadTo(cx,      cy - 5.5, cx + 6, cy)
            eye.quadTo(cx,      cy + 5.5, cx - 6, cy)
            eye.closeSubpath()
            p.setBrush(QColor(215, 215, 215, 22))
            p.setPen(QPen(QColor(185, 185, 185), 1.2,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(eye)

            # ② 홍채 (중간 회색 채워진 원)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(148, 148, 148))
            p.drawEllipse(QPointF(cx, cy), 3.0, 3.0)

            # ③ 동공 (어두운 소원)
            p.setBrush(QColor(28, 28, 28))
            p.drawEllipse(QPointF(cx, cy), 1.6, 1.6)

            # ④ 하이라이트 (좌상단 밝은 점)
            p.setBrush(QColor(215, 215, 215, 210))
            p.drawEllipse(QPointF(cx - 0.9, cy - 1.0), 0.62, 0.62)

        else:
            # ── 눈 감긴 상태 ──────────────────────────────────────────
            # ① 감긴 눈꺼풀 (하단 아크)
            lid = QPainterPath()
            lid.moveTo(cx - 6, cy)
            lid.quadTo(cx,     cy + 4.8, cx + 6, cy)
            p.setPen(QPen(QColor(82, 82, 82), 1.5,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(lid)

            # ② 속눈썹 3가닥 (아래로)
            p.setPen(QPen(QColor(66, 66, 66), 1.0,
                          Qt.SolidLine, Qt.RoundCap))
            lashes = [
                (cx - 3.3, cy + 3.9,  cx - 3.9, cy + 6.2),
                (cx,        cy + 4.8,  cx,        cy + 7.3),
                (cx + 3.3, cy + 3.9,  cx + 3.9,  cy + 6.2),
            ]
            for x1, y1, x2, y2 in lashes:
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


# ── _ItemHoverBar ──────────────────────────────────────────────────────────────

class _OpacityKnob(QLabel):
    """Opacity % scrubber label: scroll wheel or click-drag (up=more) to change."""

    def __init__(self, item, bar, parent=None):
        super().__init__(parent)
        self._item = item
        self._bar = bar
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_val = 100
        self.setCursor(Qt.SizeVerCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            'color: rgba(220,220,220,220); background: transparent; font-size:11px;'
        )
        self.setFixedHeight(20)
        self.setMouseTracking(True)

    def setValue(self, pct):
        pct = max(0, min(100, int(pct)))
        self.setText(f'{pct}%')
        fm = QFontMetrics(self.font())
        self.setFixedWidth(max(34, fm.horizontalAdvance(f'{pct}%') + 12))

    def _val(self):
        try:
            return int(self.text().rstrip('%'))
        except (ValueError, AttributeError):
            return 100

    def wheelEvent(self, e):
        step = 1 if (e.modifiers() & Qt.ShiftModifier) else 5
        delta = 1 if e.angleDelta().y() > 0 else -1
        old_val = self._val()
        new_val = max(0, min(100, old_val + delta * step))
        if new_val != old_val:
            self.setValue(new_val)
            self._item.setItemOpacity(new_val / 100.0)
            _ov, _nv, _item = old_val / 100.0, new_val / 100.0, self._item
            _record_undo(
                lambda: _item.setItemOpacity(_ov),
                lambda: _item.setItemOpacity(_nv),
            )
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_y = e.globalPos().y()
            self._drag_start_val = self._val()
            self.setCursor(Qt.BlankCursor)

    def mouseMoveEvent(self, e):
        if self._dragging:
            dy = self._drag_start_y - e.globalPos().y()
            new_val = max(0, min(100, self._drag_start_val + dy))
            self.setValue(new_val)
            self._item.setItemOpacity(new_val / 100.0)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._dragging:
            old_val = self._drag_start_val / 100.0
            new_val = self._val() / 100.0
            if abs(old_val - new_val) > 1e-4:
                _item = self._item
                _record_undo(
                    lambda: _item.setItemOpacity(old_val),
                    lambda: _item.setItemOpacity(new_val),
                )
            self._dragging = False
            self.setCursor(Qt.SizeVerCursor)


class _ItemBlendBar(QWidget):
    """Blend mode + opacity panel shown at top-left of item on hover."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(5)

        # Blend mode button
        self._blend_btn = QPushButton(self)
        self._blend_btn.setCursor(Qt.PointingHandCursor)
        self._blend_btn.setStyleSheet(
            'QPushButton{background:transparent;border:none;'
            'color:rgba(220,220,220,220);font-size:11px;padding:0 1px;}'
            'QPushButton:hover{color:white;}'
        )
        self._blend_btn.clicked.connect(self._show_blend_menu)
        lay.addWidget(self._blend_btn)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet('color: rgba(255,255,255,45);')
        sep.setFixedSize(1, 14)
        lay.addWidget(sep)

        # Opacity knob
        self._opacity_knob = _OpacityKnob(item, self, self)
        lay.addWidget(self._opacity_knob)

        self.refresh()
        self.adjustSize()

    def refresh(self):
        mode = getattr(self._item, '_blend_mode', QPainter.CompositionMode_SourceOver)
        name = _BLEND_MODE_NAMES.get(mode, 'Normal')
        self._blend_btn.setText(f'{name} \u25be')
        fm = QFontMetrics(self._blend_btn.font())
        self._blend_btn.setFixedWidth(max(58, fm.horizontalAdvance(self._blend_btn.text()) + 14))
        op_pct = int(round(getattr(self._item, '_opacity', 1.0) * 100))
        self._opacity_knob.setValue(op_pct)
        self.adjustSize()

    def _show_blend_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        current = getattr(self._item, '_blend_mode', QPainter.CompositionMode_SourceOver)
        self._preview_original = current

        _mode_map = {}
        for bname, bmode in _BLEND_MODES:
            a = menu.addAction(bname)
            a.setCheckable(True)
            a.setChecked(current == bmode)
            a.triggered.connect(lambda _, m=bmode: self._apply_blend(m))
            _mode_map[a] = bmode

        def _on_hover(action):
            bmode = _mode_map.get(action)
            if bmode is not None:
                self._item._blend_mode = bmode
                if hasattr(self._item, '_label'):
                    self._item._label.update()
                self._item.update()

        menu.hovered.connect(_on_hover)

        # 아이템 오른쪽 바깥에 메뉴 표시 (콘텐츠가 가려지지 않도록)
        menu.adjustSize()
        item = self._item
        item_tr = item.mapToGlobal(QPoint(item.width() + 4, 0))
        screen = QApplication.primaryScreen().availableGeometry()
        if item_tr.x() + menu.width() > screen.right():
            # 오른쪽 여백 부족 → 왼쪽에 표시
            item_tr = item.mapToGlobal(QPoint(-menu.width() - 4, 0))
        chosen = menu.exec_(item_tr)

        if chosen is None:
            # 취소 — 원래 모드로 복원
            self._item._blend_mode = self._preview_original
            if hasattr(self._item, '_label'):
                self._item._label.update()
            self._item.update()
            self.refresh()

    def _apply_blend(self, mode):
        # 미리보기 상태에서 호출되므로 원래 모드로 되돌린 후 적용 (undo가 정확하게 기록됨)
        self._item._blend_mode = getattr(self, '_preview_original', self._item._blend_mode)
        _set_blend_mode(self._item, mode)
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.raise_()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(QColor(18, 18, 22, 210))
        r = self.height() / 2.0
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), r, r)


class _ItemHoverBar(QWidget):
    """Small translucent toolbar shown above the image/gif/video on hover."""
    _BTN = 24   # button cell size

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self._crop_editing = False   # 자르기 오버레이 열림 여부
        self._trim_editing = False   # 구간편집 오버레이 열림 여부
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(2)

        self._lock_btn = _HoverBarBtn(self, draw_fn=self._draw_lock)
        self._lock_btn.clicked.connect(self._toggle_lock)
        lay.addWidget(self._lock_btn)

        sep1 = QFrame(self)
        sep1.setFrameShape(QFrame.VLine)
        sep1.setStyleSheet('color: rgba(255,255,255,40);')
        sep1.setFixedWidth(1)
        lay.addWidget(sep1)

        flip_h_btn = _HoverBarBtn(self, draw_fn=self._draw_flip_h)
        flip_h_btn.setToolTip('좌우 반전')
        flip_h_btn.clicked.connect(self._flip_h)
        lay.addWidget(flip_h_btn)

        flip_v_btn = _HoverBarBtn(self, draw_fn=self._draw_flip_v)
        flip_v_btn.setToolTip('상하 반전')
        flip_v_btn.clicked.connect(self._flip_v)
        lay.addWidget(flip_v_btn)

        sep2 = QFrame(self)
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet('color: rgba(255,255,255,40);')
        sep2.setFixedWidth(1)
        lay.addWidget(sep2)

        rot_cw_btn = _HoverBarBtn(self, draw_fn=self._draw_rot_cw)
        rot_cw_btn.setToolTip('시계방향 90° 회전')
        rot_cw_btn.clicked.connect(self._rotate_cw)
        lay.addWidget(rot_cw_btn)

        rot_ccw_btn = _HoverBarBtn(self, draw_fn=self._draw_rot_ccw)
        rot_ccw_btn.setToolTip('반시계방향 90° 회전')
        rot_ccw_btn.clicked.connect(self._rotate_ccw)
        lay.addWidget(rot_ccw_btn)

        sep3 = QFrame(self)
        sep3.setFrameShape(QFrame.VLine)
        sep3.setStyleSheet('color: rgba(255,255,255,40);')
        sep3.setFixedWidth(1)
        lay.addWidget(sep3)

        self._crop_btn = _HoverBarBtn(self, draw_fn=self._draw_crop)
        self._crop_btn.setToolTip('Crop')
        self._crop_btn.clicked.connect(self._open_crop)
        lay.addWidget(self._crop_btn)

        self._crop_reset_btn = _HoverBarBtn(self, draw_fn=self._draw_reset_crop, size=18)
        self._crop_reset_btn.setToolTip('Reset Crop')
        self._crop_reset_btn.clicked.connect(self._reset_crop)
        self._crop_reset_btn.setVisible(False)
        lay.addWidget(self._crop_reset_btn)

        if hasattr(item, '_trim_start'):
            self._trim_btn = _HoverBarBtn(self, draw_fn=self._draw_trim)
            self._trim_btn.setToolTip('Trim')
            self._trim_btn.clicked.connect(self._open_trim)
            lay.addWidget(self._trim_btn)

            self._trim_reset_btn = _HoverBarBtn(self, draw_fn=self._draw_reset_trim, size=18)
            self._trim_reset_btn.setToolTip('Reset Trim')
            self._trim_reset_btn.clicked.connect(self._reset_trim)
            self._trim_reset_btn.setVisible(False)
            lay.addWidget(self._trim_reset_btn)
        else:
            self._trim_btn = None
            self._trim_reset_btn = None

        sep_pin = QFrame(self)
        sep_pin.setFrameShape(QFrame.VLine)
        sep_pin.setStyleSheet('color: rgba(255,255,255,40);')
        sep_pin.setFixedWidth(1)
        lay.addWidget(sep_pin)

        self._pin_btn = _HoverBarBtn(self, draw_fn=self._draw_pin)
        self._pin_btn.setToolTip('항상 위 고정')
        self._pin_btn.clicked.connect(self._toggle_pin)
        lay.addWidget(self._pin_btn)

        self.adjustSize()

    def refresh(self):
        """Active 상태에 따라 리셋 버튼 가시성을 갱신한다."""
        crop_on = self._is_cropped()
        if self._crop_reset_btn.isVisible() != crop_on:
            self._crop_reset_btn.setVisible(crop_on)
            self.adjustSize()
        if self._trim_reset_btn:
            trim_on = self._is_trimmed()
            if self._trim_reset_btn.isVisible() != trim_on:
                self._trim_reset_btn.setVisible(trim_on)
                self.adjustSize()
        self._pin_btn.update()
        self.update()

    def _is_locked(self):
        item = self._item
        canvas = item.parent() if item else None
        if canvas and hasattr(canvas, 'main_window'):
            mw = canvas.main_window
            if mw and hasattr(mw, 'layer_panel'):
                return id(item) in mw.layer_panel._locked
        return False

    def _toggle_lock(self):
        item = self._item
        canvas = item.parent() if item else None
        if canvas and hasattr(canvas, 'main_window'):
            mw = canvas.main_window
            if mw and hasattr(mw, 'layer_panel'):
                mw.layer_panel.toggle_item_lock(item)
        self._lock_btn.update()

    def _flip_h(self):
        item = self._item
        item._flip_h = not item._flip_h
        item._label.update()
        def _toggle():
            try: item._flip_h = not item._flip_h; item._label.update()
            except RuntimeError: pass
        _record_undo(_toggle, _toggle)

    def _flip_v(self):
        item = self._item
        item._flip_v = not item._flip_v
        item._label.update()
        def _toggle():
            try: item._flip_v = not item._flip_v; item._label.update()
            except RuntimeError: pass
        _record_undo(_toggle, _toggle)

    def _rotate_cw(self):
        item = self._item
        if not hasattr(item, 'rotateCW'):
            return
        old_rot, old_pos = item._rotation, item.pos()
        item.rotateCW()
        new_rot, new_pos = item._rotation, item.pos()
        def _undo():
            try: item._rotation = old_rot; item.updateSize(); item.move(old_pos)
            except RuntimeError: pass
        def _redo():
            try: item._rotation = new_rot; item.updateSize(); item.move(new_pos)
            except RuntimeError: pass
        _record_undo(_undo, _redo)

    def _rotate_ccw(self):
        item = self._item
        if not hasattr(item, 'rotateCCW'):
            return
        old_rot, old_pos = item._rotation, item.pos()
        item.rotateCCW()
        new_rot, new_pos = item._rotation, item.pos()
        def _undo():
            try: item._rotation = old_rot; item.updateSize(); item.move(old_pos)
            except RuntimeError: pass
        def _redo():
            try: item._rotation = new_rot; item.updateSize(); item.move(new_pos)
            except RuntimeError: pass
        _record_undo(_undo, _redo)

    def _open_crop(self):
        if hasattr(self._item, 'enterCropMode'):
            self._crop_editing = True
            self._trim_editing = False
            self._item.enterCropMode()
            self._refresh_edit_btns()

    def _open_trim(self):
        if hasattr(self._item, 'enterTrimMode'):
            self._trim_editing = True
            self._crop_editing = False
            self._item.enterTrimMode()
            self._refresh_edit_btns()

    def _refresh_edit_btns(self):
        if self._crop_btn:
            self._crop_btn.update()
        if self._trim_btn:
            self._trim_btn.update()

    def _is_cropped(self):
        c = getattr(self._item, '_crop', None)
        return bool(c and c != [0.0, 0.0, 1.0, 1.0])

    def _is_trimmed(self):
        item = self._item
        fc = getattr(item, 'frame_count', 1)
        ts = getattr(item, '_trim_start', 0)
        te = getattr(item, '_trim_end', fc - 1)
        return ts != 0 or te != fc - 1

    def _reset_crop(self):
        if hasattr(self._item, 'resetCrop'):
            self._item.resetCrop()
        self.refresh()

    def _reset_trim(self):
        if hasattr(self._item, 'resetTrim'):
            self._item.resetTrim()
        self.refresh()

    def _toggle_pin(self):
        _z_toggle_always_on_top(self._item)
        self._pin_btn.update()

    def _draw_pin(self, p, rect, hovered):
        """압정(thumbtack) 아이콘 — 항상 위 고정."""
        pinned = getattr(self._item, '_z_always_on_top', False)
        if pinned:
            col = QColor(255, 50, 50)          # 고정됨: 빨강
        elif hovered:
            col = QColor(220, 220, 220, 230)   # 호버: 밝은 회색
        else:
            col = QColor(160, 160, 160, 170)   # 기본: 회색

        cx = float(rect.center().x())
        # 아이콘을 살짝 아래로 내림
        top = float(rect.top()) + 4.5

        # ── 1. 납작한 머리 디스크 (압정 상단) ──
        p.setPen(QPen(col, 1.2))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 210))
        p.drawEllipse(QRectF(cx - 5.5, top, 11, 5))

        # 고정 시: 가운데 구멍 표시
        if pinned:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawEllipse(QPointF(cx, top + 2.5), 1.4, 1.4)

        # ── 2. 샤프트 (가는 직사각형) ──
        shaft_top = top + 5.0
        shaft_h   = 4.5
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRect(QRectF(cx - 1.4, shaft_top, 2.8, shaft_h))

        # ── 3. 날카로운 바늘 끝 (삼각형) ──
        needle_base = shaft_top + shaft_h
        needle_tip  = needle_base + 6.0   # 길고 뾰족하게
        tip_poly = QPolygonF([
            QPointF(cx - 2.0, needle_base),
            QPointF(cx + 2.0, needle_base),
            QPointF(cx,       needle_tip),
        ])
        p.setBrush(col)
        p.drawPolygon(tip_poly)

    def _draw_reset_crop(self, p, rect, hovered):
        """작은 ↺ 아이콘 — 자르기 리셋 버튼. 항상 주황 계열."""
        col = QColor(255, 130, 30, 255) if hovered else QColor(255, 150, 40, 200)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        r = 4.2
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 40 * 16, 290 * 16)
        # 화살표 끝
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        tip_x = cx + r * math.cos(math.radians(40))
        tip_y = cy - r * math.sin(math.radians(40))
        arrow = QPolygonF([QPointF(tip_x, tip_y - 2.5),
                           QPointF(tip_x + 2.5, tip_y + 1.0),
                           QPointF(tip_x - 1.5, tip_y + 1.8)])
        p.drawPolygon(arrow)

    def _draw_reset_trim(self, p, rect, hovered):
        """작은 ↺ 아이콘 — 구간 리셋 버튼. 항상 파란 계열."""
        col = QColor(60, 140, 255, 255) if hovered else QColor(80, 160, 255, 200)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        r = 4.2
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 40 * 16, 290 * 16)
        # 화살표 끝
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        tip_x = cx + r * math.cos(math.radians(40))
        tip_y = cy - r * math.sin(math.radians(40))
        arrow = QPolygonF([QPointF(tip_x, tip_y - 2.5),
                           QPointF(tip_x + 2.5, tip_y + 1.0),
                           QPointF(tip_x - 1.5, tip_y + 1.8)])
        p.drawPolygon(arrow)

    def _draw_lock(self, p, rect, hovered):
        locked = self._is_locked()
        col = QColor(255, 170, 40) if locked else (QColor(255, 255, 255, 220) if hovered else QColor(190, 190, 190, 180))
        cx, cy = rect.center().x(), rect.center().y()
        # Shackle arc
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        arc_rect = QRectF(cx - 3.5, cy - 6.5, 7, 6)
        if locked:
            p.drawArc(arc_rect, 0, 180 * 16)
        else:
            p.drawArc(QRectF(cx - 3.5, cy - 7.5, 7, 6), 0, 180 * 16)
        # Body
        p.setPen(QPen(col, 1.2))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 180))
        p.drawRoundedRect(QRectF(cx - 5, cy - 1.5, 10, 7.5), 1.5, 1.5)
        # Keyhole dot
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawEllipse(QPointF(cx, cy + 2.5), 1.5, 1.5)

    def _draw_flip_h(self, p, rect, hovered):
        """H-flip: two filled inward-pointing triangles (▷◁) with dotted vertical center line."""
        col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        # Left triangle ▷ (pointing right, toward center)
        poly_l = QPolygonF([QPointF(cx - 7, cy - 4.5), QPointF(cx - 1.5, cy), QPointF(cx - 7, cy + 4.5)])
        p.drawPolygon(poly_l)
        # Right triangle ◁ (pointing left, toward center)
        poly_r = QPolygonF([QPointF(cx + 7, cy - 4.5), QPointF(cx + 1.5, cy), QPointF(cx + 7, cy + 4.5)])
        p.drawPolygon(poly_r)
        # Dotted vertical center line
        pen = QPen(col, 1.3, Qt.DotLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy - 5.5), QPointF(cx, cy + 5.5))

    def _draw_flip_v(self, p, rect, hovered):
        """V-flip: two filled inward-pointing triangles (▽△) with dotted horizontal center line."""
        col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        # Top triangle △ (pointing down, toward center)
        poly_t = QPolygonF([QPointF(cx - 4.5, cy - 7), QPointF(cx, cy - 1.5), QPointF(cx + 4.5, cy - 7)])
        p.drawPolygon(poly_t)
        # Bottom triangle ▽ (pointing up, toward center)
        poly_b = QPolygonF([QPointF(cx - 4.5, cy + 7), QPointF(cx, cy + 1.5), QPointF(cx + 4.5, cy + 7)])
        p.drawPolygon(poly_b)
        # Dotted horizontal center line
        pen = QPen(col, 1.3, Qt.DotLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - 5.5, cy), QPointF(cx + 5.5, cy))

    def _draw_rot_cw(self, p, rect, hovered):
        """Clockwise rotation icon: 3/4 arc with arrowhead."""
        col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        pen = QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = 4.5
        # Arc starts at top (90°), sweeps CW (-270°), ends at left (9 o'clock)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 90 * 16, -270 * 16)
        # Arrowhead at end (left side, pointing downward = CW direction)
        ax, ay = cx - r, cy
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        arrow = QPolygonF([QPointF(ax, ay + 3.5), QPointF(ax - 2.5, ay - 1.5), QPointF(ax + 2.5, ay - 1.5)])
        p.drawPolygon(arrow)

    def _draw_rot_ccw(self, p, rect, hovered):
        """Counter-clockwise rotation icon: 3/4 arc with arrowhead."""
        col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        pen = QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = 4.5
        # Arc starts at top (90°), sweeps CCW (+270°), ends at right (3 o'clock)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 90 * 16, 270 * 16)
        # Arrowhead at end (right side, pointing downward = CCW direction)
        ax, ay = cx + r, cy
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        arrow = QPolygonF([QPointF(ax, ay + 3.5), QPointF(ax - 2.5, ay - 1.5), QPointF(ax + 2.5, ay - 1.5)])
        p.drawPolygon(arrow)

    def _draw_crop(self, p, rect, hovered):
        """Crop icon: dashed rectangle frame with L-bracket corners. Active=orange."""
        active = self._is_cropped() or self._crop_editing
        if active:
            col = QColor(255, 170, 60, 255) if hovered else QColor(255, 150, 40, 220)
        else:
            col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        hw, hh = 5.5, 5.5
        arm = 3.0
        # Dashed inner rect
        pen = QPen(col, 1.0, Qt.DashLine, Qt.RoundCap)
        pen.setDashPattern([2, 2])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx - hw + arm, cy - hh + arm, (hw - arm) * 2, (hh - arm) * 2))
        # Corner L-brackets
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        corners = [
            (cx - hw, cy - hh, arm, 0,   0,   arm),   # TL
            (cx + hw, cy - hh, -arm, 0,  0,   arm),   # TR
            (cx - hw, cy + hh, arm, 0,   0,  -arm),   # BL
            (cx + hw, cy + hh, -arm, 0,  0,  -arm),   # BR
        ]
        for x, y, dx1, dy1, dx2, dy2 in corners:
            p.drawLine(QPointF(x, y), QPointF(x + dx1, y + dy1))
            p.drawLine(QPointF(x, y), QPointF(x + dx2, y + dy2))
        # Active indicator dot (bottom center)
        if active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 150, 40, 230))
            p.drawEllipse(QPointF(cx, rect.bottom() - 3.5), 2.5, 2.5)

    def _draw_trim(self, p, rect, hovered):
        """Trim icon: timeline bar with start/end handle markers. Active=blue."""
        active = self._is_trimmed() or self._trim_editing
        if active:
            col = QColor(80, 170, 255, 240) if hovered else QColor(60, 150, 255, 200)
            bar_col = QColor(80, 170, 255, 100)
        else:
            col = QColor(255, 255, 255, 230) if hovered else QColor(195, 195, 195, 180)
            bar_col = QColor(195, 195, 195, 60)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        # Timeline bar
        p.setPen(QPen(bar_col, 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy))
        # Start marker (left vertical line + top nub)
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx - 4, cy + 4))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(cx - 4, cy - 4),
                                  QPointF(cx - 4 + 3, cy - 1.5),
                                  QPointF(cx - 4, cy + 1)]))
        # End marker (right vertical line + top nub pointing left)
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx + 4, cy + 4))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(cx + 4, cy - 4),
                                  QPointF(cx + 4 - 3, cy - 1.5),
                                  QPointF(cx + 4, cy + 1)]))
        # Active indicator dot (bottom center)
        if active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(60, 150, 255, 230))
            p.drawEllipse(QPointF(cx, rect.bottom() - 3.5), 2.5, 2.5)

    def showEvent(self, e):
        super().showEvent(e)
        self.raise_()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(QColor(18, 18, 22, 210))
        r = self.height() / 2.0
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), r, r)


class _HoverBarBtn(QPushButton):
    """Icon button cell used inside _ItemHoverBar."""
    def __init__(self, bar, draw_fn, parent=None, size=None):
        super().__init__(parent)
        self._bar = bar
        self._draw_fn = draw_fn
        self._hovered = False
        sz = size if size is not None else _ItemHoverBar._BTN
        self.setFixedSize(sz, sz)
        self.setStyleSheet('background:transparent;border:none;')
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def event(self, e):
        if e.type() == QEvent.HoverEnter:
            self._hovered = True; self.update()
        elif e.type() == QEvent.HoverLeave:
            self._hovered = False; self.update()
        return super().event(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hovered:
            p.setBrush(QColor(255, 255, 255, 28))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(1, 1, self.width()-2, self.height()-2), 3, 3)
        self._draw_fn(p, self.rect(), self._hovered)


# ── LockButton ─────────────────────────────────────────────────────────────────

class LockButton(QPushButton):
    """Lock toggle (padlock icon) for layer rows."""
    def __init__(self, item, panel, parent=None):
        super().__init__(parent)
        self._item = item
        self._panel = panel
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background:transparent;border:none;')
        self.clicked.connect(self._toggle)

    def _is_locked(self):
        return id(self._item) in self._panel._locked

    def _toggle(self):
        self._panel.toggle_item_lock(self._item)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        locked = self._is_locked()
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if locked:
            body_color = QColor(255, 160, 30)
            shackle_color = QColor(255, 160, 30)
        else:
            body_color = QColor(80, 80, 80)
            shackle_color = QColor(70, 70, 70)
        # Draw shackle (top arc)
        p.setPen(QPen(shackle_color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        shackle_rect = QRectF(cx - 3.5, cy - 6.5, 7.0, 7.0)
        if locked:
            p.drawArc(shackle_rect, 0 * 16, 180 * 16)
        else:
            # Open shackle — rotated/lifted up
            p.drawArc(QRectF(cx - 3.5, cy - 8.0, 7.0, 7.0), 0 * 16, 180 * 16)
        # Draw body (rectangle)
        p.setPen(Qt.NoPen)
        p.setBrush(body_color)
        p.drawRoundedRect(QRectF(cx - 4.5, cy - 1.5, 9.0, 7.5), 1.5, 1.5)
        # Keyhole
        p.setBrush(QColor(20, 20, 20, 180))
        p.drawEllipse(QPointF(cx, cy + 1.5), 1.5, 1.5)


# ── LayerPanelDragFilter ───────────────────────────────────────────────────────
# App-level filter installed once at startup. Intercepts all mouse events so
# child-widget event propagation issues cannot block drag detection.

class _LayerPanelDragFilter(QObject):
    def __init__(self, panel):
        super().__init__()
        self._panel    = panel
        self._item     = None   # item being dragged
        self._group    = None   # group the item belongs to (None = ungrouped)
        self._dragging = False
        self._start_y  = 0

    def _viewport(self):
        return self._panel._scroll.viewport()

    def _global_in_panel(self, gpos):
        vp = self._viewport()
        return QRect(vp.mapToGlobal(QPoint(0, 0)), vp.size()).contains(gpos)

    def eventFilter(self, obj, event):
        t = event.type()

        if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            gpos = QCursor.pos()
            if self._global_in_panel(gpos):
                vp_pos = self._viewport().mapFromGlobal(gpos)
                list_y = self._panel._list.mapFrom(self._viewport(), vp_pos).y()
                result = self._panel._item_at_list_y(list_y)
                if result is not None:
                    self._item, self._group = result
                    self._dragging = False
                    self._start_y  = gpos.y()
                    self._panel._refresh_timer.stop()

        elif t == QEvent.MouseMove:
            if self._item is not None and event.buttons() & Qt.LeftButton:
                gpos = QCursor.pos()
                if not self._dragging and abs(gpos.y() - self._start_y) > 6:
                    self._dragging = True
                    self._panel._drop_indicator.raise_()
                    self._panel._drop_indicator.show()
                if self._dragging:
                    self._panel._update_drop_indicator(gpos, self._group)

        elif t == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._item is not None:
                if self._dragging:
                    gpos   = QCursor.pos()
                    list_y = self._panel._list.mapFromGlobal(gpos).y()
                    self._panel._do_reorder(self._item, self._group, list_y)
                self._item     = None
                self._group    = None
                self._dragging = False
                self._panel._drop_indicator.hide()
                self._panel._refresh_timer.start(250)

        return False


# ── LayerRow ───────────────────────────────────────────────────────────────────

class LayerRow(QWidget):
    THUMB = 18

    def __init__(self, item, canvas, panel, indent=False, parent=None):
        super().__init__(parent)
        self._item = item
        self._canvas = canvas
        self._panel = panel
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)
        self._hovered = False
        self._pressed = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8 + (14 if indent else 0), 0, 6, 0)
        lay.setSpacing(3)
        eye = EyeButton(item, panel, self)
        lay.addWidget(eye)
        lock = LockButton(item, panel, self)
        lay.addWidget(lock)

        thumb = QLabel()
        thumb.setFixedSize(self.THUMB, self.THUMB)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setScaledContents(True)
        try:
            if isinstance(item, ImageItem):
                thumb.setPixmap(item.pixmap.scaled(self.THUMB, self.THUMB,
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
            elif isinstance(item, GifItem):
                img = item.movie.currentImage()
                if not img.isNull():
                    thumb.setPixmap(QPixmap.fromImage(img).scaled(
                        self.THUMB, self.THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            elif isinstance(item, VideoItem):
                thumb.setText('▶')
                thumb.setStyleSheet('color:#aaa;background:#333;font-size:8px;')
            elif isinstance(item, TextItem):
                thumb.setText('T')
                thumb.setStyleSheet('color:#fff;background:#555;font-weight:bold;font-size:9px;')
            else:
                thumb.setText('?')
        except Exception:
            thumb.setText('?')
        lay.addWidget(thumb)

        name = ''
        if hasattr(item, 'file_path'):
            name = os.path.basename(item.file_path)
        elif isinstance(item, TextItem):
            name = item._label.text() or 'Text'
        self._lbl = QLabel(name)
        self._lbl.setFont(get_ui_font(7))
        self._lbl.setStyleSheet('color:#cccccc;background:transparent;')
        lay.addWidget(self._lbl)
        lay.addStretch()
        self._update_bg()

    def _is_eye_hidden(self):
        return id(self._item) in self._panel._eye_hidden

    def _update_bg(self):
        if self._is_eye_hidden():
            self.setStyleSheet('background:#484848;')
            self._lbl.setStyleSheet('color:#888888;background:transparent;')
        elif self._item.is_selected:
            self.setStyleSheet('background:#2a4a70;')
            self._lbl.setStyleSheet('color:#ffffff;background:transparent;')
        elif self._pressed:
            self.setStyleSheet('background:rgba(0,0,0,50);')
            self._lbl.setStyleSheet('color:#cccccc;background:transparent;')
        elif self._hovered:
            self.setStyleSheet('background:#383838;')
            self._lbl.setStyleSheet('color:#cccccc;background:transparent;')
        else:
            self.setStyleSheet('background:transparent;')
            self._lbl.setStyleSheet('color:#aaaaaa;background:transparent;')

    def event(self, e):
        if e.type() == QEvent.HoverEnter:
            self._hovered = True; self._update_bg()
        elif e.type() == QEvent.HoverLeave:
            self._hovered = False; self._update_bg()
        return super().event(e)

    def mousePressEvent(self, e):
        self._pressed = True
        self._update_bg()
        if e.button() == Qt.LeftButton:
            shift = bool(e.modifiers() & Qt.ShiftModifier)
            ctrl  = bool(e.modifiers() & Qt.ControlModifier)
            if not (shift or ctrl):
                self._canvas.deselectAll()
            self._item.select(additive=(shift or ctrl))
            self._update_bg()

    def mouseReleaseEvent(self, e):
        self._pressed = False
        self._update_bg()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._canvas.center_on_item(self._item)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        if hasattr(self._item, 'setItemOpacity'):
            op_menu = menu.addMenu('불투명도')
            op_menu.setStyleSheet(MENU_STYLE)
            for label, val in [('100%', 1.0), ('75%', 0.75), ('50%', 0.5), ('25%', 0.25)]:
                op_menu.addAction(label, lambda v=val: self._item.setItemOpacity(v))
            op_menu.addAction('사용자정의…', self._set_custom_opacity)
            menu.addSeparator()
        menu.addAction('삭제', self._delete_item)
        menu.exec_(e.globalPos())

    def _set_custom_opacity(self):
        from PyQt5.QtWidgets import QInputDialog
        cur = int(getattr(self._item, '_opacity', 1.0) * 100)
        val, ok = QInputDialog.getInt(self, '불투명도', '불투명도 (0–100):', cur, 0, 100, 1)
        if ok and hasattr(self._item, 'setItemOpacity'):
            self._item.setItemOpacity(val / 100.0)

    def _delete_item(self):
        mw = self._canvas.main_window
        if hasattr(mw, '_deleteSelected'):
            self._canvas.deselectAll()
            self._item.is_selected = True
            if hasattr(self._item, '_refreshBorder'):
                self._item._refreshBorder()
            else:
                self._item.update()
            self._canvas.addToSelection(self._item)
            mw._deleteSelected()


_GROUP_DRAG = object()   # sentinel: 그룹 헤더 드래그를 나타내는 고유 객체

# ── LayerGroupRow ──────────────────────────────────────────────────────────────

class _LayerFolderIcon(QWidget):
    """레이어 패널 그룹 행용 소형 폴더 아이콘 (비대화형). open=열림 상태."""
    def __init__(self, open_state=False, parent=None):
        super().__init__(parent)
        self._open = open_state
        self.setFixedSize(14, 12)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(232, 232, 236, 255)   # 상시 밝은 흰색 계열
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        if not self._open:
            # ── 닫힌 폴더 ──────────────────────────
            p.drawRoundedRect(QRectF(0, 1.5, 5.5, 3.0), 1.0, 1.0)  # 탭
            p.drawRoundedRect(QRectF(0, 3.5, 14.0, 7.5), 1.2, 1.2) # 몸통
        else:
            # ── 열린 폴더 ──────────────────────────
            # ① 뒤쪽 판 (넓은 탭 + 백 패널)
            p.drawRoundedRect(QRectF(0, 0.0, 9.5, 2.8), 1.0, 1.0)  # 넓은 탭
            p.drawRoundedRect(QRectF(0, 1.8, 14.0, 9.2), 1.2, 1.2) # 백 패널
            # ② 열린 내부 (어두운 깊이감)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(QRectF(1.2, 3.2, 11.6, 3.8), 0.8, 0.8)
            # ③ 앞쪽 패널 (하단만 덮음 — 뚜껑이 열린 것처럼)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(0, 6.5, 14.0, 4.5), 1.2, 1.2)


class LayerGroupRow(QWidget):
    def __init__(self, group, canvas, collapsed, toggle_fn, parent=None, panel=None):
        super().__init__(parent)
        self._group = group
        self._canvas = canvas
        self._toggle_fn = toggle_fn
        self._panel = panel
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        # paintEvent로 배경 직접 제어
        self.setStyleSheet('background:transparent;')

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(0)

        eye = EyeButton(group, panel, self, is_group=True)
        lay.addWidget(eye)
        lay.addSpacing(4)

        # 축소/확장 화살표
        self._icon = QLabel('›' if collapsed else '⌄')
        self._icon.setStyleSheet(
            'color:#666672;background:transparent;'
            'font-size:13px;font-weight:300;')
        self._icon.setFixedWidth(10)
        self._icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._icon)
        lay.addSpacing(5)

        # 폴더 아이콘 (열림/닫힘 상태 반영)
        lay.addWidget(_LayerFolderIcon(open_state=not collapsed, parent=self))
        lay.addSpacing(5)

        # 그룹 이름
        name = group.group_name
        lbl = QLabel(name)
        lbl.setFont(get_ui_font(9))
        lbl.setStyleSheet('color:#b0b0b8;background:transparent;')
        lbl.setMinimumWidth(0)
        lay.addWidget(lbl, 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 배경 — 순수 흑백 (채도 없음)
        if self._group.is_selected:
            p.fillRect(self.rect(), QColor(0x28, 0x28, 0x28))
        else:
            p.fillRect(self.rect(), QColor(0x1c, 0x1c, 0x1c))
        # 좌측 그룹 컬러 액센트 바
        accent = QColor(self._group._color)
        accent.setAlpha(170)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(0, 3, 2.5, self.height() - 6), 1.2, 1.2)
        # 하단 구분선
        p.setPen(QPen(QColor(255, 255, 255, 10), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._toggle_fn()
            self._canvas.deselectAll()
            self._group.select()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._canvas.center_on_item(self._group)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        menu.addAction('삭제 (박스만)', self._delete_group_only)
        menu.addAction('삭제 (멤버 포함)', self._delete_group_with_members)
        menu.exec_(e.globalPos())

    def _delete_group_only(self):
        mw = self._canvas.main_window
        if hasattr(mw, '_deleteSelected'):
            self._canvas.deselectAll()
            self._group.is_selected = True
            self._group.update()
            self._canvas.addToSelection(self._group)
            mw._deleteSelected()

    def _delete_group_with_members(self):
        QTimer.singleShot(0, self._group._deleteWithMembers)


# ── RenameFilter (module-level QObject subclass – must NOT be defined inside a
#    function, otherwise PyQt5 GC will collect the class and crash on callback) ─

class _RenameFilter(QObject):
    """Event filter for inline rename QLineEdit in VerticalTabBar."""
    def __init__(self, le, finish_fn):
        super().__init__(le)   # parent = le, so lifetime is tied to le
        self._le       = le
        self._finish   = finish_fn

    def eventFilter(self, obj, event):
        if obj is self._le:
            if event.type() == QEvent.FocusOut:
                self._finish(True)
            elif event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._finish(True)
                    return True
                elif event.key() == Qt.Key_Escape:
                    self._finish(False)
                    return True
        return False


class _GlobalClickWatcher(QObject):
    """App-level filter: commits tab rename when user clicks outside the QLineEdit.
    Needed because widgets like CanvasWidget use Qt.NoFocus and never trigger FocusOut."""
    def __init__(self, le, finish_fn):
        super().__init__()
        self._le     = le
        self._finish = finish_fn

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and obj is not self._le:
            self._finish(True)
        return False


# ── VerticalTabBar ─────────────────────────────────────────────────────────────

class VerticalTabBar(QWidget):
    """Vertical bookmark-style tab strip between layer panel and canvas."""
    tab_changed            = pyqtSignal(int)
    tab_added              = pyqtSignal()
    tab_closed             = pyqtSignal(int)
    tab_renamed            = pyqtSignal(int, str)
    tab_moved              = pyqtSignal(int, int)
    panel_toggle_requested = pyqtSignal()

    W          = 22   # strip width
    TAB_H      = 55   # height per tab
    ADD_H      = 24   # height of '+' button
    TOP_MARGIN = 24   # reserved for overlay titlebar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.W)
        self.setMouseTracking(True)
        self._names       = []
        self._active      = 0
        self._hover       = -1
        self._rename_le   = None
        self._layer_open  = True
        self._drag_idx    = -1
        self._drag_start_y = 0
        self._drag_y      = 0
        self._dragging    = False

    def setTabs(self, names, active):
        self._names  = list(names)
        self._active = active
        self.update()

    def set_layer_panel_state(self, open_state):
        self._layer_open = open_state
        self.update()

    # ── geometry helpers ──────────────────────────────────────────────────────

    def _tab_rect(self, i):
        return QRect(0, self.TOP_MARGIN + i * self.TAB_H, self.W, self.TAB_H)

    def _add_rect(self):
        return QRect(0, self.TOP_MARGIN + len(self._names) * self.TAB_H,
                     self.W, self.ADD_H)

    def _idx_at(self, pos):
        if pos.y() < self.TOP_MARGIN:
            return -2   # header / panel-toggle zone
        for i in range(len(self._names)):
            if self._tab_rect(i).contains(pos):
                return i
        if self._add_rect().contains(pos):
            return len(self._names)
        return -1

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0x18, 0x18, 0x18))

        # Dark header strip (same height as overlay titlebar)
        p.fillRect(QRect(0, 0, self.W, self.TOP_MARGIN), QColor(0x10, 0x10, 0x10))

        # Header: layer panel toggle icon
        cx = self.W // 2
        hov_hdr = (self._hover == -2)
        if hov_hdr:
            p.fillRect(QRect(0, 0, self.W, self.TOP_MARGIN), QColor(0x22, 0x22, 0x22))
        # 레이어 아이콘 — 열림: 채워진 사각형 / 닫힘: 외곽선만
        col_back  = QColor(0x90, 0x90, 0x90) if hov_hdr else QColor(0x68, 0x68, 0x68)
        col_mid   = QColor(0xb4, 0xb4, 0xb4) if hov_hdr else QColor(0x90, 0x90, 0x90)
        col_front = QColor(0xee, 0xee, 0xee) if hov_hdr else QColor(0xc4, 0xc4, 0xc4)
        if self._layer_open:
            # 패널 열림 → 채워진 사각형 (활성 상태)
            p.setPen(Qt.NoPen)
            p.setBrush(col_back)
            p.drawRoundedRect(QRectF(8.5,  6.0, 8.5, 4.5), 1.1, 1.1)
            p.setBrush(col_mid)
            p.drawRoundedRect(QRectF(7.0, 10.0, 8.5, 4.5), 1.1, 1.1)
            p.setBrush(col_front)
            p.drawRoundedRect(QRectF(5.5, 14.0, 8.5, 4.5), 1.1, 1.1)
        else:
            # 패널 닫힘 → 외곽선만 (비활성 상태)
            bg_fill = QColor(0x22, 0x22, 0x22) if hov_hdr else QColor(0x10, 0x10, 0x10)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col_back, 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(QRectF(8.5,  6.0, 8.5, 4.5), 1.1, 1.1)
            p.setBrush(bg_fill)
            p.setPen(QPen(col_mid, 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(QRectF(7.0, 10.0, 8.5, 4.5), 1.1, 1.1)
            p.setBrush(bg_fill)
            p.setPen(QPen(col_front, 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(QRectF(5.5, 14.0, 8.5, 4.5), 1.1, 1.1)

        # Right-edge divider toward canvas
        p.setPen(QPen(QColor(0x30, 0x30, 0x30), 1))
        p.drawLine(self.W - 1, 0, self.W - 1, self.height())

        font = get_ui_font(9)
        p.setFont(font)
        fm = QFontMetrics(font)

        for i, name in enumerate(self._names):
            r      = self._tab_rect(i)
            active = (i == self._active)
            hover  = (i == self._hover)

            # Background
            is_dragged = (self._dragging and i == self._drag_idx)
            if is_dragged:
                p.fillRect(r, QColor(0x00, 0x78, 0xd4, 80))
            elif active:
                p.fillRect(r, QColor(0x28, 0x28, 0x28))
                # Right accent bar (toward canvas)
                p.fillRect(QRect(self.W - 3, r.y(), 3, r.height()),
                           QColor(0x00, 0x78, 0xd4))
            elif hover:
                p.fillRect(r, QColor(0x20, 0x20, 0x20))

            # Separator
            p.setPen(QPen(QColor(0x2c, 0x2c, 0x2c), 1))
            p.drawLine(3, r.bottom(), self.W - 4, r.bottom())

            # Rotated label (reads bottom-to-top — natural for left-side tabs)
            display = name if len(name) <= 5 else name[:4] + '…'
            color = (QColor(0xff, 0xff, 0xff) if active else
                     QColor(0xcc, 0xcc, 0xcc) if hover else
                     QColor(0x66, 0x66, 0x66))
            p.setPen(color)
            p.save()
            cx = r.center().x()
            cy = r.center().y()
            p.translate(cx, cy)
            p.rotate(-90)
            tw = fm.horizontalAdvance(display)
            p.drawText(-tw // 2, fm.ascent() // 2, display)
            p.restore()

        # '+' add button
        ar = self._add_rect()
        if self._hover == len(self._names):
            p.fillRect(ar, QColor(0x20, 0x20, 0x20))
        col = (QColor(0xaa, 0xaa, 0xaa) if self._hover == len(self._names)
               else QColor(0x44, 0x44, 0x44))
        p.setPen(col)
        p.setFont(get_ui_font(11))
        p.drawText(ar, Qt.AlignCenter, '+')

        # Drop indicator while dragging
        if self._dragging and self._drag_idx >= 0:
            drop = self._drop_idx_at(self._drag_y)
            if drop < self._drag_idx:
                line_y = self.TOP_MARGIN + drop * self.TAB_H
            else:
                line_y = self.TOP_MARGIN + (drop + 1) * self.TAB_H
            p.setPen(QPen(QColor(0x00, 0x78, 0xd4), 2))
            p.drawLine(1, line_y, self.W - 1, line_y)

    # ── events ────────────────────────────────────────────────────────────────

    def mouseMoveEvent(self, e):
        if self._drag_idx >= 0 and e.buttons() & Qt.LeftButton:
            self._drag_y = e.pos().y()
            if not self._dragging and abs(self._drag_y - self._drag_start_y) > 5:
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self.update()
                return
        h = self._idx_at(e.pos())
        if h != self._hover:
            self._hover = h
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag_idx >= 0:
            if self._dragging:
                drop = self._drop_idx_at(e.pos().y())
                if drop != self._drag_idx:
                    self.tab_moved.emit(self._drag_idx, drop)
            self._drag_idx  = -1
            self._dragging  = False
            self.unsetCursor()
            self.update()

    def _drop_idx_at(self, y):
        rel = y - self.TOP_MARGIN
        idx = int(rel // self.TAB_H)
        return max(0, min(idx, len(self._names) - 1))

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            idx = self._idx_at(e.pos())
            if idx == -2:
                self.panel_toggle_requested.emit()
            elif 0 <= idx < len(self._names):
                self._drag_idx     = idx
                self._drag_start_y = e.pos().y()
                self._drag_y       = e.pos().y()
                self._dragging     = False
                self.tab_changed.emit(idx)
            elif idx == len(self._names):
                self.tab_added.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            idx = self._idx_at(e.pos())
            if 0 <= idx < len(self._names):
                self._start_rename(idx)

    def contextMenuEvent(self, e):
        idx = self._idx_at(e.pos())
        if 0 <= idx < len(self._names):
            menu = QMenu(self)
            menu.setStyleSheet(MENU_STYLE)
            menu.addAction('이름 변경').triggered.connect(
                lambda: self._start_rename(idx))
            act = menu.addAction('탭 닫기')
            act.setEnabled(len(self._names) > 1)
            act.triggered.connect(lambda: self.tab_closed.emit(idx))
            menu.exec_(e.globalPos())

    def _start_rename(self, idx):
        if self._rename_le:
            self._rename_le.hide()
            self._rename_le.deleteLater()
            self._rename_le = None
        if idx >= len(self._names):
            return
        current = self._names[idx]
        r = self._tab_rect(idx)
        par = self.parent()
        if not par:
            return
        origin = self.mapTo(par, QPoint(self.W + 2, r.center().y() - 14))
        le = QLineEdit(current, par)
        le.setGeometry(origin.x(), origin.y(), 120, 28)
        le.setStyleSheet(
            'background:#1a1a1a;color:#fff;border:1px solid #0078d4;'
            'font-size:11px;padding:0 4px;')
        le.selectAll()
        le.show()
        le.raise_()
        le.setFocus()
        self._rename_le = le

        # Use [done] list to guard against double-commit (FocusOut + Return + global click)
        _done  = [False]
        _gcw   = [None]   # holds _GlobalClickWatcher so it isn't GC'd

        def _finish(save):
            if _done[0]:
                return
            _done[0] = True
            if _gcw[0] is not None:
                QApplication.instance().removeEventFilter(_gcw[0])
                _gcw[0] = None
            if self._rename_le is le:
                self._rename_le = None
            le.hide()
            QTimer.singleShot(0, le.deleteLater)
            if save:
                self.tab_renamed.emit(idx, le.text().strip() or current)

        # Direct focusOutEvent override for focus-aware widgets (layer panel rows etc.)
        def _focus_out(event):
            _finish(True)
            QLineEdit.focusOutEvent(le, event)
        le.focusOutEvent = _focus_out

        # App-level click watcher — catches clicks on NoFocus widgets like CanvasWidget
        gcw = _GlobalClickWatcher(le, _finish)
        _gcw[0] = gcw
        QApplication.instance().installEventFilter(gcw)

        le.installEventFilter(_RenameFilter(le, _finish))


# ── LayerPanel ─────────────────────────────────────────────────────────────────

class LayerPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self.setFixedWidth(180)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet('QWidget{background:#1c1c1c;}')
        self._collapsed = set()
        self._eye_hidden = set()
        self._eye_hidden_groups = set()
        self._locked = set()

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # Header with subtle gradient
        _hdr = QWidget()
        _hdr.setFixedHeight(26)
        _hdr.setAttribute(Qt.WA_StyledBackground, True)
        _hdr.setStyleSheet(
            'QWidget {'
            '  background: qlineargradient('
            '    x1:0, y1:0, x2:0, y2:1,'
            '    stop:0 #1e1e1e, stop:1 #161616'
            '  );'
            '}')
        _hdr_lay = QHBoxLayout(_hdr)
        _hdr_lay.setContentsMargins(11, 0, 8, 0)
        _hdr_lay.setSpacing(0)
        _title_lbl = QLabel('LAYER')
        _title_lbl.setStyleSheet(
            'color:#606068;'
            'font-size:9px;'
            'font-weight:600;'
            'letter-spacing:2px;'
            'font-family:"Segoe UI","Malgun Gothic",sans-serif;'
            'background:transparent;')
        _hdr_lay.addWidget(_title_lbl)
        main_lay.addWidget(_hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            'QScrollArea{border:none;background:transparent;}'
            'QScrollBar:vertical{width:6px;background:transparent;border:none;}'
            'QScrollBar::handle:vertical{background:#404040;border-radius:3px;min-height:20px;}'
            'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}')
        self._list = QWidget()
        self._list.setStyleSheet('background:transparent;')
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._list)
        main_lay.addWidget(self._scroll)

        self._last_sig = None
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_refresh)
        self._refresh_timer.start(250)

        self._drop_indicator = QWidget(self._scroll.viewport())
        self._drop_indicator.setFixedHeight(2)
        self._drop_indicator.setStyleSheet('background:#0078d4;')
        self._drop_indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._drop_indicator.hide()

        self._drag_filter = _LayerPanelDragFilter(self)
        QApplication.instance().installEventFilter(self._drag_filter)

    def _toggle_group(self, g):
        gid = id(g)
        if gid in self._collapsed:
            self._collapsed.discard(gid)
        else:
            self._collapsed.add(gid)
        self.refresh()

    def toggle_group_visibility(self, group):
        gid = id(group)
        if gid in self._eye_hidden_groups:
            self._eye_hidden_groups.discard(gid)
            group.show()
            for m in group.member_items:
                self._eye_hidden.discard(id(m))
                m.show()
        else:
            self._eye_hidden_groups.add(gid)
            group.hide()
            for m in group.member_items:
                self._eye_hidden.add(id(m))
                m.hide()
        QTimer.singleShot(0, self.refresh)

    def toggle_item_visibility(self, item):
        iid = id(item)
        if iid in self._eye_hidden:
            self._eye_hidden.discard(iid)
            item.show()
        else:
            self._eye_hidden.add(iid)
            item.hide()
        QTimer.singleShot(0, self.refresh)

    def toggle_item_lock(self, item):
        iid = id(item)
        if iid in self._locked:
            self._locked.discard(iid)
        else:
            self._locked.add(iid)
        if hasattr(item, '_label'):
            item._label.update()
        QTimer.singleShot(0, self.refresh)

    def _sig(self):
        return (
            tuple(id(i) for i in self._canvas.items),
            tuple(id(g) for g in self._canvas.groups),
            frozenset(id(i) for i in self._canvas.selected_items),
            frozenset(self._eye_hidden | self._eye_hidden_groups),
            frozenset(self._locked),
            tuple(getattr(i, '_opacity', 1.0) for i in self._canvas.items),
        )

    # ── Drag-to-reorder (handled by _LayerPanelDragFilter) ────────────────────

    def _ungrouped_items(self):
        member_ids = set(id(m) for g in self._canvas.groups for m in g.member_items)
        return [it for it in self._canvas.items if id(it) not in member_ids]

    def _groups_section_height(self):
        return sum(
            30 + (len(g.member_items) * 36 if id(g) not in self._collapsed else 0) + 6
            for g in self._canvas.groups
        )

    def _item_at_list_y(self, list_y):
        """list_y 위치의 아이템과 소속 그룹을 반환. (item, group) or None."""
        y = 0
        last = None
        for g in self._canvas.groups:
            if y <= list_y < y + 30:
                return (g, _GROUP_DRAG)  # 그룹 헤더 — 드래그 가능
            y += 30
            if id(g) not in self._collapsed:
                for m in g.member_items:
                    if y <= list_y < y + 36:
                        return (m, g)
                    if list_y >= y:
                        last = (m, g)
                    y += 36
            y += 6  # spacer

        member_ids = set(id(m) for g in self._canvas.groups for m in g.member_items)
        for item in self._canvas.items:
            if id(item) not in member_ids:
                if y <= list_y < y + 36:
                    return (item, None)
                if list_y >= y:
                    last = (item, None)
                y += 36

        return last  # 마지막 행 아래를 클릭한 경우 최근접 아이템 반환

    def _group_members_start_y(self, group):
        """그룹 멤버 행들이 시작하는 list Y 좌표."""
        y = 0
        for g in self._canvas.groups:
            y += 30
            if g is group:
                return y
            if id(g) not in self._collapsed:
                y += len(g.member_items) * 36
            y += 6
        return y

    def _group_start_y(self, idx):
        """groups[idx]가 시작하는 list Y 좌표."""
        y = 0
        for i, g in enumerate(self._canvas.groups):
            if i == idx:
                return y
            y += 30
            if id(g) not in self._collapsed:
                y += len(g.member_items) * 36
            y += 6
        return y

    def _update_drop_indicator(self, gpos, drag_group):
        list_y = self._list.mapFromGlobal(gpos).y()
        if drag_group is _GROUP_DRAG:
            # 그룹 간 위치 계산
            groups = self._canvas.groups
            y, drop_idx = 0, len(groups)
            for i, g in enumerate(groups):
                grp_h = 30 + (len(g.member_items)*36 if id(g) not in self._collapsed else 0) + 6
                if list_y < y + grp_h / 2:
                    drop_idx = i
                    break
                y += grp_h
            line_y = self._group_start_y(drop_idx)
        elif drag_group is not None:
            start_y  = self._group_members_start_y(drag_group)
            rel_y    = list_y - start_y
            drop_idx = max(0, min(int((rel_y + 18) // 36), len(drag_group.member_items)))
            line_y   = start_y + drop_idx * 36
        else:
            ungrouped = self._ungrouped_items()
            rel_y     = list_y - self._groups_section_height()
            drop_idx  = max(0, min(int((rel_y + 18) // 36), len(ungrouped)))
            line_y    = self._groups_section_height() + drop_idx * 36
        scroll_y  = self._scroll.verticalScrollBar().value()
        vp_w      = self._scroll.viewport().width()
        self._drop_indicator.setGeometry(4, max(0, line_y - scroll_y - 1), vp_w - 4, 2)
        self._drop_indicator.raise_()

    def _do_reorder(self, drag_item, drag_group, list_y):
        if drag_group is _GROUP_DRAG:
            # 그룹 헤더 순서 변경
            groups = self._canvas.groups
            if drag_item not in groups:
                return
            src_idx = groups.index(drag_item)
            y, drop_idx = 0, len(groups)
            for i, g in enumerate(groups):
                grp_h = 30 + (len(g.member_items)*36 if id(g) not in self._collapsed else 0) + 6
                if list_y < y + grp_h / 2:
                    drop_idx = i
                    break
                y += grp_h
            if drop_idx > src_idx:
                drop_idx -= 1
            if drop_idx != src_idx:
                groups.remove(drag_item)
                groups.insert(drop_idx, drag_item)
                self.refresh()
            return
        if drag_group is not None:
            # 그룹 멤버 순서 변경
            members  = drag_group.member_items
            start_y  = self._group_members_start_y(drag_group)
            rel_y    = list_y - start_y
            drop_idx = max(0, min(int((rel_y + 18) // 36), len(members)))
            if drag_item in members:
                src_idx = members.index(drag_item)
                if drop_idx != src_idx:
                    members.remove(drag_item)
                    members.insert(min(drop_idx, len(members)), drag_item)
                    self.refresh()
        else:
            # 비그룹 아이템 순서 변경
            ungrouped = self._ungrouped_items()
            if drag_item not in ungrouped:
                return
            src_idx  = ungrouped.index(drag_item)
            rel_y    = list_y - self._groups_section_height()
            drop_idx = max(0, min(int((rel_y + 18) // 36), len(ungrouped)))
            if drop_idx == src_idx:
                return
            self._canvas.items.remove(drag_item)
            member_ids    = set(id(m) for g in self._canvas.groups for m in g.member_items)
            ungrouped_new = [it for it in self._canvas.items if id(it) not in member_ids]
            clamped       = min(drop_idx, len(ungrouped_new))
            if clamped >= len(ungrouped_new):
                insert_at = (self._canvas.items.index(ungrouped_new[-1]) + 1
                             if ungrouped_new else len(self._canvas.items))
            else:
                insert_at = self._canvas.items.index(ungrouped_new[clamped])
            self._canvas.items.insert(insert_at, drag_item)
            for it in self._canvas.items:
                it.raise_()
            self._canvas._lower_all_groups()
            self.refresh()

    # ─────────────────────────────────────────────────────────────────────────

    def _check_refresh(self):
        try:
            s = self._sig()
            if s != self._last_sig:
                self.refresh()
        except Exception:
            pass

    def refresh(self):
        try:
            # Remove old rows (keep stretch at end)
            while self._list_lay.count() > 1:
                item = self._list_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            all_member_items = set()
            for g in self._canvas.groups:
                for m in g.member_items:
                    all_member_items.add(id(m))

            insert_pos = 0
            for g in self._canvas.groups:
                collapsed = id(g) in self._collapsed
                row = LayerGroupRow(g, self._canvas, collapsed,
                                    lambda grp=g: self._toggle_group(grp),
                                    self._list, panel=self)
                self._list_lay.insertWidget(insert_pos, row)
                insert_pos += 1
                if not collapsed:
                    for m in g.member_items:
                        mrow = LayerRow(m, self._canvas, self, indent=True, parent=self._list)
                        self._list_lay.insertWidget(insert_pos, mrow)
                        insert_pos += 1
                spacer = QWidget(self._list)
                spacer.setFixedHeight(6)
                spacer.setStyleSheet('background:transparent;')
                self._list_lay.insertWidget(insert_pos, spacer)
                insert_pos += 1

            for item in self._canvas.items:
                if id(item) not in all_member_items:
                    row = LayerRow(item, self._canvas, self, indent=False, parent=self._list)
                    self._list_lay.insertWidget(insert_pos, row)
                    insert_pos += 1

            self._last_sig = self._sig()
        except Exception:
            pass


# ── ToastNotification ─────────────────────────────────────────────────────────

class ToastNotification(QWidget):
    """항상 위 토글 등 상태 변경 시 우하단에 잠깐 표시되는 토스트."""

    _MARGIN = 20

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self._text      = ''
        self._dot_color = QColor(74, 158, 255)
        self._fading_out = False

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

        self._anim = QPropertyAnimation(self._effect, b'opacity', self)
        self._anim.finished.connect(self._on_anim_done)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

        self.setFixedSize(160, 36)
        self.setVisible(False)

    def show_message(self, text, dot_color=None):
        self._text       = text
        self._dot_color  = dot_color or QColor(74, 158, 255)
        self._fading_out = False

        # 텍스트 폭에 맞게 크기 조정
        try:
            fm = QFontMetrics(get_ui_font(10))
            try:
                tw = fm.horizontalAdvance(text)
            except AttributeError:
                tw = fm.width(text)
            self.setFixedSize(tw + 52, 36)
        except Exception:
            self.setFixedSize(160, 36)

        self._reposition()
        self._timer.stop()
        self._anim.stop()
        self._effect.setOpacity(0.0)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(150)
        self.show()
        self.raise_()
        self._anim.start()
        self._timer.start(1750)   # 150ms 페이드인 + 1600ms 유지

    def _reposition(self):
        mw = self.parent()
        self.move(mw.width()  - self.width()  - self._MARGIN,
                  mw.height() - self.height() - self._MARGIN)

    def _fade_out(self):
        self._fading_out = True
        self._anim.stop()
        self._anim.setStartValue(float(self._effect.opacity()))
        self._anim.setEndValue(0.0)
        self._anim.setDuration(300)
        self._anim.start()

    def _on_anim_done(self):
        if self._fading_out:
            self.hide()

    def paintEvent(self, e):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            w, h = self.width(), self.height()

            # 배경
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(22, 22, 26))
            p.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

            # 상태 도트
            p.setPen(Qt.NoPen)
            p.setBrush(self._dot_color)
            p.drawEllipse(QPointF(20, h / 2), 4, 4)

            # 텍스트
            p.setFont(get_ui_font(10))
            p.setPen(QColor(210, 210, 215))
            p.drawText(QRectF(33, 0, w - 42, h),
                       Qt.AlignVCenter | Qt.AlignLeft, self._text)
            p.end()
        except Exception:
            pass


# ── TabShortcutFilter ──────────────────────────────────────────────────────────

class _TabShortcutFilter(QObject):
    """QApplication 레벨 이벤트 필터.
    어떤 위젯에 포커스가 있더라도 Tab 키를 가로채 단축키 오버레이를 표시한다."""

    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self._overlay = overlay
        self._active  = False   # WE가 오버레이를 열었는지 추적

    def eventFilter(self, obj, e):
        # ── 1단계: Tab 키인지 확인 (e 접근 실패 시 pass-through) ──────────
        try:
            t = e.type()
            if t not in (QEvent.KeyPress, QEvent.KeyRelease):
                return False
            if e.key() != Qt.Key_Tab or e.modifiers():
                return False
        except Exception:
            return False

        # ── 2단계: Tab 처리 — 내부 예외가 나도 반드시 True 반환 ───────────
        try:
            auto = e.isAutoRepeat()
        except Exception:
            auto = False

        if t == QEvent.KeyPress and not auto:
            try:
                focused = QApplication.focusWidget()
                if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                    self._active = True
                    self._overlay.tab_active = True
                    self._overlay.show_over_main()
            except Exception:
                pass

        elif t == QEvent.KeyRelease and not auto:
            try:
                if self._active:
                    self._active = False
                    self._overlay.tab_active = False
                    self._overlay.hide()
            except Exception:
                pass

        return True   # Tab 이벤트는 예외 여부에 상관없이 항상 소비


# ── ZoomHUD ────────────────────────────────────────────────────────────────────

class _ZoomHUD(QWidget):
    """Zoom percentage pill overlay — always floats on top of everything."""
    _ICON_SIZE = 9    # magnifier circle diameter
    _GAP = 4          # gap between icon and text
    _PAD_X = 10
    _PAD_Y = 5
    _SEP_GAP = 8      # gap between zoom text and RAM text

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pct = 100
        self._ram_mb = 0
        self._update_ram()
        self._update_size()
        if _PSUTIL_PROC is not None:
            self._ram_timer = QTimer(self)
            self._ram_timer.setInterval(2000)
            self._ram_timer.timeout.connect(self._tick_ram)
            self._ram_timer.start()

    def _update_ram(self):
        if _PSUTIL_PROC is None:
            return
        try:
            self._ram_mb = int(_PSUTIL_PROC.memory_info().rss / 1024 ** 2)
        except Exception:
            pass

    def _tick_ram(self):
        old = self._ram_mb
        self._update_ram()
        if self._ram_mb != old:
            self._update_size()
        self.update()

    def _ram_text(self):
        if _PSUTIL_PROC is None:
            return ''
        if self._ram_mb >= 1024:
            return f'{self._ram_mb / 1024:.1f}GB'
        return f'{self._ram_mb}MB'

    def _update_size(self):
        font = get_ui_font(8)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(f'{self._pct}%')
        ram = self._ram_text()
        ram_w = (self._SEP_GAP + fm.horizontalAdvance(ram)) if ram else 0
        th = fm.height()
        total_w = self._ICON_SIZE + self._GAP + tw + ram_w + self._PAD_X * 2
        total_h = max(self._ICON_SIZE + 4, th) + self._PAD_Y * 2
        self.setFixedSize(total_w, total_h)

    def set_zoom(self, pct):
        if pct == self._pct:
            return
        self._pct = pct
        self._update_size()
        self.update()

    def paintEvent(self, e):
        zoom_text = f'{self._pct}%'
        ram_text = self._ram_text()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        W, H = self.width(), self.height()
        r = H / 2.0

        # Pill background
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(QColor(30, 30, 30, 200))
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), r, r)

        # ── Magnifier icon ────────────────────────────────────────────────
        icon_col = QColor(200, 200, 200, 220)
        d = self._ICON_SIZE        # circle diameter
        cx = self._PAD_X + d / 2
        cy = H / 2.0
        circle_r = d / 2 - 1      # inner radius of circle

        p.setPen(QPen(icon_col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - circle_r, cy - circle_r,
                             circle_r * 2, circle_r * 2))

        # Handle — bottom-right at ~135° from center
        import math as _math
        angle_rad = _math.radians(135)
        hx1 = cx + circle_r * _math.cos(angle_rad)
        hy1 = cy + circle_r * _math.sin(angle_rad)
        hx2 = hx1 + 3.5 * _math.cos(angle_rad)
        hy2 = hy1 + 3.5 * _math.sin(angle_rad)
        p.setPen(QPen(icon_col, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(hx1, hy1), QPointF(hx2, hy2))

        # ── Zoom text ─────────────────────────────────────────────────────
        font = get_ui_font(8)
        fm = QFontMetrics(font)
        p.setFont(font)
        p.setPen(QColor(225, 225, 225, 240))
        text_x = self._PAD_X + self._ICON_SIZE + self._GAP
        zoom_w = fm.horizontalAdvance(zoom_text)
        p.drawText(QRectF(text_x, 0, zoom_w, H),
                   Qt.AlignVCenter | Qt.AlignLeft, zoom_text)

        # ── RAM text ──────────────────────────────────────────────────────
        if ram_text:
            ram_x = text_x + zoom_w + self._SEP_GAP
            p.setPen(QColor(160, 160, 160, 200))
            p.drawText(QRectF(ram_x, 0, W - ram_x - self._PAD_X, H),
                       Qt.AlignVCenter | Qt.AlignLeft, ram_text)


# ── ShortcutOverlay ────────────────────────────────────────────────────────────

class ShortcutOverlay(QWidget):
    """Tab 홀드 시 중앙에 표시되는 단축키 안내 오버레이."""

    _LEFT = [
        ('L',      '레이어 패널 열기/닫기'),
        ('H',      '전체 보기'),
        ('Z',      '줌 초기화'),
        ('T',      '항상 위 토글'),
        ('Space',  '재생 / 정지'),
        ('← / →',  '이전 / 다음 프레임'),
        ('Delete', '선택 삭제'),
        ('Esc',    '선택 해제'),
    ]
    _RIGHT = [
        ('Ctrl+A',       '전체 선택'),
        ('Ctrl+C',       '복사'),
        ('Ctrl+D',       '복제'),
        ('Ctrl+V',       '붙여넣기'),
        ('Ctrl+Z',       '실행 취소'),
        ('Ctrl+G',       '그룹 추가'),
        ('Ctrl+T',       '텍스트 추가'),
        ('Ctrl+I',       '색상 반전'),
        ('Ctrl+N',       '새 프로젝트'),
        ('Ctrl+O',       '열기'),
        ('Ctrl+S',       '저장'),
        ('Ctrl+Shift+S', '다른 이름으로 저장'),
    ]

    PW, PH = 440, 386

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(self.PW, self.PH)
        self.tab_active = False   # 필터가 관리 — True 동안 hide() 차단
        self.setVisible(False)

    def setVisible(self, visible):
        """Tab 홀드 중 외부 hide() 호출을 차단한다."""
        if not visible and self.tab_active:
            return
        super().setVisible(visible)

    def show_over_main(self):
        mw = self.parent()
        self.move((mw.width()  - self.PW) // 2,
                  (mw.height() - self.PH) // 2)
        super().setVisible(True)   # setVisible 우회 없이 직접 show
        self.raise_()

    def paintEvent(self, e):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)

            PW, PH = self.PW, self.PH

            # ── 배경 패널 ────────────────────────────────────────────────
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(16, 16, 18))
            p.drawRoundedRect(QRectF(0, 0, PW, PH), 14, 14)
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, PW - 1, PH - 1), 14, 14)

            # ── 타이틀 ──────────────────────────────────────────────────
            tf = get_ui_font(12)
            tf.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
            p.setFont(tf)
            p.setPen(QColor(215, 215, 220))
            p.drawText(QRectF(0, 13, PW, 26), Qt.AlignCenter, '단축키')

            # ── 구분선 ──────────────────────────────────────────────────
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(24, 44), QPointF(PW - 24, 44))

            # ── 컬럼 헤더 ───────────────────────────────────────────────
            col_w = PW // 2
            hf = get_ui_font(7)
            hf.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
            p.setFont(hf)
            p.setPen(QColor(85, 85, 98))
            p.drawText(QRectF(20, 50, col_w - 24, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, '기본')
            p.drawText(QRectF(col_w + 4, 50, col_w - 24, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, 'CTRL')

            # ── 단축키 행 렌더 ──────────────────────────────────────────
            key_font = get_ui_font(8)
            desc_font = get_ui_font(9)

            def _text_width(fm, text):
                # horizontalAdvance는 Qt 5.11+, 구버전은 width() 사용
                try:
                    return fm.horizontalAdvance(text)
                except AttributeError:
                    return fm.width(text)

            def draw_rows(rows, base_x, base_y):
                for key, desc in rows:
                    km = QFontMetrics(key_font)
                    kw = max(_text_width(km, key) + 16, 30)
                    kh = 18
                    key_rect = QRectF(base_x, base_y, kw, kh)
                    p.setPen(QPen(QColor(255, 255, 255, 18), 1))
                    p.setBrush(QColor(34, 34, 40))
                    p.drawRoundedRect(key_rect, 5, 5)
                    p.setFont(key_font)
                    p.setPen(QColor(200, 200, 208))
                    p.drawText(key_rect, Qt.AlignCenter, key)
                    p.setFont(desc_font)
                    p.setPen(QColor(130, 130, 142))
                    p.drawText(QRectF(base_x + kw + 10, base_y, col_w - kw - 30, 20),
                               Qt.AlignVCenter | Qt.AlignLeft, desc)
                    base_y += 23

            draw_rows(self._LEFT,  20,        70)
            draw_rows(self._RIGHT, col_w + 4, 70)

            p.end()
        except Exception:
            pass


# ── MainWindow ─────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        global _main_window
        _main_window = self
        self.setWindowTitle('ReView')
        self.setGeometry(100, 100, 1100, 700)
        self.is_always_on_top = False
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setStyleSheet('QMainWindow{background:#1c1c1c;}')

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_lay = QVBoxLayout(main_widget)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)
        main_widget.setMouseTracking(True)

        # Title bar (overlay – not in layout, floats over the top)
        self.titlebar = CustomTitleBar(self)
        self.titlebar.setParent(main_widget)
        self.titlebar.setGeometry(0, 0, self.width(), 24)
        self.titlebar.hide()

        # Body: canvas + divider + panel
        body = QWidget()
        self._body = body
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        self.canvas = CanvasWidget(self)
        self.titlebar.zoom_reset_btn.clicked.connect(self.canvas.resetZoom)
        self.titlebar.save_btn.clicked.connect(self._saveSelectedToSaveDir)
        self.titlebar.project_save_btn.clicked.connect(self.saveProject)
        _prog_dir = os.path.dirname(os.path.abspath(__file__))
        self.titlebar.open_saves_btn.clicked.connect(
            lambda: subprocess.Popen(['explorer', os.path.normpath(_prog_dir)]))

        # 토스트 알림 위젯
        self._toast = QPushButton(self)
        self._toast.setCursor(Qt.PointingHandCursor)
        self._toast.setStyleSheet(
            'QPushButton {'
            '  background: rgba(250,250,252,245);'
            '  color: #111111;'
            '  padding: 12px 28px;'
            '  border-radius: 10px;'
            '  font-family: "Segoe UI", "Malgun Gothic", sans-serif;'
            '  font-size: 14px;'
            '  font-weight: 600;'
            '  border: 1.5px solid rgba(0,0,0,18);'
            '}'
            'QPushButton:hover {'
            '  background: rgba(255,255,255,255);'
            '  border: 1.5px solid rgba(80,160,255,180);'
            '}')
        # 페이드 효과
        self._toast_effect = QGraphicsOpacityEffect(self._toast)
        self._toast.setGraphicsEffect(self._toast_effect)
        self._toast_effect.setOpacity(0.0)
        self._toast_anim = QPropertyAnimation(self._toast_effect, b'opacity', self)
        self._toast_anim.setDuration(180)
        self._toast_anim.finished.connect(self._on_toast_anim_done)
        self._toast_fading = False
        self._toast.hide()
        self._toast_on_click = None
        self._toast.clicked.connect(self._on_toast_click)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._fade_out_toast)

        self.layer_panel = LayerPanel(self.canvas, body)
        self.layer_panel.hide()
        self.vtab_bar = VerticalTabBar(body)
        self.vtab_bar.tab_changed.connect(self.switch_tab)
        self.vtab_bar.tab_added.connect(self.add_tab)
        self.vtab_bar.tab_closed.connect(self.close_tab)
        self.vtab_bar.tab_renamed.connect(self.rename_tab)
        self.vtab_bar.tab_moved.connect(self.move_tab)
        self.vtab_bar.panel_toggle_requested.connect(
            lambda: self.toggle_layer_panel(not self.layer_panel.isVisible()))
        self._panel_div = PanelDivider(self.layer_panel, body)
        self._panel_div.hide()
        # Zoom HUD overlay — child of MainWindow so it floats above every panel
        self._zoom_hud = _ZoomHUD(self)
        self._zoom_hud.hide()
        # 패널과 디바이더는 레이아웃 밖에서 오버레이로 관리
        body_lay.addWidget(self.vtab_bar)
        body_lay.addWidget(self.canvas)
        main_lay.addWidget(body)

        # Status label
        formats = ['GIF', 'PNG', 'JPG', 'TGA']
        if HAS_OPENCV:
            formats.append('MP4')

        self.resize_frame = ResizeFrame(self)
        self._undo = _UndoStack()
        self._deleted_tab_stack = []

        # Tab management (loadState may override these)
        self._tabs = [{
            'name': '탭 1', 'items': [], 'groups': [], 'item_float_pos': {},
            'pan_offset': QPoint(0, 0), 'pan_float': [0.0, 0.0],
            'canvas_scale': CanvasWidget.DEFAULT_SCALE, 'bg_color': QColor(30, 30, 30),
        }]
        self._active_tab = 0
        self.vtab_bar.setTabs(['탭 1'], 0)

        self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)
        self.titlebar_timer = QTimer()
        self.titlebar_timer.timeout.connect(self._hide_titlebar)
        self.titlebar_timer.setSingleShot(True)

        self.save_timer = QTimer()
        self.save_timer.timeout.connect(self.saveState)
        self.save_timer.start(2000)

        self._project_path = None       # currently open .rvw file (None = unsaved)
        self._project_temp_dir = None   # temp dir for extracted assets
        self._sys_clipboard_time = 0.0
        QApplication.clipboard().dataChanged.connect(self._onSysClipboardChanged)

        self._initial_layout_done = False   # showEvent 이후 True
        self._panel_deferred_show = False   # showEvent 전에 패널을 열어야 하면 True

        # 단축키 오버레이 (Tab 홀드) — MainWindow 직속 자식 + 앱 레벨 필터
        self._shortcut_overlay = ShortcutOverlay(self)
        self._tab_filter = _TabShortcutFilter(self._shortcut_overlay)
        QApplication.instance().installEventFilter(self._tab_filter)

        # 상태 표시 토스트 (항상 위 / 탭 복원 등 단순 알림)
        self._status_toast = ToastNotification(self)

        atexit.register(save_on_exit)
        self.loadState()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._initial_layout_done:
            self._initial_layout_done = True
            # 레이아웃이 확정된 다음 이벤트 루프 틱에 패널 배치
            QTimer.singleShot(0, self._apply_deferred_panel)

    def _apply_deferred_panel(self):
        self._reposition_overlay_panel()
        if self._panel_deferred_show:
            self._panel_deferred_show = False
            self._show_overlay_panel()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resize_frame.setGeometry(0, 0, self.width(), self.height())
        self.resize_frame.raise_()
        self._update_titlebar_geom()
        self._reposition_overlay_panel()
        if self.titlebar.isVisible():
            self.titlebar.raise_()
        if self._tab_filter._active:
            self._shortcut_overlay.show_over_main()
        if self._toast.isVisible():
            self._toast._reposition()

    def setAlwaysOnTop(self, on_top):
        self.is_always_on_top = on_top
        _win32_ok = False
        try:
            # Win32 SetWindowPos — argtypes 명시로 64bit HWND 잘림 방지
            HWND_TOPMOST   = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            SWP_NOMOVE     = 0x0002
            SWP_NOSIZE     = 0x0001
            SWP_NOACTIVATE = 0x0010
            _fn = ctypes.windll.user32.SetWindowPos
            _fn.argtypes = [
                ctypes.c_void_p,  # hWnd
                ctypes.c_void_p,  # hWndInsertAfter
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint,    # uFlags
            ]
            _fn.restype = ctypes.c_bool
            hwnd = ctypes.c_void_p(int(self.winId()))
            _win32_ok = bool(_fn(
                hwnd,
                HWND_TOPMOST if on_top else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            ))
        except Exception:
            pass

        if not _win32_ok:
            # fallback: Qt 방식 (깜빡임 있음)
            flags = self.windowFlags()
            if on_top:
                self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
            self.show()

        # 토스트 알림
        if on_top:
            self._status_toast.show_message('항상 위 고정', QColor(74, 158, 255))
        else:
            self._status_toast.show_message('고정 해제', QColor(100, 100, 110))

    def _update_titlebar_geom(self):
        """Recalculate titlebar geometry to match canvas area only."""
        cw = self.centralWidget()
        if not cw:
            return
        # mapTo needs the layout to be applied; if canvas has no size yet, skip
        if self.canvas.width() < 1:
            return
        cx = self.canvas.mapTo(cw, QPoint(0, 0)).x()
        self.titlebar.setGeometry(cx, 0, self.canvas.width(), 24)

    def _reposition_overlay_panel(self):
        """오버레이 패널을 VTabBar 오른쪽에 캔버스 위로 배치."""
        if not hasattr(self, '_body'):
            return
        body_h = self._body.height()
        if body_h < 1:
            return
        vtab_w = self.vtab_bar.width()
        panel_w = self.layer_panel.width()
        div_w = self._panel_div.width()
        self.layer_panel.setGeometry(vtab_w, 0, panel_w, body_h)
        self._panel_div.setGeometry(vtab_w + panel_w, 0, div_w, body_h)
        if self.layer_panel.isVisible():
            self.layer_panel.raise_()
            self._panel_div.raise_()
        # Zoom HUD — always at bottom-left of canvas area, regardless of layer panel
        if hasattr(self, '_zoom_hud'):
            margin = 14
            origin = self._body.mapTo(self, QPoint(0, 0))
            hud_x = origin.x() + vtab_w + margin
            hud_y = origin.y() + body_h - margin - self._zoom_hud.height()
            self._zoom_hud.setGeometry(hud_x, hud_y,
                                       self._zoom_hud.width(), self._zoom_hud.height())
            self._zoom_hud.raise_()
            self._zoom_hud.show()

    def _show_overlay_panel(self):
        """오버레이 패널 표시 (body 사이즈가 준비된 뒤 호출)."""
        self._reposition_overlay_panel()
        self.layer_panel.setVisible(True)
        self._panel_div.setVisible(True)
        self.layer_panel.raise_()
        self._panel_div.raise_()
        if hasattr(self, '_zoom_hud'):
            self._zoom_hud.raise_()
        self.vtab_bar.set_layer_panel_state(True)

    def toggle_layer_panel(self, visible):
        currently_visible = self.layer_panel.isVisible()
        if visible and not currently_visible:
            if self._initial_layout_done:
                # 윈도우가 이미 표시된 상태 → 즉시 표시
                self._show_overlay_panel()
            else:
                # 시작 직후: showEvent 이후에 표시
                self._panel_deferred_show = True
                self.vtab_bar.set_layer_panel_state(True)
            return
        elif not visible and currently_visible:
            self._panel_deferred_show = False
            self.layer_panel.setVisible(False)
            self._panel_div.setVisible(False)
        self.vtab_bar.set_layer_panel_state(visible)
        QTimer.singleShot(0, self._update_titlebar_geom)

    def show_titlebar(self):
        self._update_titlebar_geom()
        self.titlebar.show()
        self.titlebar.raise_()
        self.titlebar_timer.stop()

    def _hide_titlebar(self):
        self.titlebar.hide()

    def mouseMoveEvent(self, e):
        if e.pos().y() < 28:
            self.show_titlebar()
        elif not self.titlebar_timer.isActive():
            self.titlebar_timer.start(500)

    def leaveEvent(self, e):
        self.titlebar_timer.start(300)

    def _over_canvas(self):
        """True if the global cursor is over the canvas widget."""
        pos = self.canvas.mapFromGlobal(QCursor.pos())
        return self.canvas.rect().contains(pos)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos):
                if pos.y() < 28 and self._over_canvas():
                    self.show_titlebar()
                elif self.titlebar.isVisible() and not self.titlebar_timer.isActive():
                    self.titlebar_timer.start(500)
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_L and not e.modifiers():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self.toggle_layer_panel(not self.layer_panel.isVisible())
        elif e.key() == Qt.Key_T and not (e.modifiers() & Qt.ControlModifier):
            new_state = not self.is_always_on_top
            self.titlebar._updatePin(new_state)
            self.setAlwaysOnTop(new_state)
        elif e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.canvas.selectAll()
        elif e.key() == Qt.Key_C and e.modifiers() & Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self._copySelected()
        elif e.key() == Qt.Key_D and e.modifiers() & Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self._duplicateSelected()
        elif e.key() == Qt.Key_H and not e.modifiers():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self.canvas.zoom_to_fit_all()
        elif e.key() == Qt.Key_Z and not e.modifiers():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self.canvas.resetZoom()
        elif e.key() == Qt.Key_Z and e.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
            self.redo()
        elif e.key() == Qt.Key_Z and e.modifiers() & Qt.ControlModifier:
            self.undo()
        elif e.key() == Qt.Key_G and e.modifiers() & Qt.ControlModifier:
            self.canvas.addGroup()
        elif e.key() == Qt.Key_T and e.modifiers() & Qt.ControlModifier:
            self.canvas.addTextItem()
        elif e.key() == Qt.Key_V and e.modifiers() & Qt.ControlModifier:
            self._pasteClipboard()
        elif e.key() == Qt.Key_N and e.modifiers() == Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self.newProject()
        elif e.key() == Qt.Key_O and e.modifiers() == Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                self.openProject()
        elif e.key() == Qt.Key_S and e.modifiers() & Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                if e.modifiers() & Qt.ShiftModifier:
                    self.saveProjectAs()
                else:
                    self.saveProject()
        elif e.key() == Qt.Key_Escape:
            if self.isMaximized():
                self.showNormal()
            else:
                self.canvas.deselectAll()
        elif e.key() == Qt.Key_Space:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                for item in self.canvas.selected_items:
                    if hasattr(item, 'togglePlay'):
                        item.togglePlay()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                direction = -1 if e.key() == Qt.Key_Left else 1
                for item in self.canvas.selected_items:
                    if hasattr(item, '_onStepPressed'):
                        item._onStepPressed(direction)
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and e.isAutoRepeat():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                e.accept()
                return
        elif e.key() == Qt.Key_I and e.modifiers() == Qt.ControlModifier:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                for item in self.canvas.selected_items:
                    if hasattr(item, '_invert') and hasattr(item, '_label'):
                        _toggle_invert(item)
        elif e.key() == Qt.Key_Delete:
            self._deleteSelected()

        e.accept()

    def keyReleaseEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and not e.isAutoRepeat():
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                for item in self.canvas.selected_items:
                    if hasattr(item, '_onStepReleased'):
                        item._onStepReleased()
        super().keyReleaseEvent(e)

    def _copySelected(self):
        items = [i for i in self.canvas.selected_items if hasattr(i, 'getState')]
        if not items:
            return
        self.canvas._item_clipboard = [item.getState() for item in items]
        self.canvas._item_clipboard_time = __import__('time').time()
        self.canvas._paste_offset = 0

    def _duplicateSelected(self):
        items = [i for i in self.canvas.selected_items if hasattr(i, 'getState')]
        if not items:
            return
        states = [item.getState() for item in items]
        self.canvas.deselectAll()
        for s in states:
            s = dict(s)
            s['x'] = s.get('x', 0) + 20
            s['y'] = s.get('y', 0) + 20
            t = s.get('type', '')
            try:
                if t == 'text':
                    item = TextItem(self.canvas)
                    item.applyState(s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    item.select(additive=True)
                elif t == 'group':
                    g = GroupItem(s.get('name', 'Group'), self.canvas)
                    g.setGeometry(s['x'], s['y'], s.get('w', 200), s.get('h', 120))
                    g._color = QColor(s.get('color', '#ffa05064'))
                    g._text_color = QColor(s.get('text_color', '#ffffff'))
                    g._font_size = s.get('font_size', 10)
                    g.selected.connect(self.canvas.onItemSelected)
                    g.show()
                    g.lower()
                    self.canvas.groups.append(g)
                    g.select(additive=True)
                elif t == 'image':
                    item = ImageItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
                elif t == 'gif':
                    item = GifItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
                elif t == 'video' and HAS_OPENCV:
                    item = VideoItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
            except Exception as ex:
                print(f'[duplicate] {ex}')

    def _pasteInternalItems(self):
        states = self.canvas._item_clipboard
        if not states:
            return
        cursor = self.canvas.mapFromGlobal(QCursor.pos())
        self.canvas.deselectAll()

        # 클립보드 아이템들의 바운딩 박스 중심 계산
        min_x = min(s.get('x', 0) for s in states)
        min_y = min(s.get('y', 0) for s in states)
        max_x = max(s.get('x', 0) + s.get('w', 100) for s in states)
        max_y = max(s.get('y', 0) + s.get('h', 100) for s in states)
        bcx = (min_x + max_x) / 2
        bcy = (min_y + max_y) / 2

        for state in states:
            s = dict(state)
            s['x'] = round(cursor.x() + s.get('x', 0) - bcx)
            s['y'] = round(cursor.y() + s.get('y', 0) - bcy)
            t = s.get('type', '')
            try:
                if t == 'text':
                    item = TextItem(self.canvas)
                    item.applyState(s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    item.select(additive=True)
                elif t == 'group':
                    g = GroupItem(s.get('name', 'Group'), self.canvas)
                    g.setGeometry(s['x'], s['y'], s.get('w', 200), s.get('h', 120))
                    g._color = QColor(s.get('color', '#ffa05064'))
                    g._text_color = QColor(s.get('text_color', '#ffffff'))
                    g._font_size = s.get('font_size', 10)
                    g.selected.connect(self.canvas.onItemSelected)
                    g.show()
                    g.lower()
                    self.canvas.groups.append(g)
                    g.select(additive=True)
                elif t == 'image':
                    item = ImageItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
                elif t == 'gif':
                    item = GifItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
                elif t == 'video' and HAS_OPENCV:
                    item = VideoItem(s['path'], self.canvas)
                    item.setScale(s.get('scale', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    _apply_item_state(item, s)
                    item.move(s['x'], s['y'])
                    item.show()
                    item.selected.connect(self.canvas.onItemSelected)
                    self.canvas.items.append(item)
                    self.canvas._lower_all_groups()
                    item.select(additive=True)
            except Exception:
                pass

    def _onSysClipboardChanged(self):
        import time
        self._sys_clipboard_time = time.time()

    def _pasteClipboard(self):
        cb = QApplication.clipboard()
        mime = cb.mimeData()
        has_sys = mime.hasImage() or mime.hasUrls()
        has_internal = bool(self.canvas._item_clipboard)
        # 더 최근에 복사된 것을 붙여넣음
        if has_internal and (not has_sys or self.canvas._item_clipboard_time >= self._sys_clipboard_time):
            self._pasteInternalItems()
            return
        cursor = self.canvas.mapFromGlobal(QCursor.pos())

        def _move_to_cursor(item):
            item.move(cursor.x() - item.width() // 2,
                      cursor.y() - item.height() // 2)

        if mime.hasImage():
            img = cb.image()
            if img.isNull():
                return
            clip_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_clipboard')
            os.makedirs(clip_dir, exist_ok=True)
            fname = f'clip_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}.png'
            fpath = os.path.join(clip_dir, fname)
            img.save(fpath, 'PNG')
            try:
                item = ImageItem(fpath, self.canvas)
                self.canvas.addItem(item)
                _move_to_cursor(item)
            except Exception as ex:
                print(f'[paste] {ex}')
        elif mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                ext = path.lower()
                try:
                    if ext.endswith('.gif'):
                        item = GifItem(path, self.canvas)
                        self.canvas.addItem(item)
                        _move_to_cursor(item)
                    elif ext.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm')) and HAS_OPENCV:
                        item = VideoItem(path, self.canvas)
                        self.canvas.addItem(item)
                        _move_to_cursor(item)
                    elif ext.endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif', '.tga')):
                        item = ImageItem(path, self.canvas)
                        self.canvas.addItem(item)
                        _move_to_cursor(item)
                except Exception as ex:
                    print(f'[paste url] {ex}')
        elif mime.hasText():
            text = mime.text().strip()
            if text:
                item = TextItem(self.canvas)
                item.applyState({'type': 'text', 'text': text})
                self.canvas.addItem(item)
                _move_to_cursor(item)

    def _deleteSelected(self):
        items_to_delete = list(self.canvas.selected_items)
        if not items_to_delete:
            return
        states = []
        for item in items_to_delete:
            if hasattr(item, 'getState'):
                try:
                    s = item.getState()
                    s['x'] -= self.canvas.pan_offset.x()
                    s['y'] -= self.canvas.pan_offset.y()
                    if isinstance(item, GroupItem):
                        s['member_states'] = [
                            m.getState() for m in item.member_items if hasattr(m, 'getState')]
                    states.append(s)
                except Exception:
                    pass
        self.canvas.selected_items = []
        self.canvas.selected_item = None
        for item in items_to_delete:
            if isinstance(item, GroupItem):
                if item in self.canvas.groups:
                    self.canvas.groups.remove(item)
            else:
                if item in self.canvas.items:
                    self.canvas.items.remove(item)
            if hasattr(item, 'cleanup'):
                item.cleanup()
            else:
                item.deleteLater()
        if not states:
            return
        restored = []

        def _undo_delete():
            restored.clear()
            for s in states:
                try:
                    t = s.get('type', '')
                    pos = QPoint(int(s.get('x', 50) + self.canvas.pan_offset.x()),
                                 int(s.get('y', 50) + self.canvas.pan_offset.y()))
                    it = None
                    if t == 'image':
                        it = ImageItem(s['path'], self.canvas)
                        it.setScale(s.get('scale', 1.0))
                        _apply_item_state(it, s)
                        it.move(pos); it.show()
                        it.selected.connect(self.canvas.onItemSelected)
                        self.canvas.items.append(it)
                        self.canvas._lower_all_groups()
                    elif t == 'gif':
                        it = GifItem(s['path'], self.canvas)
                        it.setScale(s.get('scale', 1.0))
                        if not s.get('playing', True): it.togglePlay()
                        _apply_item_state(it, s)
                        it.move(pos); it.show()
                        it.selected.connect(self.canvas.onItemSelected)
                        self.canvas.items.append(it)
                        self.canvas._lower_all_groups()
                    elif t == 'video' and HAS_OPENCV:
                        it = VideoItem(s['path'], self.canvas)
                        it.setScale(s.get('scale', 1.0))
                        if not s.get('playing', True): it.togglePlay()
                        _apply_item_state(it, s)
                        it.move(pos); it.show()
                        it.selected.connect(self.canvas.onItemSelected)
                        self.canvas.items.append(it)
                        self.canvas._lower_all_groups()
                    elif t == 'text':
                        it = TextItem(self.canvas)
                        it.applyState(s)
                        it.show(); it.move(pos)
                        it.selected.connect(self.canvas.onItemSelected)
                        self.canvas.items.append(it)
                    elif t == 'group':
                        it = GroupItem(s.get('name', 'Group'), self.canvas)
                        it._color = QColor(s.get('color', '#50c8ff'))
                        it.setGeometry(pos.x(), pos.y(), s.get('w', 300), s.get('h', 200))
                        it.selected.connect(self.canvas.onItemSelected)
                        it.show(); it.lower()
                        self.canvas.groups.append(it)
                    if it:
                        restored.append(it)
                except Exception as ex:
                    print(f'[undo delete] {ex}')

        def _redo_delete():
            self.canvas.selected_items = []
            self.canvas.selected_item = None
            for it in list(restored):
                if isinstance(it, GroupItem):
                    if it in self.canvas.groups:
                        self.canvas.groups.remove(it)
                else:
                    if it in self.canvas.items:
                        self.canvas.items.remove(it)
                if hasattr(it, 'cleanup'):
                    it.cleanup()
                else:
                    it.deleteLater()
            restored.clear()

        self._undo.push(_undo_delete, _redo_delete)

    def undo(self):
        if self._deleted_tab_stack:
            entry = self._deleted_tab_stack.pop()
            self._restore_deleted_tab(entry['idx'], entry['data'])
            return
        self._undo.undo()

    def redo(self):
        self._undo.redo()

    def _restore_deleted_tab(self, idx, data):
        """삭제된 탭을 원래 위치에 복원한다."""
        self._save_current_tab()
        po_d = data.get('pan_offset', {})
        pan_offset = QPoint(po_d.get('x', 0), po_d.get('y', 0))
        items, groups = self._load_items_from_state(data, pan_offset)
        restored_tab = {
            'name':           data.get('name', '탭'),
            'items':          items,
            'groups':         groups,
            'item_float_pos': {},
            'pan_offset':     pan_offset,
            'pan_float':      [0.0, 0.0],
            'canvas_scale':   data.get('canvas_scale', CanvasWidget.DEFAULT_SCALE),
            'bg_color':       QColor(data.get('bg_color', '#1e1e1e')),
        }
        insert_idx = min(idx, len(self._tabs))
        self._tabs.insert(insert_idx, restored_tab)
        # 삽입으로 인해 active_tab 인덱스 보정
        if self._active_tab >= insert_idx:
            self._active_tab += 1
        # 복원된 탭으로 전환
        self._active_tab = insert_idx
        self._load_tab(insert_idx)
        self._status_toast.show_message('탭 복원됨', QColor(74, 200, 130))

    # ── Tab management ──────────────────────────────────────────────────────────

    def _tab_names(self):
        return [t['name'] for t in self._tabs]

    def _sync_tab_bar(self):
        self.vtab_bar.setTabs(self._tab_names(), self._active_tab)

    def _save_current_tab(self):
        """Snapshot live canvas state into the current tab dict."""
        tab = self._tabs[self._active_tab]
        tab['items']         = list(self.canvas.items)
        tab['groups']        = list(self.canvas.groups)
        tab['item_float_pos']= dict(self.canvas._item_float_pos)
        tab['pan_offset']    = QPoint(self.canvas.pan_offset)
        tab['pan_float']     = list(self.canvas._pan_float)
        tab['canvas_scale']  = self.canvas.canvas_scale
        tab['bg_color']      = QColor(self.canvas.bg_color)

    def _load_tab(self, idx):
        """Restore tab idx state to the live canvas."""
        for it in self.canvas.items:
            it.hide()
            if hasattr(it, 'control_bar') and it.control_bar:
                it.control_bar.hide()
            if hasattr(it, '_hover_bar') and it._hover_bar:
                it._hover_bar.hide()
            if hasattr(it, '_blend_bar') and it._blend_bar:
                it._blend_bar.hide()
        for g in self.canvas.groups:
            g.hide()
        self.canvas.selected_items = []
        self.canvas.selected_item  = None

        tab = self._tabs[idx]
        self.canvas.items          = list(tab.get('items', []))
        self.canvas.groups         = list(tab.get('groups', []))
        self.canvas._item_float_pos= dict(tab.get('item_float_pos', {}))
        self.canvas.pan_offset     = QPoint(tab.get('pan_offset', QPoint(0, 0)))
        self.canvas._pan_float     = list(tab.get('pan_float', [0.0, 0.0]))
        self.canvas.canvas_scale   = tab.get('canvas_scale', CanvasWidget.DEFAULT_SCALE)
        self.canvas.setBackgroundColor(tab.get('bg_color', QColor(30, 30, 30)))

        for it in self.canvas.items:
            it.show()
        for g in self.canvas.groups:
            g.show()
        for it in self.canvas.items:
            if getattr(it, '_z_always_on_top', False):
                it.raise_()
        self.canvas._lower_all_groups()

        if hasattr(self.titlebar, 'zoom_label'):
            self.titlebar.zoom_label.setText(
                f'{round(self.canvas.canvas_scale / self.canvas.DEFAULT_SCALE * 100)}%')

        self.layer_panel._collapsed.clear()
        self.layer_panel._eye_hidden.clear()
        self.layer_panel._eye_hidden_groups.clear()
        self.layer_panel._last_sig = None
        self._undo.clear()
        self._sync_tab_bar()

    def switch_tab(self, idx):
        if idx == self._active_tab or not (0 <= idx < len(self._tabs)):
            return
        self._save_current_tab()
        self._active_tab = idx
        self._load_tab(idx)

    def add_tab(self):
        self._save_current_tab()
        n = len(self._tabs) + 1
        new_tab = {
            'name': f'탭 {n}', 'items': [], 'groups': [], 'item_float_pos': {},
            'pan_offset': QPoint(0, 0), 'pan_float': [0.0, 0.0],
            'canvas_scale': CanvasWidget.DEFAULT_SCALE, 'bg_color': QColor(self.canvas.bg_color),
        }
        self._tabs.append(new_tab)
        self._active_tab = len(self._tabs) - 1
        self._load_tab(self._active_tab)

    def close_tab(self, idx):
        if len(self._tabs) <= 1 or not (0 <= idx < len(self._tabs)):
            return
        was_active = (idx == self._active_tab)
        if was_active:
            self._save_current_tab()

        # 삭제 전에 직렬화해서 undo 스택에 보존
        tab = self._tabs[idx]
        try:
            serialized = self._serialize_tab(tab)
            self._deleted_tab_stack.append({'idx': idx, 'data': serialized})
        except Exception:
            pass

        if was_active:
            self.canvas.items = []
            self.canvas.groups = []
            self.canvas.selected_items = []
        for it in tab.get('items', []):
            if hasattr(it, 'control_bar') and it.control_bar:
                it.control_bar.hide()
            if hasattr(it, '_hover_bar') and it._hover_bar:
                it._hover_bar.hide()
            if hasattr(it, '_blend_bar') and it._blend_bar:
                it._blend_bar.hide()
            it.hide(); it.deleteLater()
        for g in tab.get('groups', []):
            g.hide(); g.deleteLater()
        self._tabs.pop(idx)
        if was_active:
            new_idx = min(idx, len(self._tabs) - 1)
            self._active_tab = new_idx
            self._load_tab(new_idx)
        else:
            if self._active_tab > idx:
                self._active_tab -= 1
            self._sync_tab_bar()

    def rename_tab(self, idx, name):
        if 0 <= idx < len(self._tabs):
            self._tabs[idx]['name'] = name
            self._sync_tab_bar()

    def move_tab(self, from_idx, to_idx):
        if from_idx == to_idx or not (0 <= from_idx < len(self._tabs)) or not (0 <= to_idx < len(self._tabs)):
            return
        tab = self._tabs.pop(from_idx)
        self._tabs.insert(to_idx, tab)
        if self._active_tab == from_idx:
            self._active_tab = to_idx
        elif from_idx < self._active_tab <= to_idx:
            self._active_tab -= 1
        elif to_idx <= self._active_tab < from_idx:
            self._active_tab += 1
        self._sync_tab_bar()

    # ── Serialization helpers ────────────────────────────────────────────────

    def _serialize_tab(self, tab):
        po = tab.get('pan_offset', QPoint(0, 0))
        item_list = tab.get('items', [])
        # Temporarily set canvas.items so GroupItem.getState() finds indices
        old_canvas_items = self.canvas.items
        self.canvas.items = item_list
        result = {
            'name': tab.get('name', 'Tab'),
            'pan_offset': {'x': po.x(), 'y': po.y()},
            'canvas_scale': tab.get('canvas_scale', CanvasWidget.DEFAULT_SCALE),
            'bg_color': tab.get('bg_color', QColor(30, 30, 30)).name(),
            'items': [], 'groups': [],
        }
        for item in item_list:
            if hasattr(item, 'getState'):
                try:
                    s = item.getState()
                    s['x'] -= po.x(); s['y'] -= po.y()
                    result['items'].append(s)
                except Exception:
                    pass
        for g in tab.get('groups', []):
            try:
                s = g.getState()
                s['x'] -= po.x(); s['y'] -= po.y()
                result['groups'].append(s)
            except Exception:
                pass
        self.canvas.items = old_canvas_items
        return result

    def _load_items_from_state(self, state_data, pan_offset):
        """Create item widgets from serialized data. Returns (items, groups).
        All items start hidden; caller is responsible for showing them."""
        loaded_items = []
        for s in state_data.get('items', []):
            try:
                t = s.get('type', '')
                pos = QPoint(s.get('x', 50) + pan_offset.x(),
                             s.get('y', 50) + pan_offset.y())
                item = None
                if t == 'image':
                    if not os.path.exists(s.get('path', '')):
                        continue
                    item = ImageItem(s['path'], self.canvas)
                    item._rotation = s.get('rotation', 0)
                    item._flip_h = s.get('flip_h', False)
                    item._flip_v = s.get('flip_v', False)
                    item.setScale(s.get('scale', 1.0))
                    if s.get('opacity', 1.0) != 1.0:
                        item.setItemOpacity(s.get('opacity', 1.0))
                    if 'crop' in s:
                        item._crop = s['crop']
                        item.updateSize()
                elif t == 'gif':
                    if not os.path.exists(s.get('path', '')):
                        continue
                    item = GifItem(s['path'], self.canvas)
                    item._rotation = s.get('rotation', 0)
                    item._flip_h = s.get('flip_h', False)
                    item._flip_v = s.get('flip_v', False)
                    item.setScale(s.get('scale', 1.0))
                    if s.get('opacity', 1.0) != 1.0:
                        item.setItemOpacity(s.get('opacity', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    if 'crop' in s:
                        item._crop = s['crop']
                        item.updateSize()
                    if 'blend_mode' in s:
                        _set_blend_mode(item, s['blend_mode'])
                    if 'trim_start' in s or 'trim_end' in s:
                        item._trim_start = s.get('trim_start', 0)
                        item._trim_end = s.get('trim_end', item.frame_count - 1)
                elif t == 'video' and HAS_OPENCV:
                    if not os.path.exists(s.get('path', '')):
                        continue
                    item = VideoItem(s['path'], self.canvas)
                    item._rotation = s.get('rotation', 0)
                    item._flip_h = s.get('flip_h', False)
                    item._flip_v = s.get('flip_v', False)
                    item.setScale(s.get('scale', 1.0))
                    if s.get('opacity', 1.0) != 1.0:
                        item.setItemOpacity(s.get('opacity', 1.0))
                    if not s.get('playing', True):
                        item.togglePlay()
                    if 'crop' in s:
                        item._crop = s['crop']
                        item.updateSize()
                    if 'blend_mode' in s:
                        _set_blend_mode(item, s['blend_mode'])
                    if 'trim_start' in s or 'trim_end' in s:
                        item._trim_start = s.get('trim_start', 0)
                        item._trim_end = s.get('trim_end', item.frame_count - 1)
                elif t == 'text':
                    item = TextItem(self.canvas)
                    item.applyState(s)
                    if s.get('opacity', 1.0) != 1.0:
                        item.setItemOpacity(s.get('opacity', 1.0))
                else:
                    continue
                if item:
                    if s.get('invert', False) and hasattr(item, '_invert'):
                        item._invert = True
                    if s.get('z_always_on_top', False) and hasattr(item, '_z_always_on_top'):
                        item._z_always_on_top = True
                    item.selected.connect(self.canvas.onItemSelected)
                    item.move(pos)
                    item.hide()
                    loaded_items.append(item)
            except Exception:
                pass
        loaded_groups = []
        for s in state_data.get('groups', []):
            try:
                g = GroupItem(s.get('name', 'Group'), self.canvas)
                g._color      = QColor(s.get('color', '#ff50c8ff'))
                g._text_color = QColor(s.get('text_color', '#ffffff'))
                g._font_size  = s.get('font_size', 10)
                px = s.get('x', 0) + pan_offset.x()
                py = s.get('y', 0) + pan_offset.y()
                g.setGeometry(px, py, s.get('w', 300), s.get('h', 200))
                for midx in s.get('member_indices', []):
                    if 0 <= midx < len(loaded_items):
                        g.member_items.append(loaded_items[midx])
                g.selected.connect(self.canvas.onItemSelected)
                g.hide()
                loaded_groups.append(g)
            except Exception:
                pass
        return loaded_items, loaded_groups

    # ────────────────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self.saveState()
        if self._project_temp_dir and os.path.exists(self._project_temp_dir):
            shutil.rmtree(self._project_temp_dir, ignore_errors=True)
        e.accept()

    def saveState(self):
        try:
            self._save_current_tab()
            state = {
                'window': {
                    'x': self.x(), 'y': self.y(),
                    'width': self.width(), 'height': self.height(),
                    'always_on_top': self.is_always_on_top,
                    'panel_visible': self.layer_panel.isVisible(),
                    'panel_width': self.layer_panel.width(),
                    'titlebar_color': self.titlebar._color.name(),
                },
                'active_tab': self._active_tab,
                'tabs': [self._serialize_tab(t) for t in self._tabs],
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def loadState(self):
        try:
            if not os.path.exists(CONFIG_FILE):
                return
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self._apply_state_dict(state, restore_geometry=True)
        except Exception:
            pass

    def _apply_state_dict(self, state, restore_geometry=False):
        try:
            if 'window' in state:
                w = state['window']
                if restore_geometry:
                    self.setGeometry(w.get('x', 100), w.get('y', 100),
                                     w.get('width', 1100), w.get('height', 700))
                else:
                    self.resize(w.get('width', self.width()), w.get('height', self.height()))
                if w.get('always_on_top', False):
                    self.is_always_on_top = True
                    self.titlebar._updatePin(True)
                    self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
                panel_visible = w.get('panel_visible', False)
                self.toggle_layer_panel(panel_visible)
                if 'panel_width' in w:
                    self.layer_panel.setFixedWidth(max(100, min(450, w['panel_width'])))
                if 'titlebar_color' in w:
                    self.titlebar._color = QColor(w['titlebar_color'])
                    self.titlebar.update()

            if 'tabs' in state:
                # Multi-tab format
                self._tabs = []
                for t_data in state['tabs']:
                    po_d = t_data.get('pan_offset', {'x': 0, 'y': 0})
                    po   = QPoint(po_d.get('x', 0), po_d.get('y', 0))
                    items, groups = self._load_items_from_state(t_data, po)
                    tab = {
                        'name':          t_data.get('name', f'탭 {len(self._tabs)+1}'),
                        'items':         items,
                        'groups':        groups,
                        'item_float_pos':{},
                        'pan_offset':    po,
                        'pan_float':     [float(po.x()), float(po.y())],
                        'canvas_scale':  t_data.get('canvas_scale', CanvasWidget.DEFAULT_SCALE),
                        'bg_color':      QColor(t_data.get('bg_color', '#1e1e1e')),
                    }
                    self._tabs.append(tab)
                if not self._tabs:
                    self._tabs = [{'name': '탭 1', 'items': [], 'groups': [],
                                   'item_float_pos': {}, 'pan_offset': QPoint(0,0),
                                   'pan_float': [0.0,0.0], 'canvas_scale': CanvasWidget.DEFAULT_SCALE,
                                   'bg_color': QColor(30,30,30)}]
                self._active_tab = min(state.get('active_tab', 0), len(self._tabs) - 1)
                self._load_tab(self._active_tab)

            elif 'items' in state or 'groups' in state:
                # Legacy single-canvas format – migrate into tab 0
                if 'background' in state:
                    self.canvas.setBackgroundColor(QColor(state['background']))
                po = QPoint(state.get('pan_offset', {}).get('x', 0),
                            state.get('pan_offset', {}).get('y', 0))
                self.canvas.pan_offset = po
                self.canvas._pan_float = [float(po.x()), float(po.y())]
                scale = state.get('canvas_scale', CanvasWidget.DEFAULT_SCALE)
                self.canvas.canvas_scale = scale
                items, groups = self._load_items_from_state(state, po)
                self._tabs[0].update({
                    'items': items, 'groups': groups, 'item_float_pos': {},
                    'pan_offset': QPoint(po), 'pan_float': [float(po.x()), float(po.y())],
                    'canvas_scale': scale,
                    'bg_color': QColor(state.get('background', '#1e1e1e')),
                })
                self._active_tab = 0
                self._load_tab(0)
        except Exception:
            pass


# ── Project file helpers ────────────────────────────────────────────────────────

    def _update_window_title(self):
        if self._project_path:
            name = os.path.splitext(os.path.basename(self._project_path))[0]
            self.titlebar.title.setText(f'ReView  —  {name}')
        else:
            self.titlebar.title.setText('ReView')

    def _clear_all_items(self):
        """Remove all item widgets from every tab and reset canvas."""
        seen = set()
        for tab in self._tabs:
            for it in tab.get('items', []):
                if id(it) not in seen:
                    seen.add(id(it))
                    it.hide()
                    it.deleteLater()
            for g in tab.get('groups', []):
                if id(g) not in seen:
                    seen.add(id(g))
                    g.hide()
                    g.deleteLater()
        for it in self.canvas.items:
            if id(it) not in seen:
                it.hide(); it.deleteLater()
        for g in self.canvas.groups:
            if id(g) not in seen:
                g.hide(); g.deleteLater()
        self._tabs = []
        self.canvas.items = []
        self.canvas.groups = []
        self.canvas.selected_items = []
        self.canvas.selected_item = None
        self.canvas._item_clipboard = []
        self._undo.clear()

    def newProject(self):
        reply = QMessageBox.question(
            self, '새 프로젝트',
            '현재 작업을 닫고 새 프로젝트를 시작합니다.\n계속하시겠습니까?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._clear_all_items()
        self._tabs = [{'name': '탭 1', 'items': [], 'groups': [], 'item_float_pos': {},
                       'pan_offset': QPoint(0, 0), 'pan_float': [0.0, 0.0],
                       'canvas_scale': CanvasWidget.DEFAULT_SCALE,
                       'bg_color': QColor(30, 30, 30)}]
        self._active_tab = 0
        self._project_path = None
        if self._project_temp_dir and os.path.exists(self._project_temp_dir):
            shutil.rmtree(self._project_temp_dir, ignore_errors=True)
            self._project_temp_dir = None
        self._load_tab(0)
        self._update_window_title()

    def openProject(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '프로젝트 열기', '', 'ReView 프로젝트 (*.rvw)')
        if path:
            self._load_rvw(path)

    def _show_toast(self, msg='Saved', duration=2500, on_click=None):
        """화면 하단 중앙에 잠깐 나타났다 사라지는 알림을 표시한다."""
        self._toast_on_click = on_click
        self._toast_fading = False
        self._toast.setText(msg)
        self._toast.adjustSize()
        x = (self.width() - self._toast.width()) // 2
        y = self.height() - self._toast.height() - 60
        self._toast.move(x, y)
        self._toast.raise_()
        self._toast_anim.stop()
        self._toast_effect.setOpacity(0.0)
        self._toast.show()
        self._toast_anim.setDuration(180)
        self._toast_anim.setStartValue(0.0)
        self._toast_anim.setEndValue(1.0)
        self._toast_anim.start()
        self._toast_timer.stop()
        self._toast_timer.start(duration)

    def _fade_out_toast(self):
        self._toast_fading = True
        self._toast_anim.stop()
        self._toast_anim.setDuration(300)
        self._toast_anim.setStartValue(float(self._toast_effect.opacity()))
        self._toast_anim.setEndValue(0.0)
        self._toast_anim.start()

    def _on_toast_anim_done(self):
        if self._toast_fading:
            self._toast.hide()
            self._toast_fading = False

    def _on_toast_click(self):
        self._toast_anim.stop()
        self._toast_timer.stop()
        self._toast.hide()
        if self._toast_on_click:
            self._toast_on_click()

    def _saveSelectedToSaveDir(self):
        """선택된 미디어 아이템(들)을 SAVE_DIR 폴더에 저장한다."""
        targets = [i for i in self.canvas.selected_items
                   if hasattr(i, 'file_path') and getattr(i, 'file_path', None)]
        if not targets:
            targets = [i for i in self.canvas.items
                       if hasattr(i, 'file_path') and getattr(i, 'file_path', None)]
        if not targets:
            return
        count = sum(1 for item in targets if _quick_save_to_dir(item) is not None)
        msg = f'Saved ({count})  ·  Open Folder →' if count > 1 else 'Saved  ·  Open Folder →'
        self._show_toast(msg, duration=4000,
                         on_click=lambda: subprocess.Popen(['explorer', os.path.normpath(SAVE_DIR)]))

    def saveProject(self):
        if self._project_path:
            self._write_rvw(self._project_path)
            self._update_window_title()
        else:
            self.saveProjectAs()

    def saveProjectAs(self):
        default = self._project_path or ''
        path, _ = QFileDialog.getSaveFileName(
            self, '다른 이름으로 저장', default, 'ReView 프로젝트 (*.rvw)')
        if not path:
            return
        if not path.lower().endswith('.rvw'):
            path += '.rvw'
        self._write_rvw(path)
        self._project_path = path
        self._update_window_title()

    def _write_rvw(self, save_path):
        """Serialize current state and pack all assets into a .rvw ZIP archive."""
        try:
            self._save_current_tab()
            state = {
                'version': 1,
                'window': {
                    'width': self.width(), 'height': self.height(),
                    'always_on_top': self.is_always_on_top,
                    'panel_visible': self.layer_panel.isVisible(),
                    'panel_width': self.layer_panel.width(),
                    'titlebar_color': self.titlebar._color.name(),
                },
                'active_tab': self._active_tab,
                'tabs': [self._serialize_tab(t) for t in self._tabs],
            }
            # Build asset map: original abs path → unique relative 'assets/name.ext'
            asset_map = {}
            used_names = set()
            for tab_data in state['tabs']:
                for item_data in tab_data.get('items', []):
                    orig = item_data.get('path', '')
                    if orig and os.path.isfile(orig) and orig not in asset_map:
                        fname = os.path.basename(orig)
                        base, ext = os.path.splitext(fname)
                        name = fname
                        counter = 1
                        while name in used_names:
                            name = f'{base}_{counter}{ext}'
                            counter += 1
                        used_names.add(name)
                        asset_map[orig] = f'assets/{name}'
            # Replace absolute paths with relative asset paths
            for tab_data in state['tabs']:
                for item_data in tab_data.get('items', []):
                    orig = item_data.get('path', '')
                    if orig in asset_map:
                        item_data['path'] = asset_map[orig]
            # Write ZIP
            with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('project.json',
                            json.dumps(state, indent=2, ensure_ascii=False))
                for orig_path, rel_path in asset_map.items():
                    try:
                        zf.write(orig_path, rel_path)
                    except Exception as ex:
                        print(f'[rvw] asset skip: {ex}')
        except Exception as ex:
            QMessageBox.warning(self, '저장 실패', f'프로젝트 저장 중 오류가 발생했습니다.\n{ex}')

    def _load_rvw(self, path):
        """Extract a .rvw ZIP archive and load the project state."""
        try:
            # Clean up previous temp dir
            if self._project_temp_dir and os.path.exists(self._project_temp_dir):
                shutil.rmtree(self._project_temp_dir, ignore_errors=True)
            temp_dir = tempfile.mkdtemp(prefix='rvw_')
            self._project_temp_dir = temp_dir

            with zipfile.ZipFile(path, 'r') as zf:
                zf.extractall(temp_dir)

            json_path = os.path.join(temp_dir, 'project.json')
            if not os.path.exists(json_path):
                QMessageBox.warning(self, '열기 실패', '유효한 ReView 프로젝트 파일이 아닙니다.')
                return
            with open(json_path, 'r', encoding='utf-8') as f:
                state = json.load(f)

            # Resolve relative asset paths → absolute paths inside temp dir
            for tab_data in state.get('tabs', []):
                for item_data in tab_data.get('items', []):
                    rel = item_data.get('path', '')
                    if rel and not os.path.isabs(rel):
                        item_data['path'] = os.path.join(
                            temp_dir, rel.replace('/', os.sep))

            self._clear_all_items()
            self._apply_state_dict(state, restore_geometry=False)
            self._project_path = path
            self._update_window_title()
        except Exception as ex:
            QMessageBox.warning(self, '열기 실패', f'프로젝트를 열 수 없습니다.\n{ex}')

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    # Must be set BEFORE QApplication is created to take effect
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setFont(get_ui_font(10))
    window = MainWindow()
    window.show()
    # Open .rvw file passed as command-line argument (e.g. double-click from Explorer)
    args = [a for a in sys.argv[1:] if a.lower().endswith('.rvw') and os.path.isfile(a)]
    if args:
        QTimer.singleShot(300, lambda: window._load_rvw(args[0]))
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
