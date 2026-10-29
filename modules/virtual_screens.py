#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redmi Control Center — virtuális képernyők és alkalmazásindítás (PyQt6/ADB).

Beillesztés a projektbe: modules/virtual_screens.py
Főablakból: from modules.virtual_screens import VirtualScreensWidget
            widget = VirtualScreensWidget(parent, settings)
Önálló próba: python3 modules/virtual_screens.py

A modul nem rootol és nem módosítja a telefont. ADB + scrcpy szükséges.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QTextCharFormat, QTextCursor, QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QScrollArea, QDialog, QDialogButtonBox,
    QLabel, QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QGroupBox, QFormLayout, QSpinBox, QCheckBox, QMessageBox,
    QInputDialog, QFrame, QSizePolicy, QTabWidget, QTextEdit
)

APP_TITLE = "Virtuális képernyők és alkalmazások"
CONFIG_DIR = Path.home() / ".config" / "redmi-control-center"
CONFIG_FILE = CONFIG_DIR / "virtual_screens.json"
ICON_CACHE_DIR = Path.home() / ".cache" / "redmi-control-center" / "app-icons"

# Barátságos nevek a korábban azonosított alkalmazásokhoz; más appoknál
# a modul a csomagnevet használja, mert az ADB nem ad egyszerű, stabil
# parancsot az összes alkalmazás felhasználói címkéjének lekérésére.
KNOWN_APPS = {
    "app.organicmaps": "Organic Maps",
    "com.nickobrennan.pocketgrib": "PocketGrib",
    "com.revolut.revolut": "Revolut",
    "website.marty.detexify": "Detexify",
    "com.whatsapp": "WhatsApp",
    "com.TrofyGames.UboatAttack": "Uboat Attack",
    "com.dalakgames.flakfury": "Flak Fury",
    "com.cover.zombie.fire.shooter": "Zombie Fire Shooter",
    "org.mozilla.firefox": "Firefox",
    "org.opencpn.opencpn": "OpenCPN",
}

DEFAULT_PROFILES = [
    {"name": "Telefon – 720×1280", "width": 720, "height": 1280, "dpi": 234},
    {"name": "Álló – 800×1400", "width": 800, "height": 1400, "dpi": 240},
    {"name": "Fekvő – 1280×720", "width": 1280, "height": 720, "dpi": 240},
    {"name": "OpenCPN – 1920×1080", "width": 1920, "height": 1080, "dpi": 240},
]

STYLE = """
QWidget { font-size: 9pt; }
QFrame#connectionPanel, QGroupBox { border: 1px solid #3984a8; border-radius: 6px; margin-top: 3px; padding: 3px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #54b8df; font-weight: 600; }
QFrame#connectionPanel { background: rgba(45, 125, 160, 0.08); }
QLabel#heading { font-size: 15pt; font-weight: 700; }
QLabel#muted { color: palette(mid); }
QListWidget, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { border: 1px solid palette(mid); border-radius: 4px; padding: 3px 5px; }
QListWidget::item { padding: 3px 4px; border-radius: 4px; min-height: 36px; }
QListWidget::item:selected { background: #245f7a; color: white; }
QPushButton { border: 1px solid #54839a; border-radius: 4px; padding: 4px 8px; min-height: 22px; }
QPushButton:hover { border-color: #66c8ed; }
QPushButton#primary { background: #1976a5; color: white; border: 1px solid #2b9aca; font-weight: 700; }
QPushButton#primary:hover { background: #2189ba; }
QPushButton#save { background: #28794d; color: white; border: 1px solid #3da96d; font-weight: 600; }
QPushButton#danger { background: #8c3e3e; color: white; border: 1px solid #b85b5b; }
QFrame#separator { color: palette(mid); }
"""


def _run(args: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)


