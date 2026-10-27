#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center — Névjegyek modul

Elhelyezés: modules/contacts.py
A főablakból: from modules.contacts import open_module
                 page = open_module(parent=self, settings=self.settings)
Önálló indítás: python3 modules/contacts.py

Ez az első működő felületi/adatkezelési alap:
- készülékcsatlakozási sor és ADB-eszközfelderítés;
- füles felület, menüsor, forrásszűrő és forrás szerinti telefonszám-színezés;
- helyi JSON-adatállomány, biztonságos mentés és biztonsági másolat;
- JSON/CSV/VCF import-export alapok;
- keresés, forrás szerinti szűrés, másolás, duplikátumjelzés;
- tárcsázó és SMS-szerkesztő, amelyek a telefon saját alkalmazását nyitják meg;
- programbeállítások, szerkeszthető európai országkód-tábla és hangrögzítési backendek;
- ADB-eszközfelderítés és telefonállapot-figyelés háttérszálban;
- importálási előnézet és egymás melletti névjegy-összehasonlítás.

Korlát: az Android hozzáférési szabályai és a scrcpy-verzió meghatározhatják, hogy
a telefon lejátszott hangja vagy mikrofonja rögzíthető-e. A két telefonos forrás
külön választható, nem keverjük őket. A közvetlen SMS-küldés és a teljes Android-
névjegyszinkronizálás továbbra sem része ennek a modulnak.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import signal
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer, QUrl, QThread, pyqtSignal, QEvent
from PyQt6.QtGui import QAction, QKeySequence, QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QLabel, QPushButton, QComboBox, QLineEdit, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QMessageBox,
    QFileDialog, QCheckBox, QGroupBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
    QSplitter, QAbstractItemView, QInputDialog, QDialog, QDialogButtonBox,
    QProgressBar, QFrame, QSizePolicy, QCompleter, QScrollArea, QTextBrowser,
)

APP_NAME = "Redmi Control Center — Névjegyek"
DATA_DIR = Path.home() / ".local" / "share" / "redmi-control-center" / "contacts"
DATA_FILE = DATA_DIR / "contacts.json"
BACKUP_DIR = DATA_DIR / "backups"
RECORDINGS_DIR = DATA_DIR / "recordings"
EXPORT_DIR = DATA_DIR / "exports"
LOG_DIR = DATA_DIR / "logs"
SCHEMA_VERSION = 1

SOURCES = ["SIM-kártya", "Telefon", "Google-fiók", "Egyéb fiók", "Importált fájl"]
SOURCE_COLORS = {
    "SIM-kártya": "#d8f1ed",
    "Telefon": "#dceafa",
    "Google-fiók": "#eee3fb",
    "Egyéb fiók": "#f8edcf",
    "Importált fájl": "#e7e9ed",
    "Ismeretlen": "#e7e9ed",
}


EUROPEAN_COUNTRY_CODES = [
    {"country": "Albánia", "code": "+355"}, {"country": "Andorra", "code": "+376"},
    {"country": "Ausztria", "code": "+43"}, {"country": "Belgium", "code": "+32"},
    {"country": "Belarusz", "code": "+375"}, {"country": "Bosznia-Hercegovina", "code": "+387"},
    {"country": "Bulgária", "code": "+359"}, {"country": "Ciprus", "code": "+357"},
    {"country": "Csehország", "code": "+420"}, {"country": "Dánia", "code": "+45"},
    {"country": "Egyesült Királyság", "code": "+44"}, {"country": "Észak-Macedónia", "code": "+389"},
    {"country": "Észtország", "code": "+372"}, {"country": "Finnország", "code": "+358"},
    {"country": "Franciaország", "code": "+33"}, {"country": "Görögország", "code": "+30"},
    {"country": "Hollandia", "code": "+31"}, {"country": "Horvátország", "code": "+385"},
    {"country": "Írország", "code": "+353"}, {"country": "Izland", "code": "+354"},
    {"country": "Koszovó", "code": "+383"}, {"country": "Lengyelország", "code": "+48"},
    {"country": "Lettország", "code": "+371"}, {"country": "Liechtenstein", "code": "+423"},
    {"country": "Litvánia", "code": "+370"}, {"country": "Luxemburg", "code": "+352"},
    {"country": "Magyarország", "code": "+36"}, {"country": "Málta", "code": "+356"},
    {"country": "Moldova", "code": "+373"}, {"country": "Monaco", "code": "+377"},
    {"country": "Montenegró", "code": "+382"}, {"country": "Németország", "code": "+49"},
    {"country": "Norvégia", "code": "+47"}, {"country": "Olaszország", "code": "+39"},
    {"country": "Oroszország", "code": "+7"}, {"country": "Portugália", "code": "+351"},
    {"country": "Románia", "code": "+40"}, {"country": "San Marino", "code": "+378"},
    {"country": "Spanyolország", "code": "+34"}, {"country": "Svájc", "code": "+41"},
    {"country": "Svédország", "code": "+46"}, {"country": "Szerbia", "code": "+381"},
    {"country": "Szlovákia", "code": "+421"}, {"country": "Szlovénia", "code": "+386"},
    {"country": "Törökország", "code": "+90"}, {"country": "Ukrajna", "code": "+380"},
    {"country": "Vatikán", "code": "+379"},
]

class FunctionWorker(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result_ready.emit(self.function())
        except Exception as exc:
            self.failed.emit(str(exc))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_number(number: str, default_country_code: str = "+36") -> str:
    """Return a comparison form without overwriting the displayed original.

    Only the country code is configurable. Explicit international numbers are
    preserved; 00 is converted to +, and a leading national trunk zero is
    removed before the configured country code is applied. Hungary's 06 prefix
    is handled as a two-digit national prefix. This is normalization, not full
    numbering-plan validation.
    """
    value = (number or "").strip()
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if value.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    cc_digits = re.sub(r"\D", "", default_country_code or "+36")
    if not cc_digits:
        cc_digits = "36"
    if digits.startswith(cc_digits):
        return "+" + digits
    if cc_digits == "36" and digits.startswith("06"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return "+" + cc_digits + digits


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


class ContactsStore:
    """JSON is the primary source of truth. Writes use a temp file + replace."""
    def __init__(self, path: Path = DATA_FILE):
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": None,
            "contacts": [],
            "sources": {},
            "snapshots": [],
            "settings": {},
            "recording_index": [],
            "call_logs": [],
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("contacts", []), list):
                raise ValueError("A JSON gyökéreleme vagy a contacts mező formátuma hibás.")
            parsed.setdefault("schema_version", 1)
            parsed.setdefault("sources", {})
            parsed.setdefault("snapshots", [])
            parsed.setdefault("settings", {})
            parsed.setdefault("recording_index", [])
            parsed.setdefault("call_logs", [])
            self.data = parsed
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"A névjegy-adatállomány nem tölthető be:\n{exc}") from exc

    def save(self, backup: bool = True) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backup and self.path.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = BACKUP_DIR / f"contacts-{stamp}.json"
            try:
                shutil.copy2(self.path, backup_path)
            except OSError:
                # A mentés még folytatható; a fő fájl írását külön ellenőrizzük.
                pass
        self.data["schema_version"] = SCHEMA_VERSION
        self.data["saved_at"] = _now()
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        json.loads(temp.read_text(encoding="utf-8"))  # verify before replace
        temp.replace(self.path)

    def add_contact(self, record: dict[str, Any]) -> dict[str, Any]:
        contact = dict(record)
        contact.setdefault("id", str(uuid.uuid4()))
        contact.setdefault("display_name", "")
        contact.setdefault("phone_numbers", [])
        contact.setdefault("emails", [])
        contact.setdefault("source_type", "Importált fájl")
        contact.setdefault("source_account", "")
        contact.setdefault("source_contact_id", "")
        contact.setdefault("notes", "")
        contact.setdefault("created_at", _now())
        contact["phone_numbers"] = [
            {"number": _safe_text(p.get("number", "")),
             "normalized": _safe_text(p.get("normalized") or normalize_number(_safe_text(p.get("number", "")))),
             "label": _safe_text(p.get("label", ""))}
            if isinstance(p, dict) else {"number": _safe_text(p), "normalized": normalize_number(_safe_text(p)), "label": ""}
            for p in contact["phone_numbers"]
        ]
        self.data["contacts"].append(contact)
        return contact

    def contacts(self) -> list[dict[str, Any]]:
        return self.data.get("contacts", [])


