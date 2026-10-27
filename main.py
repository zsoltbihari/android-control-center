#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center — főablak
Követelmény: Python 3.10+ és PyQt6.
Indítás: python3 main.py

Ez az első, önállóan futtatható váz. A menü és a fő kezelőfelület működik;
a külön modulokhoz tartozó műveletek egyelőre jelzik, ha az adott modul még
nincs implementálva. A scrcpy indítás/leállítás külön vezérlőosztályba kerül
a projekt következő lépésében.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QAction, QFont, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QStackedWidget,
)


APP_NAME = "Redmi Control Center"

CONFIG_DIR = Path.home() / ".config" / "redmi-control-center"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

PRESETS = [
    "Alapértelmezett",
    "Matrix",
    "Zombie",
    "Vampire",
    "Barbie",
    "Pink pussycat",
]

THEMES = {
    "Alapértelmezett": """
        QWidget { background: #f0f0f0; color: #202020; }
        QMainWindow, QDialog { background: #f0f0f0; }
        QFrame#workspace { background: #fafafa; border: 1px solid #c8c8c8; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #b9b9b9; border-radius: 5px; background: #f8f8f8; }
        QPushButton:hover { background: #e4e4e4; }
        QPushButton:pressed { background: #d2d2d2; }
        QComboBox { padding: 6px; border: 1px solid #b9b9b9; border-radius: 5px; }
        QMenuBar, QMenu { background: #f0f0f0; color: #202020; }
        QMenu::item:selected { background: #d7d7d7; }
    """,
    "Matrix": """
        QWidget { background: #07120b; color: #72ff8b; }
        QMainWindow, QDialog { background: #07120b; }
        QFrame#workspace { background: #020704; border: 1px solid #1e8b42; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #238b45; border-radius: 5px; background: #0b2112; color: #72ff8b; }
        QPushButton:hover { background: #12391d; }
        QComboBox { padding: 6px; border: 1px solid #238b45; border-radius: 5px; background: #0b2112; }
        QMenuBar, QMenu { background: #07120b; color: #72ff8b; }
        QMenu::item:selected { background: #174d27; }
    """,
    "Zombie": """
        QWidget { background: #202522; color: #d1d8c6; }
        QMainWindow, QDialog { background: #202522; }
        QFrame#workspace { background: #171b18; border: 1px solid #68775f; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #68775f; border-radius: 5px; background: #303a31; color: #e1e8d4; }
        QPushButton:hover { background: #465342; }
        QComboBox { padding: 6px; border: 1px solid #68775f; border-radius: 5px; background: #303a31; }
        QMenuBar, QMenu { background: #202522; color: #d1d8c6; }
        QMenu::item:selected { background: #465342; }
    """,
    "Vampire": """
        QWidget { background: #171018; color: #f0dce8; }
        QMainWindow, QDialog { background: #171018; }
        QFrame#workspace { background: #0d080e; border: 1px solid #842b49; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #842b49; border-radius: 5px; background: #29131f; color: #f0dce8; }
        QPushButton:hover { background: #4a1c31; }
        QComboBox { padding: 6px; border: 1px solid #842b49; border-radius: 5px; background: #29131f; }
        QMenuBar, QMenu { background: #171018; color: #f0dce8; }
        QMenu::item:selected { background: #4a1c31; }
    """,
    "Barbie": """
        QWidget { background: #fff0f7; color: #651044; }
        QMainWindow, QDialog { background: #fff0f7; }
        QFrame#workspace { background: #fff9fc; border: 1px solid #e58abb; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #df78b0; border-radius: 5px; background: #ffd8ed; color: #651044; }
        QPushButton:hover { background: #ffb7dc; }
        QComboBox { padding: 6px; border: 1px solid #df78b0; border-radius: 5px; background: #ffd8ed; }
        QMenuBar, QMenu { background: #fff0f7; color: #651044; }
        QMenu::item:selected { background: #ffb7dc; }
    """,
    "Pink pussycat": """
        QWidget { background: #2a1426; color: #ffd9f1; }
        QMainWindow, QDialog { background: #2a1426; }
        QFrame#workspace { background: #1b0c19; border: 1px solid #b64d94; border-radius: 8px; }
        QPushButton { padding: 7px 12px; border: 1px solid #b64d94; border-radius: 5px; background: #46203e; color: #ffd9f1; }
        QPushButton:hover { background: #69305e; }
        QComboBox { padding: 6px; border: 1px solid #b64d94; border-radius: 5px; background: #46203e; }
        QMenuBar, QMenu { background: #2a1426; color: #ffd9f1; }
        QMenu::item:selected { background: #69305e; }
    """,
}


