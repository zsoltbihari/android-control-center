#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center – Beállítások párbeszédablak.

A main.py mellé szánt önálló modul. A PreferencesDialog(settings, parent)
és get_settings() interfészt biztosítja a főprogram számára.

Az új beállításokat külön fájlban tárolja:
    ~/.config/redmi-control-center/preferences.json

A meglévő settings.json és más JSON-fájlok helyét nem módosítja.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import shlex
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QLayout,
    QSizePolicy,
)

from theme_manager import ThemeManagerWidget, available_themes

CONFIG_DIR = Path.home() / ".config" / "redmi-control-center"
PREFERENCES_FILE = CONFIG_DIR / "preferences.json"
SCRCPY_WEBSITE = "https://github.com/Genymobile/scrcpy"

DEFAULT_PREFERENCES: dict[str, Any] = {
    "startup_state": "normal",
    "remember_window_geometry": True,
    "window_x": None,
    "window_y": None,
    "window_width": 1100,
    "window_height": 720,
    "adb_port": 5555,
    "active_programs": {
        "adb": "Rendszer ADB",
        "scrcpy": "Rendszer scrcpy",
        "fastboot": "Rendszer Fastboot",
    },
    "programs": {
        "adb": [{"name": "Rendszer ADB", "path": shutil.which("adb") or "adb"}],
        "scrcpy": [{"name": "Rendszer scrcpy", "path": shutil.which("scrcpy") or "scrcpy"}],
        "fastboot": [{"name": "Rendszer Fastboot", "path": shutil.which("fastboot") or "fastboot"}],
    },
}


def load_preferences(path: Path | str = PREFERENCES_FILE) -> dict[str, Any]:
    """Betölti az új modul saját beállításfájlját; hibánál biztonságos alapértékeket ad."""
    result = deepcopy(DEFAULT_PREFERENCES)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, value in data.items():
                if key in {"programs", "active_programs"} and isinstance(value, dict):
                    result[key].update(value)
                else:
                    result[key] = value
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    for kind in ("adb", "scrcpy", "fastboot"):
        entries = result.get("programs", {}).get(kind)
        if not isinstance(entries, list) or not entries:
            result["programs"][kind] = deepcopy(DEFAULT_PREFERENCES["programs"][kind])
    try:
        result["adb_port"] = max(1, min(65535, int(result.get("adb_port", 5555))))
    except (TypeError, ValueError):
        result["adb_port"] = 5555
    return result