class ContactsWidget(QWidget):
    def __init__(self, parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.settings = dict(settings or {})
        self.store = ContactsStore()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = LOG_DIR / f"contacts-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.log"
        self._log_lines: list[str] = []
        self._dirty = False
        self._visible_records: list[dict[str, Any]] = []
        self._recording_started = None
        self._recording_active = False
        self._recording_process = None
        self._recording_path = None
        self._recording_paused = False
        self._recording_elapsed_before_pause = 0.0
        self._playback_process = None
        self._playback_paused = False
        self._poll_worker = None
        self._device_worker = None
        self._active_call_started = None
        self._active_call_number = ""
        self._last_call_state = "unknown"
        self._seen_call_ids = set()
        self._last_merge_undo = None
        self._dismissed_duplicate_keys = set(self.store.data.get("settings", {}).get("dismissed_duplicate_keys", []))
        self._build_ui()
        self._build_menus()
        self._update_recording_settings_visibility()
        self.refresh_audio_sources()
        self.refresh_recording_history()
        self._apply_theme_hint()
        self.refresh_contacts()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self._poll_phone_state)
        self._refresh_timer.start()
        self._recording_clock_timer = QTimer(self)
        self._recording_clock_timer.setInterval(500)
        self._recording_clock_timer.timeout.connect(self._refresh_recording_clock)
        self._recording_clock_timer.start()
        self.refresh_devices()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Device row, following the existing module convention.
        connection = QFrame(self)
        connection.setObjectName("contactsConnectionRow")
        connection.setFrameShape(QFrame.Shape.StyledPanel)
        c = QHBoxLayout(connection)
        c.setContentsMargins(10, 7, 10, 7)
        c.addWidget(QLabel("Készülék:"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(230)
        c.addWidget(self.device_combo, 1)
        self.connection_status = QLabel("Kapcsolat ellenőrzése…")
        c.addWidget(self.connection_status)
        self.connect_button = QPushButton("Kapcsolat ellenőrzése")
        self.connect_button.clicked.connect(self.refresh_devices)
        c.addWidget(self.connect_button)
        self.device_refresh_button = QPushButton("Frissítés")
        self.device_refresh_button.clicked.connect(self.refresh_devices)
        c.addWidget(self.device_refresh_button)
        root.addWidget(connection)

        # Compact one-line recorder/player: record, pause/resume, playback,
        # latest-file history and explicit LED-like status.
        audio_frame = QFrame(self)
        audio_frame.setObjectName("audioTransport")
        audio = QHBoxLayout(audio_frame)
        audio.setContentsMargins(7, 4, 7, 4)
        audio.setSpacing(5)
        self.record_button = QPushButton("● Felvétel")
        self.record_button.setObjectName("recordButton")
        self.record_button.setToolTip("Új felvétel indítása vagy a futó felvétel leállítása")
        self.record_button.clicked.connect(self.toggle_recording)
        self.pause_button = QPushButton("Ⅱ Szünet")
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Felvétel vagy lejátszás szüneteltetése; szüneteltetéskor ismét megnyomva folytatás. A felvételt ez a gomb nem állítja le.")
        self.pause_button.clicked.connect(self.toggle_audio_pause)
        self.play_button = QPushButton("▶ Lejátszás")
        self.play_button.setObjectName("playButton")
        self.play_button.clicked.connect(self.toggle_playback)
        self.latest_recording_label = QLabel("Legújabb felvétel:")
        self.latest_recording_label.setMinimumWidth(115)
        self.recording_history_combo = QComboBox()
        self.recording_history_combo.setObjectName("recordingHistory")
        self.recording_history_combo.setMinimumWidth(180)
        self.recording_history_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.recording_history_combo.setMinimumContentsLength(20)
        self.recording_history_combo.currentIndexChanged.connect(self._recording_history_changed)
        self.delete_recording_button = QPushButton("🗑")
        self.delete_recording_button.setObjectName("deleteRecordingButton")
        self.delete_recording_button.setToolTip("A kiválasztott felvétel áthelyezése a rendszer Kukájába")
        self.delete_recording_button.setAccessibleName("Felvétel áthelyezése a Kukába")
        self.delete_recording_button.clicked.connect(self.move_recording_to_trash)
        self.delete_recording_button.setEnabled(False)
        self.audio_led = QLabel("● Készenlét")
        self.audio_led.setObjectName("audioLedIdle")
        self.audio_led.setMinimumWidth(145)
        self.audio_led.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        audio.addWidget(self.record_button)
        audio.addWidget(self.pause_button)
        audio.addWidget(self.play_button)
        # Status/LED is deliberately adjacent to Play, so recording state is
        # visible without scanning past the history selector.
        audio.addWidget(self.audio_led)
        audio.addWidget(self.latest_recording_label)
        audio.addWidget(self.recording_history_combo, 1)
        audio.addWidget(self.delete_recording_button)
        root.addWidget(audio_frame)
        self.audio_status = self.audio_led

        self.tabs = QTabWidget()
        self._build_contacts_tab()
        self._build_dialer_tab()
        self._build_sms_tab()
        self._build_call_log_tab()
        self._build_compare_tab()
        self._build_export_tab()
        self._build_settings_tab()
        self._build_log_tab()

        # Resizable right-hand dialer panel. The original Dialer tab stays in
        # place so existing tab indices and workflows are not changed.
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self.tabs)
        self.docked_dialer = self._build_docked_dialer()
        self.content_splitter.addWidget(self.docked_dialer)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setSizes([850, 280])
        root.addWidget(self.content_splitter, 1)
        self.tabs.currentChanged.connect(self._update_docked_dialer_visibility)
        self._init_docked_dialer_preferences()

        self.status = QLabel("Készen áll.")
        self.status.setObjectName("contactsStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status)

    def _build_menus(self):
        """Build action menus; tab navigation is intentionally left to the tab bar."""
        menu_row = self.layout().itemAt(1)
        menu_bar = __import__("PyQt6.QtWidgets", fromlist=["QMenuBar"]).QMenuBar(self)
        self.layout().insertWidget(1, menu_bar)
        self.menu_bar = menu_bar

        file_menu = menu_bar.addMenu("Fájl")
        self._action(file_menu, "JSON betöltése…", self.load_json_as, QKeySequence.StandardKey.Open)
        self._action(file_menu, "JSON mentése", self.save_json, QKeySequence.StandardKey.Save)
        file_menu.addSeparator()
        self._action(file_menu, "Importálás CSV/VCF/JSON fájlból…", self.import_file)
        self._action(file_menu, "Exportálás…", self.export_file)
        file_menu.addSeparator()
        self._action(file_menu, "Biztonsági mentés most", self.backup_now)
        file_menu.addSeparator()
        self._action(file_menu, "Kilépés a névjegykezelőből", self._request_close, QKeySequence.StandardKey.Close)

        edit_menu = menu_bar.addMenu("Szerkesztés")
        self._action(edit_menu, "Telefonszám másolása", self.copy_number, QKeySequence.StandardKey.Copy)
        self._action(edit_menu, "Névjegy másolása", self.copy_contact)
        self._action(edit_menu, "Összes kijelölése", self.select_all_contacts, QKeySequence.StandardKey.SelectAll)
        self._action(edit_menu, "Kijelölés megszüntetése", self.clear_selection)
        edit_menu.addSeparator()
        self._action(edit_menu, "Kijelölt helyi rekordok törlése…", self.delete_local_contacts, QKeySequence.StandardKey.Delete)

        # Navigation is handled by the visible tabs. These device actions are
        # commands, so they remain available regardless of the active tab.
        device_menu = menu_bar.addMenu("Eszköz")
        self._action(device_menu, "Eszközlista frissítése", self.refresh_devices)
        self._action(device_menu, "Tárcsázó megnyitása a telefonon", self.launch_phone_dialer)
        self._action(device_menu, "SMS-írás megnyitása a telefonon", self.launch_phone_sms)

        help_menu = menu_bar.addMenu("Súgó")
        self._action(help_menu, "Használati útmutató", self.show_help)
        self._action(help_menu, "A modul állapota és korlátai", self.show_module_status)

    def _action(self, menu, text, callback, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False, cb=callback: cb())
        menu.addAction(action)
        return action

    def _build_contacts_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Keresés név, telefonszám vagy e-mail alapján…")
        self.search_edit.textChanged.connect(self.refresh_contacts)
        search_row.addWidget(self.search_edit, 1)
        self.source_filter_button = QPushButton("Források: összes  ▾")
        self.source_filter_button.setToolTip("Válaszd ki, mely források névjegyei jelenjenek meg.")
        self.source_filter_button.clicked.connect(self.show_source_filter)
        search_row.addWidget(self.source_filter_button)
        sync_row = QHBoxLayout()
        read_phone = QPushButton("Telefon névjegyeinek beolvasása")
        read_phone.clicked.connect(self.import_phone_contacts)
        sync_row.addWidget(read_phone)
        sync_note = QLabel("A beolvasás csak hozzáad; helyi rekordot nem töröl automatikusan.")
        sync_note.setWordWrap(True)
        sync_row.addWidget(sync_note, 1)

        contact_actions = QHBoxLayout()
        add_contact = QPushButton("+ Új névjegy")
        add_contact.clicked.connect(self.add_contact_dialog)
        contact_actions.addWidget(add_contact)
        sms_selected = QPushButton("SMS a kijelöltnek")
        sms_selected.clicked.connect(self.prepare_sms_from_selection)
        contact_actions.addWidget(sms_selected)
        contact_actions.addStretch(1)
        layout.addLayout(search_row)
        layout.addLayout(sync_row)
        layout.addLayout(contact_actions)

        self.source_filter = {name: True for name in SOURCES}
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Név", "Telefonszám", "Forrás", "E-mail"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.contact_context_menu)
        self.table.itemDoubleClicked.connect(lambda *_: self.prepare_sms_from_selection())
        layout.addWidget(self.table, 1)
        self.tabs.addTab(page, "Névjegyek")

    def _build_dialer_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        left = QVBoxLayout()
        left.addWidget(QLabel("Telefonszám"))
        self.dial_number = QLineEdit()
        self.dial_number.setPlaceholderText("Szám beírása vagy névjegyből átvétel")
        self.dial_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dial_number.setMinimumHeight(48)
        self.dial_number.setStyleSheet("font-size: 20px; padding: 8px;")
        self._attach_contact_completer(self.dial_number)
        left.addWidget(self.dial_number)
        grid = QGridLayout()
        self._main_dialer_buttons = []
        for i, digit in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]):
            button = QPushButton(digit)
            button.setMinimumSize(72, 58)
            button.setStyleSheet("font-size: 20px; font-weight: 600;")
            button.setProperty("dialerAction", digit)
            button.installEventFilter(self)
            button.clicked.connect(lambda checked=False, d=digit: self.dial_number.insert(d))
            self._main_dialer_buttons.append(button)
            grid.addWidget(button, i // 3, i % 3)
        left.addLayout(grid)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        edit_actions = QHBoxLayout()
        backspace = QPushButton("⌫")
        backspace.setToolTip("Utolsó karakter törlése")
        backspace.setProperty("dialerAction", "backspace")
        backspace.installEventFilter(self)
        backspace.clicked.connect(self.dial_number.backspace)
        self._main_dialer_buttons.append(backspace)
        clear = QPushButton("Mező törlése")
        clear.setProperty("dialerAction", "clear")
        clear.installEventFilter(self)
        clear.clicked.connect(self.dial_number.clear)
        self._main_dialer_buttons.append(clear)
        call = QPushButton("☎  Hívás előkészítése")
        call.setObjectName("phoneActionButton")
        call.setToolTip("A szám átadása a telefon tárcsázójának; a hívást a telefonon indítod.")
        call.clicked.connect(self.dial_selected_number)
        call_scrcpy = QPushButton("▣  Hívás a scrcpy-ben")
        call_scrcpy.setObjectName("phoneActionButton")
        call_scrcpy.setToolTip("Előtérbe hozza vagy elindítja a scrcpy-ablakot, majd megnyitja benne a telefon tárcsázóját.")
        call_scrcpy.clicked.connect(self.dial_selected_number_scrcpy)
        edit_actions.addWidget(backspace)
        edit_actions.addWidget(clear)
        actions.addLayout(edit_actions)
        call.setMinimumHeight(40)
        call_scrcpy.setMinimumHeight(40)
        actions.addWidget(call)
        actions.addWidget(call_scrcpy)
        left.addLayout(actions)
        left.addStretch(1)
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        right.addWidget(QLabel("Használat"))
        note = QLabel(
            "A számlap egérrel és billentyűzettel használható. A Hívás előkészítése "
            "a telefon saját tárcsázóját nyitja meg a megadott számmal; nem indít "
            "észrevétlenül automatikus hívást."
        )
        note.setWordWrap(True)
        right.addWidget(note)
        self.dial_status = QLabel("Nincs előkészített hívás.")
        self.dial_status.setWordWrap(True)
        right.addWidget(self.dial_status)
        right.addStretch(1)
        layout.addLayout(right, 1)
        self.tabs.addTab(page, "Tárcsázó")

    def _build_docked_dialer(self):
        panel = QFrame(self)
        panel.setObjectName("dockedDialerPanel")
        panel.setMinimumWidth(205)
        panel.setMaximumWidth(520)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        title = QLabel("Tárcsázó")
        title.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(title)
        self.docked_dial_number = QLineEdit()
        self.docked_dial_number.setPlaceholderText("Telefonszám")
        self.docked_dial_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docked_dial_number.setMinimumHeight(36)
        self.docked_dial_number.setStyleSheet("font-size: 16px; padding: 5px;")
        self._attach_contact_completer(self.docked_dial_number)
        # Élő névjegy-azonosítás a kis tárcsázóban, az SMS címzettmezőjéhez hasonlóan.
        self.docked_dial_identity = QLabel("A beírt számhoz tartozó név itt jelenik meg.")
        self.docked_dial_identity.setWordWrap(True)
        self.docked_dial_identity.setStyleSheet("color: #8bbfe8; padding-left: 4px;")
        self.docked_dial_number.textChanged.connect(self.update_docked_dial_identity)
        # Keep both number fields synchronized without changing call behavior.
        self.docked_dial_number.textChanged.connect(self._sync_docked_number_to_main)
        self.dial_number.textChanged.connect(self._sync_main_number_to_docked)
        layout.addWidget(self.docked_dial_number)
        layout.addWidget(self.docked_dial_identity)
        grid = QGridLayout()
        grid.setSpacing(4)
        self._docked_dialer_buttons = []
        for i, digit in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]):
            button = QPushButton(digit)
            button.setMinimumHeight(39)
            button.setStyleSheet("font-size: 17px; font-weight: 600;")
            button.setProperty("dialerAction", digit)
            button.installEventFilter(self)
            button.clicked.connect(lambda checked=False, d=digit: self.docked_dial_number.insert(d))
            self._docked_dialer_buttons.append(button)
            grid.addWidget(button, i // 3, i % 3)
        layout.addLayout(grid)
        row = QHBoxLayout()
        backspace = QPushButton("⌫")
        backspace.setToolTip("Utolsó karakter törlése")
        backspace.setProperty("dialerAction", "backspace")
        backspace.installEventFilter(self)
        backspace.clicked.connect(self.docked_dial_number.backspace)
        clear = QPushButton("Törlés")
        clear.setProperty("dialerAction", "clear")
        clear.installEventFilter(self)
        clear.clicked.connect(self.docked_dial_number.clear)
        self._docked_dialer_buttons.extend([backspace, clear])
        row.addWidget(backspace)
        row.addWidget(clear)
        layout.addLayout(row)
        call_row = QVBoxLayout()
        call_row.setSpacing(6)
        call = QPushButton("☎  Hívás előkészítése")
        call.setObjectName("phoneActionButton")
        call.setMinimumHeight(38)
        call.setToolTip("A szám átadása a telefon tárcsázójának.")
        call.clicked.connect(self.dial_selected_number)
        call_scrcpy = QPushButton("▣  Hívás a scrcpy-ben")
        call_scrcpy.setObjectName("phoneActionButton")
        call_scrcpy.setMinimumHeight(38)
        call_scrcpy.setToolTip("A scrcpy-ablak fókuszálása vagy elindítása, majd a szám átadása.")
        call_scrcpy.clicked.connect(self.dial_selected_number_scrcpy)
        call.setMinimumHeight(38)
        call_scrcpy.setMinimumHeight(38)
        call.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        call_scrcpy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        call_row.addWidget(call)
        call_row.addWidget(call_scrcpy)
        layout.addLayout(call_row)
        self.docked_dial_status = QLabel("A telefon saját tárcsázóját nyitja meg.")
        self.docked_dial_status.setWordWrap(True)
        self.dial_status.setWordWrap(True)
        layout.addWidget(self.docked_dial_status)
        layout.addStretch(1)
        return panel

    def _sync_docked_number_to_main(self, text):
        if self.dial_number.text() != text:
            self.dial_number.setText(text)

    def _sync_main_number_to_docked(self, text):
        if self.docked_dial_number.text() != text:
            self.docked_dial_number.setText(text)

    def _init_docked_dialer_preferences(self):
        saved = self.store.data.get("settings", {}).get("docked_dialer_tabs", {})
        tab_names = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        # The original full-size Dialer tab never gets a second, docked dialer.
        self.docked_dialer_tab_checks = {}
        for name in tab_names:
            self.docked_dialer_tab_checks[name] = (
                False if name in ("Hívásnapló", "Tárcsázó")
                else bool(saved.get(name, True))
            )
        if hasattr(self, "docked_dialer_checkboxes"):
            for name, check in self.docked_dialer_checkboxes.items():
                check.blockSignals(True)
                check.setChecked(self.docked_dialer_tab_checks.get(name, name not in ("Hívásnapló", "Tárcsázó")))
                check.blockSignals(False)
        self._update_docked_dialer_visibility()

    def _update_docked_dialer_visibility(self, *_):
        if not hasattr(self, "docked_dialer") or not hasattr(self, "docked_dialer_tab_checks"):
            return
        tab_name = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.currentIndex() >= 0 else ""
        enabled = (
            tab_name not in ("Hívásnapló", "Tárcsázó")
            and self.docked_dialer_tab_checks.get(tab_name, True)
        )
        self.docked_dialer.setVisible(enabled)

    def _build_sms_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        form = QFormLayout()
        self.sms_recipient = QLineEdit()
        self.sms_recipient.setPlaceholderText("Kezdj el nevet vagy telefonszámot írni…")
        self._attach_contact_completer(self.sms_recipient)
        form.addRow("Címzett:", self.sms_recipient)
        self.sms_recipient_identity = QLabel("A számhoz tartozó név itt jelenik meg.")
        self.sms_recipient_identity.setWordWrap(True)
        self.sms_recipient_identity.setStyleSheet("color: #8bbfe8; padding-left: 4px;")
        form.addRow("Azonosítás:", self.sms_recipient_identity)
        self.sms_recipient.textChanged.connect(self.update_sms_recipient_identity)
        layout.addLayout(form)
        layout.addWidget(QLabel("Üzenet szövege:"))
        self.sms_body = QPlainTextEdit()
        self.sms_body.setPlaceholderText("Írd meg az SMS-t…")
        self.sms_body.setMinimumHeight(180)
        layout.addWidget(self.sms_body, 1)
        row = QVBoxLayout()
        sms_note = QLabel("Az üzenet a telefon saját SMS-alkalmazásában ellenőrizhető és küldhető el.")
        sms_note.setWordWrap(True)
        row.addWidget(sms_note)
        self.sms_send_button = QPushButton("▤  SMS átadása a telefonnak")
        self.sms_send_button.setObjectName("phoneActionButton")
        self.sms_send_button.setToolTip("Megnyitja a telefon SMS-alkalmazását; a küldést a telefonon hagyod jóvá.")
        self.sms_send_button.clicked.connect(self.send_sms_via_phone)
        self.sms_scrcpy_button = QPushButton("▣  SMS a scrcpy-ben")
        self.sms_scrcpy_button.setObjectName("phoneActionButton")
        self.sms_scrcpy_button.setToolTip("Előtérbe hozza vagy elindítja a scrcpy-ablakot, majd átadja az SMS-piszkozatot.")
        self.sms_scrcpy_button.clicked.connect(self.send_sms_via_scrcpy)
        self.sms_send_button.setMinimumHeight(40)
        self.sms_scrcpy_button.setMinimumHeight(40)
        row.addWidget(self.sms_send_button)
        row.addWidget(self.sms_scrcpy_button)
        layout.addLayout(row)
        self.sms_status = QLabel("Az üzenet még nincs átadva a telefonnak.")
        layout.addWidget(self.sms_status)
        self.tabs.addTab(page, "SMS")

    def _attach_contact_completer(self, edit: QLineEdit):
        """Név- és telefonszám-javaslat; a popup nem kezelhet tárcsázógomb-kattintásokat."""
        completer = QCompleter([], edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(8)
        edit.setCompleter(completer)
        edit.setProperty("contactSuggestionMap", {})
        completer.activated.connect(lambda text, field=edit: self._choose_contact_suggestion(field, text))
        popup = completer.popup()
        popup.installEventFilter(self)
        popup.setObjectName("contactHistoryPopup")
        popup.viewport().installEventFilter(self)
        popup.viewport().setObjectName("contactHistoryPopupViewport")
        # Store the exact owner on each popup. There are three independent
        # completers (large dialer, docked dialer, SMS recipient); never infer
        # the owner from whichever field happens to be active.
        popup.setProperty("contactHistoryOwner", edit.objectName() or (
            "sms_recipient" if edit is getattr(self, "sms_recipient", None) else
            "docked_dial_number" if edit is getattr(self, "docked_dial_number", None) else
            "dial_number" if edit is getattr(self, "dial_number", None) else "other"
        ))
        self._refresh_contact_suggestions_for(edit)
        if edit is getattr(self, "sms_recipient", None):
            self.update_sms_recipient_identity(edit.text())

    def eventFilter(self, watched, event):
        """Keep each history popup attached to its owner and preserve dial-pad clicks."""
        if watched is not None and watched.objectName() in ("contactHistoryPopup", "contactHistoryPopupViewport"):
            popup = watched if watched.objectName() == "contactHistoryPopup" else watched.parentWidget()
            if event.type() == QEvent.Type.Show and watched.objectName() == "contactHistoryPopup":
                owner = popup.property("contactHistoryOwner")
                if owner in ("dial_number", "docked_dial_number"):
                    # One deferred placement only. Multiple timers caused the
                    # popup to jump between Qt's default position and our position.
                    QTimer.singleShot(0, lambda popup=watched: self._position_popup_widget_above(popup))
            elif event.type() == QEvent.Type.MouseButtonPress:
                owner = popup.property("contactHistoryOwner")
                if owner in ("dial_number", "docked_dial_number"):
                    edit = getattr(self, owner, None)
                    if edit is not None:
                        try:
                            point = event.globalPosition().toPoint()
                        except AttributeError:
                            point = event.globalPos()
                        buttons = (getattr(self, "_docked_dialer_buttons", [])
                                   if owner == "docked_dial_number" else
                                   getattr(self, "_main_dialer_buttons", []))
                        for button in buttons:
                            if button.isVisible():
                                top_left = button.mapToGlobal(button.rect().topLeft())
                                rect = button.rect().translated(top_left)
                                if rect.contains(point):
                                    # Qt routed the physical click to the popup
                                    # because it overlapped a button. Close the
                                    # popup and execute exactly that button action.
                                    action = button.property("dialerAction")
                                    popup.hide()
                                    if action == "backspace":
                                        edit.backspace()
                                    elif action == "clear":
                                        edit.clear()
                                    elif action:
                                        edit.insert(str(action))
                                    return True
        return super().eventFilter(watched, event)

    def _position_popup_widget_above(self, popup):
        """Position only the popup belonging to a dialer, above its own field."""
        owner = popup.property("contactHistoryOwner")
        if owner not in ("dial_number", "docked_dial_number"):
            return
        edit = getattr(self, owner, None)
        if edit is not None and edit.completer() is not None and edit.completer().popup() is popup:
            self._position_contact_popup_above(edit)

    def _position_contact_popup_above(self, edit: QLineEdit):
        """A QCompleter előzménylistáját a mező fölé pozicionálja.

        A Qt alapértelmezett popup-elhelyezése a mező alá nyitja a listát,
        ami a tárcsázó gombjaira rátakar. A popup csak akkor mozog, ha látható.
        Ha a mező fölött kevés a hely, a lista magasságát a rendelkezésre álló
        területhez igazítjuk; a lista nem nyúlhat rá a beviteli mezőre.
        """
        completer = edit.completer()
        if completer is None:
            return
        popup = completer.popup()
        if popup is None or not popup.isVisible() or not edit.isVisible():
            return

        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QGuiApplication

        field_top_left = edit.mapToGlobal(QPoint(0, 0))
        screen = QGuiApplication.screenAt(field_top_left)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        rows = max(1, min(completer.maxVisibleItems(), completer.completionCount()))
        row_height = popup.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 24
        desired_height = row_height * rows + 4
        space_above = max(0, field_top_left.y() - available.top() - 4)
        popup_height = min(desired_height, space_above)
        # Ha a mező fölött nincs elég hely, ne takarja el a mezőt vagy a
        # tárcsázó gombjait: ilyenkor a javaslatlista nem jelenik meg.
        if popup_height < row_height:
            popup.hide()
            return
        popup.setMinimumHeight(0)
        popup.resize(max(edit.width(), popup.sizeHint().width()), popup_height)
        y = field_top_left.y() - popup.height() - 2
        x = field_top_left.x()
        max_x = available.right() - popup.width() + 1
        x = min(max(available.left(), x), max(available.left(), max_x))
        popup.move(x, y)

    def update_sms_recipient_identity(self, number: str = ""):
        """Identify the SMS recipient from the local contact store."""
        label = getattr(self, "sms_recipient_identity", None)
        if label is None:
            return
        number = (number or "").strip()
        normalized = self.normalize_phone(number)
        if not normalized:
            label.setText("Írd be a számot: a névjegy-azonosítás automatikusan frissül.")
            label.setStyleSheet("color: #8bbfe8; padding-left: 4px;")
            return
        matches = []
        for contact in self.store.contacts():
            for phone in contact.get("phone_numbers", []):
                candidate = phone.get("number", "") if isinstance(phone, dict) else str(phone)
                if self.normalize_phone(candidate) == normalized:
                    matches.append(contact)
                    break
        # Deduplicate identical contact records by name/source.
        unique = {}
        for c in matches:
            key = (_safe_text(c.get("display_name")), _safe_text(c.get("source_type")), _safe_text(c.get("source_account")))
            unique[key] = c
        matches = list(unique.values())
        if not matches:
            label.setText("Ismeretlen szám — nem található a helyi névjegyek között. Új névjegy a Műveletek menüből hozható létre.")
            label.setStyleSheet("color: #e0ad55; padding-left: 4px;")
        elif len(matches) == 1:
            c = matches[0]
            source = _safe_text(c.get("source_type", "Ismeretlen"))
            account = _safe_text(c.get("source_account", ""))
            extra = f" · {account}" if account else ""
            label.setText(f"Névjegy: {_safe_text(c.get('display_name'))} · Forrás: {source}{extra}")
            label.setStyleSheet("color: #79d6a2; padding-left: 4px;")
        else:
            names = "; ".join(f"{_safe_text(c.get('display_name'))} ({_safe_text(c.get('source_type', 'Ismeretlen'))})" for c in matches[:4])
            label.setText(f"Több találat ({len(matches)}): {names}. Ellenőrizd a címzettet.")
            label.setStyleSheet("color: #e0ad55; padding-left: 4px;")

    def update_docked_dial_identity(self, number: str = ""):
        """Élő telefonszám-felismerés a jobb oldali kis tárcsázó alatt."""
        label = getattr(self, "docked_dial_identity", None)
        if label is None:
            return
        number = (number or "").strip()
        normalized = self.normalize_phone(number)
        if not normalized:
            label.setText("A beírt számhoz tartozó név itt jelenik meg.")
            label.setStyleSheet("color: #8bbfe8; padding-left: 4px;")
            return

        matches = []
        for contact in self.store.contacts():
            for phone in contact.get("phone_numbers", []):
                candidate = phone.get("number", "") if isinstance(phone, dict) else str(phone)
                if self.normalize_phone(candidate) == normalized:
                    matches.append(contact)
                    break

        unique = {}
        for contact in matches:
            key = (
                _safe_text(contact.get("display_name")),
                _safe_text(contact.get("source_type")),
                _safe_text(contact.get("source_account")),
            )
            unique[key] = contact
        matches = list(unique.values())

        if not matches:
            label.setText("Ismeretlen szám — nincs ilyen szám a helyi névjegyek között.")
            label.setStyleSheet("color: #e0ad55; padding-left: 4px;")
        elif len(matches) == 1:
            contact = matches[0]
            source = _safe_text(contact.get("source_type", "Ismeretlen"))
            account = _safe_text(contact.get("source_account", ""))
            suffix = f" · {account}" if account else ""
            label.setText(
                f"Névjegy: {_safe_text(contact.get('display_name'))} · "
                f"Forrás: {source}{suffix}"
            )
            label.setStyleSheet("color: #79d6a2; padding-left: 4px;")
        else:
            names = "; ".join(
                f"{_safe_text(c.get('display_name'))} "
                f"({_safe_text(c.get('source_type', 'Ismeretlen'))})"
                for c in matches[:4]
            )
            label.setText(f"Több névjegytalálat ({len(matches)}): {names}.")
            label.setStyleSheet("color: #e0ad55; padding-left: 4px;")

    def _refresh_contact_suggestions_for(self, edit: QLineEdit):
        completer = edit.completer()
        if completer is None:
            return
        # A lenyíló listában KIZÁRÓLAG telefonszám szerepeljen.
        # Így a név soha nem kerülhet véletlenül az SMS címzettmezőjébe.
        suggestions = []
        lookup = {}
        for contact in self.store.contacts():
            phones = contact.get("phone_numbers", [])
            if not isinstance(phones, list):
                continue
            for phone in phones:
                number = _safe_text(phone.get("number", "") if isinstance(phone, dict) else phone).strip()
                if not number:
                    continue
                # Normalizáljuk a mezőben megjelenő értéket: szám, pluszjel,
                # és mahdolliset international dialing characters only.
                number = re.sub(r"[^0-9+*#;,]", "", number)
                if number and number not in lookup:
                    suggestions.append(number)
                    lookup[number] = number
        edit.setProperty("contactSuggestionMap", lookup)
        completer.model().setStringList(suggestions)

    def _refresh_contact_suggestions(self):
        for field_name in ("dial_number", "docked_dial_number", "sms_recipient"):
            edit = getattr(self, field_name, None)
            if edit is not None:
                self._refresh_contact_suggestions_for(edit)
        if hasattr(self, "docked_dial_number"):
            self.update_docked_dial_identity(self.docked_dial_number.text())

    def _choose_contact_suggestion(self, edit: QLineEdit, label: str):
        lookup = edit.property("contactSuggestionMap") or {}
        value = lookup.get(label, label)
        completer = edit.completer()
        if completer is not None and completer.popup() is not None:
            completer.popup().hide()
        edit.setText(value)
        edit.setCursorPosition(len(value))

    def _build_call_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        self.call_filter = QComboBox()
        self.call_filter.addItems(["Összes hívás", "Kimenő", "Fogadott", "Nem fogadott", "Elutasított"])
        self.call_filter.currentIndexChanged.connect(self.refresh_call_log)
        toolbar.addWidget(QLabel("Naplószűrő:"))
        toolbar.addWidget(self.call_filter)
        self.call_search = QLineEdit()
        self.call_search.setPlaceholderText("Keresés név, szám vagy jegyzet alapján…")
        self.call_search.textChanged.connect(self.refresh_call_log)
        toolbar.addWidget(self.call_search, 1)
        read_button = QPushButton("Telefon híváslistájának beolvasása")
        read_button.clicked.connect(self.import_phone_call_log)
        toolbar.addWidget(read_button)
        layout.addLayout(toolbar)
        self.call_table = QTableWidget(0, 7)
        self.call_table.setHorizontalHeaderLabels(["Időpont", "Irány / állapot", "Név", "Telefonszám", "Időtartam", "Beszélgetés lényege", "Teendő"])
        self.call_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.call_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.call_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        call_header = self.call_table.horizontalHeader()
        call_header.setStretchLastSection(False)
        # Start with content-based widths, then let the user resize every column.
        for column in range(self.call_table.columnCount()):
            call_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.call_table.resizeColumnsToContents()
        saved_widths = self.store.data.get("settings", {}).get("call_log_column_widths", [])
        if isinstance(saved_widths, list) and len(saved_widths) == self.call_table.columnCount():
            for column, width in enumerate(saved_widths):
                try:
                    if int(width) >= 24:
                        self.call_table.setColumnWidth(column, int(width))
                except (TypeError, ValueError):
                    pass
        self._call_header_restore_done = True
        self.call_header_save_timer = QTimer(self)
        self.call_header_save_timer.setSingleShot(True)
        self.call_header_save_timer.setInterval(450)
        self.call_header_save_timer.timeout.connect(self._save_call_log_column_widths)
        call_header.sectionResized.connect(self._call_log_column_resized)
        self.call_table.itemChanged.connect(self._call_log_item_changed)
        layout.addWidget(self.call_table, 1)
        buttons = QHBoxLayout()
        edit = QPushButton("Kijelölt hívás jegyzete / teendője…")
        edit.clicked.connect(self.edit_call_note)
        redial = QPushButton("Szám újrahívása…")
        redial.clicked.connect(self.redial_selected_call)
        save = QPushButton("Napló mentése")
        save.clicked.connect(self.save_json)
        buttons.addWidget(edit); buttons.addWidget(redial); buttons.addStretch(1); buttons.addWidget(save)
        layout.addLayout(buttons)
        self.call_log_status = QLabel("A helyi hívásnapló még üres. A telefon naplójának olvasása Android-jogosultságtól függ.")
        layout.addWidget(self.call_log_status)
        self.tabs.addTab(page, "Hívásnapló")
        self.refresh_call_log()

    def _call_log_column_resized(self, *_):
        if getattr(self, "_call_header_restore_done", False):
            self.call_header_save_timer.start()

    def _save_call_log_column_widths(self):
        """Persist user-adjusted call-log column widths in module settings."""
        try:
            widths = [self.call_table.columnWidth(i) for i in range(self.call_table.columnCount())]
            self.store.data.setdefault("settings", {})["call_log_column_widths"] = widths
            self.store.save()
        except Exception as exc:
            self._log(f"A hívásnapló oszlopszélességeinek mentése nem sikerült: {exc}", "WARNING")

    def _call_log_item_changed(self, item):
        if getattr(self, "_updating_call_table", False):
            return
        row = item.row()
        if row < 0 or row >= self.call_table.rowCount():
            return
        call_id = self.call_table.item(row, 0).data(Qt.ItemDataRole.UserRole) if self.call_table.item(row, 0) else None
        record = next((r for r in self.store.data.get("call_logs", []) if r.get("id") == call_id), None)
        if not record:
            return
        if item.column() == 5:
            record["summary"] = item.text()
        elif item.column() == 6:
            record["todo"] = item.text()
        record["updated_at"] = _now()
        self.store.save()
        self.call_log_status.setText("A hívásjegyzet mentve a JSON-adatállományba.")

    def refresh_call_log(self, *_):
        if not hasattr(self, "call_table"):
            return
        records = list(self.store.data.get("call_logs", []))
        filt = self.call_filter.currentText() if hasattr(self, "call_filter") else "Összes hívás"
        mapping = {"Kimenő": "outgoing", "Fogadott": "incoming", "Nem fogadott": "missed", "Elutasított": "rejected"}
        if filt in mapping:
            records = [r for r in records if r.get("direction") == mapping[filt]]
        query = self.call_search.text().casefold().strip() if hasattr(self, "call_search") else ""
        if query:
            records = [r for r in records if query in " ".join(str(r.get(k, "")) for k in ("name", "number", "summary", "todo", "direction")).casefold()]
        records.sort(key=lambda r: r.get("date", ""), reverse=True)
        self._updating_call_table = True
        self.call_table.setRowCount(0)
        for rec in records:
            row = self.call_table.rowCount(); self.call_table.insertRow(row)
            direction = {"outgoing": "Kimenő", "incoming": "Fogadott", "missed": "Nem fogadott", "rejected": "Elutasított"}.get(rec.get("direction"), rec.get("direction", "Ismeretlen"))
            duration = int(rec.get("duration_seconds", 0) or 0)
            vals = [rec.get("date", ""), direction, rec.get("name", ""), rec.get("number", ""), (f"{duration//60:02d}:{duration%60:02d}" if rec.get("duration_seconds") is not None else "—"), rec.get("summary", ""), rec.get("todo", "")]
            for col, value in enumerate(vals):
                cell = QTableWidgetItem(str(value or ""))
                if col == 0: cell.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
                self.call_table.setItem(row, col, cell)
        self._updating_call_table = False
        self.call_log_status.setText(f"{len(records)} hívás a jelenlegi szűrésben; helyi napló: {len(self.store.data.get('call_logs', []))}.")

    def edit_call_note(self):
        row = self.call_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Hívásnapló", "Jelölj ki egy hívást."); return
        call_id = self.call_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        rec = next((r for r in self.store.data.get("call_logs", []) if r.get("id") == call_id), None)
        if not rec: return
        summary, ok = QInputDialog.getMultiLineText(self, "Beszélgetés lényege", "Jegyzet:", rec.get("summary", ""))
        if not ok: return
        todo, ok = QInputDialog.getText(self, "Teendő", "Következő teendő / visszahívás:", text=rec.get("todo", ""))
        if not ok: return
        rec["summary"] = summary; rec["todo"] = todo; rec["updated_at"] = _now()
        self.store.save(); self.refresh_call_log()

    def redial_selected_call(self):
        row = self.call_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Újrahívás", "Jelölj ki egy hívást."); return
        number = self.call_table.item(row, 3).text().strip()
        if not number:
            QMessageBox.warning(self, "Újrahívás", "A kijelölt bejegyzéshez nincs telefonszám."); return
        self.dial_number.setText(number); self.tabs.setCurrentIndex(1); self.dial_selected_number()

    def import_phone_call_log(self):
        try:
            result = self._adb(["shell", "content", "query", "--uri", "content://call_log/calls", "--projection", "_id:number:type:date:duration:name"], timeout=20)
            output = (result.stdout or "").strip()
            if result.returncode or not output or "Permission Denial" in output or "SecurityException" in output:
                raise RuntimeError((result.stderr or output or "Az Android nem adott vissza hívásadatokat.").strip())
            added = 0
            # Android content CLI emits rows as "Row: 0 _id=..., number=..., type=..., date=..., duration=..., name=..."
            for line in output.splitlines():
                if "Row:" not in line or "number=" not in line:
                    continue
                fields = dict(re.findall(r"([A-Za-z_]+)=([^,]+)", line))
                number = fields.get("number", "").strip()
                raw_id = fields.get("_id", "")
                if not number: continue
                call_id = "android-call-" + raw_id if raw_id else "android-call-" + str(uuid.uuid5(uuid.NAMESPACE_URL, line))
                if any(x.get("id") == call_id for x in self.store.data.get("call_logs", [])): continue
                try: epoch = int(fields.get("date", "0")) / 1000
                except ValueError: epoch = 0
                try: duration = int(fields.get("duration", "0"))
                except ValueError: duration = 0
                typ = fields.get("type", "")
                direction = {"1": "incoming", "2": "outgoing", "3": "missed", "5": "rejected", "6": "rejected"}.get(typ, "unknown")
                name = fields.get("name", "").strip()
                self.store.data.setdefault("call_logs", []).append({"id": call_id, "date": datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds") if epoch else "", "direction": direction, "number": number, "name": name, "duration_seconds": duration, "summary": "", "todo": "", "source": "Android hívásnapló", "source_call_id": raw_id, "created_at": _now()})
                added += 1
            if added:
                self.store.save(); self.refresh_call_log()
            QMessageBox.information(self, "Hívásnapló beolvasása", f"Új hívásbejegyzések: {added}.\nA meglévő jegyzeteket a program megőrizte.")
        except Exception as exc:
            QMessageBox.warning(self, "Hívásnapló nem olvasható", "Az Android nem engedte a híváslista közvetlen ADB-s beolvasását, vagy a kimenet nem volt értelmezhető. Ez gyakori jogosultsági korlátozás.\n\nRészlet: " + str(exc) + "\n\nIlyenkor Android-oldali segédalkalmazás szükséges; nem jelöltem sikeresnek a beolvasást.")

    def _poll_phone_state(self):
        # The UI thread never waits for dumpsys; do not overlap polling workers.
        if self._poll_worker is not None and self._poll_worker.isRunning():
            return
        serial = self.selected_serial()
        if not serial:
            return
        adb_path = self._adb_path()

        def query_state():
            result = subprocess.run([adb_path, "-s", serial, "shell", "dumpsys", "telephony.registry"], capture_output=True, text=True, timeout=4, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "dumpsys nem sikerült").strip())
            dump = result.stdout or ""
            if re.search(r"mCallState\s*=\s*2|mCallState=2", dump):
                state = "active"
            elif re.search(r"mCallState\s*=\s*1|mCallState=1", dump):
                state = "ringing"
            elif re.search(r"mCallState\s*=\s*0|mCallState=0", dump):
                state = "idle"
            else:
                state = "unknown"
            return state, dump[:2000]

        worker = FunctionWorker(query_state, self)
        self._poll_worker = worker
        worker.result_ready.connect(self._process_phone_state)
        worker.failed.connect(lambda message: self._log(f"Telefonállapot-lekérdezés nem sikerült: {message}", "WARNING"))
        worker.finished.connect(lambda w=worker: self._finish_poll_worker(w))
        worker.start()

    def _finish_poll_worker(self, worker):
        if self._poll_worker is worker:
            self._poll_worker = None
        worker.deleteLater()

    def _process_phone_state(self, result):
        state, _dump = result
        if state != self._last_call_state:
            if state == "active" and self._active_call_started is None:
                self._active_call_started = time.monotonic()
                self._active_call_number = self.dial_number.text().strip()
                self.dial_status.setText("A telefon aktív hívást jelez. A beszélgetési idő mérése elindult; a szám/irány nem hitelesített.")
                self._last_call_state = state
            elif state == "idle" and self._active_call_started is not None:
                elapsed = int(time.monotonic() - self._active_call_started)
                rec = {"id": str(uuid.uuid4()), "date": _now(), "direction": "unknown", "number": self._active_call_number, "name": self._find_contact_name(self._active_call_number), "duration_seconds": elapsed, "summary": "", "todo": "", "source": "ADB állapotfigyelés – becslés", "created_at": _now(), "duration_is_estimate": True, "number_is_unverified": True}
                self.store.data.setdefault("call_logs", []).append(rec)
                try:
                    self.store.save()
                    self.refresh_call_log()
                except Exception as exc:
                    self._log(f"Becsült hívásbejegyzés mentése sikertelen: {exc}", "ERROR")
                self._active_call_started = None
                self._active_call_number = ""
                self.dial_status.setText(f"A hívásállapot visszatért nyugalmi állapotba. Mért idő: {elapsed//60:02d}:{elapsed%60:02d}; a bejegyzés becslésként került naplózásra.")
                self._last_call_state = state
            else:
                self._last_call_state = state
        if self._active_call_started is not None:
            elapsed = int(time.monotonic() - self._active_call_started)
            self.dial_status.setText(f"Aktív hívás (ADB alapján): {elapsed//60:02d}:{elapsed%60:02d}. A szám és az irány nem hitelesített.")

    def _find_contact_name(self, number):
        norm = self.normalize_phone(number)
        for contact in self.store.contacts():
            for phone in contact.get("phone_numbers", []):
                candidate = phone.get("number", "") if isinstance(phone, dict) else str(phone)
                if self.normalize_phone(candidate) == norm and norm:
                    return contact.get("display_name", "")
        return ""

    def _build_compare_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel("Források összehasonlítása és duplikátumkezelés")
        heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(heading)
        info = QLabel("Az erős egyezés azonos normalizált telefonszámot jelent. A lehetséges névegyezések csak ellenőrzési javaslatok. Semmi nem olvad össze automatikusan.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.duplicate_table = QTableWidget(0, 5)
        self.duplicate_table.setHorizontalHeaderLabels(["Egyezés", "Telefonszám / kulcs", "Rekordok", "Névjegyek", "Állapot"])
        self.duplicate_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.duplicate_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.duplicate_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.duplicate_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.duplicate_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.duplicate_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.duplicate_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.duplicate_table, 1)
        buttons = QHBoxLayout()
        scan = QPushButton("Duplikátumok vizsgálata")
        scan.clicked.connect(self.find_duplicates)
        buttons.addWidget(scan)
        detail = QPushButton("Részletes összehasonlítás")
        detail.clicked.connect(self.compare_selected_group)
        buttons.addWidget(detail)
        merge = QPushButton("Kézi összevonás…")
        merge.clicked.connect(self.merge_selected_group)
        buttons.addWidget(merge)
        dismiss = QPushButton("Nem duplikátum")
        dismiss.clicked.connect(self.dismiss_selected_group)
        buttons.addWidget(dismiss)
        undo = QPushButton("Összevonás visszavonása")
        undo.clicked.connect(self.undo_last_merge)
        buttons.addWidget(undo)
        layout.addLayout(buttons)
        self.compare_status = QLabel("Még nem történt vizsgálat.")
        layout.addWidget(self.compare_status)
        self.tabs.addTab(page, "Összehasonlítás")

    def _build_export_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(QLabel("Adatmentés és exportálás"))
        desc = QLabel("A JSON a program elsődleges adattárolója. A CSV, VCF és SQL formátumok adatcserére valók; a SQLite külön export.")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        grid = QGridLayout()
        actions = [
            ("JSON mentése", self.save_json), ("JSON betöltése…", self.load_json_as),
            ("CSV export…", lambda: self.export_specific("csv")), ("VCF export…", lambda: self.export_specific("vcf")),
            ("CSV / VCF / JSON import…", self.import_file), ("SQLite export…", lambda: self.export_specific("sqlite")),
            ("SQL-dump export…", lambda: self.export_specific("sql")), ("Biztonsági mentés", self.backup_now),
        ]
        for i, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            button.setMinimumHeight(42)
            button.clicked.connect(callback)
            grid.addWidget(button, i // 2, i % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.export_status = QLabel("Nincs folyamatban export.")
        layout.addWidget(self.export_status)
        self.tabs.addTab(page, "Mentés / export")

    def _build_settings_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        saved_settings = self.store.data.get("settings", {})

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(True)
        outer.addWidget(self.settings_tabs, 1)

        # 1. Audio recording settings. Scroll area prevents wrapped labels and
        # smaller windows from clipping controls or overlapping later rows.
        audio_page = QWidget()
        audio_page_layout = QVBoxLayout(audio_page)
        audio_page_layout.setContentsMargins(0, 0, 0, 0)
        audio_scroll = QScrollArea()
        audio_scroll.setWidgetResizable(True)
        audio_scroll.setFrameShape(QFrame.Shape.NoFrame)
        audio_content = QWidget()
        audio_outer = QVBoxLayout(audio_content)
        audio_outer.setContentsMargins(12, 12, 12, 12)
        audio_outer.setSpacing(10)
        audio_form = QFormLayout()
        audio_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        audio_form.setVerticalSpacing(12)

        self.record_device_combo = QComboBox()
        self.record_device_combo.addItems(["Telefon", "Linuxos gép"])
        self.record_device_combo.setCurrentText(str(saved_settings.get("record_device", "Telefon")))
        audio_form.addRow("Rögzítési forrás:", self.record_device_combo)

        self.phone_audio_mode_combo = QComboBox()
        self.phone_audio_mode_combo.addItem("Lejátszott telefonhang", "output")
        self.phone_audio_mode_combo.addItem("Telefon mikrofonja", "mic")
        saved_phone_mode = str(saved_settings.get("phone_audio_mode", "output"))
        self.phone_audio_mode_combo.setCurrentIndex(max(0, self.phone_audio_mode_combo.findData(saved_phone_mode)))
        audio_form.addRow("Telefon hangforrása:", self.phone_audio_mode_combo)
        self._phone_audio_row_label = audio_form.labelForField(self.phone_audio_mode_combo)

        self.pc_audio_backend_combo = QComboBox()
        for label, key in [("Automatikus (PipeWire/PulseAudio)", "auto"), ("PipeWire / PulseAudio", "pulse"), ("JACK", "jack"), ("ALSA (közvetlen eszköz)", "alsa")]:
            self.pc_audio_backend_combo.addItem(label, key)
        self.pc_audio_backend_combo.setCurrentIndex(max(0, self.pc_audio_backend_combo.findData(str(saved_settings.get("pc_audio_backend", "jack")))))
        self.pc_audio_backend_combo.currentIndexChanged.connect(self.refresh_audio_sources)
        backend_row = QHBoxLayout()
        backend_row.addWidget(self.pc_audio_backend_combo, 1)
        self.refresh_audio_sources_button = QPushButton("Hangforrások frissítése")
        self.refresh_audio_sources_button.clicked.connect(self.refresh_audio_sources)
        backend_row.addWidget(self.refresh_audio_sources_button)
        backend_wrap = QWidget()
        backend_wrap.setLayout(backend_row)
        audio_form.addRow("Hangrendszer:", backend_wrap)

        pc_source_row = QHBoxLayout()
        self.pc_audio_source_combo = QComboBox()
        self.pc_audio_source_combo.addItem("JACK alapértelmezett bemenet", "jack::default")
        saved_pc_source = str(saved_settings.get("pc_audio_source", "jack::default"))
        pc_source_row.addWidget(self.pc_audio_source_combo, 1)
        pc_source_wrap = QWidget()
        pc_source_wrap.setLayout(pc_source_row)
        audio_form.addRow("Linuxos hangforrás:", pc_source_wrap)
        self._pc_audio_row_label = audio_form.labelForField(pc_source_wrap)
        self.pc_audio_help_label = QLabel(
            "Válaszd ki a hangrendszert, majd a konkrét bemenetet vagy monitorforrást. "
            "A rendszerhang monitorforrás, a mikrofon fizikai/virtuális bemenet. "
            "A rögzíthetőség az FFmpeg buildjétől és a hangkiszolgálótól függ."
        )
        self.pc_audio_help_label.setWordWrap(True)
        self.pc_audio_help_label.setMinimumWidth(0)
        self.pc_audio_help_label.setMinimumHeight(0)
        self.pc_audio_help_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.pc_audio_help_label.setStyleSheet("color: palette(mid); margin: 2px 0 4px 0;")
        # Full-width row: explanatory text must use the complete page width,
        # not just the narrow right-hand field column of QFormLayout.
        audio_form.addRow(self.pc_audio_help_label)

        self.audio_source_status_label = QLabel("Hangforrások ellenőrzése…")
        self.audio_source_status_label.setWordWrap(True)
        self.audio_source_status_label.setMinimumWidth(0)
        self.audio_source_status_label.setMinimumHeight(0)
        self.audio_source_status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.audio_source_status_label.setStyleSheet("margin: 2px 0 4px 0;")
        audio_form.addRow(self.audio_source_status_label)

        self.record_backend_label = QLabel("A választott forrás alapján automatikus: scrcpy vagy FFmpeg.")
        self.record_backend_label.setWordWrap(True)
        self.record_backend_label.setMinimumWidth(0)
        self.record_backend_label.setMinimumHeight(0)
        self.record_backend_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.record_backend_label.setStyleSheet("margin: 2px 0 4px 0;")
        audio_form.addRow(self.record_backend_label)
        self.record_device_combo.currentTextChanged.connect(self._update_recording_settings_visibility)
        self.refresh_audio_sources()

        self.record_format_combo = QComboBox()
        self.record_format_combo.addItems(["WAV", "FLAC", "M4A / AAC"])
        self.record_format_combo.setCurrentText(str(saved_settings.get("record_format", "WAV")))
        audio_form.addRow("Mentési formátum:", self.record_format_combo)

        self.record_folder = QLineEdit(str(saved_settings.get("record_folder", RECORDINGS_DIR)))
        choose_folder = QPushButton("Tallózás…")
        choose_folder.clicked.connect(self.choose_record_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.record_folder, 1)
        folder_row.addWidget(choose_folder)
        folder_wrap = QWidget()
        folder_wrap.setLayout(folder_row)
        audio_form.addRow("Mentési könyvtár:", folder_wrap)

        self.sample_rate = QComboBox()
        self.sample_rate.addItems(["16000", "22050", "44100", "48000"])
        self.sample_rate.setCurrentText(str(saved_settings.get("sample_rate", "48000")))
        audio_form.addRow("Mintavételezés:", self.sample_rate)

        self.channels_combo = QComboBox()
        self.channels_combo.addItems(["Mono", "Sztereó"])
        self.channels_combo.setCurrentText(str(saved_settings.get("channels", "Sztereó")))
        audio_form.addRow("Csatornák:", self.channels_combo)

        self.pre_record_warning = QCheckBox("Felvétel előtt jelenjen meg figyelmeztetés")
        self.pre_record_warning.setChecked(bool(saved_settings.get("pre_record_warning", True)))
        audio_form.addRow("Adatvédelem:", self.pre_record_warning)
        audio_outer.addLayout(audio_form)
        audio_outer.addStretch(1)
        audio_scroll.setWidget(audio_content)
        audio_page_layout.addWidget(audio_scroll)
        self.settings_tabs.addTab(audio_page, "Hangrögzítés")

        # 2. Phone-number and country-code settings
        phone_page = QWidget()
        phone_outer = QVBoxLayout(phone_page)
        phone_outer.setContentsMargins(12, 12, 12, 12)
        country_box = QGroupBox("Országkódok")
        country_layout = QVBoxLayout(country_box)
        country_help = QLabel("Csak az országkódot tároljuk. Az alapértelmezett országkódot a táblázatban jelöld ki.")
        country_help.setWordWrap(True)
        country_layout.addWidget(country_help)
        self.country_table = QTableWidget(0, 3)
        self.country_table.setHorizontalHeaderLabels(["Ország", "Országkód", "Alapértelmezett"])
        self.country_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.country_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.country_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.country_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.country_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        country_layout.addWidget(self.country_table, 1)
        country_buttons = QHBoxLayout()
        add_country = QPushButton("Új országkód hozzáadása")
        add_country.clicked.connect(self.add_country_code)
        edit_country = QPushButton("Kijelölt szerkesztése")
        edit_country.clicked.connect(self.edit_country_code)
        delete_country = QPushButton("Kijelölt törlése")
        delete_country.clicked.connect(self.delete_country_code)
        country_buttons.addWidget(add_country)
        country_buttons.addWidget(edit_country)
        country_buttons.addWidget(delete_country)
        country_layout.addLayout(country_buttons)
        phone_outer.addWidget(country_box, 1)
        self._load_country_table(saved_settings)
        self.country_table.itemChanged.connect(self._country_table_item_changed)

        phone_options = QGroupBox("Telefonszám-kezelés")
        phone_options_layout = QVBoxLayout(phone_options)
        self.auto_country_code = QCheckBox("Hiányzó országkód automatikus felajánlása új számoknál")
        self.auto_country_code.setChecked(bool(saved_settings.get("auto_country_code", True)))
        phone_options_layout.addWidget(self.auto_country_code)
        self.keep_original_phone = QCheckBox("Az eredeti telefonszám-formátum megőrzése")
        self.keep_original_phone.setChecked(bool(saved_settings.get("keep_original_phone", True)))
        phone_options_layout.addWidget(self.keep_original_phone)
        phone_outer.addWidget(phone_options)
        self.settings_tabs.addTab(phone_page, "Telefonszámok")

        # 3. Display preferences
        interface_page = QWidget()
        interface_outer = QVBoxLayout(interface_page)
        interface_outer.setContentsMargins(12, 12, 12, 12)
        dialer_group = QGroupBox("Jobb oldali tárcsázópanel megjelenítése")
        dialer_group_layout = QVBoxLayout(dialer_group)
        self.docked_dialer_checkboxes = {}
        for i in range(self.tabs.count()):
            tab_name = self.tabs.tabText(i)
            if tab_name in ("Hívásnapló", "Tárcsázó"):
                continue
            check = QCheckBox(tab_name)
            saved_tabs = self.store.data.get("settings", {}).get("docked_dialer_tabs", {})
            check.setChecked(bool(saved_tabs.get(tab_name, True)))
            self.docked_dialer_checkboxes[tab_name] = check
            check.toggled.connect(lambda checked, name=tab_name: self._set_docked_dialer_preference(name, checked))
            dialer_group_layout.addWidget(check)
        dialer_group_layout.addWidget(QLabel("A nagy tárcsázó és a hívásnapló lapján a jobb oldali panel nem jelenik meg."))
        interface_outer.addWidget(dialer_group)
        interface_outer.addStretch(1)
        self.settings_tabs.addTab(interface_page, "Felület")

        self.save_settings_button = QPushButton("Beállítások mentése")
        self.save_settings_button.setMinimumHeight(38)
        self.save_settings_button.clicked.connect(self.save_module_settings)
        outer.addWidget(self.save_settings_button, 0, Qt.AlignmentFlag.AlignRight)
        self.tabs.addTab(page, "Beállítások")

    def _apply_theme_hint(self):
        theme = self.settings.get("theme", "")
        if theme == "Matrix":
            self.setStyleSheet(self.styleSheet() + "\nQPushButton#recordButton { background: #7b1717; color: #ffffff; font-weight: 700; padding: 9px; }\nQPushButton#phoneActionButton { background: #16803c; color: white; font-weight: 700; padding: 8px 10px; min-height: 22px; border: 1px solid #35a853; border-radius: 4px; }\nQPushButton#phoneActionButton:hover { background: #1b9848; }")
        else:
            self.setStyleSheet(self.styleSheet() + "\nQPushButton#recordButton { background: #b4232d; color: white; font-weight: 700; padding: 9px; }\nQPushButton#phoneActionButton { background: #16803c; color: white; font-weight: 700; padding: 8px 10px; min-height: 22px; border: 1px solid #35a853; border-radius: 4px; }\nQPushButton#phoneActionButton:hover { background: #1b9848; }")

    def _country_code(self) -> str:
        if hasattr(self, "country_table"):
            for row in range(self.country_table.rowCount()):
                default_item = self.country_table.item(row, 2)
                code_item = self.country_table.item(row, 1)
                if default_item and default_item.checkState() == Qt.CheckState.Checked and code_item:
                    digits = re.sub(r"\D", "", code_item.text())
                    if digits:
                        return "+" + digits
        return str(self.store.data.get("settings", {}).get("default_country_code", "+36"))

    def _load_country_table(self, saved_settings):
        configured = saved_settings.get("country_codes")
        rows = configured if isinstance(configured, list) and configured else EUROPEAN_COUNTRY_CODES
        default_code = str(saved_settings.get("default_country_code", "+36"))
        self.country_table.blockSignals(True)
        self.country_table.setRowCount(0)
        default_found = False
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            country = _safe_text(entry.get("country")).strip()
            code = "+" + re.sub(r"\D", "", _safe_text(entry.get("code")))
            if not country or code == "+":
                continue
            row = self.country_table.rowCount()
            self.country_table.insertRow(row)
            self.country_table.setItem(row, 0, QTableWidgetItem(country))
            self.country_table.setItem(row, 1, QTableWidgetItem(code))
            default_item = QTableWidgetItem("")
            default_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            is_default = code == default_code and not default_found
            default_item.setCheckState(Qt.CheckState.Checked if is_default else Qt.CheckState.Unchecked)
            default_found = default_found or is_default
            self.country_table.setItem(row, 2, default_item)
        if not default_found:
            for row in range(self.country_table.rowCount()):
                if self.country_table.item(row, 1).text() == "+36":
                    self.country_table.item(row, 2).setCheckState(Qt.CheckState.Checked)
                    default_found = True
                    break
        self.country_table.blockSignals(False)

    def _country_table_item_changed(self, item):
        if item.column() != 2 or item.checkState() != Qt.CheckState.Checked:
            return
        self.country_table.blockSignals(True)
        for row in range(self.country_table.rowCount()):
            if row != item.row() and self.country_table.item(row, 2):
                self.country_table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)
        self.country_table.blockSignals(False)

    def add_country_code(self):
        country, ok = QInputDialog.getText(self, "Új országkód", "Ország neve:")
        if not ok or not country.strip():
            return
        code, ok = QInputDialog.getText(self, "Új országkód", "Nemzetközi országkód (például +355):")
        if not ok:
            return
        code = "+" + re.sub(r"\D", "", code)
        if code == "+":
            QMessageBox.warning(self, "Érvénytelen országkód", "Az országkódnak számjegyeket kell tartalmaznia.")
            return
        if any(self.country_table.item(r, 1).text() == code for r in range(self.country_table.rowCount())):
            QMessageBox.warning(self, "Már létezik", f"A(z) {code} országkód már szerepel a táblázatban.")
            return
        row = self.country_table.rowCount()
        self.country_table.insertRow(row)
        self.country_table.setItem(row, 0, QTableWidgetItem(country.strip()))
        self.country_table.setItem(row, 1, QTableWidgetItem(code))
        default_item = QTableWidgetItem("")
        default_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        default_item.setCheckState(Qt.CheckState.Unchecked)
        self.country_table.setItem(row, 2, default_item)
        self.country_table.selectRow(row)

    def edit_country_code(self):
        row = self.country_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Országkód", "Jelölj ki egy sort.")
            return
        country_item = self.country_table.item(row, 0)
        code_item = self.country_table.item(row, 1)
        country, ok = QInputDialog.getText(self, "Ország szerkesztése", "Ország neve:", text=country_item.text() if country_item else "")
        if not ok or not country.strip():
            return
        code, ok = QInputDialog.getText(self, "Országkód szerkesztése", "Nemzetközi országkód:", text=code_item.text() if code_item else "+")
        if not ok:
            return
        code = "+" + re.sub(r"\D", "", code)
        if code == "+":
            QMessageBox.warning(self, "Érvénytelen országkód", "Az országkódnak számjegyeket kell tartalmaznia.")
            return
        if any(r != row and self.country_table.item(r, 1).text() == code for r in range(self.country_table.rowCount())):
            QMessageBox.warning(self, "Már létezik", f"A(z) {code} országkód már szerepel a táblázatban.")
            return
        country_item.setText(country.strip())
        code_item.setText(code)

    def delete_country_code(self):
        row = self.country_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Országkód", "Jelölj ki egy sort.")
            return
        if self.country_table.item(row, 2).checkState() == Qt.CheckState.Checked:
            QMessageBox.warning(self, "Alapértelmezett országkód", "Előbb válassz másik alapértelmezett országkódot.")
            return
        self.country_table.removeRow(row)

    def _country_table_data(self):
        result = []
        seen_codes = set()
        default_count = 0
        for row in range(self.country_table.rowCount()):
            country = self.country_table.item(row, 0).text().strip() if self.country_table.item(row, 0) else ""
            raw_code = self.country_table.item(row, 1).text().strip() if self.country_table.item(row, 1) else ""
            code = "+" + re.sub(r"\D", "", raw_code)
            is_default = bool(self.country_table.item(row, 2) and self.country_table.item(row, 2).checkState() == Qt.CheckState.Checked)
            if not country or code == "+":
                raise ValueError(f"A(z) {row + 1}. országkód sorban hiányzik az ország neve vagy az országkód.")
            if code in seen_codes:
                raise ValueError(f"Duplikált országkód: {code}")
            seen_codes.add(code)
            default_count += int(is_default)
            result.append({"country": country, "code": code})
        if not result or default_count != 1:
            raise ValueError("Legalább egy országkód és pontosan egy alapértelmezett országkód szükséges.")
        return result

    def _update_recording_settings_visibility(self, *_):
        """Csak a kiválasztott rögzítési forráshoz tartozó beállításokat mutatja."""
        if not hasattr(self, "record_device_combo"):
            return
        phone = self.record_device_combo.currentText() == "Telefon"
        self.phone_audio_mode_combo.setVisible(phone)
        if getattr(self, "_phone_audio_row_label", None):
            self._phone_audio_row_label.setVisible(phone)
        self.pc_audio_source_combo.setEnabled(not phone)
        self.refresh_audio_sources_button.setEnabled(not phone)
        self.pc_audio_source_combo.setVisible(not phone)
        self.refresh_audio_sources_button.setVisible(not phone)
        if getattr(self, "_pc_audio_row_label", None):
            self._pc_audio_row_label.setVisible(not phone)
        self.pc_audio_help_label.setVisible(not phone)
        if getattr(self, "_pc_audio_help_row_label", None):
            self._pc_audio_help_row_label.setVisible(not phone)
        self.audio_source_status_label.setVisible(not phone)
        if getattr(self, "_audio_status_row_label", None):
            self._audio_status_row_label.setVisible(not phone)
        self.pc_audio_help_label.setText(
            "A Linuxos gép hangforrása csak Linuxos rögzítéskor használatos."
            if phone else
            "Linuxos rögzítés aktív. Ha csak az alapértelmezett forrás látszik, ellenőrizd, "
            "hogy fut-e a pipewire-pulse/PulseAudio szolgáltatás, és elérhető-e a pactl parancs."
        )
        self.record_backend_label.setText(
            "scrcpy – válassz egyet: a telefon lejátszott hangja vagy a telefon mikrofonja."
            if phone else
            "FFmpeg – a kiválasztott hangrendszer bemenetéről. JACK/ALSA esetén az FFmpeg megfelelő bemeneti támogatása szükséges."
        )

    def refresh_audio_sources(self, *_):
        """PipeWire/PulseAudio, JACK és ALSA bemenetek felderítése."""
        if not hasattr(self, "pc_audio_source_combo"):
            return
        saved = self.pc_audio_source_combo.currentData() or "jack::default"
        backend = self.pc_audio_backend_combo.currentData() if hasattr(self, "pc_audio_backend_combo") else "auto"
        self.pc_audio_source_combo.blockSignals(True)
        self.pc_audio_source_combo.clear()
        status_parts = []
        found = []

        def run_cmd(args, timeout=4):
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
                return r.returncode, r.stdout or "", r.stderr or ""
            except (OSError, subprocess.SubprocessError) as exc:
                return 127, "", str(exc)

        if backend in ("auto", "pulse"):
            pactl = shutil.which("pactl")
            if pactl:
                rc, out, err = run_cmd([pactl, "info"])
                if rc == 0:
                    server = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.lower().startswith("server name:") and ":" in line), "PulseAudio-kompatibilis szerver")
                    status_parts.append(f"PulseAudio-kompatibilis kiszolgáló: {server}")
                    rc2, out2, err2 = run_cmd([pactl, "list", "short", "sources"])
                    if rc2 == 0:
                        for line in out2.splitlines():
                            parts = line.split()
                            if len(parts) >= 2:
                                name = parts[1]
                                desc = "Rendszerhang/monitor" if ".monitor" in name or name.endswith("monitor") else "Bemenet"
                                found.append((f"{name} — {desc} [PipeWire/PulseAudio]", f"pulse::{name}"))
                    else:
                        status_parts.append("A PulseAudio-forráslista lekérése sikertelen: " + (err2.strip() or f"kilépési kód {rc2}"))
                else:
                    status_parts.append("PulseAudio/PipeWire-pulse nem érhető el: " + (err.strip() or f"kilépési kód {rc}"))
            else:
                status_parts.append("A pactl parancs nem található.")

        if backend == "jack":
            jack_lsp = shutil.which("jack_lsp")
            if jack_lsp:
                rc, out, err = run_cmd([jack_lsp], timeout=5)
                if rc == 0:
                    ports = [line.strip() for line in out.splitlines() if line.strip()]
                    # Build stereo pairs first so selecting capture_FL does not silently
                    # leave the right recording channel unconnected.
                    port_set = set(ports)
                    paired = set()
                    pair_candidates = (("_FL", "_FR"), ("_1", "_2"), (" capture_1", " capture_2"))
                    for port in ports:
                        if not any(token in port.lower() for token in ("capture", "input", "system:playback")):
                            continue
                        partner = None
                        for left_suffix, right_suffix in pair_candidates:
                            if port.endswith(left_suffix):
                                candidate = port[:-len(left_suffix)] + right_suffix
                                if candidate in port_set:
                                    partner = candidate
                                    break
                        if partner and port not in paired and partner not in paired:
                            left, right = (port, partner)
                            # Ensure FL/1 is treated as left when ordering is discoverable.
                            if left.endswith(("_FR", "_2")) or left.endswith(" capture_2"):
                                left, right = right, left
                            found.append((f"{left} + {right} [JACK sztereó pár]", f"jackpair::{left}|{right}"))
                            paired.update((left, right))
                    for port in ports:
                        if port in paired:
                            continue
                        if any(token in port.lower() for token in ("capture", "input", "system:playback")):
                            found.append((f"{port} [JACK mono port]", f"jack::{port}"))
                    if not found:
                        found.append(("JACK alapértelmezett bemenet", "jack::default"))
                    status_parts.append(f"JACK portok lekérdezve: {len(ports)}; felkínált források: {len(found)}. A sztereó párként jelölt bemenetek bal és jobb portját külön köti be a rögzítéshez.")
                else:
                    status_parts.append("A JACK szerver/portlista nem érhető el: " + (err.strip() or f"kilépési kód {rc}"))
            else:
                status_parts.append("A jack_lsp parancs nem található (JACK klienseszközök hiányozhatnak).")
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                rc, out, err = run_cmd([ffmpeg, "-hide_banner", "-devices"], timeout=5)
                if " jack " not in (out + err).lower() and " jack" not in (out + err).lower():
                    status_parts.append("Figyelem: az FFmpeg build JACK-bemeneti támogatása nem igazolt.")

        if backend == "alsa":
            arecord = shutil.which("arecord")
            if arecord:
                rc, out, err = run_cmd([arecord, "-L"], timeout=5)
                if rc == 0:
                    for line in out.splitlines():
                        name = line.strip()
                        if name and not name.startswith(" ") and name not in ("null",):
                            found.append((f"{name} [ALSA]", f"alsa::{name}"))
                    status_parts.append(f"ALSA eszközök/PCM-ek: {len(found)}.")
                else:
                    status_parts.append("Az ALSA eszközlista lekérése sikertelen: " + (err.strip() or f"kilépési kód {rc}"))
            else:
                status_parts.append("Az arecord nem található; az alsa-utils csomag hiányozhat.")

        # Auto mód csak a kompatibilis PulseAudio/PipeWire-forrásokat listázza; nem állítja,
        # hogy ez a JACK és ALSA teljes listája.
        if backend == "auto":
            status_parts.append("Automatikus mód: PulseAudio-kompatibilis források. A teljes JACK/ALSA listához válaszd ki külön a hangrendszert.")
        if not found and backend in ("auto", "pulse"):
            found.insert(0, ("Alapértelmezett PulseAudio/PipeWire forrás", "pulse::default"))
        for label, value in found:
            self.pc_audio_source_combo.addItem(label, value)
        # If an older saved preference points at only the left/right member of a
        # JACK stereo pair, prefer the pair when stereo output is selected.
        if backend == "jack" and hasattr(self, "channels_combo") and self.channels_combo.currentText() == "Sztereó" and str(saved).startswith("jack::"):
            old_port = str(saved).split("::", 1)[1]
            for i in range(self.pc_audio_source_combo.count()):
                value = self.pc_audio_source_combo.itemData(i)
                if isinstance(value, str) and value.startswith("jackpair::"):
                    pair = value.split("::", 1)[1].split("|", 1)
                    if old_port in pair and len(pair) == 2:
                        saved = value
                        break
        index = self.pc_audio_source_combo.findData(saved)
        self.pc_audio_source_combo.setCurrentIndex(index if index >= 0 else (0 if self.pc_audio_source_combo.count() else -1))
        self.pc_audio_source_combo.blockSignals(False)
        if hasattr(self, "audio_source_status_label"):
            self.audio_source_status_label.setText("\n".join(status_parts) + f"\nÖsszes felsorolt forrás: {len(found)}.")


    def normalize_phone(self, number: str) -> str:
        return normalize_number(number, self._country_code())

    def _contact_phone_values(self, contact):
        values = []
        for item in contact.get("phone_numbers", []):
            raw = item.get("number", "") if isinstance(item, dict) else str(item)
            canonical = self.normalize_phone(raw)
            if canonical:
                values.append((raw, canonical, item.get("label", "") if isinstance(item, dict) else ""))
        return values

    # ---- Device / ADB ----
    def _adb_path(self) -> str:
        return str(self.settings.get("adb_path") or shutil.which("adb") or "adb")

    def selected_serial(self) -> str:
        data = self.device_combo.currentData()
        return str(data or "")

    def refresh_devices(self):
        if self._device_worker is not None and self._device_worker.isRunning():
            return
        previous = self.selected_serial() if hasattr(self, "device_combo") else ""
        self.connect_button.setEnabled(False)
        self.device_refresh_button.setEnabled(False)
        adb_path = self._adb_path()

        def query():
            result = subprocess.run([adb_path, "devices", "-l"], capture_output=True, text=True, timeout=8, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "ADB-hiba").strip())
            devices = []
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "offline"):
                    devices.append((parts[0], parts[1], line.strip()))
            return devices

        worker = FunctionWorker(query, self)
        self._device_worker = worker
        worker.result_ready.connect(lambda devices, prev=previous: self._apply_devices(devices, prev))
        worker.failed.connect(self._device_query_failed)
        worker.finished.connect(lambda w=worker: self._finish_device_worker(w))
        worker.start()

    def _apply_devices(self, devices, previous=""):
        self.device_combo.clear()
        for serial, state, details in devices:
            self.device_combo.addItem(f"{serial} — {state}", serial)
            if state != "device":
                self.device_combo.setItemData(self.device_combo.count() - 1, details, Qt.ItemDataRole.ToolTipRole)
        idx = self.device_combo.findData(previous)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        usable = any(state == "device" for _, state, _ in devices)
        if usable:
            self.connection_status.setText(f"{len(devices)} eszköz; legalább egy engedélyezett")
        elif any(state == "unauthorized" for _, state, _ in devices):
            self.connection_status.setText("A telefon USB-hibakeresési engedélyre vár")
        elif any(state == "offline" for _, state, _ in devices):
            self.connection_status.setText("A telefon offline állapotú")
        else:
            self.connection_status.setText("Nincs csatlakoztatott ADB-eszköz")
        self._log(f"ADB-eszközlista frissítve: {len(devices)} bejegyzés.")

    def _device_query_failed(self, message):
        self.device_combo.clear()
        self.device_combo.addItem("ADB nem érhető el", "")
        self.connection_status.setText("ADB-hiba – részletek a naplóban")
        self._log(f"ADB-eszközlista hiba: {message}", "ERROR")

    def _finish_device_worker(self, worker):
        if self._device_worker is worker:
            self._device_worker = None
        self.connect_button.setEnabled(True)
        self.device_refresh_button.setEnabled(True)
        worker.deleteLater()

    def _adb(self, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
        serial = self.selected_serial()
        if not serial:
            raise RuntimeError("Nincs kiválasztott ADB-eszköz.")
        return subprocess.run([self._adb_path(), "-s", serial, *args], capture_output=True, text=True, timeout=timeout, check=False)

    def launch_phone_dialer(self):
        number = self.dial_number.text().strip()
        if not number:
            QMessageBox.information(self, "Tárcsázó", "Előbb adj meg egy telefonszámot.")
            return
        self._launch_phone_intent("android.intent.action.DIAL", "tel:" + number)

    def dial_selected_number(self):
        """Simple handoff: open the phone dialer; the user confirms the call on-device."""
        number = self.dial_number.text().strip()
        if not number:
            QMessageBox.information(self, "Hívás", "Nincs megadva telefonszám.")
            return
        answer = QMessageBox.question(self, "Hívás előkészítése", f"Átadjuk a számot a telefon tárcsázójának?\n\n{number}\n\nA hívást a telefonon indítod.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._launch_phone_intent("android.intent.action.DIAL", "tel:" + number)
        self.dial_status.setText("A szám átadva a telefon tárcsázójának; a hívást a telefonon kell indítani.")

    def dial_selected_number_scrcpy(self):
        number = self.dial_number.text().strip()
        if not number:
            QMessageBox.information(self, "Hívás", "Nincs megadva telefonszám.")
            return
        answer = QMessageBox.question(self, "Hívás a scrcpy-ben", f"A scrcpy-ablakban nyissuk meg a tárcsázót ezzel a számmal?\n\n{number}\n\nA hívást továbbra is a telefonon indítod.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._ensure_scrcpy_then(lambda: self._launch_phone_intent("android.intent.action.DIAL", "tel:" + number))

    def launch_phone_sms(self):
        recipient = self.sms_recipient.text().strip()
        if not recipient:
            QMessageBox.information(self, "SMS", "Nincs megadva címzett.")
            return
        self._launch_sms_intent(recipient, self.sms_body.toPlainText())

    def _ensure_scrcpy_then(self, callback):
        """Focus an existing scrcpy window for the selected serial, or start one and then hand off."""
        serial = self.selected_serial()
        if not serial:
            QMessageBox.warning(self, "Nincs készülék", "Válassz egy engedélyezett ADB-eszközt.")
            return
        try:
            # wmctrl gives a reliable window list when available; xdotool is the fallback.
            focused = False
            wmctrl = shutil.which("wmctrl")
            if wmctrl:
                listing = subprocess.run([wmctrl, "-lx"], capture_output=True, text=True, timeout=3, check=False)
                for line in listing.stdout.splitlines():
                    if "scrcpy" in line.lower() and (serial.lower() in line.lower() or "scrcpy" in line.lower()):
                        wid = line.split()[0]
                        subprocess.run([wmctrl, "-ia", wid], capture_output=True, timeout=3, check=False)
                        focused = True
                        break
            if not focused:
                xdotool = shutil.which("xdotool")
                if xdotool:
                    found = subprocess.run([xdotool, "search", "--onlyvisible", "--class", "scrcpy"], capture_output=True, text=True, timeout=3, check=False)
                    ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
                    if ids:
                        subprocess.run([xdotool, "windowactivate", "--sync", ids[-1]], capture_output=True, timeout=3, check=False)
                        focused = True
            if focused:
                QTimer.singleShot(350, callback)
                return
            scrcpy = shutil.which("scrcpy")
            if not scrcpy:
                raise RuntimeError("A scrcpy nem található a PATH-ban. Telepítsd a scrcpy csomagot, vagy indítsd el kézzel.")
            subprocess.Popen([scrcpy, "--serial", serial], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            # Give scrcpy time to connect and create its window before launching the Android intent.
            QTimer.singleShot(1600, callback)
        except Exception as exc:
            QMessageBox.critical(self, "scrcpy-hiba", f"Nem sikerült előkészíteni a scrcpy-ablakot.\n\n{exc}")

    def _launch_phone_intent(self, action: str, uri: str):
        if not self.selected_serial():
            QMessageBox.warning(self, "Nincs készülék", "Válassz egy engedélyezett ADB-eszközt.")
            return
        if not uri.startswith("tel:") or any(ch in uri for ch in "\n\r\x00"):
            QMessageBox.warning(self, "Érvénytelen szám", "A telefonszám nem adható át biztonságosan.")
            return
        try:
            result = self._adb(["shell", "am", "start", "-a", action, "-d", uri], timeout=10)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
            self._log(f"Telefonos tárcsázó megnyitására küldött kérés: {uri}")
            self.status.setText("A telefon tárcsázójának megnyitását kértem.")
        except Exception as exc:
            QMessageBox.critical(self, "Tárcsázó hiba", str(exc))

    def _launch_sms_intent(self, recipient: str, body: str, confirm: bool = True):
        if not self.selected_serial():
            QMessageBox.warning(self, "Nincs készülék", "Válassz egy engedélyezett ADB-eszközt.")
            return
        if any(ch in recipient for ch in "\n\r\x00"):
            QMessageBox.warning(self, "Érvénytelen címzett", "A címzett nem adható át biztonságosan.")
            return
        if confirm:
            answer = QMessageBox.question(self, "SMS átadása", f"Megnyissuk a telefon üzenetíró felületét?\nCímzett: {recipient}\n\nA küldés a telefonon ellenőrizhető.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            remote_args = ["am", "start", "-a", "android.intent.action.SENDTO", "-d", "smsto:" + recipient]
            if body:
                remote_args += ["--es", "sms_body", body]
            remote_command = " ".join(shlex.quote(part) for part in remote_args)
            result = self._adb(["shell", remote_command], timeout=10)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
            self.sms_status.setText("Az SMS-piszkozat átadva a telefon üzenetírójának. Ez nem jelenti az SMS elküldését.")
            self._log(f"SMS-piszkozat átadása a telefon üzenetírójának (nem igazolt küldés): {recipient}")
        except Exception as exc:
            QMessageBox.critical(self, "SMS-átadási hiba", str(exc))

    def send_sms_via_scrcpy(self):
        recipient = self.sms_recipient.text().strip()
        body = self.sms_body.toPlainText()
        if not recipient:
            QMessageBox.information(self, "SMS", "Add meg a címzettet.")
            return
        if not body.strip():
            QMessageBox.information(self, "SMS", "Az üzenet szövege üres.")
            return
        answer = QMessageBox.question(self, "SMS a scrcpy-ben", f"A scrcpy-ablakban nyissuk meg az SMS-piszkozatot?\nCímzett: {recipient}\n\nAz elküldéshez a telefonon kell jóváhagynod.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._ensure_scrcpy_then(lambda: self._launch_sms_intent(recipient, body, confirm=False))

    def send_sms_via_phone(self):
        recipient = self.sms_recipient.text().strip()
        if not recipient:
            QMessageBox.information(self, "SMS", "Add meg a címzettet.")
            return
        if not self.sms_body.toPlainText().strip():
            QMessageBox.information(self, "SMS", "Az üzenet szövege üres.")
            return
        self._launch_sms_intent(recipient, self.sms_body.toPlainText())

    # ---- Contacts table ----
    def import_phone_contacts(self):
        """Best-effort grouped import from Android Contacts Provider via ADB.

        Android access varies by OEM/build. Phone rows are grouped by contact_id;
        emails are queried separately and attached to the same local contact.
        The import is additive and never deletes phone-side records.
        """
        if not self.selected_serial():
            QMessageBox.warning(self, "Nincs készülék", "Válassz egy engedélyezett ADB-eszközt.")
            return

        def parse_rows(output):
            rows = []
            for line in (output or "").splitlines():
                if not line.lstrip().startswith("Row:"):
                    continue
                payload = line.split(" ", 2)[-1]
                fields = {}
                for part in re.split(r",\s*(?=[A-Za-z_][A-Za-z0-9_]*=)", payload):
                    if "=" in part:
                        key, value = part.split("=", 1)
                        fields[key.strip()] = value.strip()
                rows.append(fields)
            return rows

        try:
            phone_result = self._adb([
                "shell", "content", "query",
                "--uri", "content://com.android.contacts/data/phones",
                "--projection", "display_name:data1:account_type:account_name:contact_id:_id",
            ], timeout=30)
            output = (phone_result.stdout or "").strip()
            diagnostic = "\n".join(x for x in [phone_result.stderr.strip(), output[:1500]] if x)
            if phone_result.returncode != 0 or not output or "Permission Denial" in output or "SecurityException" in output:
                raise RuntimeError(diagnostic or "Az Android nem adott vissza névjegyadatokat.")

            phone_rows = parse_rows(output)
            groups = {}
            skipped = 0
            for fields in phone_rows:
                number = fields.get("data1", "").strip()
                name = fields.get("display_name", "").strip()
                account_type = fields.get("account_type", "").strip()
                account_name = fields.get("account_name", "").strip()
                contact_id = fields.get("contact_id", "").strip() or fields.get("_id", "").strip()
                if not number:
                    skipped += 1
                    continue
                key = (account_type.casefold(), account_name.casefold(), contact_id, name.casefold())
                entry = groups.setdefault(key, {"name": name, "account_type": account_type, "account_name": account_name, "contact_id": contact_id, "phones": [], "emails": []})
                canonical = self.normalize_phone(number)
                if not any(x["normalized"] == canonical and canonical for x in entry["phones"]):
                    entry["phones"].append({"number": number, "normalized": canonical, "label": ""})

            email_warning = ""
            try:
                email_result = self._adb([
                    "shell", "content", "query",
                    "--uri", "content://com.android.contacts/data/emails",
                    "--projection", "display_name:data1:account_type:account_name:contact_id:_id",
                ], timeout=30)
                if email_result.returncode == 0 and email_result.stdout and "Permission Denial" not in email_result.stdout and "SecurityException" not in email_result.stdout:
                    for fields in parse_rows(email_result.stdout):
                        email = fields.get("data1", "").strip()
                        contact_id = fields.get("contact_id", "").strip() or fields.get("_id", "").strip()
                        account_type = fields.get("account_type", "").strip()
                        account_name = fields.get("account_name", "").strip()
                        name = fields.get("display_name", "").strip()
                        key = (account_type.casefold(), account_name.casefold(), contact_id, name.casefold())
                        if key not in groups:
                            # Email-only contacts are still useful local contacts.
                            groups[key] = {"name": name, "account_type": account_type, "account_name": account_name, "contact_id": contact_id, "phones": [], "emails": []}
                        target = groups[key]["emails"]
                        if email and email.casefold() not in {x.casefold() for x in target}:
                            target.append(email)
                else:
                    email_warning = "Az Android nem engedte az e-mail-címek lekérdezését; a telefonszámok ettől még importálhatók."
            except Exception as exc:
                email_warning = f"Az e-mail-címek lekérdezése nem sikerült: {exc}"

            existing_source_ids = {_safe_text(c.get("source_contact_id")) for c in self.store.contacts() if c.get("source_contact_id")}
            existing_phone_keys = set()
            for c in self.store.contacts():
                account = _safe_text(c.get("source_account")).casefold()
                name = _safe_text(c.get("display_name")).casefold()
                for phone in c.get("phone_numbers", []):
                    raw = phone.get("number", "") if isinstance(phone, dict) else str(phone)
                    norm = self.normalize_phone(raw)
                    if norm:
                        existing_phone_keys.add((account, name, norm))

            imported = 0
            skipped_existing = 0
            for key, group in groups.items():
                account_type = group["account_type"]
                account_name = group["account_name"]
                name = group["name"]
                source_id = f"android-contact:{account_type}:{account_name}:{group['contact_id']}"
                if source_id in existing_source_ids:
                    skipped_existing += 1
                    continue
                new_phones = []
                for phone in group["phones"]:
                    identity = (account_name.casefold(), name.casefold(), phone["normalized"])
                    if identity in existing_phone_keys:
                        continue
                    new_phones.append(phone)
                if not new_phones and not group["emails"]:
                    skipped_existing += 1
                    continue
                at = account_type.casefold()
                if "sim" in at:
                    source = "SIM-kártya"
                elif "google" in at:
                    source = "Google-fiók"
                elif not account_type:
                    source = "Telefon"
                else:
                    source = "Egyéb fiók"
                self.store.add_contact({
                    "display_name": name or (new_phones[0]["number"] if new_phones else (group["emails"][0] if group["emails"] else "(név nélkül)")),
                    "phone_numbers": new_phones, "emails": group["emails"], "source_type": source,
                    "source_account": account_name, "source_contact_id": source_id,
                    "notes": "Android Contacts Provider beolvasás",
                })
                existing_source_ids.add(source_id)
                imported += 1

            if imported:
                self.store.save()
            self.refresh_contacts()
            self._refresh_contact_suggestions()
            self.status.setText(f"Telefonbeolvasás: {imported} személy; {skipped_existing} már létező csoport kihagyva.")
            details = f"Új helyi személyrekordok: {imported}\nKihagyott meglévő csoportok: {skipped_existing}\nAndroid telefonsorok: {len(phone_rows)}"
            if email_warning:
                details += "\n\n" + email_warning
            QMessageBox.information(self, "Névjegy-beolvasás", details + "\n\nA beolvasás csak a helyi adatállományt módosítja; a telefonon nem töröl és nem ír át rekordot.")
        except Exception as exc:
            QMessageBox.warning(self, "Névjegy-beolvasási hiba", "Nem sikerült beolvasni a telefon névjegyeit.\n\n" + f"ADB-eszköz: {self.selected_serial()}\nHiba: {exc}")

    def refresh_contacts(self, *_):
        if not hasattr(self, "table"):
            return
        query = self.search_edit.text().strip().casefold()
        records = []
        for contact in self.store.contacts():
            source = contact.get("source_type", "Ismeretlen")
            if not self.source_filter.get(source, source not in SOURCES):
                continue
            haystack = " ".join([
                _safe_text(contact.get("display_name")),
                _safe_text(contact.get("source_account")),
                _safe_text(contact.get("notes")),
                " ".join(_safe_text(p.get("number")) for p in contact.get("phone_numbers", []) if isinstance(p, dict)),
                " ".join(_safe_text(e) for e in contact.get("emails", [])),
            ]).casefold()
            if query and query not in haystack:
                continue
            records.append(contact)
        self._visible_records = records
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for contact in records:
            phones = contact.get("phone_numbers", [])
            phone = _safe_text(phones[0].get("number", "")) if phones and isinstance(phones[0], dict) else ""
            emails = contact.get("emails", [])
            email = _safe_text(emails[0]) if emails else ""
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [contact.get("display_name", ""), phone, contact.get("source_type", "Ismeretlen"), email]
            for col, value in enumerate(values):
                item = QTableWidgetItem(_safe_text(value))
                item.setData(Qt.ItemDataRole.UserRole, contact.get("id"))
                if col == 1:
                    color = SOURCE_COLORS.get(contact.get("source_type", "Ismeretlen"), SOURCE_COLORS["Ismeretlen"])
                    item.setBackground(QColor(color))
                    item.setForeground(QColor("#202020"))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self._refresh_contact_suggestions()
        self.status.setText(f"{len(records)} névjegy a jelenlegi szűrésben; összes helyi rekord: {len(self.store.contacts())}.")

    def show_source_filter(self):
        menu = QMenu(self)
        all_action = QAction("Mind kijelölése / törlése", menu)
        menu.addAction(all_action)
        menu.addSeparator()
        actions = {}
        for source in SOURCES:
            action = QAction(source, menu)
            action.setCheckable(True)
            action.setChecked(self.source_filter.get(source, True))
            menu.addAction(action)
            actions[source] = action
        chosen = menu.exec(self.source_filter_button.mapToGlobal(self.source_filter_button.rect().bottomLeft()))
        if chosen is all_action:
            all_checked = not all(self.source_filter.values())
            self.source_filter = {source: all_checked for source in SOURCES}
        elif chosen in actions.values():
            source = next(key for key, action in actions.items() if action is chosen)
            self.source_filter[source] = chosen.isChecked()
        selected = [s for s, enabled in self.source_filter.items() if enabled]
        label = "Források: összes" if len(selected) == len(SOURCES) else (f"Források: {len(selected)} kiválasztva" if selected else "Források: nincs")
        self.source_filter_button.setText(label + "  ▾")
        self.refresh_contacts()

    def _selected_contacts(self) -> list[dict[str, Any]]:
        ids = set()
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item:
                ids.add(item.data(Qt.ItemDataRole.UserRole))
        return [c for c in self.store.contacts() if c.get("id") in ids]

    def _selected_contact_at(self, pos) -> dict[str, Any] | None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return None
        item = self.table.item(index.row(), 0)
        if not item:
            return None
        cid = item.data(Qt.ItemDataRole.UserRole)
        return next((c for c in self.store.contacts() if c.get("id") == cid), None)

    def contact_context_menu(self, pos):
        contact = self._selected_contact_at(pos)
        if not contact:
            return
        row = self.table.indexAt(pos).row()
        self.table.selectRow(row)
        phones = contact.get("phone_numbers", [])
        menu = QMenu(self)
        call_action = menu.addAction("Hívás / tárcsázó megnyitása")
        sms_action = menu.addAction("SMS írása")
        menu.addSeparator()
        copy_number = menu.addAction("Telefonszám másolása")
        copy_contact = menu.addAction("Névjegy másolása")
        details = menu.addAction("Névjegy részletei")
        menu.addSeparator()
        delete = menu.addAction("Helyi rekord törlése…")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        number = _safe_text(phones[0].get("number", "")) if phones and isinstance(phones[0], dict) else ""
        if chosen is call_action:
            self.dial_number.setText(number)
            self.tabs.setCurrentIndex(1)
            self.dial_selected_number()
        elif chosen is sms_action:
            self.sms_recipient.setText(number)
            self.sms_body.setFocus()
            self.tabs.setCurrentIndex(2)
        elif chosen is copy_number:
            QApplication.clipboard().setText(number)
            self.status.setText("Telefonszám a vágólapra másolva.")
        elif chosen is copy_contact:
            payload = json.dumps(contact, ensure_ascii=False, indent=2)
            QApplication.clipboard().setText(payload)
            self.status.setText("A kijelölt névjegy JSON-szövegként a vágólapra másolva.")
        elif chosen is details:
            QMessageBox.information(self, "Névjegy részletei", json.dumps(contact, ensure_ascii=False, indent=2))
        elif chosen is delete:
            self.delete_contact(contact)

    def add_contact_dialog(self):
        """Create a local JSON contact and, with explicit destination choice, a phone/SIM contact."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Új névjegy létrehozása")
        dialog.setModal(True)
        dialog.resize(460, 300)
        outer = QVBoxLayout(dialog)
        form = QFormLayout()
        name = QLineEdit()
        name.setPlaceholderText("Teljes név")
        phone = QLineEdit()
        phone.setPlaceholderText("Telefonszám országkóddal vagy belföldi formában")
        phone_hint = QLabel("Az országkód a Beállításokban megadott érték alapján egészíthető ki.")
        phone_hint.setWordWrap(True)
        phone.textChanged.connect(lambda value: phone_hint.setText(
            f"Egységesített összehasonlító forma: {self.normalize_phone(value)}" if value.strip() else
            "Az országkód a Beállításokban megadott érték alapján egészíthető ki."
        ))
        email = QLineEdit()
        email.setPlaceholderText("nev@example.com (nem kötelező)")
        destination = QComboBox()
        destination.addItems(["Telefon tárhelye", "SIM-kártya"])
        form.addRow("Név:", name)
        form.addRow("Telefonszám:", phone)
        form.addRow("Számfelismerés:", phone_hint)
        form.addRow("E-mail:", email)
        form.addRow("Telefonos mentés helye:", destination)
        outer.addLayout(form)
        local_note = QLabel("A névjegy a helyi gép JSON-adatállományába és a kiválasztott telefonos tárhelyre is mentésre kerül.")
        local_note.setWordWrap(True)
        outer.addWidget(local_note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Létrehozás és mentés")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        display_name = name.text().strip()
        number = phone.text().strip()
        mail = email.text().strip()
        if not display_name or not number:
            QMessageBox.warning(self, "Hiányzó adat", "A név és a telefonszám megadása kötelező.")
            return
        if not re.fullmatch(r"[+0-9() .-]+", number):
            QMessageBox.warning(self, "Hibás telefonszám", "A telefonszám csak számjegyeket, + jelet, szóközt, zárójelet, pontot és kötőjelet tartalmazhat.")
            return
        if mail and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail):
            QMessageBox.warning(self, "Hibás e-mail", "Ellenőrizd az e-mail-címet.")
            return

        # Offer country-code completion explicitly; preserve the typed value unless accepted.
        target_norm = self.normalize_phone(number)
        if self.auto_country_code.isChecked() and target_norm.startswith("+") and not number.lstrip().startswith(("+", "00")):
            answer = QMessageBox.question(
                self, "Országkód kiegészítése",
                f"A megadott szám egységesített nemzetközi alakja:\n{target_norm}\n\n"
                "Ezt a formátumot használjuk a névjegy telefonszámmezőjében is?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                number = target_norm
        if not self.keep_original_phone.isChecked():
            number = target_norm
        # Prevent creating an accidental duplicate locally.
        target_norm = self.normalize_phone(number)
        if any(self.normalize_phone(p.get("number", "") if isinstance(p, dict) else str(p)) == target_norm
               and target_norm for c in self.store.contacts() for p in c.get("phone_numbers", [])):
            answer = QMessageBox.question(self, "Már létező telefonszám",
                "Ez a telefonszám már szerepel a helyi névjegyek között. Ettől függetlenül létrehozod az új névjegyet?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        record = {
            "display_name": display_name,
            "phone_numbers": [{"number": number, "normalized": target_norm, "label": "mobile"}],
            "emails": ([mail] if mail else []),
            "source_type": "Helyi rekord", "source_account": "Helyi gép",
            "notes": "A Redmi Control Centerben létrehozva", "created_at": _now(),
        }
        try:
            self.store.add_contact(record)
            self.store.save()
            self._log(f"Helyi névjegy létrehozva: {display_name} / {number}; telefonos cél: {destination.currentText()}.")
            self._dirty = False
            self.refresh_contacts()
            self._refresh_contact_suggestions()
            local_saved = True
        except Exception as exc:
            QMessageBox.critical(self, "Helyi mentési hiba", f"A névjegy nem menthető a helyi JSON-adatállományba:\n{exc}")
            return

        phone_target = destination.currentText()
        try:
            self._create_android_contact(display_name, number, mail, phone_target)
            self.status.setText(f"Névjegy mentve helyben és a telefonra ({phone_target}).")
            QMessageBox.information(self, "Névjegy létrehozva",
                f"A névjegy a helyi gépen és a telefonon is létrejött.\n\nNév: {display_name}\nSzám: {number}\nTelefonos cél: {phone_target}")
        except Exception as exc:
            self._log(f"Telefonos névjegymentés sikertelen: {display_name} / {number}; cél: {phone_target}; hiba: {exc}", "ERROR")
            self.status.setText("A helyi névjegy mentve; a telefonos mentés sikertelen.")
            QMessageBox.warning(self, "Telefonos mentés nem sikerült",
                f"A névjegy a helyi gépen elmentődött, de a telefonra nem sikerült írni.\n\nCél: {phone_target}\nHiba: {exc}\n\nA helyi rekord megmaradt; nem állítjuk, hogy a telefonon is létrejött.")
        self.update_sms_recipient_identity(self.sms_recipient.text() if hasattr(self, "sms_recipient") else "")

    def _create_android_contact(self, display_name: str, number: str, email: str, target: str):
        """Insert contact rows via Android Contacts Provider; fail explicitly if Android blocks it."""
        if not self.selected_serial():
            raise RuntimeError("Nincs kiválasztott ADB-eszköz.")
        # Determine a real SIM account type from existing raw contacts; never invent one.
        account_type = ""
        account_name = ""
        if target == "SIM-kártya":
            result = self._adb(["shell", "content", "query", "--uri", "content://com.android.contacts/raw_contacts",
                                "--projection", "account_type:account_name:_id"], timeout=20)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Nem olvashatók a telefon fióktípusai.").strip())
            for line in result.stdout.splitlines():
                low = line.casefold()
                if "sim" in low and "account_type=" in low:
                    m_type = re.search(r"account_type=([^,]+)", line)
                    m_name = re.search(r"account_name=([^,]*)", line)
                    if m_type:
                        account_type = m_type.group(1).strip()
                        account_name = m_name.group(1).strip() if m_name else ""
                        break
            if not account_type:
                raise RuntimeError("A telefonból nem sikerült valós SIM-fióktípust azonosítani. Nem találgatok account_type értéket; előbb ellenőrizni kell, hogy a SIM névjegytárolója írható-e.")
        # For device storage, Android's local account is represented by null account_type/account_name.
        binds = []
        if target == "Telefon tárhelye":
            binds = ["--bind", "account_type:n:", "--bind", "account_name:n:"]
        else:
            binds = ["--bind", shlex.quote(f"account_type:s:{account_type}"), "--bind", shlex.quote(f"account_name:s:{account_name}")]
        result = self._adb(["shell", "content", "insert", "--uri", "content://com.android.contacts/raw_contacts", *binds], timeout=20)
        output = (result.stdout or "").strip()
        if result.returncode != 0 or "Error" in output or "Exception" in output:
            raise RuntimeError((result.stderr or output or "Az Android nem engedte a nyers névjegy létrehozását.").strip())
        m = re.search(r"(?:raw_contacts/|_id=)(\d+)", output)
        if not m:
            # Some Android builds print the inserted URI in a different form; verify by re-querying.
            check = self._adb(["shell", "content", "query", "--uri", "content://com.android.contacts/raw_contacts",
                               "--projection", "_id:account_type:account_name"], timeout=20)
            ids = re.findall(r"Row:\s*(\d+).*?_id=(\d+)", check.stdout or "")
            if not ids:
                raise RuntimeError(f"A nyers névjegy létrehozásának eredménye nem azonosítható. Android válasza: {output or result.stderr}")
            raw_id = ids[-1][1]
        else:
            raw_id = m.group(1)
        def insert_data(mimetype: str, values: list[tuple[str, str]]):
            args = ["shell", "content", "insert", "--uri", "content://com.android.contacts/data",
                    "--bind", f"raw_contact_id:i:{raw_id}", "--bind", f"mimetype:s:{mimetype}"]
            for column, value in values:
                # shell-quote the complete bind so spaces and non-ASCII text remain one argument.
                args.extend(["--bind", shlex.quote(f"{column}:s:{value}")])
            r = self._adb(args, timeout=20)
            out = (r.stdout or "").strip()
            if r.returncode != 0 or "Error" in out or "Exception" in out or r.stderr.strip():
                raise RuntimeError((r.stderr or out or f"Nem írható a névjegy mezője: {column}").strip())
        insert_data("vnd.android.cursor.item/name", [("data1", display_name)])
        insert_data("vnd.android.cursor.item/phone_v2", [("data1", number), ("data2", "2")])
        if email:
            insert_data("vnd.android.cursor.item/email_v2", [("data1", email), ("data2", "1")])

    def delete_contact(self, contact: dict[str, Any]):
        source = contact.get("source_type", "Ismeretlen")
        if source not in ("Importált fájl", "Helyi rekord", "Helyi adatállomány"):
            QMessageBox.warning(self, "Nem helyi rekord", "Ez a rekord forráscímkéje szerint nem helyi. A telefon/SIM módosítása még nincs bekötve; itt nem törölhető.")
            return
        answer = QMessageBox.question(self, "Helyi rekord törlése", f"Töröljük a helyi adatállományból ezt a névjegyet?\n\n{contact.get('display_name', '')}", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.data["contacts"] = [c for c in self.store.contacts() if c.get("id") != contact.get("id")]
        self._log(f"Helyi névjegy törölve: {contact.get('display_name', '')} ({contact.get('id', '')}).")
        self._save_after_change("A helyi rekord törölve.")

    def delete_local_contacts(self):
        selected = self._selected_contacts()
        if not selected:
            QMessageBox.information(self, "Törlés", "Nincs kijelölt névjegy.")
            return
        nonlocal_records = [c for c in selected if c.get("source_type") not in ("Importált fájl", "Helyi rekord", "Helyi adatállomány")]
        if nonlocal_records:
            QMessageBox.warning(self, "Vegyes kijelölés", "A kijelölés telefonról/SIM-ről származó rekordot is tartalmaz. A műveletet nem hajtottam végre.")
            return
        answer = QMessageBox.question(self, "Helyi rekordok törlése", f"Töröljük a kijelölt {len(selected)} helyi rekordot?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        ids = {c.get("id") for c in selected}
        self.store.data["contacts"] = [c for c in self.store.contacts() if c.get("id") not in ids]
        self._save_after_change(f"{len(selected)} helyi rekord törölve.")

    def copy_number(self):
        contact = (self._selected_contacts() or [None])[0]
        if not contact:
            return
        phones = contact.get("phone_numbers", [])
        number = _safe_text(phones[0].get("number", "")) if phones and isinstance(phones[0], dict) else ""
        QApplication.clipboard().setText(number)
        self.status.setText("Telefonszám másolva.")

    def copy_contact(self):
        contacts = self._selected_contacts()
        if not contacts:
            return
        QApplication.clipboard().setText(json.dumps(contacts[0], ensure_ascii=False, indent=2))
        self.status.setText("Névjegy JSON-szövegként másolva.")

    def select_all_contacts(self):
        self.table.selectAll()

    def clear_selection(self):
        self.table.clearSelection()

    def prepare_sms_from_selection(self):
        selected = self._selected_contacts()
        if not selected:
            contact = self._selected_contact_at(self.table.viewport().rect().center())
            selected = [contact] if contact else []
        if not selected:
            return
        phones = selected[0].get("phone_numbers", [])
        number = _safe_text(phones[0].get("number", "")) if phones and isinstance(phones[0], dict) else ""
        self.sms_recipient.setText(number)
        self.tabs.setCurrentIndex(2)
        self.sms_body.setFocus()

    # ---- Data operations ----
    def _save_after_change(self, message: str):
        try:
            self.store.save()
            self._dirty = False
            self.refresh_contacts()
            self.status.setText(message)
            self._log(message)
        except Exception as exc:
            QMessageBox.critical(self, "Mentési hiba", f"A változás nem menthető el:\n{exc}")

    def save_json(self):
        try:
            self.store.save()
            self.status.setText(f"JSON mentve: {self.store.path}")
            self.export_status.setText(f"JSON mentve: {self.store.path}")
            self._log(f"JSON mentve: {self.store.path}")
        except Exception as exc:
            QMessageBox.critical(self, "JSON-mentési hiba", str(exc))

    def load_json_as(self):
        path, _ = QFileDialog.getOpenFileName(self, "Névjegy-JSON betöltése", str(DATA_DIR), "JSON fájl (*.json)")
        if not path:
            return
        try:
            parsed = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("contacts", []), list):
                raise ValueError("A fájl nem érvényes névjegy-JSON.")
            answer = QMessageBox.question(self, "JSON betöltése", "A betöltés lecseréli a jelenlegi helyi adatállományt. Előtte készül mentés. Folytatod?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.store.save()
            self.store.data = parsed
            for key, default in [("schema_version", SCHEMA_VERSION), ("sources", {}), ("snapshots", []), ("settings", {}), ("recording_index", [])]:
                self.store.data.setdefault(key, default)
            self.store.save(backup=False)
            self.refresh_contacts()
            self.status.setText(f"JSON betöltve: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Betöltési hiba", str(exc))

    def backup_now(self):
        try:
            self.store.save(backup=False)
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = BACKUP_DIR / f"contacts-manual-{stamp}.json"
            shutil.copy2(self.store.path, target)
            QMessageBox.information(self, "Biztonsági mentés", f"Mentés elkészült:\n{target}")
        except Exception as exc:
            QMessageBox.critical(self, "Mentési hiba", str(exc))

    def export_file(self):
        self.export_specific("auto")

    def export_specific(self, kind: str):
        formats = {
            "auto": "JSON fájl (*.json);;CSV fájl (*.csv);;VCF névjegyek (*.vcf);;SQLite adatbázis (*.sqlite);;SQL dump (*.sql)",
            "csv": "CSV fájl (*.csv)", "vcf": "VCF névjegyek (*.vcf)", "sqlite": "SQLite adatbázis (*.sqlite)", "sql": "SQL dump (*.sql)",
        }
        path, selected_filter = QFileDialog.getSaveFileName(self, "Névjegyek exportálása", str(EXPORT_DIR), formats.get(kind, formats["auto"]))
        if not path:
            return
        suffix = Path(path).suffix.lower()
        try:
            contacts = self.store.contacts()
            if suffix == ".json" or (kind == "auto" and "JSON" in selected_filter):
                Path(path).write_text(json.dumps(self.store.data, ensure_ascii=False, indent=2), encoding="utf-8")
            elif suffix == ".csv" or (kind == "csv"):
                with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream, delimiter=";")
                    writer.writerow(["id", "display_name", "phone_number", "normalized_number", "source_type", "source_account", "email", "notes"])
                    for c in contacts:
                        phones = c.get("phone_numbers", []) or [{}]
                        emails = c.get("emails", []) or [""]
                        for p in phones:
                            writer.writerow([c.get("id", ""), c.get("display_name", ""), p.get("number", "") if isinstance(p, dict) else p, self.normalize_phone(p.get("number", "") if isinstance(p, dict) else str(p)), c.get("source_type", ""), c.get("source_account", ""), emails[0], c.get("notes", "")])
            elif suffix == ".vcf" or kind == "vcf":
                Path(path).write_text(self._to_vcf(contacts), encoding="utf-8")
            elif suffix in (".sqlite", ".db") or kind == "sqlite":
                import sqlite3
                with sqlite3.connect(path) as db:
                    db.execute("CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, display_name TEXT, source_type TEXT, source_account TEXT, data_json TEXT NOT NULL)")
                    db.execute("DELETE FROM contacts")
                    db.executemany("INSERT INTO contacts VALUES (?, ?, ?, ?, ?)", [(c.get("id", str(uuid.uuid4())), c.get("display_name", ""), c.get("source_type", ""), c.get("source_account", ""), json.dumps(c, ensure_ascii=False)) for c in contacts])
            elif suffix == ".sql" or kind == "sql":
                lines = ["-- Redmi Control Center contact export", "CREATE TABLE contacts (id TEXT PRIMARY KEY, display_name TEXT, source_type TEXT, source_account TEXT, data_json TEXT NOT NULL);"]
                for c in contacts:
                    vals = [c.get("id", ""), c.get("display_name", ""), c.get("source_type", ""), c.get("source_account", ""), json.dumps(c, ensure_ascii=False)]
                    esc = [str(v).replace("'", "''") for v in vals]
                    lines.append("INSERT INTO contacts VALUES (" + ", ".join("'" + v + "'" for v in esc) + ");")
                Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                raise ValueError("A fájlkiterjesztés nem egyezik a kiválasztott exportformátummal.")
            self.export_status.setText(f"Export elkészült: {path}")
            self.status.setText(f"Export elkészült: {path}")
            self._log(f"Export: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Exportálási hiba", str(exc))

    @staticmethod
    def _to_vcf(contacts: list[dict[str, Any]]) -> str:
        def esc(value):
            return _safe_text(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        lines = []
        for c in contacts:
            lines += ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(c.get('display_name', ''))}"]
            for p in c.get("phone_numbers", []):
                number = p.get("number", "") if isinstance(p, dict) else p
                if number:
                    lines.append(f"TEL;TYPE=CELL:{esc(number)}")
            for email in c.get("emails", []):
                if email:
                    lines.append(f"EMAIL:{esc(email)}")
            if c.get("notes"):
                lines.append(f"NOTE:{esc(c['notes'])}")
            lines.append("END:VCARD")
        return "\r\n".join(lines) + ("\r\n" if lines else "")

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Névjegyek importálása", str(Path.home()), "Névjegyfájlok (*.csv *.vcf *.json)")
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            imported = []
            if ext == ".json":
                parsed = json.loads(Path(path).read_text(encoding="utf-8"))
                imported = parsed.get("contacts", []) if isinstance(parsed, dict) else []
            elif ext == ".csv":
                with open(path, "r", encoding="utf-8-sig", newline="") as stream:
                    sample = stream.read(4096)
                    stream.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
                    except csv.Error:
                        dialect = csv.excel
                    for row in csv.DictReader(stream, dialect=dialect):
                        name = row.get("display_name") or row.get("name") or row.get("Név") or ""
                        number = row.get("phone_number") or row.get("phone") or row.get("Telefonszám") or ""
                        email = row.get("email") or row.get("E-mail") or ""
                        imported.append({"display_name": name, "phone_numbers": ([{"number": number, "normalized": self.normalize_phone(number), "label": ""}] if number else []), "emails": ([email] if email else []), "source_type": "Importált fájl", "source_account": Path(path).name, "notes": ""})
            elif ext == ".vcf":
                text = Path(path).read_text(encoding="utf-8", errors="replace")
                cards = re.split(r"BEGIN:VCARD\s*", text, flags=re.I)
                for card in cards:
                    if "END:VCARD" not in card.upper():
                        continue
                    name_match = re.search(r"(?im)^FN[^:]*:(.*)$", card)
                    phones = re.findall(r"(?im)^TEL[^:]*:(.*)$", card)
                    emails = re.findall(r"(?im)^EMAIL[^:]*:(.*)$", card)
                    notes = re.search(r"(?im)^NOTE[^:]*:(.*)$", card)
                    imported.append({"display_name": name_match.group(1).strip() if name_match else "", "phone_numbers": [{"number": p.strip(), "normalized": self.normalize_phone(p), "label": ""} for p in phones], "emails": [e.strip() for e in emails], "notes": notes.group(1).strip() if notes else "", "source_type": "Importált fájl", "source_account": Path(path).name})
            if not imported:
                QMessageBox.information(self, "Importálás", "A fájlban nem találtam importálható névjegyet.")
                return
            # Normalize/validate records and compute a cautious duplicate estimate before mutation.
            valid_imports = []
            invalid_count = 0
            existing_numbers = {self.normalize_phone(p.get("number", "") if isinstance(p, dict) else str(p)) for c in self.store.contacts() for p in c.get("phone_numbers", [])}
            potential_duplicates = 0
            seen_in_file = set()
            for record in imported:
                if not isinstance(record, dict):
                    invalid_count += 1
                    continue
                record.setdefault("source_type", "Importált fájl")
                record.setdefault("source_account", Path(path).name)
                record.setdefault("phone_numbers", [])
                record.setdefault("emails", [])
                if isinstance(record.get("phone_numbers"), (str, dict)):
                    record["phone_numbers"] = [record["phone_numbers"]]
                if not isinstance(record.get("phone_numbers"), list):
                    record["phone_numbers"] = []
                if isinstance(record.get("emails"), str):
                    record["emails"] = [record["emails"]]
                if not isinstance(record.get("emails"), list):
                    record["emails"] = []
                normalized_phones = []
                for phone in record.get("phone_numbers", []):
                    raw = phone.get("number", "") if isinstance(phone, dict) else _safe_text(phone)
                    if not _safe_text(raw).strip():
                        continue
                    canonical = self.normalize_phone(raw)
                    if canonical and (canonical in existing_numbers or canonical in seen_in_file):
                        potential_duplicates += 1
                    if canonical:
                        seen_in_file.add(canonical)
                    normalized_phones.append({"number": _safe_text(raw), "normalized": canonical, "label": _safe_text(phone.get("label", "")) if isinstance(phone, dict) else ""})
                record["phone_numbers"] = normalized_phones
                record["emails"] = [_safe_text(e).strip() for e in record.get("emails", []) if _safe_text(e).strip()]
                valid_imports.append(record)
            if not valid_imports:
                QMessageBox.warning(self, "Importálás", "Nincs érvényes névjegy a fájlban.")
                return
            preview = QDialog(self)
            preview.setWindowTitle("Importálási előnézet")
            preview.resize(900, 520)
            preview_layout = QVBoxLayout(preview)
            summary = QLabel(f"Fájl: {Path(path).name}\nRekordok: {len(valid_imports)} · Hibás rekordok kihagyva: {invalid_count} · Lehetséges telefonszám-duplikátumok: {potential_duplicates}\nAz import csak a helyi JSON-adatállományt módosítja; a telefonra nem ír automatikusan.")
            summary.setWordWrap(True)
            preview_layout.addWidget(summary)
            preview_table = QTableWidget(min(len(valid_imports), 250), 5)
            preview_table.setHorizontalHeaderLabels(["Név", "Telefonszámok", "E-mail-címek", "Forrás", "Jegyzet"] )
            for row, record in enumerate(valid_imports[:250]):
                phones = "; ".join(_safe_text(x.get("number")) for x in record.get("phone_numbers", []) if isinstance(x, dict))
                emails = "; ".join(record.get("emails", []))
                vals = [record.get("display_name", ""), phones, emails, record.get("source_type", ""), record.get("notes", "")]
                for col, value in enumerate(vals):
                    cell = QTableWidgetItem(_safe_text(value))
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    preview_table.setItem(row, col, cell)
            preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            preview_layout.addWidget(preview_table, 1)
            if len(valid_imports) > 250:
                preview_layout.addWidget(QLabel(f"Előnézetben az első 250 rekord látható; összesen {len(valid_imports)} rekord kerülhet importálásra."))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Importálás jóváhagyása")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Mégse")
            buttons.accepted.connect(preview.accept)
            buttons.rejected.connect(preview.reject)
            preview_layout.addWidget(buttons)
            if preview.exec() != QDialog.DialogCode.Accepted:
                return
            for record in valid_imports:
                self.store.add_contact(record)
            self._save_after_change(f"{len(imported)} rekord importálva: {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Importálási hiba", str(exc))

    def _duplicate_key(self, kind: str, key: str, contacts: list[dict[str, Any]]) -> str:
        ids = sorted(_safe_text(c.get("id")) for c in contacts)
        return f"{kind}|{key}|{'|'.join(ids)}"

    def find_duplicates(self):
        groups: dict[str, list[dict[str, Any]]] = {}
        for contact in self.store.contacts():
            seen_for_contact = set()
            for _raw, canonical, _label in self._contact_phone_values(contact):
                if canonical in seen_for_contact:
                    continue
                seen_for_contact.add(canonical)
                groups.setdefault(canonical, []).append(contact)
        rows = []
        for number, records in groups.items():
            unique = {c.get("id", id(c)): c for c in records}
            records = list(unique.values())
            if len(records) > 1:
                rows.append(("Erős · telefonszám", number, records))

        # Similar names with different numbers are advisory only, never auto-merged.
        name_groups: dict[str, list[dict[str, Any]]] = {}
        for contact in self.store.contacts():
            name = re.sub(r"[^a-z0-9áéíóöőúüű]", "", _safe_text(contact.get("display_name")).casefold())
            if len(name) >= 4:
                name_groups.setdefault(name, []).append(contact)
        phone_group_ids = {tuple(sorted(_safe_text(c.get("id")) for c in records)) for _, _, records in rows}
        for name, records in name_groups.items():
            unique = {c.get("id", id(c)): c for c in records}
            records = list(unique.values())
            if len(records) < 2:
                continue
            nums = {n for c in records for _, n, _ in self._contact_phone_values(c)}
            if len(nums) > 1:
                record_ids = tuple(sorted(_safe_text(c.get("id")) for c in records))
                if record_ids not in phone_group_ids:
                    rows.append(("Lehetséges · név", name, records))

        self._compare_groups = []
        self.duplicate_table.setRowCount(0)
        visible_count = 0
        for kind, key, records in sorted(rows, key=lambda r: (0 if r[0].startswith("Erős") else 1, r[1])):
            dismissal_key = self._duplicate_key(kind, key, records)
            if dismissal_key in self._dismissed_duplicate_keys:
                continue
            row = self.duplicate_table.rowCount()
            self.duplicate_table.insertRow(row)
            display_key = key if kind.startswith("Erős") else f"Név: {key}"
            names = ", ".join(_safe_text(c.get("display_name")) for c in records)
            state = "Ellenőrzendő" if kind.startswith("Erős") else "Csak javaslat"
            for col, value in enumerate((kind, display_key, str(len(records)), names, state)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, len(self._compare_groups))
                self.duplicate_table.setItem(row, col, item)
            self._compare_groups.append({"kind": kind, "key": key, "contacts": records, "dismissal_key": dismissal_key})
            visible_count += 1
        self.compare_status.setText(f"{visible_count} ellenőrzendő csoport. Az elutasított találatok rejtve maradnak, amíg a csoport adatai nem változnak.")
        for tab_index in range(self.tabs.count()):
            if self.tabs.tabText(tab_index) == "Összehasonlítás":
                self.tabs.setCurrentIndex(tab_index)
                break

    def _selected_compare_group(self):
        row = self.duplicate_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Nincs kijelölés", "Jelölj ki egy sort az összehasonlítási listában.")
            return None
        item = self.duplicate_table.item(row, 0)
        index = item.data(Qt.ItemDataRole.UserRole) if item else None
        if index is None or index >= len(getattr(self, "_compare_groups", [])):
            return None
        return self._compare_groups[index]

    def compare_selected_group(self):
        group = self._selected_compare_group()
        if not group:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Névjegyek egymás melletti összehasonlítása")
        dialog.resize(1000, 560)
        layout = QVBoxLayout(dialog)
        intro = QLabel(f"{group['kind']} · {group['key']}\nAz értékek itt csak összehasonlításra szolgálnak; módosítás vagy összevonás külön művelet.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        contacts = group["contacts"]
        fields = ["Név", "Forrás", "Fiók", "Telefonszámok (eredeti → normalizált)", "E-mail-címek", "Jegyzetek", "Rekordazonosító"]
        table = QTableWidget(len(fields), len(contacts), dialog)
        table.setVerticalHeaderLabels(fields)
        table.setHorizontalHeaderLabels([f"{c.get('display_name') or '(név nélkül)'} · {c.get('source_type', '')}" for c in contacts])
        for col, contact in enumerate(contacts):
            phones = "\n".join(f"{raw}  →  {canonical}" for raw, canonical, _ in self._contact_phone_values(contact)) or "—"
            values = [contact.get("display_name", ""), contact.get("source_type", ""), contact.get("source_account", ""), phones, "\n".join(_safe_text(e) for e in contact.get("emails", []) if _safe_text(e)) or "—", contact.get("notes", "") or "—", contact.get("id", "")]
            for row, value in enumerate(values):
                cell = QTableWidgetItem(_safe_text(value))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, cell)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setWordWrap(True)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def merge_selected_group(self):
        group = self._selected_compare_group()
        if not group:
            return
        records = group["contacts"]
        if len(records) < 2:
            QMessageBox.information(self, "Összevonás", "Ehhez a találathoz nincs legalább két rekord.")
            return
        labels = [f"{c.get('display_name', '(név nélkül)')} — {c.get('source_type', '')} / {c.get('source_account', '')} [{c.get('id', '')[:8]}]" for c in records]
        chosen_label, ok = QInputDialog.getItem(self, "Megmaradó névjegy", "Melyik rekord legyen az alaprekord? A többi rekord adatai hozzáadódnak.", labels, 0, False)
        if not ok:
            return
        keep = records[labels.index(chosen_label)]
        preview_phones = []
        for c in records:
            for raw, canonical, label in self._contact_phone_values(c):
                preview_phones.append((raw, canonical, label))
        unique_phones = {}
        for raw, canonical, label in preview_phones:
            unique_phones.setdefault(canonical, {"number": raw, "normalized": canonical, "label": label})
        emails = []
        for c in records:
            for email in c.get("emails", []):
                email = _safe_text(email).strip()
                if email and email.casefold() not in {e.casefold() for e in emails}:
                    emails.append(email)
        notes = []
        for c in records:
            note = _safe_text(c.get("notes")).strip()
            if note and note not in notes:
                notes.append(note)
        # Show exact field values and the proposed merged result before changing anything.
        dialog = QDialog(self)
        dialog.setWindowTitle("Összevonás előnézete – mezőnként")
        dialog.resize(1000, 560)
        layout = QVBoxLayout(dialog)
        notice = QLabel("A következő értékek kerülnek a helyi névjegybe. A telefonon lévő rekordokat ez a művelet nem módosítja.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        preview_table = QTableWidget(5, len(records) + 1)
        preview_table.setVerticalHeaderLabels(["Név", "Telefonszámok", "E-mail-címek", "Jegyzetek", "Forrás / fiók"])
        preview_table.setHorizontalHeaderLabels([f"Javasolt eredmény: {keep.get('display_name') or '(név nélkül)'}"] + [f"Forrás {i + 1}: {c.get('display_name') or '(név nélkül)'}" for i, c in enumerate(records)])
        proposed = [keep.get("display_name", ""), "\n".join(x["number"] for x in unique_phones.values()) or "—", "\n".join(emails) or "—", "\n\n--- Összevont jegyzetek ---\n".join(notes) or "—", f"{keep.get('source_type', '')} / {keep.get('source_account', '')}"]
        values_by_contact = []
        for c in records:
            phones_text = "\n".join(raw for raw, _canonical, _label in self._contact_phone_values(c)) or "—"
            values_by_contact.append([c.get("display_name", ""), phones_text, "\n".join(_safe_text(e) for e in c.get("emails", [])) or "—", c.get("notes", "") or "—", f"{c.get('source_type', '')} / {c.get('source_account', '')}"])
        for row, value in enumerate(proposed):
            cell = QTableWidgetItem(_safe_text(value))
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            preview_table.setItem(row, 0, cell)
        for col, values in enumerate(values_by_contact, 1):
            for row, value in enumerate(values):
                cell = QTableWidgetItem(_safe_text(value))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                preview_table.setItem(row, col, cell)
        preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        preview_table.setWordWrap(True)
        layout.addWidget(preview_table, 1)
        layout.addWidget(QLabel(f"Az eredmény tartalmaz {len(unique_phones)} telefonszámot, {len(emails)} e-mail-címet és {len(notes)} különböző jegyzetet. A jóváhagyás előtt rekord-szintű visszaállítási adat készül."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Összevonás jóváhagyása")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Mégse")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # A narrow undo record preserves unrelated edits made after the merge.
        store_before = json.loads(json.dumps(self.store.data, ensure_ascii=False))
        keeper_before = json.loads(json.dumps(keep, ensure_ascii=False))
        removed_records = [json.loads(json.dumps(c, ensure_ascii=False)) for c in records if c is not keep]
        undo_data = {
            "undo_type": "contact_merge_v1",
            "created_at": _now(),
            "keeper_id": _safe_text(keep.get("id")),
            "keeper_before": keeper_before,
            "removed_records": removed_records,
            "affected_fields": ["phone_numbers", "emails", "notes", "merged_from"],
        }
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        undo_path = BACKUP_DIR / f"pre-merge-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
        try:
            keep["phone_numbers"] = list(unique_phones.values())
            keep["emails"] = emails
            keep["notes"] = "\n\n--- Összevont jegyzetek ---\n".join(notes)
            keep["merged_from"] = sorted(set(_safe_text(x.get("id")) for x in records if x is not keep) | set(keep.get("merged_from", [])))
            undo_data["keeper_after"] = json.loads(json.dumps(keep, ensure_ascii=False))
            undo_path.write_text(json.dumps(undo_data, ensure_ascii=False, indent=2), encoding="utf-8")
            remove_ids = {c.get("id") for c in records if c is not keep}
            self.store.data["contacts"] = [c for c in self.store.contacts() if c.get("id") not in remove_ids]
            self.store.save()
            self._last_merge_undo = undo_data
            self._log(f"Kézi helyi összevonás: megtartva {keep.get('display_name')} ({keep.get('id')}); eltávolított helyi rekordok: {len(remove_ids)}; visszaállítási pont: {undo_path}")
            self.refresh_contacts()
            self._refresh_contact_suggestions()
            self.find_duplicates()
            QMessageBox.information(self, "Összevonás kész", f"A helyi JSON-adatállomány frissült.\nVisszaállítási pont: {undo_path}\nA telefonos névjegyekhez nem nyúltunk.")
        except Exception as exc:
            self.store.data = store_before
            try:
                self.store.save(backup=False)
            except Exception:
                pass
            self._last_merge_undo = None
            QMessageBox.critical(self, "Összevonási hiba", f"Az összevonás mentése sikertelen; az aktuális munkamenet adatállományát visszaállítottam.\n{exc}")

    def dismiss_selected_group(self):
        group = self._selected_compare_group()
        if not group:
            return
        answer = QMessageBox.question(self, "Nem duplikátum", "Elrejted ezt a találatot? A döntés csak erre a konkrét rekordcsoportra vonatkozik.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._dismissed_duplicate_keys.add(group["dismissal_key"])
        self.store.data.setdefault("settings", {})["dismissed_duplicate_keys"] = sorted(self._dismissed_duplicate_keys)
        self.store.save()
        self._log(f"Duplikátumtalálat elutasítva: {group['kind']} / {group['key']}")
        self.find_duplicates()

    def undo_last_merge(self):
        undo_data = self._last_merge_undo
        undo_path = None
        if undo_data is None:
            candidates = sorted(BACKUP_DIR.glob("pre-merge-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                QMessageBox.information(self, "Visszavonás", "Nincs visszaállítható összevonás ebben a munkamenetben vagy a mentések között.")
                return
            undo_path = candidates[0]
            try:
                undo_data = json.loads(undo_path.read_text(encoding="utf-8"))
            except Exception as exc:
                QMessageBox.warning(self, "Visszaállítási hiba", str(exc))
                return
        if isinstance(undo_data, dict) and undo_data.get("undo_type") == "contact_merge_v1":
            keeper_id = undo_data.get("keeper_id")
            contacts = self.store.contacts()
            keeper = next((c for c in contacts if _safe_text(c.get("id")) == keeper_id), None)
            if keeper is None:
                QMessageBox.warning(self, "Visszavonás", "A megmaradó rekord már nem található. Nem változtattam az adatállományon.")
                return
            fields = undo_data.get("affected_fields", [])
            after = undo_data.get("keeper_after", {})
            changed_since_merge = [field for field in fields if keeper.get(field) != after.get(field)]
            if changed_since_merge:
                QMessageBox.warning(self, "Visszavonás megszakítva", "Az összevonás óta ezek a mezők módosultak: " + ", ".join(changed_since_merge) + ". A későbbi adatokat nem írom felül. Előbb rendezd a konfliktust, vagy készíts biztonsági másolatot.")
                return
            answer = QMessageBox.question(self, "Összevonás visszavonása", "Csak az összevonással érintett névjegyeket állítom helyre. A többi névjegy és a közben végzett, nem érintett módosítások megmaradnak. Folytatod?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            before = undo_data.get("keeper_before", {})
            for field in fields:
                if field in before:
                    keeper[field] = json.loads(json.dumps(before[field], ensure_ascii=False))
                else:
                    keeper.pop(field, None)
            existing_ids = {_safe_text(c.get("id")) for c in contacts}
            for removed in undo_data.get("removed_records", []):
                if _safe_text(removed.get("id")) not in existing_ids:
                    contacts.append(json.loads(json.dumps(removed, ensure_ascii=False)))
            self.store.data["contacts"] = contacts
        else:
            # Legacy merge snapshots are full data files. Keep a warning because they can
            # overwrite changes made after the snapshot was created.
            answer = QMessageBox.question(self, "Régi összevonási mentés", "Ez egy régi, teljes adatállomány-pillanatkép. A későbbi módosítások elveszhetnek. Biztosan visszaállítod?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.store.data = json.loads(json.dumps(undo_data, ensure_ascii=False))
        try:
            self.store.save(backup=False)
            self._last_merge_undo = None
            self.refresh_contacts()
            self._refresh_contact_suggestions()
            self.find_duplicates()
            self._log("Az utolsó kézi összevonás visszavonva; a nem érintett helyi rekordok megmaradtak.")
            QMessageBox.information(self, "Visszavonás", "Az összevonás visszavonása befejeződött.")
        except Exception as exc:
            QMessageBox.critical(self, "Visszaállítási hiba", f"A visszaállítás mentése nem sikerült: {exc}")

    # ---- Audio recording: scrcpy for phone, FFmpeg/PulseAudio for Linux ----
    def toggle_recording(self):
        if self._recording_active:
            self._stop_recording()
            return
        if self._playback_process is not None and self._playback_process.poll() is None:
            QMessageBox.information(self, "Hangrögzítés", "Új felvétel indítása előtt állítsd le a lejátszást.")
            return
        device = self.record_device_combo.currentText()
        phone_mode = self.phone_audio_mode_combo.currentData() or "output"
        source_desc = ("Telefon – lejátszott hang" if phone_mode == "output" else "Telefon – mikrofon") if device == "Telefon" else f"Linuxos gép – {self.pc_audio_source_combo.currentText()}"
        if self.pre_record_warning.isChecked():
            answer = QMessageBox.question(self, "Hangrögzítés indítása", f"Elindítod a hangrögzítést?\n\nForrás: {source_desc}\n\nA telefon lejátszott hangja és a telefon mikrofonja két külön mód; ezeket nem keverjük össze. Az Android/scrcpy verziója korlátozhatja a rögzítést.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        folder = Path(self.record_folder.text().strip() or RECORDINGS_DIR).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fmt = self.record_format_combo.currentText()
            ext = {"WAV": ".wav", "FLAC": ".flac", "M4A / AAC": ".m4a"}.get(fmt, ".wav")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = folder / f"felvetel-{stamp}{ext}"
            if path.exists():
                path = folder / f"felvetel-{stamp}-{uuid.uuid4().hex[:6]}{ext}"
            if device == "Telefon":
                self._start_phone_recording(path, phone_mode)
                backend = "scrcpy"
            else:
                self._start_pc_recording(path)
                backend = "FFmpeg/PulseAudio"
            self._recording_path = path
            self._recording_started = time.monotonic()
            self._recording_active = True
            self._recording_paused = False
            if hasattr(self, "delete_recording_button"):
                self.delete_recording_button.setEnabled(False)
            self._recording_elapsed_before_pause = 0.0
            self.record_button.setText("■ Leállítás")
            self.record_button.setStyleSheet("QPushButton { background: #b51f2b; color: white; font-weight: bold; }")
            self.pause_button.setEnabled(True)
            self.pause_button.setText("Ⅱ Szünet")
            self.audio_led.setObjectName("audioLedRecording")
            self.audio_led.setStyleSheet("QLabel { color: #ff5252; font-weight: bold; }")
            self.audio_status.setText(f"● RÖGZÍTÉS 00:00 · {source_desc}")
            self._log(f"Hangrögzítés elindult ({backend}): forrás={source_desc}; fájl={path}")
            self.store.data.setdefault("recording_index", []).append({"id": str(uuid.uuid4()), "path": str(path), "source": source_desc, "backend": backend, "started_at": _now(), "status": "recording"})
            try:
                self.store.save()
            except Exception as exc:
                self._log(f"Felvételindex mentése sikertelen: {exc}", "WARNING")
        except Exception as exc:
            self._recording_process = None
            QMessageBox.critical(self, "Hangrögzítési hiba", f"Nem sikerült elindítani a rögzítést.\n\n{exc}\n\nEllenőrizd, hogy az ffmpeg/scrcpy telepítve van-e, a forrás elérhető-e, és a telefon engedélyezte-e a hangrögzítést.")
            self._log(f"Hangrögzítés indítása sikertelen: {exc}", "ERROR")

    def _start_phone_recording(self, path: Path, phone_mode: str):
        scrcpy = shutil.which("scrcpy")
        if not scrcpy:
            raise RuntimeError("A scrcpy parancs nem található a PATH-ban.")
        serial = self.selected_serial()
        if not serial:
            raise RuntimeError("Nincs kiválasztott ADB-eszköz.")
        # The two phone sources are intentionally exclusive: playback output OR microphone.
        source = "output" if phone_mode == "output" else "mic"
        args = [scrcpy, "--serial", serial, "--no-video", "--no-window", "--audio-source", source, "--record", str(path)]
        self._recording_process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.25)
        if self._recording_process.poll() is not None:
            code = self._recording_process.returncode
            self._recording_process = None
            raise RuntimeError(f"A scrcpy azonnal leállt (kilépési kód: {code}). A telepített verzió lehet, hogy nem támogatja a kért audio-only rögzítést vagy a megadott opciókat.")

    def _start_pc_recording(self, path: Path):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Az FFmpeg nem található. Telepítés: sudo apt install ffmpeg.")
        selected = self.pc_audio_source_combo.currentData() or "jack::default"
        if "::" in selected:
            backend, source = selected.split("::", 1)
        else:
            backend, source = "pulse", selected
        rate = self.sample_rate.currentText()
        channels = "1" if self.channels_combo.currentText() == "Mono" else "2"
        jack_sources = []
        if backend == "jackpair":
            backend = "jack"
            jack_sources = source.split("|", 1)
            if len(jack_sources) != 2:
                raise RuntimeError("Érvénytelen JACK sztereó portpár.")
        elif backend == "jack" and source != "default":
            jack_sources = [source]
        if backend == "jack":
            input_format = "jack"
            source = "redmi-control-center"
        elif backend == "alsa":
            input_format = "alsa"
        else:
            input_format = "pulse"
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", input_format, "-i", source, "-ar", rate, "-ac", channels, str(path)]
        self._recording_process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.25)
        if self._recording_process.poll() is not None:
            code = self._recording_process.returncode
            self._recording_process = None
            raise RuntimeError(f"Az FFmpeg azonnal leállt (kilépési kód: {code}). Ellenőrizd a kiválasztott {input_format} bemenetet és azt, hogy az FFmpeg build támogatja-e ezt a hangrendszert.")
        if backend == "jack" and jack_sources:
            jack_connect = shutil.which("jack_connect")
            if not jack_connect:
                self._recording_process.terminate()
                self._recording_process = None
                raise RuntimeError("A jack_connect parancs nem található. Telepítsd a JACK klienseszközöket, majd próbáld újra.")
            try:
                time.sleep(0.3)
                targets = ["redmi-control-center:input_1"]
                if len(jack_sources) == 2 and channels == "2":
                    targets.append("redmi-control-center:input_2")
                for idx, jack_source in enumerate(jack_sources):
                    target = targets[min(idx, len(targets) - 1)]
                    connected = subprocess.run([jack_connect, jack_source, target], capture_output=True, text=True, timeout=3, check=False)
                    if connected.returncode != 0:
                        self._recording_process.terminate()
                        self._recording_process = None
                        raise RuntimeError("Nem sikerült a JACK portot az FFmpeg bemenetére kötni: " + (connected.stderr.strip() or connected.stdout.strip() or f"kilépési kód {connected.returncode}"))
                if len(jack_sources) == 1 and channels == "2":
                    self._log("Figyelem: csak egy JACK port van kiválasztva sztereó felvételhez; a felvétel kétcsatornás fájl lehet, de valódi sztereó térhatás nem lesz.", "WARNING")
            except Exception:
                if self._recording_process is not None and self._recording_process.poll() is None:
                    self._recording_process.terminate()
                self._recording_process = None
                raise

    def _stop_recording(self):
        process = self._recording_process
        if process is not None and process.poll() is None:
            try:
                if self._recording_paused:
                    process.send_signal(signal.SIGCONT)
                process.terminate()
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except Exception as exc:
                self._log(f"Hiba a rögzítő leállításakor: {exc}", "ERROR")
        self._recording_process = None
        self._recording_active = False
        self._recording_paused = False
        self._recording_elapsed_before_pause = 0.0
        self._recording_started = None
        self.record_button.setText("● Felvétel")
        self.record_button.setStyleSheet("")
        self.pause_button.setEnabled(self._playback_process is not None and self._playback_process.poll() is None)
        self.pause_button.setText("Ⅱ Szünet")
        path = self._recording_path
        self.audio_led.setObjectName("audioLedIdle")
        self.audio_led.setStyleSheet("QLabel { color: #aab3bf; }")
        self.audio_status.setText("● Készenlét · felvétel leállítva" if path else "● Készenlét")
        if path:
            try:
                size = path.stat().st_size if path.exists() else 0
                self._log(f"Hangrögzítés leállítva: {path}; méret={size} bájt")
                for item in reversed(self.store.data.get("recording_index", [])):
                    if item.get("path") == str(path) and item.get("status") == "recording":
                        item["status"] = "completed" if size > 0 else "empty_or_failed"
                        item["finished_at"] = _now()
                        item["size_bytes"] = size
                        break
                self.store.save()
            except Exception as exc:
                self._log(f"Felvétel befejezési adatainak mentése sikertelen: {exc}", "WARNING")
        self._recording_path = None
        self.refresh_recording_history()

    def toggle_audio_pause(self):
        # This button must only pause/resume. It must never stop or finalize a recording.
        # Stopping is exclusively handled by record_button / toggle_recording().
        if self._recording_active:
            process = self._recording_process
            if process is None:
                self._log("Szünet/folytatás kérve, de nincs rögzítőfolyamat.", "WARNING")
                return
            if process.poll() is not None:
                # A real process exit is an error/end condition, not a pause action.
                code = process.returncode
                self._log(f"A rögzítőfolyamat már befejeződött (kilépési kód: {code}).", "ERROR")
                self._stop_recording()
                return
            try:
                if self._recording_paused:
                    # Repeated press while paused is explicitly RESUME.
                    process.send_signal(signal.SIGCONT)
                    self._recording_started = time.monotonic()
                    self._recording_paused = False
                    self.pause_button.setText("Ⅱ Szünet")
                    self.audio_led.setObjectName("audioLedRecording")
                    self.audio_led.setStyleSheet("QLabel { color: #ff5252; font-weight: bold; }")
                    self.audio_status.setText("● RÖGZÍTÉS folytatódik")
                    self._log("A hangrögzítés folytatódik ugyanabba a fájlba.")
                else:
                    process.send_signal(signal.SIGSTOP)
                    if self._recording_started:
                        self._recording_elapsed_before_pause += time.monotonic() - self._recording_started
                    self._recording_started = None
                    self._recording_paused = True
                    self.pause_button.setText("▶ Folytatás")
                    self.audio_led.setObjectName("audioLedPaused")
                    self.audio_led.setStyleSheet("QLabel { color: #ffca58; font-weight: bold; }")
                    self.audio_status.setText("Ⅱ FELVÉTEL SZÜNETEL · ugyanabba a fájlba folytatható")
                    self._log("A hangrögzítés szünetel; új fájl nem jött létre.")
            except Exception as exc:
                QMessageBox.warning(self, "Szüneteltetési hiba", f"A felvétel szüneteltetése/folytatása nem sikerült:\n{exc}")
            return
        process = self._playback_process
        if process is not None and process.poll() is None:
            try:
                if not self._playback_paused:
                    process.send_signal(signal.SIGSTOP)
                    self._playback_paused = True
                    self.pause_button.setText("▶ Folytatás")
                    self.audio_led.setObjectName("audioLedPlaybackPaused")
                    self.audio_led.setStyleSheet("QLabel { color: #ffca58; font-weight: bold; }")
                    self.audio_status.setText("Ⅱ LEJÁTSZÁS SZÜNETEL")
                else:
                    process.send_signal(signal.SIGCONT)
                    self._playback_paused = False
                    self.pause_button.setText("Ⅱ Szünet")
                    self.audio_led.setObjectName("audioLedPlayback")
                    self.audio_led.setStyleSheet("QLabel { color: #5ca9ff; font-weight: bold; }")
                    self.audio_status.setText("▶ LEJÁTSZÁS")
            except Exception as exc:
                QMessageBox.warning(self, "Lejátszási hiba", f"A lejátszás szüneteltetése/folytatása nem sikerült:\n{exc}")

    def refresh_recording_history(self):
        if not hasattr(self, "recording_history_combo"):
            return
        folder = Path(self.record_folder.text().strip() or RECORDINGS_DIR).expanduser()
        current = self.recording_history_combo.currentData()
        self.recording_history_combo.blockSignals(True)
        self.recording_history_combo.clear()
        files = []
        try:
            if folder.exists():
                files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".wav", ".flac", ".ogg", ".mp3", ".m4a"}), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError as exc:
            self._log(f"A felvételi előzmények listázása sikertelen: {exc}", "WARNING")
        for path in files:
            self.recording_history_combo.addItem(path.name, str(path))
        if current:
            idx = self.recording_history_combo.findData(current)
            if idx >= 0:
                self.recording_history_combo.setCurrentIndex(idx)
        self.recording_history_combo.blockSignals(False)
        if self.recording_history_combo.count():
            self.recording_history_combo.setToolTip(str(self.recording_history_combo.currentData()))
        else:
            self.recording_history_combo.addItem("(nincs még felvétel)", "")
            self.recording_history_combo.setEnabled(False)
        if files:
            self.recording_history_combo.setEnabled(True)
        if hasattr(self, "delete_recording_button"):
            selected = self.recording_history_combo.currentData()
            self.delete_recording_button.setEnabled(bool(selected) and not self._recording_active)

    def _recording_history_changed(self, *_):
        path = self.recording_history_combo.currentData()
        self.recording_history_combo.setToolTip(str(path or ""))
        if hasattr(self, "delete_recording_button"):
            playback_running = self._playback_process is not None and self._playback_process.poll() is None
            playing_path = getattr(self, "_playback_path", None)
            is_current_playback = bool(path and playing_path and Path(path) == Path(playing_path) and playback_running)
            self.delete_recording_button.setEnabled(bool(path) and not self._recording_active and not is_current_playback)

    def move_recording_to_trash(self):
        """Move the selected recording to the desktop environment's Trash; never permanently unlink it."""
        raw_path = self.recording_history_combo.currentData() if hasattr(self, "recording_history_combo") else None
        if not raw_path:
            QMessageBox.information(self, "Felvétel törlése", "Nincs kiválasztott felvétel.")
            return
        path = Path(str(raw_path)).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            QMessageBox.warning(self, "Felvétel törlése", "A kiválasztott fájl már nem található. Frissítsd a felvétellistát.")
            self.refresh_recording_history()
            return
        if not resolved.is_file():
            QMessageBox.warning(self, "Felvétel törlése", "A kiválasztott elem nem fájl.")
            return
        if self._recording_active and self._recording_path and resolved == Path(self._recording_path).resolve():
            QMessageBox.warning(self, "Felvétel törlése", "Az éppen rögzített fájlt nem lehet a Kukába helyezni. Előbb állítsd le a felvételt.")
            return
        playback_running = self._playback_process is not None and self._playback_process.poll() is None
        playing_path = getattr(self, "_playback_path", None)
        if playback_running and playing_path and resolved == Path(playing_path).resolve():
            QMessageBox.warning(self, "Felvétel törlése", "A lejátszás alatt álló fájlt nem lehet a Kukába helyezni. Előbb állítsd le a lejátszást.")
            return
        answer = QMessageBox.question(
            self, "Felvétel áthelyezése a Kukába",
            f"Áthelyezed a rendszer Kukájába ezt a felvételt?\n\n{resolved.name}\n\nA fájl nem törlődik véglegesen; a rendszer Kukájából visszaállítható lehet.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            moved = False
            try:
                from send2trash import send2trash
                send2trash(str(resolved))
                moved = True
            except ImportError:
                gio = shutil.which("gio")
                if gio:
                    result = subprocess.run([gio, "trash", str(resolved)], capture_output=True, text=True, timeout=15)
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout or "ismeretlen gio hiba").strip()
                        raise RuntimeError(detail)
                    moved = True
            if not moved:
                QMessageBox.warning(
                    self, "A rendszer Kukája nem érhető el",
                    "Nem található sem a Python send2trash modul, sem a gio parancs. A fájl biztonságából nem engedünk: nem töröltük véglegesen.\n\nTelepíthető megoldás Debian/AV Linux alatt: sudo apt install libglib2.0-bin vagy a Python send2trash csomag telepítése."
                )
                return
            # Mark matching index entries as trashed instead of keeping them as active recordings.
            for item in self.store.data.get("recording_index", []):
                try:
                    if Path(str(item.get("path", ""))).resolve() == resolved:
                        item["status"] = "trashed"
                        item["trashed_at"] = _now()
                except (OSError, RuntimeError):
                    continue
            try:
                self.store.save()
            except Exception as exc:
                self._log(f"A Kukába helyezett felvétel indexének frissítése sikertelen: {exc}", "WARNING")
            self._log(f"Felvétel a rendszer Kukájába helyezve: {resolved}")
            self.refresh_recording_history()
            self.audio_status.setText(f"● Készenlét · Kukába helyezve: {resolved.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Felvétel áthelyezése sikertelen", f"A fájlt nem sikerült a rendszer Kukájába helyezni. Nem töröltük véglegesen.\n\n{exc}")
            self._log(f"Felvétel Kukába helyezése sikertelen: {resolved}: {exc}", "ERROR")

    def toggle_playback(self):
        if self._recording_active:
            QMessageBox.information(self, "Lejátszás", "Lejátszás előtt állítsd le a felvételt.")
            return
        process = self._playback_process
        if process is not None and process.poll() is None:
            try:
                if self._playback_paused:
                    process.send_signal(signal.SIGCONT)
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try: process.kill()
                except Exception: pass
            self._playback_process = None
            self._playback_path = None
            self._playback_paused = False
            self.play_button.setText("▶ Lejátszás")
            self.pause_button.setEnabled(False)
            self.pause_button.setText("Ⅱ Szünet")
            self.audio_led.setObjectName("audioLedIdle")
            self.audio_led.setStyleSheet("QLabel { color: #aab3bf; }")
            self.audio_status.setText("● Készenlét · lejátszás leállítva")
            return
        path = self.recording_history_combo.currentData() if hasattr(self, "recording_history_combo") else None
        if not path or not Path(path).is_file():
            QMessageBox.information(self, "Lejátszás", "Nincs kiválasztott, lejátszható felvétel.")
            return
        ffplay = shutil.which("ffplay")
        if not ffplay:
            QMessageBox.warning(self, "Lejátszás", "A beépített lejátszáshoz az ffplay szükséges. Telepítés: sudo apt install ffmpeg")
            return
        try:
            self._playback_process = subprocess.Popen([ffplay, "-nodisp", "-autoexit", "-loglevel", "error", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self._playback_path = str(path)
            self._playback_paused = False
            self.play_button.setText("■ Leállítás")
            self.pause_button.setEnabled(True)
            self.pause_button.setText("Ⅱ Szünet")
            self.audio_led.setObjectName("audioLedPlayback")
            self.audio_led.setStyleSheet("QLabel { color: #5ca9ff; font-weight: bold; }")
            self.audio_status.setText(f"▶ LEJÁTSZÁS · {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "Lejátszási hiba", str(exc))

    def _refresh_recording_clock(self):
        if self._playback_process is not None and self._playback_process.poll() is not None:
            self._playback_process = None
            self._playback_path = None
            self._playback_paused = False
            self.play_button.setText("▶ Lejátszás")
            self.pause_button.setEnabled(self._recording_active)
            self.pause_button.setText("Ⅱ Szünet")
            if not self._recording_active:
                self.audio_led.setObjectName("audioLedIdle")
                self.audio_led.setStyleSheet("QLabel { color: #aab3bf; }")
                self.audio_status.setText("● Készenlét · lejátszás befejeződött")
        if not self._recording_active:
            return
        process = self._recording_process
        if process is not None and process.poll() is not None:
            code = process.returncode
            self._log(f"A rögzítőfolyamat váratlanul leállt (kilépési kód: {code}).", "ERROR")
            self._stop_recording()
            return
        if self._recording_paused:
            elapsed = int(self._recording_elapsed_before_pause)
            self.audio_status.setText(f"Ⅱ SZÜNETEL · {elapsed // 60:02d}:{elapsed % 60:02d}")
        else:
            elapsed = int(self._recording_elapsed_before_pause + (time.monotonic() - self._recording_started if self._recording_started else 0))
            self.audio_status.setText(f"● RÖGZÍTÉS · {elapsed // 60:02d}:{elapsed % 60:02d}")

    def open_recordings(self):
        # Compatibility action: select a file in the history control, then play it
        # through the inline player instead of opening an unrelated desktop window.
        self.refresh_recording_history()
        if self.recording_history_combo.count() <= 0 or not self.recording_history_combo.currentData():
            QMessageBox.information(self, "Felvételek", "Ebben a könyvtárban még nincs felvétel.")
            return
        self.toggle_playback()

    def choose_record_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Hangfelvételek mentési könyvtára", self.record_folder.text())
        if folder:
            self.record_folder.setText(folder)

    def _set_docked_dialer_preference(self, tab_name, enabled):
        self.docked_dialer_tab_checks[tab_name] = bool(enabled)
        self._update_docked_dialer_visibility()

    def save_module_settings(self):
        module_settings = self.store.data.setdefault("settings", {})
        try:
            country_codes = self._country_table_data()
            module_settings.update({
                "record_device": self.record_device_combo.currentText(),
                "phone_audio_mode": self.phone_audio_mode_combo.currentData() or "output",
                "pc_audio_backend": self.pc_audio_backend_combo.currentData() or "jack",
                "pc_audio_source": self.pc_audio_source_combo.currentData() or "jack::default",
                "record_format": self.record_format_combo.currentText(),
                "record_folder": self.record_folder.text().strip(),
                "sample_rate": self.sample_rate.currentText(),
                "channels": self.channels_combo.currentText(),
                "pre_record_warning": self.pre_record_warning.isChecked(),
                "country_codes": country_codes,
                "default_country_code": self._country_code(),
                "auto_country_code": self.auto_country_code.isChecked(),
                "keep_original_phone": self.keep_original_phone.isChecked(),
                "docked_dialer_tabs": dict(self.docked_dialer_tab_checks),
                "dismissed_duplicate_keys": sorted(self._dismissed_duplicate_keys),
            })
            # Rebuild comparison forms using the current country-code preference;
            # the original number string is preserved for display and audit.
            for contact in self.store.contacts():
                for phone in contact.get("phone_numbers", []):
                    if isinstance(phone, dict):
                        phone["normalized"] = self.normalize_phone(phone.get("number", ""))
            self.store.save()
            self._refresh_contact_suggestions()
            self.find_duplicates()
            self.status.setText("Modulbeállítások elmentve a JSON-adatállományba.")
            self._log("Modulbeállítások mentve.")
        except Exception as exc:
            QMessageBox.critical(self, "Beállításmentési hiba", str(exc))

    # ---- Misc ----
    def _build_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel("A modul műveletei és hibái. Minden munkamenet külön, dátum- és időbélyeges fájlba kerül.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.module_log = QPlainTextEdit()
        self.module_log.setReadOnly(True)
        self.module_log.setPlaceholderText("Műveletek és hibák naplója…")
        layout.addWidget(self.module_log, 1)
        row = QHBoxLayout()
        path_label = QLabel(f"Naplófájl: {self.log_file}")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(path_label, 1)
        open_button = QPushButton("Naplófájl megnyitása…")
        open_button.clicked.connect(self.open_log_file)
        row.addWidget(open_button)
        clear_button = QPushButton("Képernyő ürítése")
        clear_button.clicked.connect(self.module_log.clear)
        row.addWidget(clear_button)
        layout.addLayout(row)
        self.tabs.addTab(page, "Programnapló")
        self._log("Naplózás elindult.")

    def _log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] [{level.upper()}] {message}"
        self._log_lines.append(line)
        if hasattr(self, "module_log"):
            self.module_log.appendPlainText(line)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError as exc:
            if hasattr(self, "status"):
                self.status.setText(f"Naplóírási hiba: {exc}")

    def open_log_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Naplófájl megnyitása", str(LOG_DIR), "Naplófájlok (*.log);;Minden fájl (*)")
        if path:
            try:
                self.module_log.setPlainText(Path(path).read_text(encoding="utf-8"))
                self.tabs.setCurrentIndex(7)
            except OSError as exc:
                QMessageBox.warning(self, "Naplómegnyitási hiba", str(exc))

    def show_help(self):
        """Részletes, lapokra bontott használati útmutató a csatlakozáshoz és a fő műveletekhez."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Névjegykezelő – használati útmutató")
        dialog.resize(760, 620)
        outer = QVBoxLayout(dialog)
        tabs = QTabWidget(dialog)

        sections = [
            ("Csatlakozás", """
                <h2>Telefon csatlakoztatása</h2>
                <ol>
                  <li>A telefonon engedélyezd a <b>Fejlesztői beállítások → USB-hibakeresés</b> lehetőséget.</li>
                  <li>Csatlakoztasd USB-kábellel a telefont a Linuxos géphez, majd oldd fel a telefon képernyőjét.</li>
                  <li>Ha a telefon engedélykérést mutat az RSA-kulcsról, ellenőrizd a számítógép megbízhatóságát, majd fogadd el.</li>
                  <li>A modul felső csatlakozási sorában kattints az <b>Eszközlista frissítése</b> gombra, és válaszd ki a <b>device</b> állapotú készüléket.</li>
                </ol>
                <h3>Állapotok</h3>
                <ul>
                  <li><b>device:</b> az ADB-kapcsolat engedélyezett.</li>
                  <li><b>unauthorized:</b> a telefonon még jóvá kell hagyni az USB-hibakeresést.</li>
                  <li><b>offline:</b> az eszköz látható, de jelenleg nem válaszol; húzd ki és csatlakoztasd újra, majd frissíts.</li>
                  <li><b>ADB-hiba:</b> ellenőrizd, hogy az <code>adb</code> telepítve van-e, elérhető-e a PATH-ban, és működik-e az ADB-szolgáltatás. A részleteket a Programnaplóban keresd.</li>
                </ul>
                <p>Telefonos műveletekhez engedélyezett ADB-eszköz szükséges. Ha több készülék csatlakozik, mindig ellenőrizd a kiválasztott sorozatszámot.</p>
            """),
            ("Névjegyek", """
                <h2>Névjegyek kezelése</h2>
                <ul>
                  <li>A <b>Névjegyek</b> fülön a kereső név, telefonszám vagy e-mail alapján szűr.</li>
                  <li>A <b>Források</b> gombbal a helyi listában látható névjegyek forrása szűrhető.</li>
                  <li>Az <b>Új névjegy</b> helyi rekordot hoz létre. A helyi mentés önmagában nem jelenti azt, hogy a telefonon is létrejött a névjegy.</li>
                  <li>A <b>Telefon névjegyeinek beolvasása</b> a telefonról elérhető adatokat próbálja beolvasni a helyi tárba. Ellenőrizd az eredményt és a duplikátumokat; a beolvasás nem teljes kétirányú szinkronizálás.</li>
                  <li>A táblázatban több sor is kijelölhető. A helyi rekord törlése nem törli automatikusan a telefon névjegyét.</li>
                </ul>
                <h3>Duplikátumok</h3>
                <p>Az <b>Összehasonlítás</b> fülön ellenőrizd az egyező telefonszámokat és a lehetséges névegyezéseket. Összevonás előtt vizsgáld meg az adatokat; a téves találatot utasítsd el. Az összevonás helyi adatokat módosít, nem végez automatikus telefonos törlést.</p>
            """),
            ("Tárcsázó és SMS", """
                <h2>Tárcsázó használata</h2>
                <p>Írj be egy telefonszámot, vagy válassz névjegyjavaslatot. A számgombok és a billentyűzet egyaránt használhatók. A <b>Hívás előkészítése</b> megerősítés után a telefon saját tárcsázóját nyitja meg a számmal; a program nem indít észrevétlenül hívást.</p>
                <p>A jobb oldali tárcsázópanel a Beállítások → Felület lapon szabályozható. A számmezők egymással szinkronban vannak.</p>
                <h2>SMS</h2>
                <p>Írd be a címzettet és az üzenetet, majd válaszd az átadást. A telefon saját üzenetíró alkalmazása nyílik meg; az üzenet ott ellenőrizhető és küldhető el. A státusz, hogy a piszkozat átadva, <b>nem jelenti azt, hogy az SMS el is ment</b>. A modul nem küld közvetlenül SMS-t.</p>
                <h2>Hívásnapló</h2>
                <p>A napló helyi bejegyzéseket kezel. A telefon tényleges hívásnaplójának hozzáférése Android-verziótól és jogosultságoktól függ; ellenőrizd a forrást és a bejegyzés adatait.</p>
            """),
            ("Fájlok és mentés", """
                <h2>Importálás, exportálás és mentés</h2>
                <ul>
                  <li>A <b>Fájl → JSON betöltése</b> másik helyi adatállományt tölt be; betöltés előtt ellenőrizd, hogy a megfelelő fájlt választottad-e.</li>
                  <li>A <b>JSON mentése</b> elmenti a helyi adatállományt.</li>
                  <li>Az <b>Importálás</b> CSV, VCF vagy JSON fájl feldolgozását indítja. Nézd át az előnézetet, a mező-hozzárendelést és a figyelmeztetéseket, mielőtt jóváhagyod.</li>
                  <li>Az <b>Exportálás</b> külső fájlt készít. Export után ellenőrizd a célkönyvtárat és a fájl tartalmát.</li>
                  <li>A <b>Biztonsági mentés most</b> külön másolatot készít a helyi adatokról. Jelentős importálás vagy összevonás előtt ajánlott menteni.</li>
                </ul>
                <p>Az import/export formátumok mezőtámogatása eltérhet. Fontos adatállományon végzett művelet előtt készíts biztonsági másolatot.</p>
            """),
            ("Hangrögzítés", """
                <h2>Hangrögzítés</h2>
                <p>A felső <b>Hangrögzítés indítása</b> gomb a Beállítások → Hangrögzítés lapon kiválasztott forrást használja. Egyszerre csak egy forrás választható: a telefon vagy a Linuxos gép.</p>
                <ul>
                  <li><b>Telefon – lejátszott hang:</b> a telefon lejátszott hangját próbálja rögzíteni a scrcpy segítségével.</li>
                  <li><b>Telefon – mikrofon:</b> a telefon mikrofonját próbálja rögzíteni. Ez nem azonos a lejátszott hang rögzítésével.</li>
                  <li><b>Linuxos gép:</b> az FFmpeg a kiválasztott PulseAudio-kompatibilis hangforrásból rögzít, PipeWire/PulseAudio környezetben.</li>
                </ul>
                <p>Telefonos rögzítéshez a scrcpy kompatibilis verziója, engedélyezett ADB-kapcsolat és Android-támogatás szükséges. A mikrofon és a belső hang egyidejű rögzítése nem garantált; a két telefonos mód külön választható. Linuxos forrás hiányában ellenőrizd a hangkiszolgálót, a <code>pactl</code> elérhetőségét és az FFmpeg pulse bemeneti támogatását.</p>
                <p>A <b>Felvételek / visszajátszás</b> gombbal nyithatók meg a rögzített fájlok. A rögzítés leállítása után ellenőrizd, hogy a fájl létrejött-e és lejátszható-e.</p>
            """),
            ("Beállítások és hibakeresés", """
                <h2>Beállítások</h2>
                <ul>
                  <li><b>Hangrögzítés:</b> forrás, telefonos hangmód, Linuxos hangforrás, formátum, mintavételezés, csatornák és mentési könyvtár.</li>
                  <li><b>Telefonszámok:</b> az alapértelmezett országkód és az országkód-táblázat. A nemzetközi formátumú, <code>+</code> jellel kezdődő számokat a normalizálás nem alakítja át más országra.</li>
                  <li><b>Felület:</b> a jobb oldali tárcsázópanel megjelenítése a kiválasztott füleken.</li>
                </ul>
                <h2>Ha valami nem működik</h2>
                <ol>
                  <li>Olvasd el a felületen megjelenő állapotüzenetet.</li>
                  <li>Nyisd meg a <b>Programnapló</b> fület, és keresd meg az időben odaillő hibaüzenetet.</li>
                  <li>Telefonos funkciónál frissítsd az ADB-eszközlistát, és ellenőrizd, hogy az eszköz <code>device</code> állapotú-e.</li>
                  <li>Hangrögzítésnél ellenőrizd a választott forrást, a szükséges program telepítését és a mentési könyvtár írhatóságát.</li>
                  <li>Hiba bejelentésekor másold ki a teljes hibaüzenetet, az Android-verziót, a scrcpy/FFmpeg verziót és a releváns naplórészletet. Jelszót vagy privát telefonszámokat ne küldj a naplóval együtt.</li>
                </ol>
                <p><b>Adatvédelmi megjegyzés:</b> a helyi névjegyadatok és a rögzített hangfájlok a számítógépen tárolódnak a beállított adatkönyvtárban. Gondoskodj a mentések védelméről.</p>
            """),
        ]
        for title, html in sections:
            browser = QTextBrowser(dialog)
            browser.setOpenExternalLinks(False)
            browser.setHtml(html)
            browser.setReadOnly(True)
            browser.setStyleSheet("QTextBrowser { padding: 10px; }")
            tabs.addTab(browser, title)
        outer.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        outer.addWidget(buttons)
        dialog.exec()

    def show_module_status(self):
        QMessageBox.information(
            self,
            "Névjegykezelő állapota és korlátai",
            "A modul helyi JSON-adatkezelést, CSV/VCF/JSON import-exportot, ADB-alapú telefonos tárcsázó- és SMS-piszkozat-átadást, országkód-táblázatot, importálási előnézetet és hangrögzítési indítási folyamatot tartalmaz.\n\n"
            "A telefonos névjegy- és hanghozzáférés az Android-verziótól, a jogosultságoktól és a scrcpy verziójától függ. A közvetlen SMS-küldés és a teljes kétirányú Android-névjegyszinkronizálás nem támogatott. A hangrögzítést a tényleges eszközön kell ellenőrizni."
        )

    def _request_close(self):
        self.close()

    def closeEvent(self, event):
        if self._recording_active:
            answer = QMessageBox.question(self, "Hangrögzítés folyamatban", "A felvétel állapota aktív. Bezárjuk a modult?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._stop_recording()
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if hasattr(self, "_recording_clock_timer"):
            self._recording_clock_timer.stop()
        for worker in (self._poll_worker, self._device_worker):
            if worker is not None and worker.isRunning():
                worker.wait(9000)
        event.accept()


def open_module(parent=None, settings: dict | None = None):
    """Return a QWidget page compatible with main.py's QStackedWidget integration."""
    widget = ContactsWidget(parent=parent, settings=settings)
    return widget


def main():
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle(APP_NAME)
    window.resize(1200, 780)
    widget = ContactsWidget(settings={})
    window.setCentralWidget(widget)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