def load_settings() -> dict:
    defaults = {
        "theme": "Alapértelmezett",
        "window_width": 1100,
        "window_height": 720,
        "scrcpy_path": shutil.which("scrcpy") or "scrcpy",
        "adb_path": shutil.which("adb") or "adb",
    }
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Beállítások betöltési hibája: {exc}", file=sys.stderr)
    if defaults.get("theme") not in THEMES:
        defaults["theme"] = "Alapértelmezett"
    return defaults


def save_settings(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = SETTINGS_FILE.with_suffix(".json.tmp")
    temp_file.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(SETTINGS_FILE)


class PreferencesDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Beállítások")
        self.setMinimumWidth(440)
        self.settings = dict(settings)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(PRESETS)
        self.theme_combo.setCurrentText(self.settings.get("theme", "Alapértelmezett"))
        form.addRow("Témasablon:", self.theme_combo)

        self.scrcpy_path = __import__("PyQt6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit(
            self.settings.get("scrcpy_path", "scrcpy")
        )
        form.addRow("scrcpy parancs:", self.scrcpy_path)

        self.adb_path = __import__("PyQt6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit(
            self.settings.get("adb_path", "adb")
        )
        form.addRow("ADB parancs:", self.adb_path)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(800, 3840)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(int(self.settings.get("window_width", 1100)))
        form.addRow("Ablak szélessége:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(500, 2160)
        self.height_spin.setSuffix(" px")
        self.height_spin.setValue(int(self.settings.get("window_height", 720)))
        form.addRow("Ablak magassága:", self.height_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict:
        result = dict(self.settings)
        result.update({
            "theme": self.theme_combo.currentText(),
            "scrcpy_path": self.scrcpy_path.text().strip() or "scrcpy",
            "adb_path": self.adb_path.text().strip() or "adb",
            "window_width": self.width_spin.value(),
            "window_height": self.height_spin.value(),
        })
        return result


class MainWindow(QMainWindow):
    MODULES = {
        "Kapcsolatok": ("modules.connections", "Kapcsolatok"),
        "Telefonkönyv": ("modules.contacts", "Telefonkönyv"),
        "Eszközadatok": ("modules.device_info", "Eszközadatok"),
        "Fájlkezelő": ("modules.file_manager", "Fájlkezelő"),
        "Tárhely": ("modules.storage", "Tárhely"),
        "Alkalmazások": ("modules.applications", "Alkalmazások"),
        "Hálózat": ("modules.network", "Hálózat"),
        "Diagnosztika": ("modules.diagnostics", "Diagnosztika"),
        "Hálózati diagnosztika": ("modules.diagnostics", "Hálózati diagnosztika"),
        "FRP-diagnosztika és lekérdezések": ("modules.google_accounts", "FRP-diagnosztika és lekérdezések"),
        "Képernyőtükrözés": ("modules.screen_mirroring", "Képernyőtükrözés"),
    }

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.scrcpy_process: QProcess | None = None
        self.setWindowTitle(APP_NAME)
        self.resize(
            int(self.settings.get("window_width", 1100)),
            int(self.settings.get("window_height", 720)),
        )
        self._build_menu()
        self._build_ui()
        self.apply_theme(self.settings.get("theme", "Alapértelmezett"))
        self.statusBar().showMessage("Készen áll. A modulok külön fejleszthetők.")

    def _build_menu(self):
        # Egyetlen felső sáv: a menüsor balra, a témaválasztó jobbra igazítva.
        menu_container = QWidget(self)
        menu_layout = QHBoxLayout(menu_container)
        menu_layout.setContentsMargins(0, 0, 6, 0)
        menu_layout.setSpacing(0)

        bar = QMenuBar(menu_container)
        menu_layout.addWidget(bar, 0)
        menu_layout.addStretch(1)

        file_menu = bar.addMenu("Fájl")
        home_action = QAction("Kezdőlap", self)
        home_action.triggered.connect(self.show_home)
        file_menu.addAction(home_action)
        file_menu.addSeparator()
        preferences_action = QAction("Beállítások…", self)
        preferences_action.triggered.connect(self.open_preferences)
        file_menu.addAction(preferences_action)
        file_menu.addSeparator()
        exit_action = QAction("Kilépés", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        phone_menu = bar.addMenu("Telefon")
        self._add_module_action(phone_menu, "Kapcsolatok")
        self._add_module_action(phone_menu, "Telefonkönyv")
        self._add_module_action(phone_menu, "Eszközadatok")
        self._add_module_action(phone_menu, "Hálózati diagnosztika")
        self._add_module_action(phone_menu, "Tárhely")
        self._add_module_action(phone_menu, "Alkalmazások")
        self._add_module_action(phone_menu, "FRP-diagnosztika és lekérdezések")

        screen_menu = bar.addMenu("Képernyő")
        self._add_module_action(screen_menu, "Képernyőtükrözés")
        extended_action = QAction("Kiterjesztett", self)
        extended_action.triggered.connect(self.start_extended)
        screen_menu.addAction(extended_action)
        virtual_action = QAction("Virtuális kijelző", self)
        virtual_action.triggered.connect(self.start_virtual_display)
        screen_menu.addAction(virtual_action)
        stop_action = QAction("scrcpy leállítása", self)
        stop_action.triggered.connect(self.stop_scrcpy)
        screen_menu.addAction(stop_action)
        screen_menu.addSeparator()

        tools_menu = bar.addMenu("Eszközök")
        file_manager_workspace_action = QAction("Fájlkezelő a munkaterületen", self)
        file_manager_workspace_action.triggered.connect(
            lambda checked=False: self.open_module("Fájlkezelő")
        )
        tools_menu.addAction(file_manager_workspace_action)
        file_manager_window_action = QAction("Fájlkezelő külön ablakban", self)
        file_manager_window_action.triggered.connect(
            lambda checked=False: self.open_file_manager_window()
        )
        tools_menu.addAction(file_manager_window_action)
        tools_menu.addSeparator()
        self._add_module_action(tools_menu, "Hálózat")

        help_menu = bar.addMenu("Súgó")
        about_action = QAction("Névjegy", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        theme_widget = QWidget(menu_container)
        theme_layout = QHBoxLayout(theme_widget)
        theme_layout.setContentsMargins(8, 0, 8, 0)
        theme_layout.setSpacing(6)
        theme_label = QLabel("Témasablon:", theme_widget)
        theme_layout.addWidget(theme_label)
        self.preset_combo = QComboBox(theme_widget)
        self.preset_combo.addItems(PRESETS)
        self.preset_combo.setCurrentText(self.settings.get("theme", "Alapértelmezett"))
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        self.preset_combo.setMinimumWidth(150)
        theme_layout.addWidget(self.preset_combo)
        menu_layout.addWidget(theme_widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setMenuWidget(menu_container)

    def _add_module_action(self, menu, title: str):
        action = QAction(title, self)
        action.triggered.connect(lambda checked=False, name=title: self.open_module(name))
        menu.addAction(action)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        self.workspace = QFrame()
        self.workspace.setObjectName("workspace")
        workspace_layout = QVBoxLayout(self.workspace)
        workspace_layout.setContentsMargins(8, 8, 8, 8)
        workspace_layout.setSpacing(4)

        # A kezdőlap és a modulok ugyanabban a munkaterületben váltanak nézetet.
        self.views = QStackedWidget(self.workspace)
        workspace_layout.addWidget(self.views)

        self.home_page = QWidget()
        home_layout = QVBoxLayout(self.home_page)
        home_layout.setContentsMargins(24, 24, 24, 24)
        home_layout.addStretch(1)
        heading = QLabel("Redmi Control Center")
        heading.setFont(QFont("", 20, QFont.Weight.DemiBold))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        home_layout.addWidget(heading)
        description = QLabel(
            "Válassz funkciót a felső menüsorból.\n"
            "A modulok ezen a munkaterületen jelennek meg."
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        home_layout.addWidget(description)

        # A képernyőfunkciók elérhetők a Képernyő menüből, nem duplikáljuk őket gombokkal.
        screen_hint = QLabel("Képernyőtükrözés és megjelenítési módok: Képernyő menü")
        screen_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        home_layout.addWidget(screen_hint)
        home_layout.addStretch(1)
        self.views.addWidget(self.home_page)

        self._module_pages = {}
        self.connections_widget = None
        root.addWidget(self.workspace, 1)
        self.setCentralWidget(central)

    def show_home(self):
        self.views.setCurrentWidget(self.home_page)
        self.statusBar().showMessage("Kezdőlap", 2500)

    def apply_theme(self, theme_name: str):
        if theme_name not in THEMES:
            theme_name = "Alapértelmezett"
        QApplication.instance().setStyleSheet(THEMES[theme_name])
        self.settings["theme"] = theme_name
        if hasattr(self, "preset_combo") and self.preset_combo.currentText() != theme_name:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText(theme_name)
            self.preset_combo.blockSignals(False)

    def on_preset_changed(self, theme_name: str):
        self.apply_theme(theme_name)
        self._save_current_settings()
        self.statusBar().showMessage(f"Témasablon: {theme_name}", 3000)

    def _save_current_settings(self):
        self.settings["window_width"] = self.width()
        self.settings["window_height"] = self.height()
        try:
            save_settings(self.settings)
        except OSError as exc:
            QMessageBox.warning(self, "Beállítási hiba", f"A beállítások nem menthetők:\n{exc}")

    def open_preferences(self):
        dialog = PreferencesDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.get_settings()
            self.apply_theme(self.settings["theme"])
            self.resize(self.settings["window_width"], self.settings["window_height"])
            self._save_current_settings()
            self.statusBar().showMessage("Beállítások mentve.", 3000)

    def open_file_manager_window(self):
        """A fájlkezelőt külön, önálló ablakban nyitja meg; meglévőt előtérbe hoz."""
        existing = getattr(self, "_file_manager_window", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._file_manager_window = None

        try:
            from modules.file_manager import open_module as open_file_manager
            window = open_file_manager(parent=None, settings=self.settings)
            window.setWindowTitle("Redmi Control Center — Fájlkezelő")
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._file_manager_window = window
            window.destroyed.connect(lambda *_: setattr(self, "_file_manager_window", None))
            window.show()
            window.raise_()
            window.activateWindow()
            self.statusBar().showMessage("Fájlkezelő megnyitva külön ablakban", 3000)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Fájlkezelő hiba",
                f"A fájlkezelő külön ablakban nem nyitható meg:\n{exc}",
            )

    def open_module(self, name: str):
        # A modulnézetek a főablak munkaterületében jelennek meg; nem nyitunk
        # külön QMainWindow-t, és a kezdőlapot sem töröljük.
        if name in self._module_pages:
            self.views.setCurrentWidget(self._module_pages[name])
            self.statusBar().showMessage(f"Megnyitva: {name}", 3000)
            return

        if name == "Képernyőtükrözés":
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            try:
                from modules.screen_mirroring import ScreenMirroringWidget
                widget = ScreenMirroringWidget(page, self.settings)
                layout.addWidget(widget)
            except Exception as exc:
                heading = QLabel("A Képernyőtükrözés modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(str(exc))
                detail.setWordWrap(True)
                layout.addWidget(detail)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Képernyőtükrözés", 3000)
            return

        if name == "Fájlkezelő":
            try:
                from modules.file_manager import open_module as open_file_manager
                page = open_file_manager(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                heading = QLabel("A Fájlkezelő modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(str(exc))
                detail.setWordWrap(True)
                layout.addWidget(detail)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Fájlkezelő a munkaterületen", 3000)
            return

        if name == "Eszközadatok":
            # Ugyanaz a munkaterületi minta, mint a Hálózati diagnosztikánál:
            # maga a modul-widget kerül a QStackedWidgetbe, nincs fölösleges
            # köztes oldal vagy külön ablak.
            try:
                from modules.device_info import open_module as open_device_info
                page = open_device_info(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("Az Eszközadatok modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\n\nEllenőrizd, hogy a modules/device_info.py fájl a helyén van-e, "
                    "és a modul függőségei telepítve vannak-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Eszközadatok", 3000)
            return

        if name == "Kapcsolatok":
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            try:
                from modules.connections import ConnectionsWidget
                self.connections_widget = ConnectionsWidget(page, self.settings)
                layout.addWidget(self.connections_widget)
            except Exception as exc:
                heading = QLabel("A Kapcsolatok modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(str(exc))
                detail.setWordWrap(True)
                layout.addWidget(detail)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Kapcsolatok", 3000)
            return

        if name == "Telefonkönyv":
            try:
                from modules.contacts import open_module as open_contacts
                page = open_contacts(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("A Telefonkönyv modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\n\nEllenőrizd, hogy a modules/contacts.py fájl a helyén van-e, "
                    "és a modul függőségei telepítve vannak-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Telefonkönyv", 3000)
            return

        if name == "FRP-diagnosztika és lekérdezések":
            try:
                from modules.google_accounts import open_module as open_frp_module
                page = open_frp_module(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("Az FRP-diagnosztika modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\n\nEllenőrizd, hogy a google_accounts.py fájl a modules könyvtárban van-e, "
                    "és a PyQt6 függőség telepítve van-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: FRP-diagnosztika és lekérdezések", 3000)
            return

        if name == "Tárhely":
            try:
                from modules.storage import open_module as open_storage
                page = open_storage(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("A Tárhely modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\\n\\nEllenőrizd, hogy a modules/storage.py fájl a helyén van-e, "
                    "és a modul függőségei telepítve vannak-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Tárhely", 3000)
            return

        if name == "Alkalmazások":
            try:
                from modules.applications import build_tab
                page = build_tab(self)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("Az Alkalmazások modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\n\nEllenőrizd, hogy a modules/applications.py fájl a helyén van-e, "
                    "és a modul függőségei telepítve vannak-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Alkalmazások", 3000)
            return

        if name == "Hálózati diagnosztika":
            try:
                from modules.diagnostics import open_module as open_diagnostics
                page = open_diagnostics(parent=self, settings=self.settings)
            except Exception as exc:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(16, 16, 16, 16)
                heading = QLabel("A Hálózati diagnosztika modul nem tölthető be")
                heading.setFont(QFont("", 16, QFont.Weight.DemiBold))
                layout.addWidget(heading)
                detail = QLabel(
                    f"{exc}\\n\\nEllenőrizd, hogy a modules/diagnostics.py fájl a helyén van-e, "
                    "és a modul függőségei telepítve vannak-e."
                )
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(detail)
                layout.addStretch(1)
            self.views.addWidget(page)
            self._module_pages[name] = page
            self.views.setCurrentWidget(page)
            self.statusBar().showMessage("Megnyitva: Hálózati diagnosztika", 3000)
            return

        # Ismeretlen vagy még nem implementált modul biztonságos helyőrzője.
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        heading = QLabel(name)
        heading.setFont(QFont("", 18, QFont.Weight.DemiBold))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        detail = QLabel("Ez a modul még nincs implementálva. A felülete a főablakban fog megjelenni.")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addStretch(1)
        self.views.addWidget(page)
        self._module_pages[name] = page
        self.views.setCurrentWidget(page)
        self.statusBar().showMessage(f"Modul előkészítve: {name}", 3000)

    def _start_scrcpy(self, mode: str):
        if self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(
                self, "scrcpy", "A scrcpy már fut. Előbb állítsd le, majd indítsd újra másik módban."
            )
            return

        executable = self.settings.get("scrcpy_path", "scrcpy").strip() or "scrcpy"
        if not (os.path.isabs(executable) and os.path.isfile(executable)) and shutil.which(executable) is None:
            QMessageBox.warning(
                self, "scrcpy nem található",
                f"Nem találom a scrcpy parancsot:\n{executable}\n\n"
                "Ellenőrizd a Beállítások ablakban, vagy telepítsd a scrcpy csomagot."
            )
            return

        args = []
        if mode == "extended":
            # scrcpy versions differ in multi-display support; do not guess a display ID.
            QMessageBox.information(
                self, "Kiterjesztett mód",
                "A kiterjesztett mód pontos scrcpy-paramétereit a telepített verzió alapján "
                "kell beállítani. Most a tükrözés nem indul el automatikusan, nehogy hibás "
                "parancsot futtassunk."
            )
            return
        if mode == "virtual":
            QMessageBox.information(
                self, "Virtuális kijelző",
                "A virtuális kijelző támogatása a telepített scrcpy-verziótól és az Android "
                "verziójától függ. Ezt külön ellenőrző lépésben kötjük be."
            )
            return

        self.scrcpy_process = QProcess(self)
        self.scrcpy_process.setProgram(executable)
        self.scrcpy_process.setArguments(args)
        self.scrcpy_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.scrcpy_process.readyReadStandardOutput.connect(self._read_scrcpy_output)
        self.scrcpy_process.errorOccurred.connect(self._scrcpy_error)
        self.scrcpy_process.finished.connect(self._scrcpy_finished)
        self.scrcpy_process.start()
        self.statusBar().showMessage("scrcpy indítása…", 5000)

    def start_mirroring(self):
        self._start_scrcpy("mirror")

    def start_extended(self):
        self._start_scrcpy("extended")

    def start_virtual_display(self):
        self._start_scrcpy("virtual")

    def _read_scrcpy_output(self):
        if self.scrcpy_process:
            output = bytes(self.scrcpy_process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
            if output:
                self.statusBar().showMessage(output[-180:], 5000)

    def _scrcpy_error(self, error):
        if self.scrcpy_process and self.scrcpy_process.error() == QProcess.ProcessError.FailedToStart:
            QMessageBox.warning(
                self, "scrcpy indítási hiba",
                "A scrcpy folyamat nem indult el. Ellenőrizd a parancs útvonalát és a telepítést."
            )

    def _scrcpy_finished(self, exit_code, exit_status):
        self.statusBar().showMessage(f"scrcpy leállt (kilépési kód: {exit_code}).", 5000)
        self.scrcpy_process = None

    def stop_scrcpy(self):
        stopped_local = False
        process = self.scrcpy_process
        if process and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(1500):
                process.kill()
                process.waitForFinished(1000)
            self.scrcpy_process = None
            stopped_local = True

        # A tükrözőmodul külön subprocess.Popen folyamatot indít, ezért a
        # QProcess-objektum önmagában nem látja. A menüpont neve alapján ez a
        # művelet az összes, aktuálisan futó scrcpy példányt leállítja.
        try:
            result = subprocess.run(
                ["pkill", "-TERM", "-x", "scrcpy"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                stopped_local = True
            elif result.returncode not in (1,):
                detail = (result.stderr or result.stdout or "").strip()
                QMessageBox.warning(
                    self,
                    "scrcpy leállítása",
                    "A pkill parancs hibával tért vissza."
                    + (f"\\n{detail}" if detail else ""),
                )
                return
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "scrcpy leállítása",
                "A pkill parancs nem található, ezért a külön indított scrcpy-folyamatokat "
                "nem tudom leállítani.",
            )
            return
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "scrcpy leállítása", "Időtúllépés a leállítás során.")
            return

        if stopped_local:
            self.statusBar().showMessage("scrcpy leállítási parancs elküldve.", 3000)
        else:
            QMessageBox.information(self, "scrcpy", "Nem található futó scrcpy-folyamat.")

    def show_about(self):
        """A látványtervhez igazított, képes névjegyablak."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Névjegy")
        dialog.setModal(True)
        dialog.setMinimumSize(900, 620)
        dialog.resize(1040, 700)
        dialog.setStyleSheet("""
            QDialog { background: #080b0d; color: #e9edf1; border: 2px solid #1748a4; }
            QLabel { color: #c4cbd2; background: transparent; }
            QLabel#appTitle { color: #f1f3f5; font-size: 35px; font-weight: 700; }
            QLabel#redTitle { color: #ff2028; }
            QLabel#subtitle { color: #b9c1c9; font-size: 20px; }
            QLabel#sectionLabel { color: #9aa5af; font-size: 16px; }
            QLabel#sectionValue { color: #f2f4f6; font-size: 17px; font-weight: 600; }
            QLabel#bodyText { color: #aeb7c0; font-size: 14px; }
            QLabel#email { color: #32a7ff; font-size: 16px; }
            QPushButton { color: #f4f7fa; background: #202a32; border: 1px solid #1688d8;
                          border-radius: 7px; padding: 10px 24px; font-size: 15px; }
            QPushButton:hover { background: #2a3b49; }
        """)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        content = QHBoxLayout()
        content.setSpacing(28)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumWidth(320)
        image_label.setMaximumWidth(390)
        # A névjegykép külön fájlból töltődik be; nem növeli a main.py méretét.
        image_path = Path(__file__).resolve().with_name("scrcpy.png")
        pixmap = QPixmap(str(image_path))
        if not pixmap.isNull():
            image_label.setPixmap(pixmap.scaled(
                370, 500, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
            image_label.setText("A scrcpy.png névjegykép nem található.")
            image_label.setWordWrap(True)
        content.addWidget(image_label, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        title = QHBoxLayout()
        red = QLabel("Redmi")
        red.setObjectName("redTitle")
        red.setStyleSheet("font-size: 35px; font-weight: 700; color: #ff2028;")
        white = QLabel("Control Center")
        white.setObjectName("appTitle")
        title.addWidget(red)
        title.addWidget(white)
        title.addStretch(1)
        right.addLayout(title)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #ff2028; max-height: 2px; border: none;")
        right.addWidget(line)

        subtitle = QLabel("Xiaomi / Redmi telefonok kezelése Linuxon")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        right.addWidget(subtitle)

        version = QLabel("Verzió:  <b>1.0</b>")
        version.setStyleSheet("font-size: 19px; color: #b9c1c9;")
        right.addWidget(version)

        description = QLabel(
            "Eszköz a Xiaomi / Redmi telefonok kezeléséhez és karbantartásához Linux környezetben. "
            "A program ADB-t, fastbootot és egyéb eszközöket használ a mindennapi feladatok "
            "egyszerűsítéséhez."
        )
        description.setObjectName("bodyText")
        description.setWordWrap(True)
        right.addWidget(description)

        divider1 = QFrame()
        divider1.setFrameShape(QFrame.Shape.HLine)
        divider1.setStyleSheet("color: #39434b;")
        right.addWidget(divider1)

        creator = QHBoxLayout()
        creator_label = QLabel("Létrehozó:")
        creator_label.setObjectName("sectionLabel")
        creator_value = QLabel("Bihari Zsolt")
        creator_value.setObjectName("sectionValue")
        creator.addWidget(creator_label, 1)
        creator.addWidget(creator_value, 2)
        right.addLayout(creator)

        email_row = QHBoxLayout()
        email_label = QLabel("Elérhetőség:")
        email_label.setObjectName("sectionLabel")
        email = QLabel('<a href="mailto:info@tengerikajak.net" style="color:#32a7ff;">info@tengerikajak.net</a>')
        email.setObjectName("email")
        email.setTextFormat(Qt.TextFormat.RichText)
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        email.setOpenExternalLinks(True)
        email_row.addWidget(email_label, 1)
        email_row.addWidget(email, 2)
        right.addLayout(email_row)

        divider2 = QFrame()
        divider2.setFrameShape(QFrame.Shape.HLine)
        divider2.setStyleSheet("color: #39434b;")
        right.addWidget(divider2)

        thanks_label = QLabel("Köszönet:")
        thanks_label.setObjectName("sectionLabel")
        thanks_text = QLabel(
            "a nyílt forráskódú közösségnek, az ADB és fastboot fejlesztőinek, "
            "valamint minden felhasználónak, aki visszajelzéseivel hozzájárul a program fejlődéséhez."
        )
        thanks_text.setObjectName("bodyText")
        thanks_text.setWordWrap(True)
        thanks_row = QHBoxLayout()
        thanks_row.addWidget(thanks_label, 1, Qt.AlignmentFlag.AlignTop)
        thanks_row.addWidget(thanks_text, 2)
        right.addLayout(thanks_row)

        disclaimer_title = QLabel("Felelősségkizárás")
        disclaimer_title.setObjectName("sectionLabel")
        right.addWidget(disclaimer_title)
        disclaimer = QLabel(
            "A program használatáért semmilyen felelősséget nem vállalok. Meggyőződésem szerint "
            "azonban atomháború esetén is működőképes marad, kivéve, ha a nukleáris csapás "
            "következtében megsérül a Python-indító, az ADB, vagy valaki kihúzza a kábelt."
        )
        disclaimer.setObjectName("bodyText")
        disclaimer.setWordWrap(True)
        right.addWidget(disclaimer)
        right.addStretch(1)

        buttons = QDialogButtonBox()
        ok = buttons.addButton("✓  OK", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.clicked.connect(dialog.accept)
        right.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        content.addLayout(right, 2)
        root.addLayout(content, 1)
        dialog.exec()

    def closeEvent(self, event):
        self._save_current_settings()
        # Avoid leaving a child scrcpy process behind when closing the main window.
        if self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning:
            self.scrcpy_process.terminate()
            if not self.scrcpy_process.waitForFinished(1200):
                self.scrcpy_process.kill()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("RedmiControlCenter")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
