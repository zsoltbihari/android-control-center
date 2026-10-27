#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alkalmazáskezelő modul a Redmi Control Center Qt6 felületéhez.

Integráció: build_tab(main_window) -> QWidget
A modul a főablak meglévő shell(), require_device(), call_worker() és log_msg()
metódusait használja; nem hoz létre külön ADB-kapcsolatot.
"""
from __future__ import annotations

import re
import urllib.parse
import subprocess
import shutil
import os
import json
import zipfile
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import QSettings, QProcess, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFileDialog, QMenu, QDialog, QDialogButtonBox, QInputDialog
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QMessageBox, QGroupBox, QTabWidget, QHeaderView,
    QSplitter, QSizePolicy, QAbstractItemView
)

# A keretek az elfogadott látványterv négy színkódolt területét jelölik.
FRAME_STYLE = {
    "filter": "QGroupBox { border: 2px solid #ff4545; border-radius: 8px; margin-top: 10px; padding: 8px; } QGroupBox::title { color: #ff6565; subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 700; }",
    "list": "QGroupBox { border: 2px solid #18d978; border-radius: 8px; margin-top: 10px; padding: 8px; } QGroupBox::title { color: #35e994; subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 700; }",
    "details": "QGroupBox { border: 2px solid #269fff; border-radius: 8px; margin-top: 10px; padding: 8px; } QGroupBox::title { color: #52b6ff; subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 700; }",
    "log": "QGroupBox { border: 2px solid #ffd43b; border-radius: 8px; margin-top: 10px; padding: 8px; } QGroupBox::title { color: #ffdf62; subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 700; }",
}


def build_tab(main_window):
    """Elkészíti az alkalmazáskezelő QWidgetet a főablak meglévő szolgáltatásaival."""
    return ApplicationsPanel(main_window)


class ApplicationsPanel(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._all_packages: list[tuple[str, str]] = []
        self._selected_package = None
        self._build_ui()
        self.refresh_devices()
        self._log("INFO", "Alkalmazáskezelő készen áll. Válaszd ki a készüléket, majd frissítsd az alkalmazáslistát.")
        # Az első fül telefonos listáját induláskor ténylegesen betöltjük.
        # Az APK- és állapotlisták ettől függetlenül a saját helyi könyvtárukból olvasnak.
        if self.device_combo.currentData():
            QTimer.singleShot(0, self.refresh_apps)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(5)
        self.main_tabs = QTabWidget()
        self.main_tabs.setDocumentMode(True)
        root.addWidget(self.main_tabs)
        apps_page = QWidget()
        app_layout = QVBoxLayout(apps_page)
        app_layout.setContentsMargins(4, 4, 4, 4)
        app_layout.setSpacing(5)

        filters = QGroupBox("Keresés és szűrők")
        filters.setStyleSheet(FRAME_STYLE["filter"])
        fl = QHBoxLayout(filters)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.setToolTip("Az adb devices -l kimenetéből választja ki a készüléket; több eszköz esetén itt válthatsz.")
        self.device_combo.currentIndexChanged.connect(lambda _index: self._sync_host_serial())
        fl.addWidget(self.device_combo)
        self.device_refresh_btn = QPushButton("Eszközök")
        self.device_refresh_btn.clicked.connect(self.refresh_devices)
        fl.addWidget(self.device_refresh_btn)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Keresés alkalmazásnév vagy csomagnév alapján…")
        self.search.textChanged.connect(self._apply_filter)
        fl.addWidget(self.search, 2)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Felhasználói alkalmazások", "user")
        self.kind_filter.addItem("Rendszeralkalmazások", "system")
        self.kind_filter.addItem("Összes alkalmazás", "all")
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        fl.addWidget(self.kind_filter, 1)
        self.enabled_only = QCheckBox("Csak engedélyezettek")
        self.enabled_only.setToolTip("A lista betöltésekor a csomagok engedélyezési állapotát is lekérdezi; nagy listánál lassabb lehet.")
        self.enabled_only.toggled.connect(self._apply_filter)
        fl.addWidget(self.enabled_only)
        self.refresh_btn = QPushButton("⟳ Lista frissítése")
        self.refresh_btn.setToolTip("Újra lekéri a csomaglistát a kiválasztott telefonról.")
        self.refresh_btn.clicked.connect(self.refresh_apps)
        fl.addWidget(self.refresh_btn)
        app_layout.addWidget(filters)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)

        list_box = QGroupBox("2 · Alkalmazáslista")
        list_box.setStyleSheet(FRAME_STYLE["list"])
        ll = QVBoxLayout(list_box)
        self.count_label = QLabel("Nincs betöltött lista")
        ll.addWidget(self.count_label)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Csomagnév", "Típus", "Állapot"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        ll.addWidget(self.table, 1)
        split.addWidget(list_box)

        details_box = QGroupBox("3 · Részletes adatok és műveletek")
        details_box.setStyleSheet(FRAME_STYLE["details"])
        dl = QVBoxLayout(details_box)
        self.selected_label = QLabel("Válassz alkalmazást a listából")
        self.selected_label.setWordWrap(True)
        self.selected_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.selected_label)
        self.detail_tabs = QTabWidget()
        self.basic = QPlainTextEdit(); self.basic.setReadOnly(True)
        self.permissions = QPlainTextEdit(); self.permissions.setReadOnly(True)
        self.storage = QPlainTextEdit(); self.storage.setReadOnly(True)
        self.components = QPlainTextEdit(); self.components.setReadOnly(True)
        self.raw = QPlainTextEdit(); self.raw.setReadOnly(True)
        for widget, title in ((self.basic, "Alapadatok"), (self.permissions, "Engedélyek"),
                              (self.storage, "Tárhely"), (self.components, "Komponensek"),
                              (self.raw, "További adatok")):
            widget.setPlaceholderText("Válassz alkalmazást, majd kérd le az adatait.")
            self.detail_tabs.addTab(widget, title)
        dl.addWidget(self.detail_tabs, 1)

        actions = QGridLayout()
        self.inspect_btn = QPushButton("Adatok lekérdezése")
        self.inspect_btn.clicked.connect(self.inspect_selected)
        self.start_btn = QPushButton("▶ Alkalmazás indítása")
        self.start_btn.clicked.connect(self.start_selected)
        self.activity_btn = QPushButton("Indítható Activity kiválasztása")
        self.activity_btn.setToolTip("Lekéri az alkalmazás Android-tevékenységeit, és lehetővé teszi egy konkrét Activity indítását.")
        self.activity_btn.clicked.connect(self.choose_activity)
        self.launch_view_btn = QPushButton("▶ Indítás és megjelenítés")
        self.launch_view_btn.setToolTip(
            "Elindítja a kiválasztott alkalmazást a telefonon, majd megnyitja a scrcpy ablakot, "
            "amelyben a telefon képernyőjét egérrel és billentyűzettel kezelheted. "
            "A működéshez telepített scrcpy és engedélyezett ADB-kapcsolat szükséges."
        )
        self.launch_view_btn.clicked.connect(self.launch_and_mirror)
        self.backup_apk_btn = QPushButton("APK mentése PC-re")
        self.backup_apk_btn.setToolTip("Elmenti az alkalmazás APK-fájljait. Osztott APK esetén minden szükséges csomagot ment.")
        self.backup_apk_btn.clicked.connect(self.backup_apk)
        self.install_apk_btn = QPushButton("APK telepítése PC-ről")
        self.install_apk_btn.setToolTip("APK-fájl(ok) kiválasztása a számítógépen és telepítése a kiválasztott telefonra.")
        self.install_apk_btn.clicked.connect(self.install_apk)
        self.clear_data_btn = QPushButton("Alkalmazásadatok törlése")
        self.clear_data_btn.setToolTip("Törli a kiválasztott alkalmazás helyi adatait. A művelet nem vonható vissza.")
        self.clear_data_btn.clicked.connect(self.clear_app_data)
        self.logcat_btn = QPushButton("Élő hibakeresési napló")
        self.logcat_btn.setToolTip("Megnyitja az alkalmazás folyamatához szűrt Android logcat naplót.")
        self.logcat_btn.clicked.connect(self.show_logcat)
        self.stop_btn = QPushButton("■ Kényszerített leállítás")
        self.stop_btn.clicked.connect(self.stop_selected)
        self.uninstall_btn = QPushButton("Eltávolítás a felhasználótól")
        self.uninstall_btn.clicked.connect(self.uninstall_selected)
        self.disable_btn = QPushButton("Letiltás")
        self.disable_btn.clicked.connect(lambda: self.set_enabled(False))
        self.enable_btn = QPushButton("Engedélyezés")
        self.enable_btn.clicked.connect(lambda: self.set_enabled(True))
        self.play_btn = QPushButton("Play Áruház megnyitása")
        self.play_btn.clicked.connect(self.open_play_store)
        self.website_btn = QPushButton("Fejlesztői webes keresés")
        self.website_btn.clicked.connect(lambda: self.web_search("website"))
        self.github_btn = QPushButton("GitHub-keresés")
        self.github_btn.clicked.connect(lambda: self.web_search("github"))
        self.general_btn = QPushButton("Általános webes keresés")
        self.general_btn.clicked.connect(lambda: self.web_search("general"))
        self.validate_apk_btn = QPushButton("APK ellenőrzése")
        self.validate_apk_btn.setToolTip("Telepítés előtt ellenőrzi a fájl épségét, csomagnevét és – ha elérhető – Android-verziókövetelményeit.")
        self.validate_apk_btn.clicked.connect(self.validate_local_apk)
        self.compare_apk_btn = QPushButton("APK-verzió összehasonlítása")
        self.compare_apk_btn.clicked.connect(self.compare_local_apk)
        self.save_state_btn = QPushButton("Telefonállapot mentése")
        self.save_state_btn.clicked.connect(self.save_device_state)
        self.compare_state_btn = QPushButton("Állapotmentések összehasonlítása")
        self.compare_state_btn.clicked.connect(self.compare_device_states)
        action_buttons = (self.inspect_btn, self.start_btn, self.activity_btn, self.launch_view_btn,
                          self.clear_data_btn, self.logcat_btn, self.stop_btn,
                          self.uninstall_btn, self.disable_btn, self.enable_btn, self.play_btn,
                          self.website_btn, self.github_btn, self.general_btn)
        for i, btn in enumerate(action_buttons):
            actions.addWidget(btn, i // 2, i % 2)
        dl.addLayout(actions)
        split.addWidget(details_box)
        split.setStretchFactor(0, 11)
        split.setStretchFactor(1, 10)
        app_layout.addWidget(split, 1)
        self.main_tabs.addTab(apps_page, "Alkalmazások")

        # Önálló APK-kezelési munkafelület
        apk_page = QWidget()
        apk_layout = QVBoxLayout(apk_page)
        apk_layout.setContentsMargins(14, 14, 14, 14)
        apk_layout.setSpacing(10)
        apk_intro = QLabel("APK-archívum és telepítőeszközök. Válassz egy archívumkönyvtárat; a listában kijelölt APK-t ellenőrizheted és összevetheted a telefonos verzióval.")
        apk_intro.setWordWrap(True)
        apk_layout.addWidget(apk_intro)
        archive_bar = QHBoxLayout()
        # A régebbi modul az "apk_directory" kulcsba mentette az APK-k helyét.
        # Először az új beállítást, majd a régi útvonalat használjuk, de nem ragadunk
        # bent egy már nem létező könyvtárban.
        _settings = self._settings()
        _saved_apk_dir = str(_settings.value("apk_archive_directory", "") or "").strip()
        _legacy_apk_dir = str(_settings.value("apk_directory", "") or "").strip()
        _default_apk_dir = str(Path.home() / "APK-mentesek")
        _apk_dir = _saved_apk_dir or _legacy_apk_dir or _default_apk_dir
        if not Path(_apk_dir).expanduser().is_dir():
            if _legacy_apk_dir and Path(_legacy_apk_dir).expanduser().is_dir():
                _apk_dir = _legacy_apk_dir
            elif Path(_default_apk_dir).expanduser().is_dir():
                _apk_dir = _default_apk_dir
            else:
                # A Home mappát mutatjuk, hogy ne egy nem létező, kitalált útvonalon álljon.
                _apk_dir = str(Path.home())
        # Lenyló, szerkeszthető előzménylista az APK-archívum könyvtáraihoz.
        _apk_history = _settings.value("apk_archive_history", [])
        if isinstance(_apk_history, str):
            _apk_history = [_apk_history]
        _apk_history = [str(x) for x in (_apk_history or []) if x]
        if _apk_dir not in _apk_history:
            _apk_history.insert(0, _apk_dir)
        self.apk_directory_edit = QComboBox()
        self.apk_directory_edit.setEditable(True)
        self.apk_directory_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.apk_directory_edit.setMinimumContentsLength(34)
        self.apk_directory_edit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.apk_directory_edit.setToolTip("APK-archívum könyvtára. A korábban használt könyvtárak a lenyíló listából választhatók.")
        self.apk_directory_edit.addItems(_apk_history[:12])
        self.apk_directory_edit.setCurrentText(_apk_dir)
        archive_bar.addWidget(self.apk_directory_edit, 1)
        self.apk_directory_edit.activated.connect(lambda *_: self.refresh_apk_archive())
        self.apk_directory_edit.lineEdit().editingFinished.connect(self.refresh_apk_archive)
        browse_apk_dir = QPushButton("Könyvtár…")
        browse_apk_dir.clicked.connect(self.choose_apk_directory)
        archive_bar.addWidget(browse_apk_dir)
        scan_apks = QPushButton("Lista frissítése")
        scan_apks.clicked.connect(self.refresh_apk_archive)
        archive_bar.addWidget(scan_apks)
        apk_layout.addLayout(archive_bar)
        self.apk_table = QTableWidget(0, 4)
        self.apk_table.setHorizontalHeaderLabels(["APK-fájl", "Méret", "Csomagnév", "Verzió"])
        self.apk_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.apk_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.apk_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.apk_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.apk_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.apk_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.apk_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.apk_table.itemSelectionChanged.connect(self._apk_selection_changed)
        self.apk_table.doubleClicked.connect(lambda *_: self.validate_local_apk())
        apk_layout.addWidget(self.apk_table, 1)
        self.apk_status_label = QLabel("Válassz egy létező APK-archívumkönyvtárat, majd frissítsd a listát.")
        self.apk_status_label.setWordWrap(True)
        apk_layout.addWidget(self.apk_status_label)
        self.apk_selected_label = QLabel("Nincs kijelölt helyi APK.")
        apk_layout.addWidget(self.apk_selected_label)
        apk_group = QGroupBox("APK-műveletek")
        apk_grid = QGridLayout(apk_group)
        apk_buttons = (self.backup_apk_btn, self.install_apk_btn, self.validate_apk_btn, self.compare_apk_btn)
        for i, btn in enumerate(apk_buttons):
            apk_grid.addWidget(btn, i // 2, i % 2)
        apk_layout.addWidget(apk_group)
        self.main_tabs.addTab(apk_page, "APK-kezelés")
        self.refresh_apk_archive()

        # Önálló telefonállapot-mentési és összehasonlító munkafelület
        state_page = QWidget()
        state_layout = QVBoxLayout(state_page)
        state_layout.setContentsMargins(14, 14, 14, 14)
        state_layout.setSpacing(10)
        state_intro = QLabel("Telefonállapotok pillanatfelvételei. A mentéslistában több állapotot is kijelölhetsz összehasonlításhoz.")
        state_intro.setWordWrap(True)
        state_layout.addWidget(state_intro)
        state_dir_bar = QHBoxLayout()
        self.state_directory_edit = QComboBox()
        self.state_directory_edit.setEditable(True)
        self.state_directory_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.state_directory_edit.setMinimumContentsLength(34)
        self.state_directory_edit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.state_directory_edit.setToolTip("Az állapotmentések könyvtára. A legutóbb használt könyvtárak a lenyíló listából választhatók.")
        state_history = self._settings().value("state_archive_history", [])
        if isinstance(state_history, str): state_history = [state_history]
        current_state_dir = str(self._settings().value("state_archive_directory", str(Path.home() / "Redmi-állapotmentések")))
        for old_dir in state_history or []:
            if old_dir and old_dir != current_state_dir and self.state_directory_edit.findText(str(old_dir)) < 0:
                self.state_directory_edit.addItem(str(old_dir))
        self.state_directory_edit.setCurrentText(current_state_dir)
        self.state_directory_edit.currentTextChanged.connect(self._state_directory_changed)
        state_dir_bar.addWidget(self.state_directory_edit, 1)
        state_browse = QPushButton("Könyvtár…")
        state_browse.clicked.connect(self.choose_state_directory)
        state_dir_bar.addWidget(state_browse)
        state_refresh = QPushButton("Lista frissítése")
        state_refresh.clicked.connect(self.refresh_state_archive)
        state_dir_bar.addWidget(state_refresh)
        state_layout.addLayout(state_dir_bar)
        self.state_table = QTableWidget(0, 3)
        self.state_table.setHorizontalHeaderLabels(["Állapotmentés", "Dátum", "Alkalmazások"])
        self.state_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.state_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.state_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.state_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.state_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.state_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        state_layout.addWidget(self.state_table, 1)
        self.state_status_label = QLabel("Még nincs állapotmentés ebben a könyvtárban.")
        self.state_status_label.setWordWrap(True)
        state_layout.addWidget(self.state_status_label)
        state_group = QGroupBox("Mentés és összehasonlítás")
        state_grid = QGridLayout(state_group)
        state_grid.addWidget(self.save_state_btn, 0, 0)
        state_grid.addWidget(self.compare_state_btn, 0, 1)
        state_layout.addWidget(state_group)
        self.main_tabs.addTab(state_page, "Állapotmentések")
        self.refresh_state_archive()

        log_box = QGroupBox("Műveleti napló")
        log_box.setStyleSheet(FRAME_STYLE["log"])
        log_layout = QVBoxLayout(log_box)
        log_head = QHBoxLayout()
        log_head.addStretch(1)
        clear_log = QPushButton("Napló törlése")
        clear_log.clicked.connect(lambda: self.log_view.clear())
        log_head.addWidget(clear_log)
        log_layout.addLayout(log_head)
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1200)
        self.log_view.setMaximumHeight(16777215)
        log_layout.addWidget(self.log_view, 1)
        log_page = QWidget()
        log_page_layout = QVBoxLayout(log_page)
        log_page_layout.setContentsMargins(4, 4, 4, 4)
        log_page_layout.addWidget(log_box)
        self.main_tabs.addTab(log_page, "Műveleti napló")

    def _log(self, level, message):
        from datetime import datetime
        line = f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"
        if hasattr(self, "log_view"):
            self.log_view.appendPlainText(line)
        if hasattr(self.host, "log_msg"):
            self.host.log_msg(f"[Alkalmazások] {level}: {message}")

    def refresh_devices(self):
        """A google_accounts.py-ban használt közvetlen adb devices -l logikával listáz."""
        previous = self.device_combo.currentData() or getattr(self.host, "serial", None)
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        adb_path = shutil.which("adb") or ("/usr/bin/adb" if __import__("os").path.exists("/usr/bin/adb") else None)
        if not adb_path:
            self.device_combo.addItem("Az adb nem található a PATH-ban", None)
            self.device_combo.blockSignals(False)
            self._log("HIBA", "Az adb futtatható fájl nem található. Ellenőrizd: which adb")
            return
        self._adb_executable = adb_path
        try:
            p = subprocess.run([adb_path, "devices", "-l"], capture_output=True, text=True,
                               timeout=12, check=False)
            out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
            if p.returncode:
                raise RuntimeError(err or out or f"adb devices hibakód: {p.returncode}")
            found = []
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 2:
                    continue
                serial, state = parts[0], parts[1]
                extra = " ".join(parts[2:])
                model = ""
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part[6:].replace("_", " ")
                        break
                label = f"{model or serial} — {serial} [{state}]"
                if extra and not model:
                    label += f" {extra}"
                found.append((label, serial, state))
            for label, serial, state in found:
                self.device_combo.addItem(label, serial)
                self.device_combo.setItemData(self.device_combo.count() - 1,
                                              state == "device", Qt.ItemDataRole.UserRole + 1)
            if not found:
                self.device_combo.addItem("Nincs felismert ADB-eszköz", None)
            # Tartsuk meg a korábbi/főprogrambeli eszközt, ha továbbra is engedélyezett.
            idx = self.device_combo.findData(previous) if previous else -1
            if idx < 0:
                idx = next((i for i, (_, serial, state) in enumerate(found) if state == "device"), -1)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
            self.device_combo.blockSignals(False)
            self._sync_host_serial()
            states = ", ".join(f"{serial}: {state}" for _, serial, state in found) or "nincs eszköz"
            self._log("INFO", f"ADB-eszközlista: {states}")
            if len([d for d in found if d[2] == "device"]) > 1:
                self._log("INFO", "Több engedélyezett készülék található; a fenti listából választhatsz.")
        except Exception as exc:
            self.device_combo.blockSignals(False)
            self.device_combo.addItem("ADB-eszközlista lekérdezése sikertelen", None)
            self._log("HIBA", f"adb devices -l: {exc}")

    def _sync_host_serial(self):
        """A modul választását visszaírja a főablak közös aktív sorozatszámába."""
        serial = self.device_combo.currentData()
        if serial and self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1) is True:
            try:
                self.host.serial = serial
            except Exception:
                pass

    def _selected_serial(self):
        serial = self.device_combo.currentData()
        if serial:
            self._sync_host_serial()
        return serial

    def _has_device(self):
        serial = self._selected_serial()
        if not serial:
            QMessageBox.warning(self, "Nincs telefon", "Nincs kiválasztott ADB-eszköz. Ellenőrizd az USB/Wi-Fi kapcsolatot, majd frissítsd az eszközlistát.")
            return False
        if self.device_combo.currentIndex() >= 0 and self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1) is False:
            QMessageBox.warning(self, "ADB-eszköz nem engedélyezett", "A kiválasztott eszköz állapota nem 'device'. Engedélyezd az ADB-hozzáférést a telefonon.")
            return False
        return True

    def _shell(self, *args, timeout=20):
        serial = self._selected_serial()
        adb_path = getattr(self, "_adb_executable", None) or shutil.which("adb")
        if not serial:
            raise RuntimeError("Nincs kiválasztott ADB-eszköz.")
        if not adb_path:
            raise RuntimeError("Az adb nem található a PATH-ban.")
        # A google_accounts.py mintáját követi: adb -s SERIAL shell ...
        p = subprocess.run([adb_path, "-s", serial, "shell", *args],
                           capture_output=True, text=True, timeout=timeout, check=False)
        out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
        if p.returncode:
            raise RuntimeError(err or out or f"ADB exit code: {p.returncode}")
        return out

    def refresh_apps(self):
        if not self._has_device():
            return
        self.refresh_btn.setEnabled(False)
        self.count_label.setText("Alkalmazáslista lekérdezése…")
        def query():
            user_out = self._shell("pm", "list", "packages", "-3", timeout=35)
            all_out = self._shell("pm", "list", "packages", timeout=35)
            enabled_out = self._shell("pm", "list", "packages", "-e", timeout=35)
            disabled_out = self._shell("pm", "list", "packages", "-d", timeout=35)
            user = {x.removeprefix("package:").strip() for x in user_out.splitlines() if x.startswith("package:")}
            all_pkgs = {x.removeprefix("package:").strip() for x in all_out.splitlines() if x.startswith("package:")}
            enabled = {x.removeprefix("package:").strip() for x in enabled_out.splitlines() if x.startswith("package:")}
            disabled = {x.removeprefix("package:").strip() for x in disabled_out.splitlines() if x.startswith("package:")}
            return [(pkg, "Felhasználói" if pkg in user else "Rendszer",
                     "Engedélyezve" if pkg in enabled else ("Letiltva" if pkg in disabled else "Ismeretlen"))
                    for pkg in sorted(all_pkgs)]
        def done(rows):
            self._all_packages = rows
            self.refresh_btn.setEnabled(True)
            self._apply_filter()
            self._log("OK", f"{len(rows)} alkalmazás betöltve ({sum(1 for _,k,_ in rows if k == 'Felhasználói')} felhasználói).")
        def failed(message):
            self.refresh_btn.setEnabled(True)
            self.count_label.setText("A lista lekérdezése sikertelen")
            self._log("HIBA", message)
            QMessageBox.warning(self, "Alkalmazáslista", message)
        if hasattr(self.host, "call_worker"):
            self.host.call_worker(query, done)
        else:
            try: done(query())
            except Exception as exc: failed(str(exc))

    def _apply_filter(self, *_):
        query = self.search.text().strip().casefold()
        kind = self.kind_filter.currentData()
        visible = 0
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for pkg, app_kind, state in self._all_packages:
            if kind == "user" and app_kind != "Felhasználói": continue
            if kind == "system" and app_kind != "Rendszer": continue
            if query and query not in pkg.casefold(): continue
            if self.enabled_only.isChecked() and state != "Engedélyezve": continue
            row = self.table.rowCount(); self.table.insertRow(row)
            for col, value in enumerate((pkg, app_kind, state)):
                item = QTableWidgetItem(value)
                if col == 0: item.setData(Qt.ItemDataRole.UserRole, pkg)
                self.table.setItem(row, col, item)
            visible += 1
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"{visible} alkalmazás megjelenítve · {len(self._all_packages)} összesen")

    def selected_package(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self):
        pkg = self.selected_package()
        self._selected_package = pkg
        self.selected_label.setText(f"Kijelölt csomag: {pkg}" if pkg else "Válassz alkalmazást a listából")
        if pkg:
            self.basic.setPlainText(f"Csomagnév: {pkg}\nKattints az „Adatok lekérdezése” gombra a részletek beolvasásához.")

    def inspect_selected(self):
        pkg = self.selected_package()
        if not pkg:
            QMessageBox.information(self, "Alkalmazásadatok", "Előbb válassz ki egy alkalmazást.")
            return
        if not self._has_device(): return
        self.inspect_btn.setEnabled(False)
        self.basic.setPlainText(f"Adatok lekérdezése: {pkg}…")
        def query():
            raw = self._shell("dumpsys", "package", pkg, timeout=35)
            if not raw or "Unable to find package" in raw:
                raise RuntimeError("A telefon nem talált ilyen csomagot.")
            def first(pattern, default="Nem érhető el"):
                m = re.search(pattern, raw, re.MULTILINE)
                return m.group(1).strip() if m else default
            installer = "Nem érhető el"
            try:
                inst = self._shell("pm", "list", "packages", "-i", pkg)
                m = re.search(r"installer=([^\s]+)", inst)
                if m: installer = m.group(1)
            except Exception: pass
            requested = re.search(r"requested permissions:\s*(.*?)(?:\n\s*install permissions:|\n\s*User \d+:|\Z)", raw, re.S)
            perms = [x.strip() for x in requested.group(1).splitlines() if x.strip().startswith("android.permission.")] if requested else []
            state_match = re.search(r"User 0:.*?\benabled=(\d+)", raw, re.S)
            state = ("Engedélyezve" if state_match.group(1) == "0" else "Letiltva / egyéb állapot") if state_match else "Nem állapítható meg"
            version_name = first(r"^\s*versionName=([^\s]+)")
            version_code = first(r"^\s*versionCode=(\d+)")
            first_install = first(r"^\s*firstInstallTime=(.+)$")
            last_update = first(r"^\s*lastUpdateTime=(.+)$")
            uid = first(r"^\s*userId=(\d+)")
            target_sdk = first(r"^\s*targetSdk=(\d+)")
            min_sdk = first(r"^\s*minSdk=(\d+)")
            basic = "\n".join((
                f"Csomagnév: {pkg}", f"Verziónév: {version_name}",
                f"Verziókód: {version_code}", f"Első telepítés: {first_install}",
                f"Utoljára frissítve: {last_update}", f"Telepítő csomag: {installer}",
                f"UID: {uid}", f"Cél SDK: {target_sdk}",
                f"Minimális SDK: {min_sdk}", f"Állapot: {state}"))
            storage = "A pontos alkalmazás- és adatméretet az Android verziója és jogosultságai befolyásolják.\n\nA csomagadatok nem mindig teszik lehetővé a teljes méret megbízható lekérdezését."
            try:
                resolved = self._shell("cmd", "package", "resolve-activity", "--brief", "-a",
                                       "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", pkg)
            except Exception as exc:
                resolved = f"Az indítási Activity nem kérdezhető le: {exc}"
            components = ("Alapértelmezett indítási Activity:\n" + (resolved or "Nem található indítási Activity.") +
                          "\n\nA további Activity/Service/Receiver komponensek a „További adatok” fül teljes dumpsys kimenetében láthatók.")
            return pkg, basic, "\n".join(perms) if perms else "A kért engedélyek nem voltak kiolvashatók.", storage, components, raw, state
        def done(result):
            pkg, basic, perms, storage, components, raw, state = result
            self.basic.setPlainText(basic)
            self.permissions.setPlainText(perms)
            self.storage.setPlainText(storage)
            self.components.setPlainText(components)
            self.raw.setPlainText(raw)
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == pkg:
                    self.table.item(r, 2).setText(state)
                    break
            self._all_packages = [(p, k, state if p == pkg else s) for p, k, s in self._all_packages]
            self.inspect_btn.setEnabled(True)
            self._log("OK", f"Alkalmazásadatok betöltve: {pkg}")
        def failed(message):
            self.inspect_btn.setEnabled(True)
            self.basic.setPlainText(f"Csomagnév: {pkg}\nLekérdezési hiba: {message}")
            self._log("HIBA", f"Adatlekérdezés ({pkg}): {message}")
        if hasattr(self.host, "call_worker"):
            self.host.call_worker(query, done)
        else:
            try: done(query())
            except Exception as exc: failed(str(exc))

    def _confirm_package(self, title, message):
        pkg = self.selected_package()
        if not pkg:
            QMessageBox.information(self, title, "Előbb válassz ki egy alkalmazást.")
            return None
        if not self._has_device(): return None
        answer = QMessageBox.warning(self, title, message.format(pkg=pkg),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        return pkg if answer == QMessageBox.StandardButton.Yes else None

    def start_selected(self):
        pkg = self.selected_package()
        if not pkg: QMessageBox.information(self, "Indítás", "Előbb válassz ki egy alkalmazást."); return
        if not self._has_device(): return
        try:
            out = self._shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            self._log("OK", f"Indítási kérés elküldve: {pkg}. {out}")
        except Exception as exc:
            self._log("HIBA", f"Indítás ({pkg}): {exc}"); QMessageBox.warning(self, "Indítási hiba", str(exc))

    def choose_activity(self):
        pkg = self.selected_package()
        if not pkg:
            QMessageBox.information(self, "Activity indítása", "Előbb válassz ki egy alkalmazást."); return
        if not self._has_device(): return
        try:
            out = self._shell("cmd", "package", "query-activities", "--brief", "-a",
                                 "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER",
                                 "-p", pkg, timeout=25), ""
            components = []
            for line in out.splitlines():
                line = line.strip()
                if "/" in line and pkg in line and not line.startswith("Querying"):
                    components.append(line.split()[-1])
            components = list(dict.fromkeys(components))
            if not components:
                # Régebbi Android-kiadásokon a query-activities nem minden esetben érhető el.
                resolved = self._shell("cmd", "package", "resolve-activity", "--brief", "-a",
                                       "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", pkg)
                components = [line.strip() for line in resolved.splitlines() if "/" in line and pkg in line]
            if not components:
                QMessageBox.information(self, "Activity indítása", "Nem találtam indítható MAIN/LAUNCHER Activity-t ehhez az alkalmazáshoz.")
                self._log("INFO", f"Nem található indítható Activity: {pkg}")
                return
            choice, ok = __import__("PyQt6.QtWidgets", fromlist=["QInputDialog"]).QInputDialog.getItem(
                self, "Activity kiválasztása", f"Indítandó komponens — {pkg}:", components, 0, False)
            if not ok or not choice: return
            result = self._shell("am", "start", "-n", choice, timeout=25)
            if "Error" in result or "Exception" in result:
                raise RuntimeError(result)
            self._log("OK", f"Activity indítási kérés elküldve: {choice}. {result}")
        except Exception as exc:
            self._log("HIBA", f"Activity lekérdezése/indítása ({pkg}): {exc}")
            QMessageBox.warning(self, "Activity indítása", str(exc))

    def _adb_run(self, *args, timeout=60):
        """Futtat ADB-parancsot a modulban kiválasztott sorozatszámmal; ellenőrzi a valódi eredményt."""
        serial = self._selected_serial()
        adb_path = getattr(self, "_adb_executable", None) or shutil.which("adb") or "/usr/bin/adb"
        if not serial:
            raise RuntimeError("Nincs kiválasztott ADB-eszköz.")
        p = subprocess.run([adb_path, "-s", serial, *map(str, args)], capture_output=True,
                           text=True, timeout=timeout, check=False)
        out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
        if p.returncode:
            raise RuntimeError(err or out or f"ADB exit code: {p.returncode}")
        return out, err

    def _settings(self):
        return QSettings("RedmiControlCenter", "Applications")

    def backup_apk(self):
        # Az APK-fül önálló munkafelület: ne használja észrevétlenül az Alkalmazások fül régi kijelölését.
        pkg = self.selected_package() if self.main_tabs.currentIndex() == 0 else None
        if not self._has_device(): return
        if not pkg:
            try:
                out = self._shell("pm", "list", "packages", timeout=35)
                packages = sorted({line.removeprefix("package:").strip() for line in out.splitlines()
                                   if line.startswith("package:") and line.removeprefix("package:").strip()})
            except Exception as exc:
                QMessageBox.warning(self, "APK mentése", f"Nem sikerült lekérni a telefon alkalmazáslistáját.\n{exc}")
                return
            if not packages:
                QMessageBox.information(self, "APK mentése", "A telefonon nem található menthető alkalmazás.")
                return
            pkg, ok = QInputDialog.getItem(self, "APK mentése", "Válaszd ki a telefonon lévő alkalmazást:", packages, 0, False)
            if not ok or not pkg: return
        settings = self._settings()
        previous_dir = settings.value("apk_directory", str(Path.home() / "APK-mentesek"))
        default = str(Path(previous_dir) / f"{pkg.replace('.', '_')}")
        target, _ = QFileDialog.getSaveFileName(self, "APK mentése PC-re", default + ".apk",
                                                 "Android APK (*.apk);;Minden fájl (*)")
        if not target: return
        try:
            out = self._shell("pm", "path", pkg, timeout=20)
            remote_paths = [line.split(":", 1)[1].strip() for line in out.splitlines()
                            if line.strip().startswith("package:") and ":" in line]
            if not remote_paths:
                raise RuntimeError("A telefon nem adott vissza APK-elérési utat ehhez a csomaghoz.")
            target_path = Path(target)
            settings.setValue("apk_directory", str(target_path.parent))
            self.apk_directory_edit.setCurrentText(str(target_path.parent))
            if len(remote_paths) == 1:
                self._adb_run("pull", remote_paths[0], str(target_path), timeout=120)
                self._log("OK", f"APK mentve: {target_path} ({pkg})")
                self.refresh_apk_archive()
                QMessageBox.information(self, "APK mentése", f"Sikeres mentés:\n{target_path}")
            else:
                # Osztott APK-kat könyvtárba mentünk egyértelmű fájlnevekkel; minden split megmarad.
                folder = target_path.with_suffix("")
                folder.mkdir(parents=True, exist_ok=True)
                for i, remote in enumerate(remote_paths):
                    name = Path(remote).name or f"split_{i}.apk"
                    self._adb_run("pull", remote, str(folder / name), timeout=120)
                meta = folder / "MENTES_INFO.txt"
                meta.write_text(f"Csomag: {pkg}\nAPK-fájlok: {len(remote_paths)}\nForrás: {self._selected_serial()}\n",
                                encoding="utf-8")
                self.apk_directory_edit.setCurrentText(str(folder))
                self._settings().setValue("apk_archive_directory", str(folder))
                self._log("OK", f"Osztott APK mentve: {folder} ({len(remote_paths)} fájl)")
                self.refresh_apk_archive()
                QMessageBox.information(self, "APK mentése", f"Az alkalmazás több APK-ból áll.\nMinden fájl ide került:\n{folder}")
        except Exception as exc:
            self._log("HIBA", f"APK-mentés ({pkg}): {exc}")
            QMessageBox.warning(self, "APK-mentési hiba", str(exc))

    def install_apk(self):
        if not self._has_device(): return
        settings = self._settings()
        start_dir = settings.value("apk_directory", str(Path.home()))
        # Ha az APK-listában van kijelölés, azt használjuk; így a fül saját kijelölése ténylegesen működik.
        selected = []
        if self.main_tabs.currentIndex() == 1:
            rows = self.apk_table.selectionModel().selectedRows()
            selected = [self.apk_table.item(r.row(), 0).data(Qt.ItemDataRole.UserRole)
                        for r in rows if self.apk_table.item(r.row(), 0)]
        if selected:
            files = selected
        else:
            files, _ = QFileDialog.getOpenFileNames(self, "APK-fájl(ok) kiválasztása", start_dir,
                                                    "Android APK (*.apk);;Minden fájl (*)")
        if not files: return
        settings.setValue("apk_directory", str(Path(files[0]).parent))
        try:
            # Telepítés előtt ellenőrizzük az APK-k ZIP-épségét, manifestjét és a minimum API-t,
            # amennyiben aapt/apkanalyzer telepítve van. Split APK-knál a csomagnévnek egyeznie kell.
            metas = [self._apk_metadata(filename) for filename in files]
            known_packages = {m["package"] for m in metas if m.get("package")}
            if len(known_packages) > 1:
                raise RuntimeError("A kiválasztott APK-fájlok különböző alkalmazásokhoz tartoznak: " + ", ".join(sorted(known_packages)))
            sdk = self._shell("getprop", "ro.build.version.sdk", timeout=10).strip()
            for meta in metas:
                if meta.get("min_sdk") and sdk.isdigit() and int(sdk) < int(meta["min_sdk"]):
                    raise RuntimeError(f"Az APK nem kompatibilis ezzel a telefonnal: a telefon API-szintje {sdk}, az APK minimuma {meta['min_sdk']} ({meta['file']}).")
            if len(files) == 1:
                out, err = self._adb_run("install", "-r", files[0], timeout=180)
            else:
                out, err = self._adb_run("install-multiple", "-r", *files, timeout=240)
            result = out or err or "Az ADB parancs hibakód nélkül befejeződött."
            if "Success" not in result and "success" not in result.casefold():
                raise RuntimeError(f"Az ADB nem igazolta a telepítés sikerét:\n{result}")
            self._log("OK", f"Telepítés sikeres: {', '.join(Path(f).name for f in files)} — {result}")
            QMessageBox.information(self, "APK telepítése", f"Sikeres telepítés.\n\n{result}")
            self.refresh_apps()
        except Exception as exc:
            self._log("HIBA", f"APK-telepítés: {exc}")
            QMessageBox.warning(self, "APK-telepítési hiba", str(exc))

    def launch_and_mirror(self):
        pkg = self.selected_package()
        if not pkg:
            QMessageBox.information(self, "Indítás és megjelenítés", "Előbb válassz ki egy alkalmazást."); return
        if not self._has_device(): return
        scrcpy = shutil.which("scrcpy")
        if not scrcpy:
            QMessageBox.warning(self, "Scrcpy nem található", "A scrcpy nem található a PATH-ban. Telepítsd a scrcpy csomagot, majd próbáld újra.")
            self._log("HIBA", "Az indítás és megjelenítés sikertelen: scrcpy nem található.")
            return
        try:
            serial = self._selected_serial()
            # Előbb ébresszük fel a kijelzőt, majd a scrcpy-t indítsuk; a folyamatok
            # külön indulnak, így az alkalmazásindítás nem versenyez a képernyőtükrözés felállásával.
            try:
                self._shell("input", "keyevent", "KEYCODE_WAKEUP", timeout=10)
            except Exception as wake_exc:
                self._log("FIGYELEM", f"A kijelző ébresztése nem sikerült: {wake_exc}")
            # A wm dismiss-keyguard nem minden Android-buildben érhető el, ezért nem blokkolhatja a tükrözést.
            proc = subprocess.Popen([scrcpy, "-s", serial, "--stay-awake"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
            # A tükrözés elindulása után küldjük el az appindítást.
            QTimer.singleShot(1400, lambda p=pkg: self._launch_after_scrcpy(p, proc))
            self._log("INFO", f"Scrcpy indítása ({serial}); képernyő-ébresztés és alkalmazásindítás előkészítve ({pkg}).")
        except Exception as exc:
            self._log("HIBA", f"Indítás és megjelenítés ({pkg}): {exc}")
            QMessageBox.warning(self, "Indítás és megjelenítés", str(exc))

    def _launch_after_scrcpy(self, pkg, proc):
        try:
            if proc.poll() is not None:
                stderr = ""
                try:
                    stderr = (proc.stderr.read() if proc.stderr else "").strip()
                except Exception:
                    pass
                raise RuntimeError("A scrcpy idő előtt kilépett." + (("\n\nRészletek: " + stderr[-2500:]) if stderr else " A hibaüzenet nem olvasható ki."))
            result = self._shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1", timeout=30)
            if "No activities found" in result or "monkey aborted" in result.casefold():
                raise RuntimeError(result or "Nem található indítható Launcher Activity ehhez az alkalmazáshoz.")
            self._log("OK", f"Alkalmazásindítás elküldve ({pkg}); scrcpy fut. {result}")
        except Exception as exc:
            self._log("HIBA", f"Alkalmazásindítás/scrcpy: {exc}")
            QMessageBox.warning(self, "Indítás és megjelenítés", str(exc))

    def _remember_apk_directory(self, directory):
        """Elmenti az aktuális APK-könyvtárat és a legutóbbi könyvtárak előzményeit."""
        directory = str(Path(directory).expanduser())
        settings = self._settings()
        history = settings.value("apk_archive_history", [])
        if isinstance(history, str):
            history = [history]
        history = [str(item) for item in (history or []) if item and str(item) != directory]
        history.insert(0, directory)
        history = history[:12]
        settings.setValue("apk_archive_history", history)
        settings.setValue("apk_archive_directory", directory)
        settings.setValue("apk_directory", directory)  # kompatibilitás a régi beállításkulccsal
        if hasattr(self, "apk_directory_edit"):
            combo = self.apk_directory_edit
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(history)
            combo.setCurrentText(directory)
            combo.blockSignals(False)

    def choose_apk_directory(self):
        current = self.apk_directory_edit.currentText().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "APK-archívum könyvtára", current)
        if directory:
            self.apk_directory_edit.setCurrentText(directory)
            self._remember_apk_directory(directory)
            self.refresh_apk_archive()

    def selected_local_apk(self):
        rows = self.apk_table.selectionModel().selectedRows() if hasattr(self, "apk_table") else []
        if not rows:
            return None
        item = self.apk_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _apk_selection_changed(self):
        filename = self.selected_local_apk()
        if filename:
            self.apk_selected_label.setText(f"Kijelölt APK: {filename}")
        else:
            self.apk_selected_label.setText("Nincs kijelölt helyi APK.")

    def refresh_apk_archive(self):
        """A helyi archívum saját listáját frissíti; nem függ az alkalmazásfül kijelölésétől."""
        if not hasattr(self, "apk_table"):
            return
        directory = Path(self.apk_directory_edit.currentText().strip() or str(Path.home())).expanduser()
        self.apk_directory_edit.setCurrentText(str(directory))
        self._remember_apk_directory(str(directory))
        self.apk_table.setRowCount(0)
        if not directory.is_dir():
            self.apk_status_label.setText(f"A megadott könyvtár nem található: {directory} — válassz létező mappát a „Könyvtár…” gombbal.")
            self.apk_selected_label.setText("Nincs kijelölt helyi APK.")
            self._log("FIGYELEM", f"APK-archívum könyvtára nem található: {directory}")
            return
        try:
            files = sorted(directory.rglob("*.apk"), key=lambda p: str(p).casefold())
            for path in files:
                row = self.apk_table.rowCount(); self.apk_table.insertRow(row)
                name = QTableWidgetItem(path.name); name.setData(Qt.ItemDataRole.UserRole, str(path))
                self.apk_table.setItem(row, 0, name)
                self.apk_table.setItem(row, 1, QTableWidgetItem(f"{path.stat().st_size / 1024 / 1024:.1f} MiB"))
                package, version = "—", "—"
                try:
                    meta = self._apk_metadata(str(path))
                    package = meta.get("package") or "ismeretlen"
                    version = meta.get("version_name") or meta.get("version_code") or "ismeretlen"
                except Exception:
                    package, version = "Nem olvasható", "—"
                self.apk_table.setItem(row, 2, QTableWidgetItem(package))
                self.apk_table.setItem(row, 3, QTableWidgetItem(str(version)))
            if files:
                self.apk_status_label.setText(f"{len(files)} APK-fájl található ebben a könyvtárban és az almappáiban: {directory}")
            else:
                self.apk_status_label.setText(f"A könyvtár létezik, de nem található benne APK-fájl: {directory}")
            self._log("INFO", f"APK-archívum frissítve: {len(files)} APK — {directory}")
        except Exception as exc:
            self.apk_status_label.setText(f"Nem sikerült beolvasni az APK-archívumot: {exc}")
            self._log("HIBA", f"APK-archívum olvasása: {exc}")

    def _remember_state_directory(self, directory):
        """Eltárolja a mentési könyvtárat és a legutóbbi könyvtárak lenyíló előzményeit."""
        directory = str(Path(directory).expanduser())
        settings = self._settings()
        history = settings.value("state_archive_history", [])
        if isinstance(history, str):
            history = [history]
        history = [str(x) for x in (history or []) if x and str(x) != directory]
        history.insert(0, directory)
        history = history[:12]
        settings.setValue("state_archive_history", history)
        settings.setValue("state_archive_directory", directory)
        if hasattr(self, "state_directory_edit"):
            combo = self.state_directory_edit
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(history)
            combo.setCurrentText(current if current else directory)
            combo.blockSignals(False)

    def _state_directory_changed(self, directory):
        directory = (directory or "").strip()
        if not directory:
            return
        self._settings().setValue("state_archive_directory", directory)
        # Ne szkenneljünk minden billentyűleütésre; csak meglévő könyvtár esetén frissítünk.
        if Path(directory).expanduser().is_dir():
            self.refresh_state_archive()

    def choose_state_directory(self):
        current = self.state_directory_edit.currentText().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Állapotmentések könyvtára", current)
        if directory:
            self.state_directory_edit.setCurrentText(directory)
            self._remember_state_directory(directory)
            self.refresh_state_archive()

    def refresh_state_archive(self):
        """Az állapotmentések saját könyvtárát olvassa be, függetlenül az alkalmazáslistától."""
        if not hasattr(self, "state_table"):
            return
        directory = Path(self.state_directory_edit.currentText().strip() or str(Path.home() / "Redmi-állapotmentések")).expanduser()
        self._remember_state_directory(str(directory))
        self.state_table.setRowCount(0)
        if not directory.is_dir():
            self.state_status_label.setText(f"A mentési könyvtár nem található: {directory} — válassz létező mappát, vagy ments ide egy új állapotot.")
            self._log("FIGYELEM", f"Állapotmentési könyvtár nem található: {directory}")
            return
        loaded = 0
        invalid = 0
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                row = self.state_table.rowCount(); self.state_table.insertRow(row)
                name = QTableWidgetItem(path.name); name.setData(Qt.ItemDataRole.UserRole, str(path))
                self.state_table.setItem(row, 0, name)
                self.state_table.setItem(row, 1, QTableWidgetItem(str(data.get("created_at", "ismeretlen dátum"))))
                self.state_table.setItem(row, 2, QTableWidgetItem(str(len(data.get("packages", {})))))
                loaded += 1
            except Exception:
                invalid += 1
        if loaded:
            self.state_status_label.setText(f"{loaded} állapotmentés betöltve: {directory}" + (f"; {invalid} hibás JSON-fájl kihagyva." if invalid else ""))
        elif invalid:
            self.state_status_label.setText(f"A könyvtárban {invalid} JSON-fájl van, de egyik sem olvasható érvényes állapotmentésként: {directory}")
        else:
            self.state_status_label.setText(f"A könyvtár létezik, de még nincs benne állapotmentés (.json): {directory}")

    def _pick_apk(self):
        selected = self.selected_local_apk()
        if selected and Path(selected).is_file():
            return selected
        settings = self._settings()
        start_dir = settings.value("apk_directory", str(Path.home()))
        filename, _ = QFileDialog.getOpenFileName(self, "APK kiválasztása", start_dir,
                                                   "Android APK (*.apk);;Minden fájl (*)")
        if filename:
            settings.setValue("apk_directory", str(Path(filename).parent))
        return filename

    def _apk_metadata(self, filename):
        path = Path(filename)
        if not path.is_file():
            raise RuntimeError("A kiválasztott fájl nem található.")
        if path.suffix.casefold() != ".apk":
            raise RuntimeError("A fájl kiterjesztése nem .apk.")
        if path.stat().st_size < 100:
            raise RuntimeError("A fájl túl kicsi ahhoz, hogy érvényes APK legyen.")
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"Sérült APK-archívum; hibás elem: {bad}")
            names = set(zf.namelist())
            if "AndroidManifest.xml" not in names:
                raise RuntimeError("Az APK-ból hiányzik az AndroidManifest.xml.")
        meta = {"file": str(path), "size": path.stat().st_size, "package": None,
                "version_name": None, "version_code": None, "min_sdk": None, "target_sdk": None,
                "native_arch": sorted({n.split('/')[1] for n in names if n.startswith('lib/') and len(n.split('/')) > 2})}
        tool = shutil.which("apkanalyzer")
        if tool:
            def run_tool(*args):
                r = subprocess.run([tool, *args, str(path)], capture_output=True, text=True, timeout=20)
                return r.stdout.strip() if r.returncode == 0 else ""
            meta["package"] = run_tool("manifest", "application-id") or None
            meta["version_name"] = run_tool("manifest", "version-name") or None
            meta["version_code"] = run_tool("manifest", "version-code") or None
            meta["min_sdk"] = run_tool("manifest", "min-sdk") or None
            meta["target_sdk"] = run_tool("manifest", "target-sdk") or None
        else:
            aapt = shutil.which("aapt") or shutil.which("aapt2")
            if aapt:
                r = subprocess.run([aapt, "dump", "badging", str(path)], capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    textout = r.stdout
                    patterns = {"package": r"^package: name='([^']+)'", "version_code": r"^package:.*?versionCode='([^']+)'",
                                "version_name": r"^package:.*?versionName='([^']*)'", "min_sdk": r"^sdkVersion:'([^']+)'",
                                "target_sdk": r"^targetSdkVersion:'([^']+)'"}
                    for key, pattern in patterns.items():
                        m = re.search(pattern, textout, re.MULTILINE)
                        if m: meta[key] = m.group(1) or None
        return meta

    def validate_local_apk(self):
        filename = self._pick_apk()
        if not filename: return
        try:
            meta = self._apk_metadata(filename)
            lines = [f"Fájl: {meta['file']}", f"Méret: {meta['size']:,} bájt", "ZIP-szerkezet: rendben",
                     f"Csomagnév: {meta['package'] or 'Nem olvasható ki (apkanalyzer/aapt nem található)'}",
                     f"Verziónév: {meta['version_name'] or 'ismeretlen'}", f"Verziókód: {meta['version_code'] or 'ismeretlen'}",
                     f"Minimális Android API: {meta['min_sdk'] or 'ismeretlen'}", f"Cél Android API: {meta['target_sdk'] or 'ismeretlen'}",
                     f"Natív architektúrák: {', '.join(meta['native_arch']) or 'nincs / tisztán Java/Kotlin'}"]
            warning = ""
            if self._has_device():
                try:
                    sdk = self._shell("getprop", "ro.build.version.sdk", timeout=10).strip()
                    if meta["min_sdk"] and sdk.isdigit() and int(sdk) < int(meta["min_sdk"]):
                        warning = f"\n\nINKOMPATIBILIS: a telefon API-szintje {sdk}, az APK legalább {meta['min_sdk']} API-t igényel."
                    if meta["package"]:
                        try:
                            existing = self._shell("dumpsys", "package", meta["package"], timeout=20)
                            sig = re.search(r"signatures=\[([^]]*)\]", existing)
                            if sig: lines.append(f"Telefonos aláírásadat: {sig.group(1)}")
                        except Exception: pass
                except Exception: pass
            lines.append("\nMegjegyzés: a ZIP- és manifest-ellenőrzés nem bizonyítja a digitális aláírás megbízhatóságát.")
            self._log("OK" if not warning else "FIGYELEM", f"APK-ellenőrzés: {filename}; csomag={meta['package']}")
            QMessageBox.information(self, "APK ellenőrzés eredménye", "\n".join(lines) + warning)
        except Exception as exc:
            self._log("HIBA", f"APK-ellenőrzés ({filename}): {exc}")
            QMessageBox.warning(self, "APK-ellenőrzési hiba", str(exc))

    def compare_local_apk(self):
        filename = self._pick_apk()
        if not filename: return
        if not self._has_device(): return
        try:
            meta = self._apk_metadata(filename)
            if not meta["package"]:
                raise RuntimeError("A csomagnév nem olvasható ki. Telepítsd az Android SDK build-tools (aapt) vagy apkanalyzer eszközét, majd próbáld újra.")
            installed = self._shell("dumpsys", "package", meta["package"], timeout=25)
            if "Unable to find package" in installed or not installed:
                QMessageBox.information(self, "APK-verzió összehasonlítása", f"A(z) {meta['package']} csomag nincs telepítve a telefonra.\n\nHelyi APK verzió: {meta['version_name'] or '?'} ({meta['version_code'] or '?'})")
                return
            vm = re.search(r"versionName=([^\s]+)", installed)
            vc = re.search(r"versionCode=(\d+)", installed)
            phone_name = vm.group(1) if vm else "ismeretlen"
            phone_code = vc.group(1) if vc else "ismeretlen"
            local_code = str(meta["version_code"] or "ismeretlen")
            if local_code.isdigit() and phone_code.isdigit():
                relation = "újabb" if int(local_code) > int(phone_code) else "régebbi" if int(local_code) < int(phone_code) else "azonos verziókódú"
            else: relation = "nem dönthető el verziókód alapján"
            text = (f"Csomag: {meta['package']}\nHelyi APK: {meta['version_name'] or '?'} (kód: {local_code})\n"
                    f"Telefon: {phone_name} (kód: {phone_code})\n\nA helyi APK a telefonos verzióhoz képest: {relation}.\n"
                    f"APK mérete: {meta['size']:,} bájt")
            self._log("INFO", f"APK-verzió összehasonlítva: {meta['package']} — helyi {local_code}, telefon {phone_code} ({relation}).")
            QMessageBox.information(self, "APK-verzió összehasonlítása", text)
        except Exception as exc:
            self._log("HIBA", f"APK-verzió összehasonlítása: {exc}")
            QMessageBox.warning(self, "APK-verzió összehasonlítása", str(exc))

    def _collect_device_state(self):
        serial = self._selected_serial()
        packages = self._shell("pm", "list", "packages", timeout=40)
        names = sorted({line.removeprefix("package:").strip() for line in packages.splitlines() if line.startswith("package:")})
        enabled_out = self._shell("pm", "list", "packages", "-e", timeout=35)
        disabled_out = self._shell("pm", "list", "packages", "-d", timeout=35)
        enabled = {line.removeprefix("package:").strip() for line in enabled_out.splitlines() if line.startswith("package:")}
        disabled = {line.removeprefix("package:").strip() for line in disabled_out.splitlines() if line.startswith("package:")}
        user_out = self._shell("pm", "list", "packages", "-3", timeout=35)
        user = {line.removeprefix("package:").strip() for line in user_out.splitlines() if line.startswith("package:")}
        state = {}
        for i, name in enumerate(names, 1):
            try:
                raw = self._shell("dumpsys", "package", name, timeout=12)
                vn = re.search(r"versionName=([^\s]+)", raw)
                vc = re.search(r"versionCode=(\d+)", raw)
                state[name] = {"version_name": vn.group(1) if vn else None,
                               "version_code": vc.group(1) if vc else None,
                               "enabled": name in enabled, "system": name not in user}
            except Exception:
                state[name] = {"version_name": None, "version_code": None,
                               "enabled": name in enabled, "system": name not in user}
        return {"schema": 1, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "serial": serial, "device_model": self._shell("getprop", "ro.product.model", timeout=10),
                "android_version": self._shell("getprop", "ro.build.version.release", timeout=10),
                "android_api": self._shell("getprop", "ro.build.version.sdk", timeout=10), "packages": state}

    def save_device_state(self):
        if not self._has_device(): return
        default_dir = self.state_directory_edit.currentText().strip() or str(Path.home() / "Redmi-állapotmentések")
        try:
            Path(default_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "Állapotmentések könyvtára", f"Nem hozható létre a mentési könyvtár:\n{default_dir}\n\n{exc}")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Telefonállapot mentése", str(Path(default_dir) / f"telefon_allapot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"), "JSON (*.json)")
        if not filename: return
        if not filename.lower().endswith(".json"): filename += ".json"
        try:
            state = self._collect_device_state()
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            Path(filename).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log("OK", f"Telefonállapot mentve: {filename} ({len(state['packages'])} csomag).")
            self.state_directory_edit.setCurrentText(str(Path(filename).parent))
            self._remember_state_directory(str(Path(filename).parent))
            self.refresh_state_archive()
            QMessageBox.information(self, "Telefonállapot mentése", f"Mentés kész.\nCsomagok: {len(state['packages'])}\nFájl: {filename}")
        except Exception as exc:
            self._log("HIBA", f"Telefonállapot mentése: {exc}")
            QMessageBox.warning(self, "Telefonállapot mentése", str(exc))

    def compare_device_states(self):
        selected_rows = self.state_table.selectionModel().selectedRows() if hasattr(self, "state_table") else []
        if len(selected_rows) >= 2:
            selected_paths = [self.state_table.item(r.row(), 0).data(Qt.ItemDataRole.UserRole) for r in selected_rows[:2]]
            first, second = selected_paths[0], selected_paths[1]
        else:
            first, _ = QFileDialog.getOpenFileName(self, "Első állapotmentés kiválasztása", str(Path.home()), "JSON (*.json)")
            if not first: return
            second, _ = QFileDialog.getOpenFileName(self, "Második (újabb) állapotmentés kiválasztása", str(Path(first).parent), "JSON (*.json)")
            if not second: return
        try:
            a = json.loads(Path(first).read_text(encoding="utf-8"))
            b = json.loads(Path(second).read_text(encoding="utf-8"))
            pa, pb = a.get("packages", {}), b.get("packages", {})
            added, removed, changed = [], [], []
            for name in sorted(set(pb) - set(pa)): added.append(name)
            for name in sorted(set(pa) - set(pb)): removed.append(name)
            for name in sorted(set(pa) & set(pb)):
                diffs = []
                for key, label in (("version_name", "verziónév"), ("version_code", "verziókód"), ("enabled", "engedélyezés"), ("system", "rendszer/felhasználói típus")):
                    if pa[name].get(key) != pb[name].get(key): diffs.append(f"{label}: {pa[name].get(key)} → {pb[name].get(key)}")
                if diffs: changed.append(f"{name}: " + "; ".join(diffs))
            lines = [f"Korábbi mentés: {a.get('created_at', first)}", f"Újabb mentés: {b.get('created_at', second)}",
                     f"Készülék: {a.get('device_model', '?')} → {b.get('device_model', '?')}", "",
                     f"Új csomagok ({len(added)}):", *(f"  + {x}" for x in added), "",
                     f"Eltűnt csomagok ({len(removed)}):", *(f"  - {x}" for x in removed), "",
                     f"Megváltozott csomagok ({len(changed)}):", *(f"  * {x}" for x in changed)]
            report = "\n".join(lines)
            dialog = QDialog(self); dialog.setWindowTitle("Telefonállapotok különbsége"); dialog.resize(850, 650)
            layout = QVBoxLayout(dialog); view = QPlainTextEdit(); view.setReadOnly(True); view.setPlainText(report); layout.addWidget(view)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject); buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
            def save_report():
                out, _ = QFileDialog.getSaveFileName(dialog, "Különbségjelentés mentése", str(Path(second).with_suffix("")) + "_kulonbseg.txt", "Szövegfájl (*.txt)")
                if out: Path(out).write_text(report, encoding="utf-8")
            buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(save_report); layout.addWidget(buttons)
            self._log("INFO", f"Állapotmentések összehasonlítva: +{len(added)} új, -{len(removed)} eltűnt, {len(changed)} módosult csomag.")
            dialog.exec()
        except Exception as exc:
            self._log("HIBA", f"Állapotmentések összehasonlítása: {exc}")
            QMessageBox.warning(self, "Állapotmentések összehasonlítása", str(exc))

    def clear_app_data(self):
        pkg = self._confirm_package("Alkalmazásadatok törlése",
            "FIGYELEM: a(z) {pkg} alkalmazás helyi adatai, beállításai és esetleges helyi mentései törlődnek. Ez nem vonható vissza. Folytatod?")
        if not pkg: return
        try:
            result = self._shell("pm", "clear", pkg, timeout=60)
            if "Success" not in result:
                raise RuntimeError(result or "Az Android nem igazolta a törlés sikerét.")
            self._log("OK", f"Alkalmazásadatok törölve: {pkg} — {result}")
            QMessageBox.information(self, "Alkalmazásadatok törlése", f"Sikeres művelet: {result}")
        except Exception as exc:
            self._log("HIBA", f"Adattörlés ({pkg}): {exc}")
            QMessageBox.warning(self, "Adattörlési hiba", str(exc))

    def _show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item:
            self.table.selectRow(item.row())
        pkg = self.selected_package()
        if not pkg: return
        menu = QMenu(self)
        actions = [
            ("Alkalmazás indítása", self.start_selected),
            ("Indítás és megjelenítés (scrcpy)", self.launch_and_mirror),
            ("Indítható Activity kiválasztása", self.choose_activity),
            ("Adatok lekérdezése", self.inspect_selected),
            ("APK mentése PC-re", self.backup_apk),
            ("Alkalmazásadatok törlése…", self.clear_app_data),
            ("Kényszerített leállítás", self.stop_selected),
            ("Eltávolítás…", self.uninstall_selected),
        ]
        for label, callback in actions:
            action = menu.addAction(label)
            action.triggered.connect(callback)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def show_logcat(self):
        pkg = self.selected_package()
        if not pkg:
            QMessageBox.information(self, "Élő hibakeresési napló", "Előbb válassz ki egy alkalmazást."); return
        if not self._has_device(): return
        try:
            pid = self._shell("pidof", pkg, timeout=10).strip()
        except Exception:
            pid = ""
        if not pid:
            QMessageBox.information(self, "Élő hibakeresési napló",
                "Az alkalmazásnak jelenleg nincs futó folyamata. Indítsd el, majd kérd le újra a naplót.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Logcat — {pkg}")
        dialog.resize(900, 550)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(); view.setReadOnly(True); layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        proc = QProcess(dialog)
        proc.setProgram(getattr(self, "_adb_executable", None) or shutil.which("adb") or "/usr/bin/adb")
        proc.setArguments(["-s", self._selected_serial(), "logcat", "--pid", pid])
        proc.readyReadStandardOutput.connect(lambda: view.moveCursor(view.textCursor().MoveOperation.End) if False else view.appendPlainText(bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))
        proc.readyReadStandardError.connect(lambda: view.appendPlainText(bytes(proc.readAllStandardError()).decode("utf-8", "replace").rstrip()))
        dialog.finished.connect(lambda _result: proc.kill() if proc.state() != QProcess.ProcessState.NotRunning else None)
        proc.start()
        dialog.exec()

    def stop_selected(self):
        pkg = self._confirm_package("Kényszerített leállítás", "Leállítod a(z) {pkg} alkalmazást? Ez nem törli az alkalmazást vagy annak adatait. Rendszeralkalmazás leállítása átmeneti hibát okozhat.")
        if not pkg: return
        try:
            self._shell("am", "force-stop", pkg)
            self._log("OK", f"Kényszerített leállítás elküldve: {pkg}")
        except Exception as exc:
            self._log("HIBA", f"Leállítás ({pkg}): {exc}"); QMessageBox.warning(self, "Leállítási hiba", str(exc))

    def uninstall_selected(self):
        pkg = self._confirm_package("Eltávolítás", "Eltávolítod a(z) {pkg} alkalmazást a jelenlegi Android-felhasználótól? A rendszerpartíción lévő APK-t ez nem törli, de az alkalmazás elérhetetlenné válhat.")
        if not pkg: return
        try:
            result = self._shell("pm", "uninstall", "--user", "0", pkg, timeout=35)
            if "success" not in result.casefold():
                raise RuntimeError(result or "Az Android nem igazolta az eltávolítás sikerét.")
            self._log("OK", f"Eltávolítási eredmény ({pkg}): {result}")
            self.refresh_apps()
        except Exception as exc:
            self._log("HIBA", f"Eltávolítás ({pkg}): {exc}"); QMessageBox.warning(self, "Eltávolítási hiba", str(exc))

    def set_enabled(self, enabled):
        action = "engedélyezés" if enabled else "letiltás"
        pkg = self._confirm_package("Alkalmazás " + action, f"Biztosan végrehajtod a(z) {{pkg}} alkalmazáson a(z) {action} műveletet? Rendszeralkalmazás letiltása működési problémát okozhat.")
        if not pkg: return
        try:
            result = self._shell("pm", "enable" if enabled else "disable-user", "--user", "0", pkg, timeout=25)
            if "error" in result.casefold() or "unknown package" in result.casefold():
                raise RuntimeError(result)
            self._log("OK", f"{action.capitalize()} ({pkg}): {result or 'Az ADB parancs sikeresen befejeződött.'}")
            self.inspect_selected()
        except Exception as exc:
            self._log("HIBA", f"{action} ({pkg}): {exc}"); QMessageBox.warning(self, "Alkalmazásművelet", str(exc))

    def open_play_store(self):
        pkg = self.selected_package()
        if not pkg: QMessageBox.information(self, "Play Áruház", "Előbb válassz ki egy alkalmazást."); return
        if not self._has_device(): return
        answer = QMessageBox.question(self, "Play Áruház", f"Megnyitod a telefonon a(z) {pkg} Play Áruház-oldalát?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes: return
        try:
            result = self._shell("am", "start", "-a", "android.intent.action.VIEW", "-d", "market://details?id=" + pkg)
            self._log("OK", f"Play Áruház megnyitása: {pkg}. {result}")
        except Exception as exc:
            self._log("HIBA", f"Play Áruház ({pkg}): {exc}"); QMessageBox.warning(self, "Play Áruház", str(exc))

    def web_search(self, kind="general"):
        pkg = self.selected_package()
        if not pkg: QMessageBox.information(self, "Webes keresés", "Előbb válassz ki egy alkalmazást."); return
        terms = {"website": f'"{pkg}" developer official website', "github": f'"{pkg}" GitHub', "general": f'"{pkg}" Android app developer'}
        url = "https://www.google.com/search?q=" + urllib.parse.quote(terms.get(kind, terms["general"]))
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self._log("INFO", f"Webes keresés megnyitva: {pkg} ({kind})")
        except Exception as exc:
            self._log("HIBA", f"Webes keresés ({pkg}): {exc}"); QMessageBox.warning(self, "Böngésző indítása", str(exc))


if __name__ == "__main__":
    # Önálló felületpróba: a modul főprogramba integrálva a build_tab(self)
    # függvényen keresztül működik. Közvetlen indításkor csak a felületet mutatjuk.
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    class _PreviewHost:
        serial = None

        def log_msg(self, message):
            print(message)

        def require_device(self):
            QMessageBox.information(
                self_window,
                "Önálló felületpróba",
                "Ez a modul önálló előnézeti módban fut. Telefonos műveletekhez a Redmi Control Center főprogramjából kell megnyitni."
            )
            return False

    app = QApplication(sys.argv)
    self_window = QMainWindow()
    self_window.setWindowTitle("Redmi Control Center — Alkalmazások (felületpróba)")
    self_window.resize(1400, 850)
    host = _PreviewHost()
    self_window.setCentralWidget(build_tab(host))
    self_window.show()
    sys.exit(app.exec())
