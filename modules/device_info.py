#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telefon-eszközadatok modul a Redmi Control Centerhez.

Önálló indítás: python3 device_info.py
Főprogramból: from modules.device_info import open_module
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QInputDialog, QFileDialog, QComboBox, QTextEdit, QProgressBar, QFrame
)

SETTINGS_PATH = Path.home() / ".config" / "redmi-control-center" / "settings.json"

# property, label, category
PROPERTIES = [
    ("ro.product.manufacturer", "Gyártó", "overview"),
    ("ro.product.brand", "Márka", "overview"),
    ("ro.product.model", "Modell", "overview"),
    ("ro.product.device", "Eszköznév", "overview"),
    ("ro.build.version.release", "Android-verzió", "overview"),
    ("ro.build.version.sdk", "Android API-szint", "overview"),
    ("ro.build.display.id", "Buildazonosító", "overview"),
    ("ro.build.version.security_patch", "Biztonsági javítás", "overview"),
    ("ro.miui.ui.version.name", "MIUI-verzió", "overview"),
    ("ro.mi.os.version.name", "HyperOS-verzió", "overview"),
    ("ro.product.cpu.abi", "Elsődleges ABI", "hardware"),
    ("ro.product.cpu.abilist", "Támogatott ABI-k", "hardware"),
    (("ro.hardware", "ro.boot.hardware"), "Hardverplatform", "hardware"),
    ("ro.soc.model", "SoC-modell", "hardware"),
    ("ro.soc.manufacturer", "SoC-gyártó", "hardware"),
    ("ro.build.characteristics", "Készülékjellemzők", "hardware"),
    ("ro.bootloader", "Bootloader-verzió", "connection"),
    ("ro.debuggable", "Debug build", "connection"),
    ("ro.secure", "Rendszerbiztonsági jelző", "connection"),
]

class Adb:
    def __init__(self, path: str | None = None):
        self.path = path or shutil.which("adb") or "/usr/bin/adb"

    def run(self, args, timeout=15):
        try:
            p = subprocess.run([self.path, *args], capture_output=True, text=True,
                               timeout=timeout, check=False)
        except FileNotFoundError:
            raise RuntimeError("Az adb parancs nem található. Telepítsd az Android platform-tools csomagot.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Az ADB-parancs időtúllépés miatt leállt: {' '.join(args)}")
        return p.returncode, p.stdout.strip(), p.stderr.strip()

