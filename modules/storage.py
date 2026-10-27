#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center – Tárhelyelemző modul
Elhelyezés: modules/storage.py
Belépési pont: open_module(parent=None, settings=None)
"""

from __future__ import annotations
import json
import os, shutil, subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QBrush, QPainter, QPen, QPainterPath, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QLabel, QPushButton, QMenu, QMessageBox, QProgressBar,
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QCheckBox, QFormLayout, QHeaderView
)

ROOT_PATH = "/storage/emulated/0"
STORAGE_SETTINGS_PATH = Path.home() / ".config" / "redmi-control-center" / "storage_settings.json"

def human_size(value):
    if value is None: return "Ismeretlen"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"

def fmt_time(value):
    try: return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M") if value else "—"
    except (ValueError, OSError, TypeError): return "—"

@dataclass
class Node:
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    mtime: Optional[float] = None
    children: list["Node"] = field(default_factory=list)
    count: int = 0
    readable: bool = True
    writable: bool = False
    complete: bool = True
    permissions: str = "—"
    file_type: str = "Könyvtár"

class ScanWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, adb, serial, root):
        super().__init__()
        self.adb, self.serial, self.root = adb, serial, root

    def command(self, *args, timeout=30):
        cmd = [self.adb]
        if self.serial: cmd += ["-s", self.serial]
        return subprocess.run(cmd + list(args), capture_output=True, text=True,
                              errors="replace", timeout=timeout)

    def run(self):
        """Az Android tárhelyét kizárólag ADB shell-parancsokkal járja be."""
        try:
            state = self.command("get-state", timeout=10)
            if state.returncode or state.stdout.strip() != "device":
                self.failed.emit(
                    "Nincs engedélyezett ADB-kapcsolat. "
                    "Ellenőrizd a kiválasztott telefon kapcsolatát."
                )
                return

            # Ne indítsunk Androidon külön 'sh -c' parancsot: az adb shell
            # közvetlenül futtatja a toybox parancsokat, és a hibakimenet megmarad.
            root_check = self.command("shell", "ls", "-ld", self.root, timeout=15)
            if root_check.returncode:
                self.failed.emit(
                    f"Az Android-tárhely nem érhető el: {self.root}\\n"
                    f"ADB-hiba: {(root_check.stderr or root_check.stdout).strip() or 'ismeretlen hiba'}"
                )
                return

            self.progress.emit("Android-könyvtárak felmérése ADB-n keresztül…")
            found = self.command("shell", "find", self.root, "-type", "d", timeout=120)
            if found.returncode:
                self.failed.emit(
                    f"Az Android find parancsa hibát adott ({found.returncode}).\\n"
                    f"{(found.stderr or found.stdout).strip() or 'Nincs részletes hibaüzenet.'}"
                )
                return

            paths = sorted(
                {line.strip() for line in found.stdout.splitlines() if line.strip()},
                key=lambda value: (value.count("/"), value)
            )
            if self.root not in paths:
                # Egyes Android-verziókon a find kimenete eltérhet; a már
                # ellenőrzött gyökérkönyvtárat ilyenkor is vegyük fel.
                paths.insert(0, self.root)
            if not paths:
                self.failed.emit(f"Az Android-tárhely üres vagy nem listázható: {self.root}")
                return

            records = {}
            for i, path in enumerate(paths):
                if i % 10 == 0:
                    self.progress.emit(f"Android-könyvtárak felmérése: {i+1}/{len(paths)}")

                du = self.command("shell", "du", "-sk", path, timeout=45)
                listing = self.command("shell", "ls", "-la", path, timeout=25)
                stat = self.command("shell", "stat", "-c", "%Y", path, timeout=10)

                try:
                    size = int(du.stdout.split()[0]) * 1024 if du.returncode == 0 else None
                except (ValueError, IndexError):
                    size = None
                try:
                    mtime = float(stat.stdout.strip().splitlines()[0]) if stat.returncode == 0 else None
                except (ValueError, IndexError):
                    mtime = None

                # Az Android ls -la kimenetének fájlneve a 8. mezőtől kezdődik,
                # így a szóközt tartalmazó neveket is egyben tartjuk.
                children_count = 0
                if listing.returncode == 0:
                    for line in listing.stdout.splitlines():
                        parts = line.split(None, 7)
                        if len(parts) >= 8 and parts[0][:1] in ("d", "-", "l"):
                            if parts[7] not in (".", ".."):
                                children_count += 1

                records[path] = {
                    "name": path.rstrip("/").split("/")[-1] or path,
                    "path": path, "dir": True, "size": size, "mtime": mtime,
                    "children": [], "count": children_count,
                    "readable": listing.returncode == 0,
                    "writable": False,
                    "complete": size is not None and listing.returncode == 0,
                    "permissions": "—",
                    "type": "Könyvtár",
                }

            # A fájlok listázása is közvetlen ADB-paranccsal történik.
            for i, path in enumerate(paths):
                if i % 10 == 0:
                    self.progress.emit(f"Android-fájlok feldolgozása: {i+1}/{len(paths)}")
                listing = self.command("shell", "ls", "-la", path, timeout=25)
                if listing.returncode:
                    records[path]["readable"] = False
                    records[path]["complete"] = False
                    continue

                for line in listing.stdout.splitlines():
                    parts = line.split(None, 7)
                    if len(parts) < 8 or parts[0][:1] not in ("-", "l"):
                        continue
                    name = parts[7]
                    if name in (".", ".."):
                        continue
                    try:
                        size = int(parts[4])
                    except ValueError:
                        size = None
                    file_path = path.rstrip("/") + "/" + name
                    records[file_path] = {
                        "name": name, "path": file_path, "dir": False,
                        "size": size, "mtime": None, "children": [], "count": 0,
                        "readable": True, "writable": False,
                        "complete": size is not None,
                        "permissions": parts[0],
                        "type": (name.rsplit(".", 1)[-1].upper() + " fájl") if "." in name and not name.startswith(".") else "Fájl",
                    }

            for path, record in records.items():
                if path == self.root:
                    continue
                parent_path = path.rsplit("/", 1)[0] or "/"
                parent = records.get(parent_path)
                if parent is not None:
                    parent["children"].append(record)

            self.finished.emit(records[self.root])
        except FileNotFoundError:
            self.failed.emit("Az adb program nem található.")
        except subprocess.TimeoutExpired as exc:
            self.failed.emit(f"Az ADB-parancs időtúllépés miatt leállt: {exc}")
        except Exception as exc:
            self.failed.emit(f"Tárhelyfelmérési hiba: {exc}")

class SunburstWidget(QWidget):
    pathClicked = pyqtSignal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setMouseTracking(True)
        self.root = None
        self.current_path = ROOT_PATH
        self.selected_path = ROOT_PATH
        self.segments = []

    def find(self, node, path):
        if node is None: return None
        if node.path == path: return node
        for child in node.children:
            result = self.find(child, path)
            if result: return result
        return None

    def set_tree(self, root, current, selected):
        self.root, self.current_path, self.selected_path = root, current, selected
        self.rebuild()
        self.update()

    def rebuild(self):
        self.segments = []
        node = self.find(self.root, self.current_path)
        if not node: return
        palette = ["#2da69e", "#4369bd", "#7546a1", "#a42b66", "#c62945",
                   "#df5a12", "#e7b322", "#70b94e", "#2786a1"]
        def visit(parent, depth, start, span):
            if depth > 5: return
            children = [c for c in parent.children if c.is_dir and c.size and c.size > 0]
            total = sum(c.size for c in children)
            if not total: return
            angle = start
            for i, child in enumerate(sorted(children, key=lambda x: x.size or 0, reverse=True)):
                part = span * child.size / total
                color = QColor(palette[(depth + i) % len(palette)])
                if not child.readable: color = QColor("#b71c1c")
                elif not child.complete: color = QColor("#d4a017")
                elif child.writable: color = QColor("#239b56")
                self.segments.append((child, depth, angle, part, color))
                visit(child, depth + 1, angle, part)
                angle += part
        visit(node, 1, -90, 360)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        cx, cy = self.width()/2, self.height()/2
        radius = min(self.width(), self.height()) * .43
        import math
        for node, depth, start, span, color in self.segments:
            if span < .2: continue
            r1, r2 = radius*(depth-1)/5.0, radius*depth/5.0
            steps = max(3, int(span/3))
            outer, inner = [], []
            for i in range(steps+1):
                a = math.radians(start + span*i/steps)
                outer.append((cx+r2*math.cos(a), cy+r2*math.sin(a)))
                inner.append((cx+r1*math.cos(a), cy+r1*math.sin(a)))
            path = QPainterPath()
            path.moveTo(*outer[0])
            for pt in outer[1:]: path.lineTo(*pt)
            for pt in reversed(inner): path.lineTo(*pt)
            path.closeSubpath()
            p.setPen(QPen(self.palette().base().color(), 1))
            p.setBrush(color)
            p.drawPath(path)
            if node.path == self.selected_path:
                p.setPen(QPen(self.palette().text().color(), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        current = self.find(self.root, self.current_path)
        p.setPen(self.palette().text().color())
        p.setFont(QFont(self.font().family(), 10, QFont.Weight.DemiBold))
        p.drawText(int(cx-radius*.45), int(cy-15), int(radius*.9), 25,
                   Qt.AlignmentFlag.AlignCenter, human_size(current.size if current else None))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(int(cx-radius*.45), int(cy+8), int(radius*.9), 25,
                   Qt.AlignmentFlag.AlignCenter, current.name if current else "Nincs adat")

    def hit(self, pos):
        import math
        cx, cy = self.width()/2, self.height()/2
        dx, dy = pos.x()-cx, pos.y()-cy
        r = math.hypot(dx, dy)
        radius = min(self.width(), self.height())*.43
        if r <= 12 or r > radius: return None
        angle = (math.degrees(math.atan2(dy, dx))+90) % 360
        depth = int(r/(radius/5.0))+1
        for node, d, start, span, color in reversed(self.segments):
            if d == depth and (angle-start) % 360 <= span: return node
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            node = self.hit(event.position())
            if node: self.pathClicked.emit(node.path)

    def mouseMoveEvent(self, event):
        node = self.hit(event.position())
        self.setToolTip(f"{node.path}\nMéret: {human_size(node.size)}" if node else "")

class StorageWidget(QWidget):
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings or {}
        self.adb = shutil.which("adb") or "/usr/bin/adb"
        self.serial = ""
        self.root_path = ROOT_PATH
        self.current_path = ROOT_PATH
        self.selected_path = ROOT_PATH
        self.root_node = None
        self.nodes = {}
        self.history = [ROOT_PATH]
        self.history_index = 0
        self.thread = None
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Közös készülékcsatlakozási sor – a google_accounts modul mintájára.
        device_bar = QHBoxLayout()
        device_bar.addWidget(QLabel("ADB-készülék:"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(300)
        self.device_combo.setToolTip("A tárhelyelemzés ehhez a kiválasztott ADB-készülékhez kapcsolódik.")
        device_bar.addWidget(self.device_combo, 1)
        self.device_refresh = QPushButton("Eszközök frissítése")
        self.device_refresh.clicked.connect(self.refresh_devices)
        device_bar.addWidget(self.device_refresh)
        layout.addLayout(device_bar)

        bar = QHBoxLayout()
        self.back = QPushButton("← Vissza")
        self.forward = QPushButton("Előre →")
        self.up = QPushButton("↑ Egy szinttel feljebb")
        self.home = QPushButton("⌂ Gyökér")
        self.refresh = QPushButton("⟳  Újramérés")
        self.refresh.setStyleSheet(
            "QPushButton { background:#174a8b; color:#ffffff; border:1px solid #4b86d1;"
            " border-radius:4px; padding:6px 12px; font-weight:bold; }"
            "QPushButton:hover { background:#2163b5; }"
            "QPushButton:disabled { background:#30343a; color:#8b929b; }"
        )
        self.settings_button = QPushButton("🔧  Beállítások")
        self.settings_button.setToolTip("A bal oldali lista oszlopainak beállítása")
        self.settings_button.clicked.connect(self.open_column_settings)
        for b in (self.back, self.forward, self.up, self.home): 
            b.setStyleSheet(
                "QPushButton { background:#292d33; color:#dce2ea; border:1px solid #414852;"
                " border-radius:3px; padding:6px 9px; }"
                "QPushButton:hover { background:#373e47; }"
            )
            bar.addWidget(b)
        bar.addWidget(self.refresh)
        bar.addWidget(self.settings_button)
        layout.addLayout(bar)
        self.path_label = QLabel(ROOT_PATH)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.status = QLabel("Csatlakoztasd a telefont ADB-n keresztül, majd indítsd az újramérést.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0); self.progress.hide()
        layout.addWidget(self.progress)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.column_defs = [
            ("name", "Könyvtár / fájl"),
            ("size", "Méret"),
            ("count", "Tartalom"),
            ("modified", "Módosítva"),
            ("permissions", "Jogosultságok"),
            ("type", "Fájltípus"),
        ]
        self.column_settings = self.load_column_settings()
        self.tree.setColumnCount(len(self.column_defs))
        self.tree.setHeaderLabels([title for _key, title in self.column_defs])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().sectionResized.connect(self.save_column_settings)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.itemClicked.connect(self.tree_clicked)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1,2,3): self.tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.tree)
        self.chart = SunburstWidget()
        self.chart.pathClicked.connect(self.chart_clicked)
        splitter.addWidget(self.chart)
        splitter.setStretchFactor(0, 5); splitter.setStretchFactor(1, 5)
        layout.addWidget(splitter, 1)
        self.back.clicked.connect(self.go_back)
        self.forward.clicked.connect(self.go_forward)
        self.up.clicked.connect(self.go_up)
        self.home.clicked.connect(self.go_home)
        self.refresh.clicked.connect(self.scan)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        self.apply_column_settings()
        self.update_nav()
        self.refresh_devices()

    def load_column_settings(self):
        defaults = {
            "visible": ["name", "size", "count"],
            "widths": {},
        }
        try:
            data = json.loads(STORAGE_SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                visible = data.get("visible")
                widths = data.get("widths")
                if isinstance(visible, list):
                    defaults["visible"] = [key for key, _ in self.column_defs if key in visible]
                    if "name" not in defaults["visible"]:
                        defaults["visible"].insert(0, "name")
                if isinstance(widths, dict):
                    defaults["widths"] = {str(k): int(v) for k, v in widths.items()
                                          if str(k) in {key for key, _ in self.column_defs}}
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def save_column_settings(self, *args):
        if not hasattr(self, "column_defs") or not hasattr(self, "tree"):
            return
        self.column_settings["widths"] = {
            key: self.tree.columnWidth(i)
            for i, (key, _title) in enumerate(self.column_defs)
        }
        self.column_settings["visible"] = [
            key for i, (key, _title) in enumerate(self.column_defs)
            if not self.tree.isColumnHidden(i)
        ]
        try:
            STORAGE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            STORAGE_SETTINGS_PATH.write_text(
                json.dumps(self.column_settings, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError as exc:
            if hasattr(self, "status"):
                self.status.setText(f"A tárhelybeállítások mentése sikertelen: {exc}")

    def apply_column_settings(self):
        visible = set(self.column_settings.get("visible", ["name", "size", "count"]))
        widths = self.column_settings.get("widths", {})
        for i, (key, _title) in enumerate(self.column_defs):
            self.tree.setColumnHidden(i, key not in visible)
            if key in widths and int(widths[key]) > 20:
                self.tree.setColumnWidth(i, int(widths[key]))
        # Useful defaults if no previous width has been saved.
        for key, width in {"name": 240, "size": 90, "count": 85, "modified": 150,
                           "permissions": 120, "type": 130}.items():
            i = next((idx for idx, item in enumerate(self.column_defs) if item[0] == key), None)
            if i is not None and key not in widths:
                self.tree.setColumnWidth(i, width)

    def open_column_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Tárhelyelemző – Beállítások")
        dialog.setMinimumWidth(320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("A bal oldali listában megjelenő oszlopok:"))
        checks = {}
        visible = set(self.column_settings.get("visible", ["name", "size", "count"]))
        for key, title in self.column_defs:
            check = QCheckBox(title)
            check.setChecked(key in visible)
            if key == "name":
                check.setEnabled(False)
                check.setToolTip("A névoszlop mindig látható marad.")
            layout.addWidget(check)
            checks[key] = check
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.column_settings["visible"] = [
                key for key, _title in self.column_defs if checks[key].isChecked()
            ]
            self.apply_column_settings()
            self.save_column_settings()

    def refresh_devices(self):
        previous = self.device_combo.currentData()
        self.device_combo.clear()
        try:
            result = subprocess.run([self.adb, "devices", "-l"], capture_output=True,
                                    text=True, errors="replace", timeout=10, check=False)
            if result.returncode != 0:
                self.device_combo.addItem("ADB-hiba: nem olvasható az eszközlista", "")
                self.status.setText((result.stderr or "Az adb devices -l parancs hibát adott.").strip())
                return
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 2:
                    continue
                serial, state = parts[0], parts[1]
                detail = " ".join(parts[2:])
                label = f"{serial} — {state}" + (f" ({detail})" if detail else "")
                self.device_combo.addItem(label, serial if state == "device" else "")
                idx = self.device_combo.count() - 1
                if state != "device":
                    self.device_combo.setItemData(idx, f"Az eszköz állapota: {state}", Qt.ItemDataRole.ToolTipRole)
            if not self.device_combo.count():
                self.device_combo.addItem("Nincs csatlakoztatott ADB-eszköz", "")
            if previous:
                idx = self.device_combo.findData(previous)
                if idx >= 0: self.device_combo.setCurrentIndex(idx)
            if not self.device_combo.currentData():
                for i in range(self.device_combo.count()):
                    if self.device_combo.itemData(i):
                        self.device_combo.setCurrentIndex(i)
                        break
            self.status.setText("Válaszd ki a készüléket, majd indítsd az újramérést." if self.device_combo.currentData()
                                else "ADB elérhető, de nincs engedélyezett (device állapotú) készülék.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.device_combo.addItem("Az ADB eszközlista nem érhető el", "")
            self.status.setText(f"ADB-hiba: {exc}")

    def _device_changed(self, _index):
        if hasattr(self, "status") and self.device_combo.currentData():
            self.status.setText(f"Kiválasztott készülék: {self.device_combo.currentData()}")

    def selected_serial(self):
        return str(self.device_combo.currentData() or "")

    def scan(self):
        if self.thread and self.thread.isRunning(): return
        if not os.path.exists(self.adb):
            self.adb = shutil.which("adb") or self.adb
        self.serial = self.selected_serial()
        if not self.serial:
            QMessageBox.warning(self, "Nincs kiválasztott készülék",
                                "Válassz ki egy engedélyezett, 'device' állapotú ADB-készüléket a felső listában.")
            return
        self.thread = QThread(self)
        self.worker = ScanWorker(self.adb, self.serial, self.root_path)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status.setText)
        self.worker.failed.connect(self.scan_failed)
        self.worker.finished.connect(self.scan_finished)
        self.worker.failed.connect(self.thread.quit)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup_thread)
        self.progress.show(); self.refresh.setEnabled(False)
        self.device_refresh.setEnabled(False); self.device_combo.setEnabled(False)
        self.status.setText(f"Tárhelyfelmérés indul ezen a készüléken: {self.serial}")
        self.thread.start()

    def cleanup_thread(self):
        self.progress.hide(); self.refresh.setEnabled(True)
        self.device_refresh.setEnabled(True); self.device_combo.setEnabled(True)
        if self.worker: self.worker.deleteLater()
        self.worker = None; self.thread = None

    def scan_failed(self, message):
        self.status.setText("A tárhelyfelmérés nem sikerült.")
        QMessageBox.warning(self, "Tárhelyelemző", message)

    def scan_finished(self, data):
        self.root_node = self.from_dict(data)
        self.nodes = {}
        self.index(self.root_node)
        self.current_path = self.selected_path = self.root_path
        self.history, self.history_index = [self.root_path], 0
        self.render_tree(); self.update_chart(); self.update_nav()
        self.status.setText(f"Felmérés kész. Gyökérméret: {human_size(self.root_node.size)}")

    def from_dict(self, d):
        n = Node(d["name"], d["path"], bool(d["dir"]), d.get("size"), d.get("mtime"),
                 [], int(d.get("count", 0)), bool(d.get("readable", True)),
                 bool(d.get("writable", False)), bool(d.get("complete", True)))
        n.permissions = str(d.get("permissions", "—"))
        n.file_type = str(d.get("type", "Könyvtár" if n.is_dir else "Fájl"))
        n.children = [self.from_dict(x) for x in d.get("children", [])]
        return n

    def index(self, node):
        self.nodes[node.path] = node
        for child in node.children: self.index(child)

    def render_tree(self):
        self.tree.clear()
        def add(parent, node):
            item = QTreeWidgetItem([
                node.name, human_size(node.size),
                str(node.count) if node.is_dir else "—",
                fmt_time(node.mtime), node.permissions, node.file_type
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, node.path)
            item.setData(0, Qt.ItemDataRole.UserRole+1, node.is_dir)
            if not node.readable: color = "#b71c1c"
            elif not node.complete: color = "#c58a00"
            elif node.writable: color = "#239b56"
            else: color = ""
            if color: item.setForeground(0, QBrush(QColor(color)))
            if parent is None: self.tree.addTopLevelItem(item)
            else: parent.addChild(item)
            for child in node.children: add(item, child)
        add(None, self.root_node)
        self.tree.expandToDepth(1)
        self.select_path(self.current_path)

    def find_item(self, path):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, Qt.ItemDataRole.UserRole) == path: return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def select_path(self, path):
        item = self.find_item(path)
        if item:
            ancestors = []
            parent = item.parent()
            while parent: ancestors.append(parent); parent = parent.parent()
            for ancestor in reversed(ancestors): ancestor.setExpanded(True)
            self.tree.setCurrentItem(item); self.tree.scrollToItem(item)

    def tree_clicked(self, item, column):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        is_dir = item.data(0, Qt.ItemDataRole.UserRole+1)
        self.selected_path = path
        self.navigate(path if is_dir else str(PurePosixPath(path).parent))

    def chart_clicked(self, path):
        self.selected_path = path
        self.navigate(path)

    def navigate(self, path, record=True):
        if path not in self.nodes: return
        self.current_path = self.selected_path = path
        if record:
            self.history = self.history[:self.history_index+1]
            if not self.history or self.history[-1] != path:
                self.history.append(path); self.history_index = len(self.history)-1
        self.path_label.setText(path)
        self.select_path(path)
        self.update_chart(); self.update_nav()

    def update_chart(self):
        self.chart.set_tree(self.root_node, self.current_path, self.selected_path)

    def update_nav(self):
        self.back.setEnabled(self.history_index > 0)
        self.forward.setEnabled(self.history_index < len(self.history)-1)
        self.up.setEnabled(self.current_path != self.root_path)
        self.home.setEnabled(self.current_path != self.root_path)

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            self.navigate(self.history[self.history_index], False)

    def go_forward(self):
        if self.history_index < len(self.history)-1:
            self.history_index += 1
            self.navigate(self.history[self.history_index], False)

    def go_up(self):
        if self.current_path != self.root_path:
            self.navigate(str(PurePosixPath(self.current_path).parent))

    def go_home(self):
        self.navigate(self.root_path)

    def context_menu(self, pos: QPoint):
        item = self.tree.itemAt(pos)
        if not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        open_window = menu.addAction("Megnyitás külön ablakban")
        open_workspace = menu.addAction("Megnyitás a munkaterületen belül")
        menu.addSeparator()
        copy = menu.addAction("Elérési út másolása")
        details = menu.addAction("Részletes adatok")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == copy:
            QApplication.clipboard().setText(path)
        elif chosen == details:
            n = self.nodes.get(path)
            if n:
                QMessageBox.information(
                    self, "Részletes adatok",
                    f"Név: {n.name}\\nÚtvonal: {n.path}\\nMéret: {human_size(n.size)}\\n"
                    f"Elemek: {n.count}\\nMódosítva: {fmt_time(n.mtime)}\\n"
                    f"Olvasható: {'igen' if n.readable else 'nem'}\\n"
                    f"Teljes mérés: {'igen' if n.complete else 'nem'}"
                )
        elif chosen == open_window:
            self.open_file_manager(path, mode="window")
        elif chosen == open_workspace:
            self.open_file_manager(path, mode="workspace")

    def open_file_manager(self, path, mode="window"):
        """A kiválasztott Android-útvonal megnyitása külön ablakban vagy a fő munkaterületen."""
        try:
            try:
                from .file_manager import FileManagerWindow
            except ImportError:
                from file_manager import FileManagerWindow

            window = FileManagerWindow(
                parent=None,
                settings=self.settings,
                initial_remote_path=path,
                serial=self.serial,
            )

            if mode == "workspace":
                host = self.window()
                views = getattr(host, "views", None)
                module_pages = getattr(host, "_module_pages", None)
                if views is None or module_pages is None:
                    window.close()
                    raise RuntimeError("A főablak munkaterülete nem található.")

                page_key = "Fájlkezelő (Tárhely)"
                old_page = module_pages.get(page_key)
                if old_page is not None:
                    index = views.indexOf(old_page)
                    if index >= 0:
                        views.removeWidget(old_page)
                    old_page.deleteLater()

                window.setParent(host)
                window.setWindowFlags(Qt.WindowType.Widget)
                window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
                views.addWidget(window)
                module_pages[page_key] = window
                views.setCurrentWidget(window)
                window.show()
                return

            if not getattr(self, "_file_manager_windows", None):
                self._file_manager_windows = []
            window.setWindowTitle("Redmi Control Center — Fájlkezelő")
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._file_manager_windows.append(window)
            window.destroyed.connect(
                lambda _obj=None, w=window: self._remove_file_manager_window(w)
            )
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as exc:
            QMessageBox.warning(
                self, "Fájlkezelő indítása",
                f"A fájlkezelőt nem sikerült megnyitni.\\n\\n{exc}"
            )

    def _remove_file_manager_window(self, window):
        windows = getattr(self, "_file_manager_windows", [])
        if window in windows:
            windows.remove(window)

def open_module(parent=None, settings=None):
    return StorageWidget(parent=parent, settings=settings)

def main():
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Redmi Control Center – Tárhelyelemző")
    window.resize(1250, 780)
    window.setCentralWidget(open_module())
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