def save_preferences(data: dict[str, Any], path: Path | str = PREFERENCES_FILE) -> None:
    """Atomikus mentés; csak a preferences.json fájlt kezeli."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def _program_version(path: str, kind: str) -> tuple[bool, str]:
    """Ellenőrzi az útvonalat és megpróbálja lekérdezni a verziót."""
    candidate = path.strip()
    if not candidate:
        return False, "Nincs megadva elérési út."
    resolved = shutil.which(candidate) if os.sep not in candidate else candidate
    if not resolved or not Path(resolved).is_file():
        package_guidance = {
            "adb": (
                "Javasolt megoldás Debian/Ubuntu rendszeren:\n"
                "sudo apt update\n"
                "sudo apt install android-sdk-platform-tools\n"
                "Ez telepíti az ADB platformeszközt."
            ),
            "fastboot": (
                "Javasolt megoldás Debian/Ubuntu rendszeren:\n"
                "sudo apt update\n"
                "sudo apt install android-sdk-platform-tools\n"
                "Ez a csomag tartalmazza a Fastboot eszközt is. "
                "A Fastboot csak bootloader-módú műveletekhez szükséges."
            ),
            "scrcpy": (
                "Javasolt megoldás Debian/Ubuntu rendszeren:\n"
                "sudo apt update\n"
                "sudo apt install scrcpy\n"
                "Ha saját, újabb verziót használsz, ellenőrizd annak tényleges elérési útját."
            ),
        }
        guidance = package_guidance.get(kind, "Ellenőrizd a program elérési útját.")
        return False, f"Nem található: {candidate}\n\nMEGOLDÁSI JAVASLAT\n{guidance}"
    if not os.access(resolved, os.X_OK):
        return False, f"A fájl nem futtatható: {resolved}"
    args = [resolved, "--version"]
    if kind == "adb":
        args = [resolved, "version"]
    elif kind == "fastboot":
        args = [resolved, "--version"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False)
        output = (proc.stdout or proc.stderr).strip().splitlines()
        detail = output[0] if output else f"Kilépési kód: {proc.returncode}"
        # A scrcpy verziószövegéből hagyjuk el a terminálban felesleges
        # szögletes zárójelek közé tett webcímet; a teljes URL külön mezőben
        # továbbra is kimásolható és megnyitható.
        if kind == "scrcpy":
            import re
            detail = re.sub(r"\s*<https?://[^>]+>", "", detail).strip()
        return proc.returncode == 0, f"{resolved}\n{detail}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{resolved}\nVerziólekérdezési hiba: {exc}"


class PreferencesDialog(QWidget):
    """Ötfüles, a főablak munkaterületébe ágyazható beállítási oldal."""

    settings_saved = pyqtSignal(dict)
    cancel_requested = pyqtSignal()

    def __init__(self, settings: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        # Beágyazott oldalként ne kényszerítse rá a sizeHintjét a főablakra.
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.settings = deepcopy(settings or {})
        self.preferences = load_preferences()
        self.program_rows: dict[str, QListWidget] = {}
        self.active_program_combos: dict[str, QComboBox] = {}
        self.program_status: dict[str, QLabel] = {}

        # A main.py régi settings.json-értékei maradnak érvényben, amíg külön nem állítjuk őket.
        self.preferences["window_width"] = int(
            self.preferences.get("window_width") or self.settings.get("window_width", 1100)
        )
        self.preferences["window_height"] = int(
            self.preferences.get("window_height") or self.settings.get("window_height", 720)
        )

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 0)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        root.addWidget(self.tabs, 1)

        self._build_general_tab()
        self._build_programs_tab()
        self._build_connection_and_phone_tab()
        self._build_theme_tab()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save_and_accept)
        self.buttons.rejected.connect(self.cancel_requested.emit)
        root.addWidget(self.buttons)

    def _build_general_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.startup_state = QComboBox()
        self.startup_state.addItem("Normál ablak", "normal")
        self.startup_state.addItem("Maximalizált", "maximized")
        current = self.preferences.get("startup_state", "normal")
        self.startup_state.setCurrentIndex(max(0, self.startup_state.findData(current)))
        form.addRow("Indítási ablakállapot:", self.startup_state)

        self.remember_geometry = QCheckBox("Ablakméret és -pozíció megjegyzése")
        self.remember_geometry.setChecked(bool(self.preferences.get("remember_window_geometry", True)))
        form.addRow("", self.remember_geometry)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(800, 3840)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(int(self.preferences.get("window_width", 1100)))
        form.addRow("Ablak szélessége:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(500, 2160)
        self.height_spin.setSuffix(" px")
        self.height_spin.setValue(int(self.preferences.get("window_height", 720)))
        form.addRow("Ablak magassága:", self.height_spin)

        layout.addLayout(form)
        layout.addWidget(QLabel(
            "Az ablak tényleges indítási állapotának és pozíciójának alkalmazásához "
            "a main.py-nak is használnia kell ezeket az értékeket."
        ))
        layout.addStretch(1)
        self.tabs.addTab(page, "Általános")

    def _build_programs_tab(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        intro = QLabel(
            "PROGRAMOK ÉS ELÉRÉSI UTAK  |  Itt állíthatod be, melyik ADB-, scrcpy- vagy Fastboot-programot használja az alkalmazás."
        )
        intro.setWordWrap(True)
        intro.setObjectName("programsIntro")
        intro.setStyleSheet(
            "QLabel#programsIntro { background: #10283a; color: #e5f4ff; "
            "border-left: 4px solid #28a8ff; padding: 10px 12px; font-weight: 600; }"
        )
        root.addWidget(intro)

        self.program_tabs = QTabWidget()
        for kind, title in (("adb", "ADB"), ("scrcpy", "scrcpy"), ("fastboot", "Fastboot")):
            pane = QWidget()
            pane_root = QVBoxLayout(pane)
            pane_root.setContentsMargins(8, 8, 8, 8)
            pane_root.setSpacing(8)
            descriptions = {
                "adb": "Mire való: parancsok futtatása a működő Androidon, alkalmazáslista és naplók lekérése, valamint ADB-kapcsolat kezelése. Helye: a számítógépen fut; a telefonon az USB-hibakeresést kell engedélyezni.",
                "scrcpy": "Mire való: a telefon képének megjelenítése a számítógépen, valamint támogatott egér- és billentyűzetes vezérlés. Helye: a számítógépen fut; ADB-n keresztül éri el a telefont.",
                "fastboot": "Mire való: Android-eszközök bootloader-módú karbantartása, például bizonyos partícióműveletek. Helye: a számítógépen fut, miközben a telefon Fastboot/bootloader módban van. A scrcpy-hez és a szokásos ADB-használathoz nem szükséges; hiánya önmagában nem hiba. Egyes műveletek adatvesztést okozhatnak.",
            }
            # Jól elkülönülő magyarázó panel, két könnyen áttekinthető sorral.
            info_box = QGroupBox("MIT CSINÁL EZ AZ ESZKÖZ?")
            info_box.setObjectName("programInfoBox")
            info_box.setStyleSheet(
                "QGroupBox#programInfoBox { border: 1px solid #27648a; "
                "border-radius: 5px; margin-top: 8px; padding-top: 5px; "
                "font-weight: 600; color: #7dcaff; }"
                "QGroupBox#programInfoBox::title { subcontrol-origin: margin; "
                "left: 10px; padding: 0 5px; }"
                "QLabel#programDescription { color: #e7edf2; font-size: 13px; }"
                "QLabel#programUsage { color: #8bd0ff; font-weight: 600; }"
            )
            info_layout = QVBoxLayout(info_box)
            info_layout.setContentsMargins(12, 8, 12, 8)
            info_layout.setSpacing(4)
            description = QLabel(descriptions[kind])
            description.setWordWrap(True)
            description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            description.setObjectName("programDescription")
            info_layout.addWidget(description)
            usage = {
                "adb": "MIKOR KELL?  A működő Android-rendszer kezeléséhez, például eszközlista és naplók lekéréséhez.",
                "scrcpy": "MIKOR KELL?  Ha a telefon képét a számítógépen szeretnéd látni és vezérelni.",
                "fastboot": "MIKOR KELL?  Csak bootloader-módú karbantartáskor. A hétköznapi telefonkezeléshez nem szükséges.",
            }[kind]
            usage_label = QLabel(usage)
            usage_label.setWordWrap(True)
            usage_label.setObjectName("programUsage")
            info_layout.addWidget(usage_label)
            pane_root.addWidget(info_box)

            body = QHBoxLayout()
            body.setSpacing(12)

            actions_panel = QWidget()
            actions_panel.setMinimumWidth(165)
            actions_panel.setMaximumWidth(205)
            actions = QVBoxLayout(actions_panel)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(8)

            action_heading = QLabel("Programkezelés")
            action_heading.setStyleSheet("font-weight: 600;")
            actions.addWidget(action_heading)

            add = QPushButton("Hozzáadás")
            edit = QPushButton("Szerkesztés")
            remove = QPushButton("Eltávolítás")
            check = QPushButton("Útvonal / verzió ellenőrzése")
            for button in (add, edit, remove, check):
                button.setMinimumHeight(34)
                button.setToolTip({
                    "Hozzáadás": "Új futtatható program felvétele",
                    "Szerkesztés": "A kijelölt program nevének vagy útvonalának módosítása",
                    "Eltávolítás": "A kijelölt program eltávolítása a listából",
                    "Útvonal / verzió ellenőrzése": "Útvonal, futtathatóság és verzió ellenőrzése",
                }[button.text()])
                actions.addWidget(button)

            actions.addSpacing(8)
            status_title = QLabel("Ellenőrzési eredmény")
            status_title.setStyleSheet("font-weight: 600;")
            actions.addWidget(status_title)
            status = QLabel("Még nincs ellenőrizve.")
            status.setWordWrap(True)
            status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.program_status[kind] = status
            actions.addWidget(status)

            actions.addStretch(1)

            listing_panel = QWidget()
            listing_layout = QVBoxLayout(listing_panel)
            listing_layout.setContentsMargins(0, 0, 0, 0)
            listing_layout.setSpacing(6)

            active = QComboBox()
            self.active_program_combos[kind] = active
            active.currentTextChanged.connect(
                lambda _text, k=kind: self._sync_active_program(k)
            )
            active_form = QFormLayout()
            active_form.addRow("Aktív példány:", active)
            listing_layout.addLayout(active_form)

            listing_label = QLabel("Felvett programok")
            listing_label.setStyleSheet("font-weight: 600;")
            listing_layout.addWidget(listing_label)

            listing = QListWidget()
            listing.setAlternatingRowColors(True)
            listing.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
            listing.setMinimumHeight(180)
            self.program_rows[kind] = listing
            listing_layout.addWidget(listing, 1)

            hint = QLabel(
                "Válassz ki egy sort a szerkesztéshez, eltávolításhoz vagy ellenőrzéshez."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid);")
            listing_layout.addWidget(hint)

            if kind == "scrcpy":
                website_box = QGroupBox("SCRCPY – HIVATALOS FEJLESZTŐI OLDAL")
                website_layout = QHBoxLayout(website_box)
                website_url = QLineEdit(SCRCPY_WEBSITE)
                website_url.setReadOnly(True)
                website_url.setToolTip("A scrcpy hivatalos GitHub-oldala")
                website_layout.addWidget(website_url, 1)
                copy_url = QPushButton("Hivatkozás másolása")
                copy_url.setMinimumHeight(32)
                copy_url.clicked.connect(
                    lambda _=False, edit=website_url: QApplication.clipboard().setText(edit.text())
                )
                website_layout.addWidget(copy_url)
                open_url = QPushButton("Megnyitás böngészőben")
                open_url.setMinimumHeight(32)
                open_url.clicked.connect(
                    lambda _=False, url=SCRCPY_WEBSITE: QDesktopServices.openUrl(QUrl(url))
                )
                website_layout.addWidget(open_url)
                listing_layout.addWidget(website_box)

            body.addWidget(actions_panel)
            body.addWidget(listing_panel, 1)
            pane_root.addLayout(body, 1)

            add.clicked.connect(lambda _=False, k=kind: self._add_program(k))
            edit.clicked.connect(lambda _=False, k=kind: self._edit_program(k))
            remove.clicked.connect(lambda _=False, k=kind: self._remove_program(k))
            check.clicked.connect(lambda _=False, k=kind: self._check_program(k))

            edit.setEnabled(False)
            remove.setEnabled(False)
            check.setEnabled(False)
            listing.currentRowChanged.connect(
                lambda row, e=edit, r=remove, c=check: (
                    e.setEnabled(row >= 0),
                    r.setEnabled(row >= 0),
                    c.setEnabled(row >= 0),
                )
            )
            self.program_tabs.addTab(pane, title)

        root.addWidget(self.program_tabs, 1)
        for kind in ("adb", "scrcpy", "fastboot"):
            self._refresh_program_list(kind)
        self.tabs.addTab(page, "Programok")

    def _refresh_program_list(self, kind: str, select_name: str | None = None) -> None:
        entries = self.preferences["programs"][kind]
        listing = self.program_rows[kind]
        active = self.active_program_combos[kind]
        selected = select_name or self.preferences.get("active_programs", {}).get(kind, "")
        listing.clear()
        active.blockSignals(True)
        active.clear()
        for entry in entries:
            name = str(entry.get("name", "")).strip() or "Névtelen"
            path = str(entry.get("path", "")).strip()
            item = QListWidgetItem(f"{name} — {path}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            listing.addItem(item)
            active.addItem(name, name)
        idx = active.findData(selected)
        active.setCurrentIndex(idx if idx >= 0 else 0)
        active.blockSignals(False)
        if listing.count():
            active_name = active.currentData()
            selected_row = next(
                (i for i in range(listing.count())
                 if listing.item(i).data(Qt.ItemDataRole.UserRole) == active_name),
                0,
            )
            listing.setCurrentRow(selected_row)
        self.preferences.setdefault("active_programs", {})[kind] = active.currentData()

    def _sync_active_program(self, kind: str) -> None:
        active = self.active_program_combos[kind]
        self.preferences.setdefault("active_programs", {})[kind] = active.currentData()

    def _selected_program_index(self, kind: str) -> int:
        return self.program_rows[kind].currentRow()

    def _program_dialog(self, title: str, name: str = "", path: str = "") -> tuple[str, str] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("Pl. Rendszer ADB vagy saját ADB")
        path_row = QHBoxLayout()
        path_edit = QLineEdit(path)
        browse = QPushButton("Tallózás…")
        browse.clicked.connect(lambda: self._browse_program(path_edit))
        path_row.addWidget(path_edit, 1)
        path_row.addWidget(browse)
        form.addRow("Megnevezés:", name_edit)
        form.addRow("Futtatható fájl:", path_row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        new_name, new_path = name_edit.text().strip(), path_edit.text().strip()
        if not new_name or not new_path:
            QMessageBox.warning(self, "Hiányzó adat", "A név és az elérési út kötelező.")
            return None
        return new_name, new_path

    def _browse_program(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Program kiválasztása", edit.text() or str(Path.home()))
        if path:
            edit.setText(path)

    def _add_program(self, kind: str) -> None:
        result = self._program_dialog(f"{kind.upper()} hozzáadása")
        if not result:
            return
        name, path = result
        if any(e.get("name") == name for e in self.preferences["programs"][kind]):
            QMessageBox.warning(self, "Már létezik", "Ezzel a névvel már van program.")
            return
        self.preferences["programs"][kind].append({"name": name, "path": path})
        self._refresh_program_list(kind, name)

    def _edit_program(self, kind: str) -> None:
        index = self._selected_program_index(kind)
        entries = self.preferences["programs"][kind]
        if index < 0 or index >= len(entries):
            QMessageBox.information(self, "Nincs kiválasztás", "Előbb jelölj ki egy programot.")
            return
        old = entries[index]
        result = self._program_dialog("Program szerkesztése", old.get("name", ""), old.get("path", ""))
        if not result:
            return
        name, path = result
        if any(i != index and e.get("name") == name for i, e in enumerate(entries)):
            QMessageBox.warning(self, "Már létezik", "Ezzel a névvel már van program.")
            return
        entries[index] = {"name": name, "path": path}
        self._refresh_program_list(kind, name)

    def _remove_program(self, kind: str) -> None:
        index = self._selected_program_index(kind)
        entries = self.preferences["programs"][kind]
        if index < 0 or index >= len(entries):
            QMessageBox.information(self, "Nincs kiválasztás", "Előbb jelölj ki egy programot.")
            return
        if len(entries) == 1:
            QMessageBox.warning(self, "Nem törölhető", "Legalább egy programbejegyzésnek maradnia kell.")
            return
        del entries[index]
        self._refresh_program_list(kind)

    def _check_program(self, kind: str) -> None:
        index = self._selected_program_index(kind)
        entries = self.preferences["programs"][kind]
        if index < 0 or index >= len(entries):
            QMessageBox.information(self, "Nincs kiválasztás", "Előbb jelölj ki egy programot.")
            return
        ok, message = _program_version(entries[index].get("path", ""), kind)
        self.program_status[kind].setText(("Sikeres ellenőrzés\\n" if ok else "Ellenőrzési hiba\\n") + message)
        if not ok and "nem található" in message.lower():
            packages = ["android-sdk-platform-tools"] if kind in ("adb", "fastboot") else ["scrcpy"]
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Hiányzó program")
            prompt.setIcon(QMessageBox.Icon.Warning)
            prompt.setText(f"A(z) {kind} program nem található.")
            prompt.setInformativeText(
                "A hiányzó eszköz telepítése külön terminálablakban indítható. "
                "Ott megadható a sudo jelszó. Folytatod?"
            )
            install = prompt.addButton("Telepítés terminálban…", QMessageBox.ButtonRole.AcceptRole)
            prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.exec()
            if prompt.clickedButton() is install:
                self._install_package_in_terminal(packages)

    def _install_package_in_terminal(self, packages: list[str]) -> None:
        if not shutil.which("apt-get"):
            QMessageBox.warning(self, "Nem támogatott rendszer",
                                "Az apt-get nem található. Ez Debian/Ubuntu/AV Linux rendszerekhez készült.")
            return
        terminals = {
            "xfce4-terminal": "xfce",
            "mate-terminal": "mate",
            "gnome-terminal": "gnome",
            "konsole": "konsole",
            "xterm": "simple",
            "lxterminal": "simple",
            "kitty": "simple",
            "tilix": "tilix",
            "x-terminal-emulator": "simple",
        }
        terminal = next((name for name in terminals if shutil.which(name)), None)
        if not terminal:
            QMessageBox.critical(self, "Terminál nem található",
                                 "Nem található támogatott terminálprogram.")
            return
        pkg = " ".join(shlex.quote(item) for item in packages)
        shell = (
            "#!/usr/bin/env bash\\n"
            "sudo apt-get update && sudo apt-get install -y " + pkg + "\\n"
            "rc=$?\\n"
            "echo\\n"
            "echo \"Telepítés kilépési kódja: $rc\"\\n"
            "read -r -p 'Bezáráshoz nyomj Entert…' _\\n"
        )
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".sh",
                                             prefix="rcc-install-", delete=False) as f:
                script_path = f.name
                f.write(shell)
            os.chmod(script_path, 0o700)
            kind = terminals[terminal]
            if kind == "xfce":
                args = [terminal, "--disable-server", "--command", "bash " + shlex.quote(script_path)]
            elif kind == "tilix":
                args = [terminal, "--command", "bash " + shlex.quote(script_path)]
            elif kind in ("mate", "gnome"):
                args = [terminal, "--", "bash", script_path]
            else:
                args = [terminal, "-e", "bash", script_path]
            subprocess.Popen(args, start_new_session=True)
        except OSError as exc:
            QMessageBox.critical(self, "Telepítés nem indítható", f"A terminál indítása sikertelen:\\n{exc}")

    def _build_connection_and_phone_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        connection = QGroupBox("Kapcsolatkezelés")
        connection_layout = QVBoxLayout(connection)
        form = QFormLayout()
        self.adb_port_spin = QSpinBox()
        self.adb_port_spin.setRange(1, 65535)
        self.adb_port_spin.setValue(int(self.preferences.get("adb_port", 5555)))
        form.addRow("Alapértelmezett ADB TCP-port:", self.adb_port_spin)
        connection_layout.addLayout(form)
        connection_layout.addWidget(QLabel(
            "USB- és Wi-Fi-kapcsolat, ADB-eszközök felismerése és csatlakozási állapot."
        ))
        layout.addWidget(connection)

        phone = QGroupBox("Telefon és scrcpy")
        phone_layout = QVBoxLayout(phone)
        phone_layout.addWidget(QLabel(
            "A scrcpy a telefon képét tükrözi a számítógépre, és támogatott egér- "
            "és billentyűzetes vezérlést biztosít. A telefonon az USB-hibakeresést "
            "engedélyezni kell. A scrcpy-server sok verzióban indításkor kerül át "
            "a telefonra, ezért nem feltétlenül létezik állandó Android-oldali telepítés."
        ))
        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatikusan kiválasztott ADB-eszköz", "")
        phone_layout.addWidget(self.device_combo)
        row = QHBoxLayout()
        refresh = QPushButton("ADB-eszközök frissítése")
        refresh.clicked.connect(self._refresh_devices)
        check = QPushButton("Telefon és scrcpy-kapcsolat ellenőrzése")
        check.clicked.connect(self._check_phone_server)
        row.addWidget(refresh)
        row.addWidget(check)
        phone_layout.addLayout(row)
        self.phone_result = QLabel("Még nem történt ellenőrzés.")
        self.phone_result.setWordWrap(True)
        phone_layout.addWidget(self.phone_result)
        layout.addWidget(phone)
        layout.addStretch(1)
        self.tabs.addTab(page, "Kapcsolat és eszközök")

    def _active_program_path(self, kind: str) -> str:
        name = self.preferences.get("active_programs", {}).get(kind)
        for entry in self.preferences.get("programs", {}).get(kind, []):
            if entry.get("name") == name:
                return str(entry.get("path", kind))
        return kind

    def _refresh_devices(self) -> None:
        adb = self._active_program_path("adb")
        try:
            proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5, check=False)
            devices = []
            for line in proc.stdout.splitlines()[1:]:
                bits = line.strip().split()
                if len(bits) >= 2 and bits[1] == "device":
                    devices.append(bits[0])
            self.device_combo.clear()
            self.device_combo.addItem("Automatikusan kiválasztott ADB-eszköz", "")
            for serial in devices:
                self.device_combo.addItem(serial, serial)
            self.phone_result.setText(
                f"Talált, engedélyezett eszközök: {len(devices)}\n" +
                ("\n".join(devices) if devices else "Nem található engedélyezett eszköz.")
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.phone_result.setText(f"Az ADB nem indítható:\n{exc}")

    def _check_phone_server(self) -> None:
        adb = self._active_program_path("adb")
        serial = self.device_combo.currentData()
        prefix = [adb]
        if serial:
            prefix += ["-s", serial]
        try:
            devices = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5, check=False)
            if serial and serial not in devices.stdout:
                self.phone_result.setText(f"A kiválasztott eszköz nem látszik az ADB-listában: {serial}")
                return
            getprop = subprocess.run(prefix + ["shell", "getprop", "ro.build.version.release"],
                                     capture_output=True, text=True, timeout=5, check=False)
            android_version = getprop.stdout.strip() or "nem lekérdezhető"
            server_check = subprocess.run(
                prefix + ["shell", "ls", "/data/local/tmp/scrcpy-server"],
                capture_output=True, text=True, timeout=5, check=False
            )
            if server_check.returncode == 0 and "scrcpy-server" in server_check.stdout:
                server_status = "Található /data/local/tmp/scrcpy-server fájl (verziója külön ellenőrizendő)."
            else:
                server_status = (
                    "Nem található /data/local/tmp/scrcpy-server. Ez önmagában nem hiba: "
                    "a scrcpy rendszerint indításkor feltölti a saját szerverét."
                )
            self.phone_result.setText(
                f"Android-verzió: {android_version}\n"
                f"ADB-eszköz: {serial or 'automatikusan kiválasztva'}\n"
                f"{server_status}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.phone_result.setText(f"Ellenőrzési hiba:\n{exc}")

    def _build_theme_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.theme_widget = ThemeManagerWidget(active_theme=self.settings.get("theme", "Alapértelmezett"))
        layout.addWidget(self.theme_widget)
        self.tabs.addTab(page, "Témakezelő")

    def _save_and_accept(self) -> None:
        # Olvassuk be a témaválasztó legfrissebb listáját, hogy frissen létrehozott témát is lehessen választani.
        theme_names = list(available_themes().keys())
        current_theme = self.settings.get("theme", "Alapértelmezett")
        if current_theme not in theme_names:
            current_theme = "Alapértelmezett"

        self.preferences.update({
            "startup_state": self.startup_state.currentData(),
            "remember_window_geometry": self.remember_geometry.isChecked(),
            "window_width": self.width_spin.value(),
            "window_height": self.height_spin.value(),
            "adb_port": self.adb_port_spin.value(),
            "active_programs": {
                kind: self.active_program_combos[kind].currentData()
                for kind in ("adb", "scrcpy", "fastboot")
            },
        })
        save_preferences(self.preferences)
        self.settings.update({
            "theme": current_theme,
            "window_width": self.width_spin.value(),
            "window_height": self.height_spin.value(),
            "adb_path": self._active_program_path("adb"),
            "scrcpy_path": self._active_program_path("scrcpy"),
            "fastboot_path": self._active_program_path("fastboot"),
            "adb_port": self.adb_port_spin.value(),
            "startup_state": self.startup_state.currentData(),
            "remember_window_geometry": self.remember_geometry.isChecked(),
        })
        self.settings_saved.emit(self.get_settings())

    def get_settings(self) -> dict[str, Any]:
        return deepcopy(self.settings)


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    dialog = PreferencesDialog({})
    dialog.show()
    sys.exit(app.exec())