class VirtualScreensWidget(QWidget):
    """Beágyazható PyQt6-widget a főablak munkaterületéhez."""
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings or {}
        # A modul saját, visszafogott színvilága akkor is érvényesüljön,
        # ha a főprogram más globális témát használ.
        self.setStyleSheet(STYLE)
        self.adb = shutil.which("adb") or "/usr/bin/adb"
        self.scrcpy = shutil.which("scrcpy") or "/usr/local/bin/scrcpy"
        self.processes: list[QProcess] = []
        self._external_scrcpy_pids: set[int] = set()
        self._refreshing_runtime = False
        self.apps: list[dict] = []
        self._icon_cache: dict[str, QIcon] = {}
        self.profiles = [dict(x) for x in DEFAULT_PROFILES]
        self.app_settings: dict[str, dict] = {}
        self.favorites: set[str] = set()
        self._loading = False
        self._command_manual = False
        self._updating_command = False
        self._build_ui()
        self._load_config()
        self._refresh_profile_combo()
        self.refresh_devices()
        self.refresh_apps()
        self._append_log(f"Modul elindult. ADB={self.adb}; scrcpy={self.scrcpy}; konfiguráció={CONFIG_FILE}")
        self._set_status("Készen áll. Válassz alkalmazást és indítási profilt.")
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(3000)
        self.runtime_timer.timeout.connect(self.refresh_runtime_status)
        self.runtime_timer.start()
        self.refresh_runtime_status()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(7, 7, 7, 7)
        root.setSpacing(4)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        main_page = QWidget()
        page_layout = QVBoxLayout(main_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(4)
        self.tabs.addTab(main_page, "Virtuális képernyők")

        # Állapotsáv: a saját QProcess-ek mellett a korábbi indításból
        # visszamaradt, külső scrcpy-példányokat is számolja.
        runtime_panel = QFrame(); runtime_panel.setObjectName("connectionPanel")
        runtime_row = QHBoxLayout(runtime_panel); runtime_row.setContentsMargins(6, 3, 6, 3)
        runtime_row.setSpacing(6)
        self.runtime_adb_label = QLabel("ADB: ellenőrzés…")
        self.runtime_scrcpy_label = QLabel("Futó scrcpy: —")
        self.runtime_display_label = QLabel("Virtuális kijelzők: —")
        runtime_row.addWidget(self.runtime_adb_label)
        runtime_row.addWidget(self.runtime_scrcpy_label)
        runtime_row.addWidget(self.runtime_display_label, 1)
        self.runtime_refresh_btn = QPushButton("Állapot frissítése")
        self.runtime_refresh_btn.clicked.connect(self.refresh_runtime_status)
        runtime_row.addWidget(self.runtime_refresh_btn)
        self.stop_all_btn = QPushButton("Összes scrcpy leállítása")
        self.stop_all_btn.setObjectName("danger")
        self.stop_all_btn.clicked.connect(self.stop_all_scrcpy)
        runtime_row.addWidget(self.stop_all_btn)
        page_layout.addWidget(runtime_panel)

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_header = QLabel("Részletes működési napló — ADB, scrcpy, parancsok és folyamatállapot")
        log_header.setObjectName("muted")
        log_layout.addWidget(log_header)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_edit.setPlaceholderText("A működési események itt jelennek meg…")
        log_layout.addWidget(self.log_edit, 1)
        log_buttons = QHBoxLayout()
        copy_log_btn = QPushButton("Napló másolása")
        copy_log_btn.clicked.connect(self.copy_log)
        log_buttons.addWidget(copy_log_btn)
        clear_log_btn = QPushButton("Napló ürítése")
        clear_log_btn.clicked.connect(self.clear_log)
        log_buttons.addWidget(clear_log_btn)
        log_layout.addLayout(log_buttons)
        self.tabs.addTab(log_page, "Napló")
        # Nincs külön címsor: a felső fül már azonosítja ezt a nézetet.
        # A kapcsolat és a szűrés két kompakt oszlopba kerül.
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)

        conn = QFrame(); conn.setObjectName("connectionPanel")
        ch = QHBoxLayout(conn); ch.setContentsMargins(6, 3, 6, 3); ch.setSpacing(5)
        ch.addWidget(QLabel("Telefon:"))
        self.device_combo = QComboBox(); self.device_combo.setMinimumWidth(100); ch.addWidget(self.device_combo, 1)
        self.connection_status = QLabel("Nincs ellenőrizve"); ch.addWidget(self.connection_status)
        refresh_dev = QPushButton("Frissítés"); refresh_dev.setToolTip("Telefonkapcsolat frissítése")
        refresh_dev.clicked.connect(self.refresh_devices); ch.addWidget(refresh_dev)
        controls_row.addWidget(conn, 3)

        filter_panel = QFrame(); filter_panel.setObjectName("connectionPanel")
        fh = QHBoxLayout(filter_panel); fh.setContentsMargins(6, 3, 6, 3); fh.setSpacing(5)
        self.app_type_filter = QComboBox()
        self.app_type_filter.addItem("Minden alkalmazás", "all")
        self.app_type_filter.addItem("Felhasználói", "user")
        self.app_type_filter.addItem("Rendszer", "system")
        self.app_type_filter.setMinimumWidth(115)
        self.app_type_filter.currentIndexChanged.connect(self._filter_apps)
        fh.addWidget(self.app_type_filter, 1)
        self.favorites_only = QCheckBox("Csak kedvencek")
        self.favorites_only.toggled.connect(self._filter_apps)
        fh.addWidget(self.favorites_only)
        controls_row.addWidget(filter_panel, 2)
        page_layout.addLayout(controls_row)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Keresés név vagy csomagnév szerint…")
        self.search_edit.textChanged.connect(self._filter_apps)
        search_row.addWidget(self.search_edit, 1)
        reload_apps = QPushButton("Lista frissítése")
        reload_apps.clicked.connect(self.refresh_apps)
        search_row.addWidget(reload_apps)
        page_layout.addLayout(search_row)

        # Rejtett kompatibilitási lista: a meglévő kiválasztási és parancslogika ezt használja.
        self.app_list = QListWidget(self); self.app_list.hide()
        self.app_list.currentItemChanged.connect(self._app_selected)
        self.cards_area = QScrollArea(); self.cards_area.setWidgetResizable(True); self.cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget(); self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(1, 1, 1, 1); self.cards_layout.setSpacing(4)
        self.cards_area.setWidget(self.cards_host); page_layout.addWidget(self.cards_area, 1)

        # Beállítások és indítási parancs: külön modal ablakban jelenik meg.
        self.profile_combo = QComboBox()
        self.profile_name = QLineEdit(); self.profile_name.setPlaceholderText("Profil neve")
        self.width_spin = QSpinBox(); self.width_spin.setRange(240, 4096); self.width_spin.setSingleStep(80)
        self.height_spin = QSpinBox(); self.height_spin.setRange(320, 4096); self.height_spin.setSingleStep(80)
        self.dpi_spin = QSpinBox(); self.dpi_spin.setRange(72, 640); self.dpi_spin.setValue(240)
        self.crop_enabled = QCheckBox("Kivágás bekapcsolása")
        self.force_resize_check = QCheckBox("Alkalmazás átméretezhetőségének kényszerítése")
        self.force_resize_check.setToolTip(
            "Android globális fejlesztői beállítás: engedélyezi az átméretezhető Activity-ket. "
            "Nem írja felül minden alkalmazás saját tájolási korlátozását."
        )
        self.crop_w = QSpinBox(); self.crop_w.setRange(1,4096); self.crop_w.setValue(720)
        self.crop_h = QSpinBox(); self.crop_h.setRange(1,4096); self.crop_h.setValue(1200)
        self.crop_x = QSpinBox(); self.crop_x.setRange(0,4096)
        self.crop_y = QSpinBox(); self.crop_y.setRange(0,4096)
        self.extra_args = QLineEdit(); self.extra_args.setPlaceholderText("További scrcpy-paraméterek, pl. --always-on-top")
        self.command_edit = QPlainTextEdit(); self.command_edit.setPlaceholderText("Indítási parancs"); self.command_edit.setMinimumHeight(65)
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        self.crop_enabled.toggled.connect(self._settings_changed)
        self.force_resize_check.toggled.connect(self._settings_changed)
        for spin in (self.width_spin, self.height_spin, self.dpi_spin,
                     self.crop_w, self.crop_h, self.crop_x, self.crop_y):
            spin.valueChanged.connect(self._settings_changed)
        self.extra_args.textChanged.connect(self._settings_changed)
        self.command_edit.textChanged.connect(self._command_edited)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.launch_btn = QPushButton("▶  Alkalmazás indítása"); self.launch_btn.setObjectName("primary"); self.launch_btn.clicked.connect(self.launch_app); actions.addWidget(self.launch_btn)
        save_btn = QPushButton("Beállítások mentése"); save_btn.setObjectName("save"); save_btn.clicked.connect(lambda: self.save_config()); actions.addWidget(save_btn)
        page_layout.addLayout(actions)
        self.status_label = QLabel(" "); self.status_label.setWordWrap(False); self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.status_label.setObjectName("muted"); page_layout.addWidget(self.status_label)

    def _show_profile_dialog(self, package):
        item = next((self.app_list.item(i) for i in range(self.app_list.count()) if self.app_list.item(i).data(Qt.ItemDataRole.UserRole) == package), None)
        if item is not None: self.app_list.setCurrentItem(item)
        # A Mégse valódi visszavonás legyen: a mezők közös widgetek, ezért
        # megnyitás előtt külön mentjük az állapotukat.
        snapshot = {
            "app_settings": dict(self.app_settings.get(package, {})),
            "profile_index": self.profile_combo.currentIndex(),
            "profile_name": self.profile_name.text(),
            "width": self.width_spin.value(), "height": self.height_spin.value(), "dpi": self.dpi_spin.value(),
            "crop_enabled": self.crop_enabled.isChecked(),
            "force_resize": self.force_resize_check.isChecked(),
            "crop_w": self.crop_w.value(), "crop_h": self.crop_h.value(),
            "crop_x": self.crop_x.value(), "crop_y": self.crop_y.value(),
            "extra_args": self.extra_args.text(), "command": self.command_edit.toPlainText(),
            "command_manual": self._command_manual,
        }
        dialog = QDialog(self); dialog.setWindowTitle(f"Profil beállításai — {KNOWN_APPS.get(package, package)}")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("Profil:", self.profile_combo)
        form.addRow("Profil neve:", self.profile_name)
        dimensions = QHBoxLayout(); dimensions.addWidget(QLabel("Szélesség")); dimensions.addWidget(self.width_spin); dimensions.addWidget(QLabel("Magasság")); dimensions.addWidget(self.height_spin)
        form.addRow("Méret:", dimensions); form.addRow("DPI:", self.dpi_spin)
        form.addRow(self.crop_enabled)
        form.addRow(self.force_resize_check)
        form.addRow(QLabel("Megjegyzés: az Android-beállítás rendszer-szintű; az alkalmazás saját tájolási zárolását nem minden esetben lehet felülírni."))
        crop = QGridLayout(); crop.addWidget(QLabel("Szélesség"),0,0); crop.addWidget(self.crop_w,0,1); crop.addWidget(QLabel("Magasság"),0,2); crop.addWidget(self.crop_h,0,3); crop.addWidget(QLabel("X"),1,0); crop.addWidget(self.crop_x,1,1); crop.addWidget(QLabel("Y"),1,2); crop.addWidget(self.crop_y,1,3)
        form.addRow("Kivágás:", crop); form.addRow("Extra paraméterek:", self.extra_args)
        layout.addLayout(form)
        profile_actions = QHBoxLayout()
        add_profile_btn = QPushButton("Új profil")
        save_profile_btn = QPushButton("Profil mentése")
        delete_profile_btn = QPushButton("Profil törlése")
        add_profile_btn.clicked.connect(self.add_profile)
        save_profile_btn.clicked.connect(self.save_profile)
        delete_profile_btn.clicked.connect(self.delete_profile)
        profile_actions.addWidget(add_profile_btn); profile_actions.addWidget(save_profile_btn); profile_actions.addWidget(delete_profile_btn)
        layout.addLayout(profile_actions)
        layout.addWidget(QLabel("Indítási parancs:")); layout.addWidget(self.command_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Mentés")
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        # A beállításmezők a fő widgetből kerülnek át a modális ablakba.
        # Tartsuk életben a QDialog objektumot az exec() után is, különben
        # a lokális Python-változó megszűnésekor a Qt a benne levő mezőket
        # (köztük a command_edit-et) is törölheti.
        self._profile_dialog = dialog
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._store_current_app_settings()
            self.save_config(show_message=False)
            self._set_status(f"Beállítások elmentve: {KNOWN_APPS.get(package, package)}")
        else:
            self.app_settings[package] = snapshot["app_settings"]
            self._loading = True
            self.profile_combo.setCurrentIndex(snapshot["profile_index"])
            self.profile_name.setText(snapshot["profile_name"])
            self.width_spin.setValue(snapshot["width"]); self.height_spin.setValue(snapshot["height"]); self.dpi_spin.setValue(snapshot["dpi"])
            self.crop_enabled.setChecked(snapshot["crop_enabled"])
            self.force_resize_check.setChecked(snapshot.get("force_resize", False))
            self.crop_w.setValue(snapshot["crop_w"]); self.crop_h.setValue(snapshot["crop_h"])
            self.crop_x.setValue(snapshot["crop_x"]); self.crop_y.setValue(snapshot["crop_y"])
            self.extra_args.setText(snapshot["extra_args"])
            self._loading = False
            self._command_manual = snapshot["command_manual"]
            self._updating_command = True
            self.command_edit.setPlainText(snapshot["command"])
            self._updating_command = False
        self._render_app_cards()

    def _render_app_cards(self):
        while self.cards_layout.count():
            child = self.cards_layout.takeAt(0); w = child.widget()
            if w: w.deleteLater()
        for i in range(self.app_list.count()):
            item = self.app_list.item(i); package = item.data(Qt.ItemDataRole.UserRole)
            app = next((a for a in self.apps if a["package"] == package), None)
            if not app: continue
            card = QFrame(); card.setObjectName("appCard"); card.setStyleSheet("QFrame#appCard { border: 1px solid #315b70; border-radius: 5px; background: rgba(20,32,40,0.7); }")
            row = QHBoxLayout(card); row.setContentsMargins(6,3,6,3); row.setSpacing(7)
            icon_label = QLabel(); icon_label.setFixedSize(36,36); icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = self._application_icon(package)
            if not icon.isNull(): icon_label.setPixmap(icon.pixmap(34,34))
            else: icon_label.setPixmap(self.style().standardIcon(self.style().StandardPixmap.SP_DesktopIcon).pixmap(32,32))
            row.addWidget(icon_label)
            info = QVBoxLayout(); name = QLabel(app["name"]); name.setStyleSheet("font-weight: 700; font-size: 9.5pt;")
            kind = "Felhasználói alkalmazás" if app["user"] else "Rendszeralkalmazás"
            meta = QLabel(f"{package}  ·  {kind}" + ("  ·  ★ Kedvenc" if package in self.favorites else "")); meta.setObjectName("muted"); meta.setWordWrap(False); meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addWidget(name); info.addWidget(meta); row.addLayout(info, 3)
            data = self.app_settings.get(package, {})
            profile_name = data.get("profile")
            prof = next((p for p in self.profiles if p["name"] == profile_name), None)
            if not prof and package == "org.opencpn.opencpn": prof = next((p for p in self.profiles if p["name"] == "OpenCPN – 1920×1080"), None)
            preview = QHBoxLayout(); preview.setSpacing(5)
            if prof:
                preview_w = int(data.get("width", prof['width']))
                preview_h = int(data.get("height", prof['height']))
                preview_dpi = int(data.get("dpi", prof.get('dpi', 240)))
                label = QLabel(f"{preview_w} × {preview_h} · {preview_dpi} DPI")
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                label.setStyleSheet("font-weight:600; color:#80cfee;")
                label.setMinimumWidth(135)
                preview.addWidget(label)
                shape = QFrame()
                shape.setStyleSheet("background:#203b49; border:1px solid #55b9ed; border-radius:2px;")
                ratio = preview_w / max(1, preview_h); maxw,maxh=20,24
                if ratio >= maxw/maxh: sw,sh=maxw,max(1,int(maxw/ratio))
                else: sh,sw=maxh,max(1,int(maxh*ratio))
                shape.setFixedSize(sw,sh)
                preview.addWidget(shape)
            else:
                label = QLabel("Nincs profil")
                label.setStyleSheet("color:#a9b9c2;")
                preview.addWidget(label)
            row.addLayout(preview, 0)
            if prof:
                edit = QPushButton("⚙"); edit.setToolTip("A profil beállításai"); edit.setFixedWidth(30); edit.clicked.connect(lambda checked=False,p=package: self._show_profile_dialog(p)); row.addWidget(edit)
            else:
                create = QPushButton("＋ Profil létrehozása"); create.setObjectName("save"); create.clicked.connect(lambda checked=False,p=package: self._create_app_profile(p)); row.addWidget(create)
            fav = QPushButton("★" if package in self.favorites else "☆"); fav.setToolTip("Kedvenc ki/be"); fav.setFixedWidth(30); fav.clicked.connect(lambda checked=False,p=package: self._toggle_favorite_for(p)); row.addWidget(fav)
            launch = QPushButton("Indítás"); launch.setObjectName("primary"); launch.clicked.connect(lambda checked=False,p=package: self._launch_package(p)); row.addWidget(launch)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def _launch_package(self, package):
        for i in range(self.app_list.count()):
            item=self.app_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==package:
                self.app_list.setCurrentItem(item); break
        self.launch_app()

    def _toggle_favorite_for(self, package):
        if package in self.favorites: self.favorites.remove(package)
        else: self.favorites.add(package)
        self._filter_apps(); self.save_config(show_message=False)

    def _create_app_profile(self, package):
        # A kártyáról indított művelet előbb az adott alkalmazást válassza ki,
        # ne a korábban kijelölt másik alkalmazás profilját írja át.
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == package:
                self.app_list.setCurrentItem(item)
                break
        # Minden új alkalmazás külön profilt kap az OpenCPN alapértékeinek másolatából.
        base = next((p for p in self.profiles if p.get("name") == "OpenCPN – 1920×1080"), {"name":"OpenCPN – 1920×1080","width":1920,"height":1080,"dpi":240})
        name = f"{KNOWN_APPS.get(package, package)} – {base['width']}×{base['height']}"
        existing = {p['name'] for p in self.profiles}
        if name in existing: name += " (egyéni)"
        profile = {"name":name,"width":base['width'],"height":base['height'],"dpi":base.get('dpi',240)}
        self.profiles.append(profile)
        self.app_settings[package] = dict(self.app_settings.get(package, {}), profile=name, width=profile['width'], height=profile['height'], dpi=profile['dpi'])
        self._refresh_profile_combo(name)
        self._store_current_app_settings_for(package)
        self.save_config(show_message=False); self._render_app_cards()
        self._set_status(f"Létrejött az alkalmazás saját profilja: {name}")

    def _store_current_app_settings_for(self, package):
        if self.current_package() == package: self._store_current_app_settings()

    def _append_log(self, message: str):
        """Időbélyeges, kategória szerint színezett és másolható napló."""
        try:
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
            line = f"[{stamp}] {message}"
            upper = message.upper()
            if any(token in upper for token in ("HIBA:", "[ERROR]", " ERROR", "SCRCPY INDÍTÁSI HIBA", "SIKERTELEN", "KILÉPÉSI KÓD=-", "KILÉPÉSI KÓD: ")) and ("KILÉPÉSI KÓD: 0" not in upper and "KILÉPÉSI KÓD=0" not in upper):
                color = "#ff6666"
            elif any(token in upper for token in ("FIGYELEM:", "[WARNING]", "WARNING", "ÉRVÉNYTELEN", "IDŐTÚLLÉPÉS", "NEM ÁLLT LE")):
                color = "#f0b35a"
            elif any(token in upper for token in ("[INFO]", "SCRCPY INDÍTÁS", "ADB-ESZKÖZLISTA", "VIRTUÁLIS KIJELZŐ", "ÁLLAPOT:")):
                color = "#75c8f0"
            else:
                color = "#d5d9dc"
            cursor = self.log_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat(); fmt.setForeground(QColor(color))
            cursor.insertText(line + "\n", fmt)
            self.log_edit.setTextCursor(cursor)
            self.log_edit.ensureCursorVisible()
        except Exception as exc:
            print(f"[naplózási hiba] {message} ({exc})", flush=True)

    def _system_scrcpy_processes(self):
        """A futó scrcpy-k PID-je és parancssora, a saját QProcess-eken kívül is."""
        result = _run(["ps", "-eo", "pid=,args="], 4)
        found = {}
        if result.returncode != 0:
            return found
        for line in result.stdout.splitlines():
            try:
                pid_text, command = line.strip().split(None, 1)
                pid = int(pid_text)
                executable = shlex.split(command)[0] if command.strip() else ""
            except (ValueError, IndexError):
                continue
            if pid == os.getpid():
                continue
            if Path(executable).name == "scrcpy":
                found[pid] = command
        return found

    def refresh_runtime_status(self):
        if self._refreshing_runtime:
            return
        self._refreshing_runtime = True
        try:
            processes = self._system_scrcpy_processes()
            self._external_scrcpy_pids = set(processes)
            own_running = sum(1 for p in self.processes if p.state() != QProcess.ProcessState.NotRunning)
            self.runtime_scrcpy_label.setText(f"Futó scrcpy: {len(processes)}")
            ready = self.device_combo.currentData() is not None
            self.runtime_adb_label.setText("ADB: csatlakoztatva" if ready else "ADB: nincs kiválasztott eszköz")
            try:
                result = _run(self._adb_prefix() + ["shell", "dumpsys", "display"], 5)
                ids = set()
                if result.returncode == 0:
                    for match in re.finditer(r"mDisplayId\s*=\s*(\d+)", result.stdout):
                        display_id = int(match.group(1))
                        if display_id != 0:
                            ids.add(display_id)
                    self.runtime_display_label.setText(f"Virtuális kijelzők (ADB): {len(ids)}")
                else:
                    self.runtime_display_label.setText("Virtuális kijelzők: nem lekérdezhető")
            except Exception:
                self.runtime_display_label.setText("Virtuális kijelzők: nem lekérdezhető")
            if len(processes) > own_running:
                self.runtime_scrcpy_label.setStyleSheet("color: #f0b35a; font-weight: 700;")
                self.runtime_scrcpy_label.setToolTip("A rendszerben olyan scrcpy-folyamat is fut, amelyet ez a modul nem indított vagy nem tart nyilván.")
            else:
                self.runtime_scrcpy_label.setStyleSheet("color: palette(window-text);")
                self.runtime_scrcpy_label.setToolTip("A rendszerben észlelt scrcpy-folyamatok száma.")
        finally:
            self._refreshing_runtime = False

    def stop_all_scrcpy(self):
        """Leállít minden scrcpy-klienst; az ADB démont nem állítja le."""
        answer = QMessageBox.question(
            self, "Összes scrcpy leállítása",
            "Valóban leállítod az összes futó scrcpy-ablakot, a korábbi indításból visszamaradt példányokat is?\n\nAz ADB-kapcsolat megmarad.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._append_log("Az összes scrcpy leállítását a felhasználó megszakította.")
            return
        self._append_log("ÖSSZES SCRCPY LEÁLLÍTÁSA: folyamatlista lekérése.")
        initial = self._system_scrcpy_processes()
        stopped = 0
        for proc in list(self.processes):
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(1800):
                    self._append_log("FIGYELEM: a saját scrcpy nem állt le 1800 ms alatt; kényszerített leállítás.")
                    proc.kill()
                    proc.waitForFinished(3000)
        # Külső/korábbi folyamatok: csak a scrcpy klienst állítjuk le, az adb-t nem.
        for pid, command in initial.items():
            try:
                os.kill(pid, signal.SIGTERM)
                stopped += 1
                self._append_log(f"Leállítási jel elküldve: PID={pid}; parancs={command}")
            except ProcessLookupError:
                self._append_log(f"A folyamat már befejeződött: PID={pid}")
            except PermissionError:
                self._append_log(f"HIBA: nincs jogosultság a scrcpy leállításához (PID={pid}).")
        # Várakozás, majd csak a megmaradt scrcpy kliensek kényszerített leállítása.
        deadline = time.monotonic() + 2.0
        remaining = self._system_scrcpy_processes()
        while remaining and time.monotonic() < deadline:
            time.sleep(0.1)
            remaining = self._system_scrcpy_processes()
        for pid, command in remaining.items():
            try:
                os.kill(pid, signal.SIGKILL)
                self._append_log(f"FIGYELEM: beragadt scrcpy kényszerítetten leállítva: PID={pid}; parancs={command}")
            except (ProcessLookupError, PermissionError) as exc:
                self._append_log(f"HIBA: PID={pid} kényszerített leállítása nem sikerült: {exc}")
        for proc in list(self.processes):
            if proc.state() == QProcess.ProcessState.NotRunning and proc in self.processes:
                self.processes.remove(proc)
        self.refresh_runtime_status()
        remaining_count = len(self._system_scrcpy_processes())
        self._append_log(f"ÖSSZES SCRCPY LEÁLLÍTÁS EREDMÉNYE: kezdeti={len(initial)}, leállítási jelek={stopped}, visszamaradt={remaining_count}. ADB-démon érintetlen.")
        self._set_status(f"Összes scrcpy leállítása befejeződött. Visszamaradt folyamatok: {remaining_count}.", remaining_count > 0)

    def copy_log(self):
        QApplication.clipboard().setText(self.log_edit.toPlainText())
        self._set_status("A teljes látható napló a vágólapra másolva.")

    def clear_log(self):
        self.log_edit.clear()
        self._append_log("A naplót a felhasználó ürítette.")

    def _set_status(self, text: str, error=False):
        self.status_label.setText(text)
        self._append_log(("HIBA: " if error else "ÁLLAPOT: ") + text)
        self.status_label.setStyleSheet("color: #e57373;" if error else "")

    def refresh_devices(self):
        self.device_combo.clear()
        if not shutil.which("adb") and self.adb == "/usr/bin/adb" and not Path(self.adb).exists():
            self.connection_status.setText("ADB nem található")
            self._set_status("Nem találom az adb parancsot. Telepítsd, vagy ellenőrizd a PATH változót.", True)
            return
        try:
            self._append_log(f"ADB-eszközlista lekérdezése: {self.adb} devices")
            result = _run([self.adb, "devices"], 5)
            self._append_log(f"adb devices kilépési kód: {result.returncode}\n{result.stdout.strip()}" + (f"\nSTDERR: {result.stderr.strip()}" if result.stderr.strip() else ""))
            devices = []
            for line in result.stdout.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2:
                    devices.append((parts[0], parts[1]))
            for serial, state in devices:
                self.device_combo.addItem(f"{serial}  [{state}]", serial)
            ready = [d for d in devices if d[1] == "device"]
            self.connection_status.setText(f"Kapcsolódva: {len(ready)}" if ready else "Nincs engedélyezett eszköz")
            if not ready:
                self._set_status("Csatlakoztasd a telefont USB-n vagy Wi-Fi ADB-n, és engedélyezd az USB-hibakeresést.", True)
            else:
                self._set_status("ADB-kapcsolat rendben.")
        except Exception as exc:
            self.connection_status.setText("Kapcsolati hiba")
            self._set_status(f"ADB-lekérdezési hiba: {exc}", True)

    def _adb_prefix(self):
        serial = self.device_combo.currentData()
        return [self.adb] + (["-s", serial] if serial else [])

    def refresh_apps(self):
        if self.device_combo.currentData() is None:
            self._set_status("Előbb csatlakozz a telefonhoz.", True)
            return
        try:
            prefix = self._adb_prefix()
            self._append_log(f"Alkalmazáslista frissítése. Eszköz: {self.device_combo.currentData()}; parancs: {shlex.join(prefix + ['shell', 'pm', 'list', 'packages'])}")
            all_result = _run(prefix + ["shell", "pm", "list", "packages"], 15)
            user_result = _run(prefix + ["shell", "pm", "list", "packages", "-3"], 15)
            if all_result.returncode != 0:
                raise RuntimeError(all_result.stderr.strip() or "A csomaglista nem kérdezhető le.")
            user_packages = {x.strip().removeprefix("package:") for x in user_result.stdout.splitlines() if x.startswith("package:")}
            packages = sorted({x.strip().removeprefix("package:") for x in all_result.stdout.splitlines() if x.startswith("package:")}, key=str.casefold)
            self.apps = []
            for package in packages:
                self.apps.append({"package": package, "name": KNOWN_APPS.get(package, package), "user": package in user_packages})
            self._filter_apps()
            self._set_status(f"Betöltve: {len(packages)} csomag; ebből {len(user_packages)} felhasználói alkalmazás (pm list packages -3).")
        except Exception as exc:
            self._set_status(f"Az alkalmazáslista nem olvasható: {exc}", True)

    def _application_icon(self, package: str) -> QIcon:
        """Visszaadja a gyorsítótárban levő ikont APK-letöltés nélkül."""
        if package in self._icon_cache:
            return self._icon_cache[package]

        cached = ICON_CACHE_DIR / f"{package.replace('/', '_')}.png"
        if cached.is_file():
            icon = QIcon(str(cached))
            if not icon.isNull():
                self._icon_cache[package] = icon
                return icon

        # Nincs cache-elt ikon: ne indítsunk ADB-t, adb pullt vagy aapt-ot
        # minden alkalmazásnál. Ez korábban a GUI főszálát is blokkolhatta.
        icon = QIcon()
        self._icon_cache[package] = icon
        return icon

    def _filter_apps(self, *_):
        query = self.search_edit.text().strip().casefold() if hasattr(self, "search_edit") else ""
        only_fav = self.favorites_only.isChecked() if hasattr(self, "favorites_only") else False
        type_filter = self.app_type_filter.currentData() if hasattr(self, "app_type_filter") else "all"
        selected = self.current_package()
        self.app_list.blockSignals(True); self.app_list.clear()
        for app in self.apps:
            package=app["package"]
            if query and query not in app["name"].casefold() and query not in package.casefold(): continue
            if only_fav and package not in self.favorites: continue
            if type_filter == "user" and not app["user"]: continue
            if type_filter == "system" and app["user"]: continue
            item=QListWidgetItem(self._application_icon(package), f"{app['name']}\n{package}")
            item.setData(Qt.ItemDataRole.UserRole, package); self.app_list.addItem(item)
            if package == selected: self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        if self.app_list.currentItem() is None and self.app_list.count(): self.app_list.setCurrentRow(0)
        self._render_app_cards()

    def current_package(self):
        item = self.app_list.currentItem() if hasattr(self, "app_list") else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _app_selected(self, current, previous=None):
        if not current:
            return
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self.app_settings.get(package, {})
        self._loading = True
        saved_profile = data.get("profile")
        # OpenCPN korábban rögzített alapfelbontása: 1920×1080.
        if not saved_profile and package == "org.opencpn.opencpn":
            saved_profile = "OpenCPN – 1920×1080"
        if saved_profile:
            idx = self.profile_combo.findText(saved_profile)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
            else:
                self._profile_selected(self.profile_combo.currentIndex())
        else:
            self._profile_selected(self.profile_combo.currentIndex())
        self.extra_args.setText(data.get("extra_args", ""))
        self.crop_enabled.setChecked(data.get("crop_enabled", False))
        self.force_resize_check.setChecked(bool(data.get("force_resize", False)))
        self.crop_w.setValue(int(data.get("crop_w", 720)))
        self.crop_h.setValue(int(data.get("crop_h", 1200)))
        self.crop_x.setValue(int(data.get("crop_x", 0)))
        self.crop_y.setValue(int(data.get("crop_y", 0)))
        if "width" in data:
            self.width_spin.setValue(int(data.get("width", self.width_spin.value())))
            self.height_spin.setValue(int(data.get("height", self.height_spin.value())))
            self.dpi_spin.setValue(int(data.get("dpi", self.dpi_spin.value())))
        self._loading = False
        # A korábbi verzió minden generált parancsot elmentett és visszatöltött,
        # így a régi felbontás és kivágás maradhatott a parancsban.
        saved_command = data.get("command", "")
        self._command_manual = bool(data.get("command_manual", False) and saved_command)
        if self._command_manual:
            self._updating_command = True
            self.command_edit.setPlainText(saved_command)
            self._updating_command = False
        else:
            self._update_command()

    def _profile_selected(self, index):
        if self._loading or index < 0 or index >= len(self.profiles):
            return
        profile = self.profiles[index]
        self._loading = True
        self.profile_name.setText(profile["name"])
        self.width_spin.setValue(int(profile["width"]))
        self.height_spin.setValue(int(profile["height"]))
        self.dpi_spin.setValue(int(profile.get("dpi", 240)))
        self._loading = False
        self._command_manual = False
        self._update_command()

    def _refresh_profile_combo(self, select_name=None):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems([p["name"] for p in self.profiles])
        if select_name:
            idx = self.profile_combo.findText(select_name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        self._profile_selected(self.profile_combo.currentIndex())

    def add_profile(self):
        name, ok = QInputDialog.getText(self, "Új képernyőprofil", "Profil neve:", text="Saját profil")
        if not ok or not name.strip():
            return
        self.profiles.append({"name": name.strip(), "width": self.width_spin.value(), "height": self.height_spin.value(), "dpi": self.dpi_spin.value()})
        self._refresh_profile_combo(name.strip())
        self._set_status(f"Új profil létrehozva: {name.strip()} (mentéshez kattints a Beállítások mentése gombra).")

    def save_profile(self):
        name = self.profile_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Hiányzó név", "Adj nevet a képernyőprofilnak.")
            return
        idx = self.profile_combo.currentIndex()
        profile = {"name": name, "width": self.width_spin.value(), "height": self.height_spin.value(), "dpi": self.dpi_spin.value()}
        if 0 <= idx < len(self.profiles):
            self.profiles[idx] = profile
        else:
            self.profiles.append(profile)
        self._refresh_profile_combo(name)
        self._store_current_app_settings()
        self.save_config(show_message=False)
        self._set_status(f"Profil mentve: {name}. A profilválasztóban találod; az aktuálisan kijelölt alkalmazáshoz a Beállítások mentése gombbal rögzítheted.")

    def delete_profile(self):
        if len(self.profiles) <= 1:
            QMessageBox.information(self, "Profil", "Legalább egy képernyőprofilt meg kell tartani.")
            return
        idx = self.profile_combo.currentIndex()
        if idx < 0:
            return
        name = self.profiles[idx]["name"]
        if QMessageBox.question(self, "Profil törlése", f"Törlöd ezt a profilt?\n{name}") != QMessageBox.StandardButton.Yes:
            return
        del self.profiles[idx]
        self._refresh_profile_combo()
        self.save_config(show_message=False)
        self._set_status(f"Profil törölve: {name}")

    def toggle_favorite(self):
        package = self.current_package()
        if not package:
            QMessageBox.information(self, "Kedvencek", "Előbb válassz alkalmazást.")
            return
        if package in self.favorites:
            self.favorites.remove(package)
            self._set_status("Eltávolítva a kedvencek közül.")
        else:
            self.favorites.add(package)
            self._set_status("Kedvencekhez adva.")
        self._filter_apps()
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == package:
                self.app_list.setCurrentItem(item)
                break

    def _settings_changed(self, *_):
        if self._loading:
            return
        if not self._command_manual:
            self._update_command()

    def _update_command(self, *_):
        if self._loading or not hasattr(self, "command_edit"):
            return
        package = self.current_package()
        if not package:
            self._updating_command = True
            self.command_edit.setPlainText("")
            self._updating_command = False
            return

        vw, vh = self.width_spin.value(), self.height_spin.value()
        # A virtuális kijelző életciklusa kövesse a scrcpy folyamatot.
        # A --no-vd-destroy-content régi kijelzőn hagyhatja az alkalmazást,
        # ezért itt szándékosan nem használjuk.
        args = [self.scrcpy, f"--new-display={vw}x{vh}/{self.dpi_spin.value()}",
                f"--start-app=+{package}"]
        if self.crop_enabled.isChecked():
            cw, ch = self.crop_w.value(), self.crop_h.value()
            cx, cy = self.crop_x.value(), self.crop_y.value()
            if cw > 0 and ch > 0 and cx >= 0 and cy >= 0 and cx + cw <= vw and cy + ch <= vh:
                args.append(f"--crop={cw}:{ch}:{cx}:{cy}")
            else:
                self._set_status(
                    f"A kivágás ({cw}×{ch}, X={cx}, Y={cy}) nem fér el a "
                    f"{vw}×{vh}-es virtuális kijelzőn; a --crop kimarad a parancsból.", True
                )
        try:
            args.extend(shlex.split(self.extra_args.text()))
        except ValueError as exc:
            self._set_status(f"Hibás extra paraméterek: {exc}", True)
            return
        self._updating_command = True
        self.command_edit.blockSignals(True)
        self.command_edit.setPlainText(shlex.join(args))
        self.command_edit.blockSignals(False)
        self._updating_command = False
        self._store_current_app_settings()

    def _store_current_app_settings(self):
        package = self.current_package()
        if not package or not hasattr(self, "extra_args"):
            return
        self.app_settings[package] = {
            "extra_args": self.extra_args.text(),
            "crop_enabled": self.crop_enabled.isChecked(),
            "force_resize": self.force_resize_check.isChecked(),
            "crop_w": self.crop_w.value(), "crop_h": self.crop_h.value(),
            "crop_x": self.crop_x.value(), "crop_y": self.crop_y.value(),
            "profile": self.profile_combo.currentText(),
            "width": self.width_spin.value(), "height": self.height_spin.value(),
            "dpi": self.dpi_spin.value(),
            "command": self.command_edit.toPlainText(),
            "command_manual": self._command_manual,
        }

    def _command_edited(self):
        if self._loading or self._updating_command:
            return
        self._command_manual = True
        self._store_current_app_settings()

    def launch_app(self):
        package = self.current_package()
        if not package:
            QMessageBox.information(self, "Alkalmazásindítás", "Válassz alkalmazást a bal oldali listából.")
            return
        if not shutil.which("scrcpy") and not Path(self.scrcpy).exists():
            QMessageBox.critical(self, "scrcpy nem található", "Nem találom a scrcpy programot. Ellenőrizd a telepítést és a PATH változót.")
            return
        text = self.command_edit.toPlainText().strip()
        try:
            args = shlex.split(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Hibás parancs", f"A parancs idézőjelezése hibás:\n{exc}")
            return
        if not args:
            return
        # Régi mentett parancsból is távolítsuk el a virtuális kijelző tartalmát
        # a scrcpy bezárása után életben tartó kapcsolót.
        stale_display_flag_removed = "--no-vd-destroy-content" in args
        args = [arg for arg in args if arg != "--no-vd-destroy-content"]
        # Régi mentett parancs esetén is kényszerítsük ki az app újraindítását,
        # különben a futó Activity a korábbi kijelzőn maradhat.
        for i, arg in enumerate(args):
            if arg.startswith("--start-app="):
                value = arg.split("=", 1)[1]
                if value and not value.startswith("+"):
                    args[i] = "--start-app=+" + value
                break
        # Régebbi mentett parancs is tartalmazhat hibás --crop értéket.
        # A --new-display méretéből ellenőrizzük, és az érvénytelen crop-ot
        # automatikusan elhagyjuk, mielőtt elindítanánk a scrcpy-t.
        display_w, display_h = self.width_spin.value(), self.height_spin.value()
        for arg in args:
            if arg.startswith("--new-display="):
                spec = arg.split("=", 1)[1].split("/", 1)[0]
                try:
                    display_w, display_h = (int(v) for v in spec.lower().split("x", 1))
                except (ValueError, TypeError):
                    pass
                break
        clean_args = []
        crop_removed = False
        for arg in args:
            if arg.startswith("--crop="):
                try:
                    cw, ch, cx, cy = (int(v) for v in arg.split("=", 1)[1].split(":"))
                    if cw > 0 and ch > 0 and cx >= 0 and cy >= 0 and cx + cw <= display_w and cy + ch <= display_h:
                        clean_args.append(arg)
                    else:
                        crop_removed = True
                except (ValueError, TypeError):
                    crop_removed = True
            else:
                clean_args.append(arg)
        if crop_removed:
            args = clean_args
            text = shlex.join(args)
            self.command_edit.blockSignals(True)
            self.command_edit.setPlainText(text)
            self.command_edit.blockSignals(False)
            self._command_manual = True
            self._set_status(
                f"Érvénytelen --crop paraméter elhagyva: a kivágás nem fér el a "
                f"{display_w}×{display_h}-es virtuális kijelzőn. A parancs javítva.", True
            )
        # Androidon nincs általános scrcpy --maximize kapcsoló. A legközelebbi
        # támogatott rendszerbeállítás a force_resizable_activities; ez globális,
        # és nem garantálja, hogy az app saját tájolási zárolását felülírja.
        if self.force_resize_check.isChecked():
            try:
                result = _run(self._adb_prefix() + ["shell", "settings", "put", "global", "force_resizable_activities", "1"], 5)
                if result.returncode != 0:
                    detail = result.stderr.strip() or result.stdout.strip() or f"kilépési kód: {result.returncode}"
                    QMessageBox.warning(
                        self, "Android átméretezés",
                        "Nem sikerült bekapcsolni az Android átméretezhetőségi beállítását.\n\n" + detail
                    )
                    return
                self._set_status(
                    "Android force_resizable_activities=1 bekapcsolva. Ez rendszer-szintű; "
                    "az alkalmazás tájolási zárolása miatt a teljes kitöltés nem garantálható."
                )
            except Exception as exc:
                QMessageBox.warning(self, "Android átméretezés", f"A beállítás nem alkalmazható: {exc}")
                return
        # Ne használjunk shell=True: a szerkeszthető parancs argumentumlistaként fut.
        self._store_current_app_settings()
        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setProperty("scrcpy_command", text)
        proc.setProperty("scrcpy_package", package)
        proc.setProperty("scrcpy_log", "")
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._read_process_output(p))
        proc.errorOccurred.connect(lambda err, p=proc: self._set_status(f"scrcpy indítási hiba: {p.errorString()}", True))
        proc.finished.connect(lambda code, status, p=proc: self._process_finished(p, code))
        self._append_log(f"SCRCPY INDÍTÁS\nCsomag: {package}\nProgram: {args[0]}\nArgumentumok: {shlex.join(args[1:])}\nEszköz: {self.device_combo.currentData()}\nVirtuális kijelző: {display_w}×{display_h}\nCrop aktív: {any(a.startswith('--crop=') for a in args)}")
        proc.start()
        self.processes.append(proc)
        self._append_log(f"QProcess.start() meghívva; azonnali folyamatállapot: {proc.state().name}")
        self.refresh_runtime_status()
        if crop_removed:
            self._set_status(
                f"Indítás elküldve: {package}. Az érvénytelen --crop paramétert automatikusan eltávolítottam.", True
            )
        elif stale_display_flag_removed:
            self._set_status(
                f"Indítás elküldve: {package}. A régi --no-vd-destroy-content kapcsolót eltávolítottam.", True
            )
        else:
            self._set_status(f"Indítási kérés elküldve: {package} — {text}")

    def _read_process_output(self, proc):
        try:
            output = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            if output:
                previous = proc.property("scrcpy_log") or ""
                proc.setProperty("scrcpy_log", (previous + output)[-12000:])
                print(output, end="", flush=True)
                self._append_log(f"[scrcpy | {proc.property('scrcpy_package') or 'ismeretlen alkalmazás'}] {output.rstrip()}")
                self._set_status(output.strip()[-1000:])
        except Exception as exc:
            print(f"[scrcpy naplóolvasási hiba] {exc}", flush=True)

    def _process_finished(self, proc, code):
        # A kilépés előtt még a pufferben maradt hibaüzenetet is olvassuk ki.
        self._read_process_output(proc)
        log = (proc.property("scrcpy_log") or "").strip()
        command = proc.property("scrcpy_command") or "(ismeretlen parancs)"
        package = proc.property("scrcpy_package") or "(ismeretlen csomag)"
        message = f"scrcpy folyamat befejeződött (kilépési kód: {code})."
        self._append_log(f"SCRCPY BEFEJEZŐDÖTT: kilépési kód={code}; csomag={package}; parancs={command}\nTELJES UTOLSÓ NAPLÓ: {log or '(üres)'}")
        self._set_status(message, code != 0)
        print(f"\n[{message}]\nCsomag: {package}\nParancs: {command}", flush=True)
        if code != 0:
            details = log if log else "A scrcpy nem adott vissza szöveges hibaüzenetet."
            self._show_scrcpy_error_dialog(code, package, command, details[-12000:])
        if proc in self.processes:
            self.processes.remove(proc)
        proc.deleteLater()
        self.refresh_runtime_status()

    def _show_scrcpy_error_dialog(self, code, package, command, details):
        """Görgethető, kijelölhető és vágólapra másolható scrcpy-hibanapló."""
        dialog = QDialog(self)
        dialog.setWindowTitle("scrcpy indítási hiba")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        summary = QLabel(f"Az alkalmazás nem indult el. Kilépési kód: {code}\nCsomag: {package}\n\nParancs: {command}")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        log_edit = QPlainTextEdit()
        log_edit.setReadOnly(True)
        log_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        log_edit.setPlainText(details)
        log_edit.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(log_edit, 1)
        buttons = QHBoxLayout()
        copy_button = QPushButton("Hibanapló másolása")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(
            f"Kilépési kód: {code}\nCsomag: {package}\nParancs: {command}\n\n{details}"
        ))
        buttons.addWidget(copy_button)
        close_button = QPushButton("Bezárás")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.exec()

    def stop_scrcpy(self):
        self._append_log(f"SCRCPY LEÁLLÍTÁS KÉRVE; futó folyamatok: {len(self.processes)}")
        stopped = 0
        for proc in list(self.processes):
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(1200):
                    self._append_log("A scrcpy nem állt le 1200 ms alatt; kill() következik.")
                    proc.kill()
                    if not proc.waitForFinished(3000):
                        self._append_log("FIGYELEM: a scrcpy folyamat a kill() után sem jelezte a befejezést 3000 ms alatt.")
                self._append_log(f"Leállított folyamat PID-je: {proc.processId()}; állapot: {proc.state().name}")
                stopped += 1
        self._set_status(f"Leállítási kérés kész. Leállított folyamatok: {stopped}.")

    def save_config(self, show_message=True):
        self._store_current_app_settings()
        data = {"profiles": self.profiles, "favorites": sorted(self.favorites), "apps": self.app_settings}
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CONFIG_FILE)
            if show_message:
                self._set_status(f"Beállítások elmentve: {CONFIG_FILE}")
        except OSError as exc:
            QMessageBox.critical(self, "Mentési hiba", f"A beállítások nem menthetők:\n{exc}")

    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("A konfiguráció gyökéreleme nem objektum.")
            loaded_profiles = data.get("profiles")
            if isinstance(loaded_profiles, list) and loaded_profiles:
                valid = [p for p in loaded_profiles if isinstance(p, dict) and p.get("name")
                         and isinstance(p.get("width"), int) and isinstance(p.get("height"), int)]
                if valid:
                    self.profiles = valid
            if not any(p.get("name") == "OpenCPN – 1920×1080" for p in self.profiles):
                self.profiles.append({"name": "OpenCPN – 1920×1080", "width": 1920, "height": 1080, "dpi": 240})
            favorites = data.get("favorites", [])
            self.favorites = set(favorites) if isinstance(favorites, list) else set()
            apps = data.get("apps", {})
            self.app_settings = apps if isinstance(apps, dict) else {}
        except FileNotFoundError:
            if not any(p.get("name") == "OpenCPN – 1920×1080" for p in self.profiles):
                self.profiles.append({"name": "OpenCPN – 1920×1080", "width": 1920, "height": 1080, "dpi": 240})
        except (OSError, ValueError, TypeError) as exc:
            self._set_status(f"A mentett beállítások nem olvashatók: {exc}", True)

    def closeEvent(self, event):
        if hasattr(self, "runtime_timer"):
            self.runtime_timer.stop()
        try:
            self._store_current_app_settings()
            self.save_config(show_message=False)
        except Exception as exc:
            print(f"[virtual_screens] bezáráskori mentési hiba: {exc}", flush=True)
        super().closeEvent(event)


def open_module(parent=None, settings=None):
    """Kompatibilis belépési pont, ha a főprogram ezt a mintát használja."""
    return VirtualScreensWidget(parent=parent, settings=settings)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    widget = VirtualScreensWidget()
    widget.setWindowTitle(f"Redmi Control Center — {APP_TITLE}")
    widget.resize(1100, 800)
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