class DeviceInfoWindow(QMainWindow):
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = dict(settings or {})
        self.adb = Adb(self.settings.get("adb_path"))
        self.serial: str | None = None
        self.devices: list[tuple[str, str]] = []
        self.data: dict[str, str] = {}
        self.raw_text = ""
        self.setWindowTitle("Redmi Control Center — Eszközadatok")
        self.resize(900, 650)
        self.setMinimumSize(700, 450)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#171a1f; color:#e6e9ef; }
            QPushButton { background:#28313b; border:1px solid #465565; border-radius:5px; padding:6px 10px; }
            QPushButton:hover { background:#354454; }
            QComboBox, QTableWidget, QTextEdit { background:#101317; color:#e6e9ef; border:1px solid #39434e; padding:4px; }
            QHeaderView::section { background:#252c34; color:#e6e9ef; padding:6px; border:0; }
            QTabBar::tab { background:#252c34; padding:9px 16px; }
            QTabBar::tab:selected { background:#354454; border-bottom:2px solid #5b9cf5; }
            QFrame#MetricCard { background:#20262e; border:1px solid #39434e; border-radius:8px; }
            QProgressBar { background:#101317; border:1px solid #39434e; border-radius:4px; height:10px; text-align:center; }
            QProgressBar::chunk { background:#5b9cf5; border-radius:3px; }
        """)
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)
        top = QHBoxLayout()
        heading = QLabel("TELEFON / ESZKÖZADATOK")
        heading.setFont(QFont("Sans Serif", 14, QFont.Weight.Bold))
        top.addWidget(heading); top.addStretch()
        self.device_label = QLabel("ADB-eszköz keresése…")
        top.addWidget(self.device_label)
        self.refresh_button = QPushButton("Frissítés")
        self.refresh_button.clicked.connect(self.refresh_devices)
        top.addWidget(self.refresh_button)
        root.addLayout(top)
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.tables = {}
        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.setContentsMargins(8, 8, 8, 8)
        overview_layout.setSpacing(10)
        self.summary_model = QLabel("Modell: —")
        self.summary_model.setStyleSheet("font-size:18px;font-weight:700;padding:8px 4px;")
        overview_layout.addWidget(self.summary_model)
        cards = QHBoxLayout()
        self.battery_card, self.battery_value, self.battery_bar = self._make_metric_card("AKKUMULÁTOR")
        self.storage_card, self.storage_value, self.storage_bar = self._make_metric_card("BELSŐ TÁRHELY")
        self.ram_card, self.ram_value, self.ram_bar = self._make_metric_card("RAM")
        for card in (self.battery_card, self.storage_card, self.ram_card):
            cards.addWidget(card, 1)
        overview_layout.addLayout(cards)
        self.tables["overview"] = self._make_table()
        overview_layout.addWidget(self.tables["overview"], 1)
        self.tabs.addTab(overview_page, "Áttekintés")
        for key, title in (("hardware", "Hardver"), ("connection", "Kapcsolat")):
            table = self._make_table()
            self.tables[key] = table
            self.tabs.addTab(table, title)
        self.raw = QTextEdit(); self.raw.setReadOnly(True)
        self.tabs.addTab(self.raw, "Részletek")
        bottom = QHBoxLayout()
        self.status = QLabel("Telefon nélkül is használható; a helyi program nem végez módosításokat.")
        self.status.setWordWrap(True); bottom.addWidget(self.status, 1)
        copy_btn = QPushButton("Adatok másolása"); copy_btn.clicked.connect(self.copy_data); bottom.addWidget(copy_btn)
        save_btn = QPushButton("Exportálás…"); save_btn.clicked.connect(self.export_data); bottom.addWidget(save_btn)
        root.addLayout(bottom)
        # Nincs modális figyelmeztetés indításkor, ha nincs telefon.
        self.refresh_devices(initial=True)

    def _make_table(self):
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Tulajdonság", "Érték"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        return table

    def _make_metric_card(self, title):
        card = QFrame()
        card.setObjectName("MetricCard")
        layout = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:10px;font-weight:700;color:#aeb9c7;letter-spacing:1px;border:0;")
        value = QLabel("—")
        value.setStyleSheet("font-size:22px;font-weight:700;border:0;")
        detail = QProgressBar()
        detail.setRange(0, 100); detail.setValue(0); detail.setTextVisible(False)
        detail.setFixedHeight(9)
        layout.addWidget(heading)
        layout.addWidget(value)
        layout.addWidget(detail)
        return card, value, detail

    @staticmethod
    def _number_before(text, suffix):
        try:
            return float(text.replace(suffix, "").strip().replace(",", "."))
        except (ValueError, AttributeError):
            return None

    def _update_metric_cards(self, data):
        model = data.get("Modell", "Nem elérhető")
        android = data.get("Android-verzió", "")
        self.summary_model.setText(f"{model}  ·  Android {android}" if android and android != "Nem elérhető" else model)
        battery = self._number_before(data.get("Akkumulátor töltöttsége", ""), "%")
        self.battery_value.setText(f"{battery:.0f}%" if battery is not None else "Nem elérhető")
        self.battery_bar.setValue(max(0, min(100, int(battery or 0))))
        total = self._number_before(data.get("Belső tárhely összesen", ""), "GiB")
        used = self._number_before(data.get("Belső tárhely használt", ""), "GiB")
        if total and used is not None:
            pct = max(0, min(100, round(used / total * 100)))
            self.storage_value.setText(f"{pct}%  ·  {used:.1f}/{total:.1f} GiB")
            self.storage_bar.setValue(pct)
        else:
            self.storage_value.setText("Nem elérhető"); self.storage_bar.setValue(0)
        total_ram = self._number_before(data.get("RAM összesen", ""), "MiB")
        avail_ram = self._number_before(data.get("RAM elérhető", ""), "MiB")
        if total_ram and avail_ram is not None:
            used_pct = max(0, min(100, round((total_ram - avail_ram) / total_ram * 100)))
            self.ram_value.setText(f"{used_pct}% használat")
            self.ram_bar.setValue(used_pct)
        else:
            self.ram_value.setText("Nem elérhető"); self.ram_bar.setValue(0)

    def refresh_devices(self, initial=False):
        previous = self.serial
        try:
            rc, out, err = self.adb.run(["devices", "-l"], timeout=12)
            if rc:
                raise RuntimeError(err or out or f"adb devices hibakód: {rc}")
            devices = []
            for line in out.splitlines()[1:]:
                bits = line.split()
                if len(bits) >= 2 and bits[1] == "device":
                    devices.append((bits[0], " ".join(bits[2:])))
            self.devices = devices
        except Exception as exc:
            self.devices = []
            self.serial = None
            self.device_label.setText("ADB nem érhető el")
            self.clear_data()
            self.status.setText(f"Az ADB-eszközlista nem kérdezhető le: {exc}")
            return

        if not devices:
            self.serial = None
            self.device_label.setText("ADB: nincs engedélyezett telefon")
            self.clear_data()
            self.status.setText("Nincs engedélyezett telefon. Csatlakoztasd, vagy folytasd később a Frissítés gombbal. Nincs felugró hibaablak.")
            return

        serials = [d[0] for d in devices]
        if len(devices) == 1:
            self.serial = devices[0][0]
        else:
            preferred = previous if previous in serials else self.settings.get("serial")
            if preferred not in serials:
                preferred = None
            labels = [f"{s} — {extra}" if extra else s for s, extra in devices]
            current = serials.index(preferred) if preferred in serials else 0
            label, ok = QInputDialog.getItem(self, "ADB-eszköz kiválasztása", "Aktív telefon:", labels, current, False)
            if ok and label in labels:
                self.serial = devices[labels.index(label)][0]
            elif preferred:
                self.serial = preferred
            else:
                self.serial = None
                self.device_label.setText("ADB: nincs kiválasztott telefon")
                self.clear_data()
                self.status.setText("Nem történt eszközválasztás. Válassz telefont a Frissítés gombbal.")
                return
        self.device_label.setText(f"ADB: {self.serial}")
        self.settings["serial"] = self.serial
        self.load_device_data()

    def clear_data(self):
        self.data = {}; self.raw_text = ""
        for table in self.tables.values(): table.setRowCount(0)
        self.raw.clear()
        self.summary_model.setText("Modell: —")
        for label, bar in ((self.battery_value, self.battery_bar), (self.storage_value, self.storage_bar), (self.ram_value, self.ram_bar)):
            label.setText("—"); bar.setValue(0)

    def _getprop(self, prop):
        if isinstance(prop, tuple):
            for item in prop:
                value = self._getprop(item)
                if value and value != "Nem elérhető": return value
            return "Nem elérhető"
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "getprop", prop], timeout=12)
            return out if rc == 0 and out else "Nem elérhető"
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return "Nem elérhető"

    def load_device_data(self):
        if not self.serial:
            self.clear_data(); return
        self.status.setText("Eszközadatok lekérdezése…")
        QApplication.processEvents()
        data = {label: self._getprop(prop) for prop, label, _ in PROPERTIES}
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "cat", "/proc/meminfo"], timeout=12)
            if rc == 0:
                mem = {}
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] in ("MemTotal:", "MemAvailable:"):
                        mem[parts[0]] = int(parts[1])
                if "MemTotal:" in mem: data["RAM összesen"] = f"{mem['MemTotal:'] / 1024:.0f} MiB"
                if "MemAvailable:" in mem: data["RAM elérhető"] = f"{mem['MemAvailable:'] / 1024:.0f} MiB"
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "df", "-k", "/data"], timeout=12)
            lines = out.splitlines()
            if rc == 0 and len(lines) > 1:
                parts = lines[-1].split()
                if len(parts) >= 4:
                    data["Belső tárhely összesen"] = f"{int(parts[1]) / 1024 / 1024:.2f} GiB"
                    data["Belső tárhely használt"] = f"{int(parts[2]) / 1024 / 1024:.2f} GiB"
                    data["Belső tárhely szabad"] = f"{int(parts[3]) / 1024 / 1024:.2f} GiB"
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "dumpsys", "battery"], timeout=15)
            if rc == 0:
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("level:"): data["Akkumulátor töltöttsége"] = line.split(":",1)[1].strip() + "%"
                    elif line.startswith("temperature:"):
                        try: data["Akkumulátor hőmérséklete"] = f"{int(line.split(':',1)[1].strip()) / 10:.1f} °C"
                        except ValueError: pass
                    elif line.startswith("status:"): data["Akkumulátor állapota (kód)"] = line.split(":",1)[1].strip()
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "wm", "size"], timeout=12)
            if rc == 0 and out: data["Kijelző felbontása"] = out.splitlines()[-1].replace("Physical size:", "").replace("Override size:", "Felülírt: ").strip()
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "wm", "density"], timeout=12)
            if rc == 0 and out: data["Kijelző képpontsűrűsége"] = out.splitlines()[-1].replace("Physical density:", "").replace("Override density:", "Felülírt: ").strip()
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "ip", "addr", "show", "wlan0"], timeout=12)
            if rc == 0:
                import re
                ips = re.findall(r"inet ([0-9.]+)/", out)
                data["Wi-Fi IPv4-cím"] = ", ".join(ips) if ips else "Nem elérhető"
        except Exception: pass
        try:
            rc, out, err = self.adb.run(["-s", self.serial, "shell", "id"], timeout=12)
            data["Root / shell UID"] = "Root (UID 0)" if rc == 0 and "uid=0(" in out else ("Shell elérhető, UID 0 nélkül" if rc == 0 else "Nem elérhető")
        except Exception: pass
        data["ADB-sorozatszám"] = self.serial
        self.data = data
        self.raw_text = json.dumps({"serial": self.serial, "queried_at": datetime.now().isoformat(timespec="seconds"), "properties": data}, ensure_ascii=False, indent=2)
        categories = {
            "overview": ["Gyártó", "Márka", "Modell", "Eszköznév", "Android-verzió", "Android API-szint", "MIUI-verzió", "HyperOS-verzió", "Buildazonosító", "Biztonsági javítás", "Akkumulátor töltöttsége", "Akkumulátor hőmérséklete", "Akkumulátor állapota (kód)"],
            "hardware": ["Elsődleges ABI", "Támogatott ABI-k", "Hardverplatform", "SoC-modell", "SoC-gyártó", "Készülékjellemzők", "RAM összesen", "RAM elérhető", "Belső tárhely összesen", "Belső tárhely használt", "Belső tárhely szabad", "Kijelző felbontása", "Kijelző képpontsűrűsége"],
            "connection": ["ADB-sorozatszám", "Wi-Fi IPv4-cím", "Root / shell UID", "Bootloader-verzió", "Debug build", "Rendszerbiztonsági jelző"],
        }
        self._update_metric_cards(data)
        for key, labels in categories.items():
            table = self.tables[key]; table.setRowCount(len(labels))
            for row, label in enumerate(labels):
                table.setItem(row, 0, QTableWidgetItem(label))
                table.setItem(row, 1, QTableWidgetItem(str(data.get(label, "Nem elérhető"))))
        self.raw.setPlainText(self.raw_text)
        self.status.setText(f"Lekérdezés kész: {self.serial} · {len(data)} adatmező. A nem elérhető értékek nem feltétlenül jelentenek hibát.")

    def copy_data(self):
        if not self.data:
            self.status.setText("Nincs másolható eszközadat."); return
        QApplication.clipboard().setText(self.raw_text)
        self.status.setText("Az eszközadatok JSON-formátumban a vágólapra kerültek.")

    def export_data(self):
        if not self.data:
            QMessageBox.information(self, "Exportálás", "Nincs exportálható eszközadat."); return
        path, selected = QFileDialog.getSaveFileName(self, "Eszközadatok mentése", f"device-info-{self.serial or 'nincs-eszkoz'}.json", "JSON (*.json);;Szövegfájl (*.txt)")
        if not path: return
        try:
            if path.lower().endswith(".txt"):
                content = "Redmi Control Center — Eszközadatok\n" + "=" * 36 + "\n" + "\n".join(f"{k}: {v}" for k,v in self.data.items()) + "\n"
            else:
                if not path.lower().endswith(".json"): path += ".json"
                content = self.raw_text
            Path(path).write_text(content, encoding="utf-8")
            self.status.setText(f"Mentve: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Exportálási hiba", str(exc))

def open_module(parent=None, settings=None):
    """Belépési pont a főprogram modulbetöltőjéhez."""
    return DeviceInfoWindow(parent=parent, settings=settings)

def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = DeviceInfoWindow(); window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
