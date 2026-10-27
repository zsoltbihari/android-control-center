#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redmi Control Center — önálló scrcpy képernyőtükröző modul.

A modul külön is futtatható:
    python3 screen_mirroring.py

Projektmodulként:
    from modules.screen_mirroring import open_module
    open_module(parent, settings)

A modul a telepített scrcpy parancsot indítja el, ezért a telefon ADB-
engedélyezése és az scrcpy telepítése szükséges. A tükrözés külön folyamat,
nem ágyazza be a scrcpy képét a PyQt-ablakba.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QComboBox,
    QLineEdit, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QMessageBox,
    QFormLayout
)


class ScreenMirroringWidget(QWidget):
    def __init__(self, parent=None, settings: Optional[dict] = None):
        super().__init__(parent)
        self.settings = settings or {}
        self._process: Optional[subprocess.Popen] = None
        self._rotation = 0
        self._wm_name = "ismeretlen"
        self._wm_class = "ismeretlen"
        self._wm_pid = "ismeretlen"
        self._build_ui()
        self._refresh_window_manager()
        self._refresh_devices()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(700)
        self._poll_timer.timeout.connect(self._poll_process)
        self._poll_timer.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("Telefon képernyőjének tükrözése")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        info = QLabel(
            "Az Indítás gomb a telepített scrcpy programot külön ablakban indítja. "
            "A telefonon az ADB-hibakeresésnek engedélyezve kell lennie."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        form = QFormLayout()
        self.adb_path = QLineEdit(self.settings.get("adb_path", "adb"))
        self.adb_path.setPlaceholderText("adb elérési útja (alapértelmezés: adb)")
        form.addRow("ADB parancs:", self.adb_path)

        self.scrcpy_path = QLineEdit(self.settings.get("scrcpy_path", "scrcpy"))
        self.scrcpy_path.setPlaceholderText("scrcpy elérési útja (alapértelmezés: scrcpy)")
        form.addRow("scrcpy parancs:", self.scrcpy_path)

        self.device_combo = QComboBox()
        form.addRow("Eszköz:", self.device_combo)
        root.addLayout(form)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Eszközlista frissítése")
        self.refresh_button.clicked.connect(self._refresh_devices)
        buttons.addWidget(self.refresh_button)

        self.start_button = QPushButton("Tükrözés indítása")
        self.start_button.clicked.connect(self.start_mirroring)
        buttons.addWidget(self.start_button)

        self.stop_button = QPushButton("scrcpy leállítása")
        self.stop_button.clicked.connect(self.stop_mirroring)
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.stop_button)
        root.addLayout(buttons)

        rotation_buttons = QHBoxLayout()
        self.rotate_left_button = QPushButton("↶ Balra 90°")
        self.rotate_left_button.clicked.connect(lambda: self.set_rotation(1))
        rotation_buttons.addWidget(self.rotate_left_button)

        self.rotate_right_button = QPushButton("↷ Jobbra 90°")
        self.rotate_right_button.clicked.connect(lambda: self.set_rotation(3))
        rotation_buttons.addWidget(self.rotate_right_button)

        self.rotate_reset_button = QPushButton("0° / Visszaállítás")
        self.rotate_reset_button.clicked.connect(lambda: self.set_rotation(0))
        rotation_buttons.addWidget(self.rotate_reset_button)
        root.addLayout(rotation_buttons)

        self.wm_label = QLabel("Ablakkezelő: lekérdezés…")
        self.wm_label.setWordWrap(True)
        root.addWidget(self.wm_label)

        rotation_info = QLabel(
            "A forgatógombok csak olyan grafikus környezetben működhetnek, amely "
            "támogatja az egyedi ablak transzformációját. Az ablakkezelő neve "
            "automatikusan lekérdezhető, de az X11/WM szabványos felületén nincs "
            "általános parancs egyetlen ablak elforgatására. A program nem küld "
            "forgató billentyűparancsot a scrcpy-nak, és nem módosítja a telefon tájolását."
        )
        rotation_info.setWordWrap(True)
        root.addWidget(rotation_info)

        self.status_label = QLabel("Készen áll.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        root.addWidget(QLabel("Napló"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(100)
        root.addWidget(self.log_view, 1)

    def _log(self, message: str):
        self.log_view.appendPlainText(message)

    def _refresh_devices(self):
        adb = self.adb_path.text().strip() or "adb"
        adb_exe = shutil.which(adb) if "/" not in adb else (adb if shutil.which(adb) else None)
        self.device_combo.clear()
        if not adb_exe:
            self.status_label.setText(f"Nem található az ADB: {adb}")
            self._log(f"[HIBA] Nem található az ADB: {adb}")
            return
        try:
            result = subprocess.run(
                [adb_exe, "devices"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=6, check=False
            )
        except Exception as exc:
            self.status_label.setText(f"ADB-hiba: {exc}")
            self._log(f"[HIBA] ADB-eszközlista: {exc}")
            return
        if result.returncode:
            self._log(result.stderr.strip() or result.stdout.strip() or "Az adb devices hibával tért vissza.")
            self.status_label.setText("Nem sikerült lekérdezni az ADB-eszközöket.")
            return

        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                serial, state = parts[0], parts[1]
                label = f"{serial} — {state}"
                self.device_combo.addItem(label, (serial, state))
        if self.device_combo.count() == 0:
            self.status_label.setText("Nem található ADB-eszköz.")
        else:
            self.status_label.setText(f"{self.device_combo.count()} ADB-bejegyzés található.")
        self._log("ADB-eszközlista frissítve.")

    def _refresh_window_manager(self):
        """Az aktuális ablakkezelő nevének automatikus lekérdezése wmctrl-lel."""
        wmctrl = shutil.which("wmctrl")
        if not wmctrl:
            message = "Ablakkezelő: nem állapítható meg (wmctrl nincs telepítve)."
            self.wm_label.setText(message)
            self._log("[WM] " + message)
            self._set_rotation_supported(False)
            return

        try:
            result = subprocess.run(
                [wmctrl, "-m"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3, check=False
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "ismeretlen hiba"
                message = f"Ablakkezelő: lekérdezési hiba ({detail})."
                self.wm_label.setText(message)
                self._log("[WM HIBA] " + message)
                self._set_rotation_supported(False)
                return

            fields = {}
            for line in result.stdout.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip().lower()] = value.strip()

            name = fields.get("name", "ismeretlen")
            wm_class = fields.get("class", "ismeretlen")
            pid = fields.get("pid", "ismeretlen")
            self._wm_name = name
            self._wm_class = wm_class
            self._wm_pid = pid
            self.wm_label.setText(
                f"Ablakkezelő: {name} | Class: {wm_class} | PID: {pid}"
            )
            self._log(f"[WM] Automatikusan felismerve: {name}; Class={wm_class}; PID={pid}")
            # Nincs általános X11-es WM-parancs egyetlen ablak forgatására.
            # Ezért nem engedélyezzük a gombokat olyan támogatás nélkül,
            # amelyet ténylegesen felismertünk.
            self._set_rotation_supported(False)
        except Exception as exc:
            message = f"Ablakkezelő: lekérdezési hiba ({exc})."
            self.wm_label.setText(message)
            self._log("[WM HIBA] " + message)
            self._set_rotation_supported(False)

    def _set_rotation_supported(self, supported: bool):
        """A gombok állapota a valóban elérhető, ellenőrzött forgatási támogatás szerint."""
        for button in (
            self.rotate_left_button,
            self.rotate_right_button,
            self.rotate_reset_button,
        ):
            button.setEnabled(supported)
        if not supported:
            self.status_label.setText(
                "Az ablakkezelő felismerése működik, de az egyedi ablakforgatás "
                "nincs támogatva ezen az általános felületen."
            )

    def set_rotation(self, rotation: int):
        """Nem színlel forgatást: ehhez igazolt, ablakonkénti transzformációs backend kell."""
        self._refresh_window_manager()
        self._log(
            "[FORGATÁS NEM ELÉRHETŐ] A felismert ablakkezelő nem biztosít "
            "általános ablakonkénti forgatási parancsot. Scrcpy-gyorsbillentyű "
            "és telefonbeállítás-módosítás nem történik."
        )
        QMessageBox.information(
            self,
            "Ablakforgatás nem támogatott",
            "Az ablakkezelőt sikerült automatikusan felismerni, de ezen a "
            "grafikus környezeten nincs általános, ablakonkénti forgatási API. "
            "A gomb nem módosítja a scrcpy-t vagy a telefon tájolását."
        )

    def start_mirroring(self):
        if self._process is not None and self._process.poll() is None:
            self.status_label.setText("A scrcpy már fut.")
            return

        item = self.device_combo.currentData()
        if not item:
            QMessageBox.warning(self, "Tükrözés", "Nem található vagy nincs kiválasztva ADB-eszköz.")
            return
        serial, state = item
        if state != "device":
            QMessageBox.warning(
                self, "Tükrözés",
                f"Az eszköz állapota „{state}”. Oldd fel a telefont, és engedélyezd az ADB-kapcsolatot."
            )
            return

        scrcpy = self.scrcpy_path.text().strip() or "scrcpy"
        scrcpy_exe = shutil.which(scrcpy) if "/" not in scrcpy else (scrcpy if shutil.which(scrcpy) else None)
        if not scrcpy_exe:
            QMessageBox.critical(self, "scrcpy nem található", f"Nem található a scrcpy program:\n{scrcpy}")
            return

        adb = self.adb_path.text().strip() or "adb"
        args = [
            scrcpy_exe, "--serial", serial,
            "--window-title", f"Redmi Control Center — {serial}",
        ]
        # scrcpy uses the ADB executable from PATH. For a custom ADB path,
        # prepend its directory to PATH for the child process.
        import os
        env = os.environ.copy()
        if "/" in adb:
            adb_exe = shutil.which(adb)
            if not adb_exe:
                QMessageBox.critical(self, "ADB nem található", f"Nem található az ADB program:\n{adb}")
                return
            env["PATH"] = str(__import__("pathlib").Path(adb_exe).parent) + os.pathsep + env.get("PATH", "")
        try:
            self._process = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env
            )
        except Exception as exc:
            self._log(f"[HIBA] scrcpy indítása: {exc}")
            self.status_label.setText("A scrcpy nem indult el.")
            return

        self._log(f"[INDÍTÁS] {' '.join(args)}")
        self.status_label.setText(f"scrcpy elindítva: {serial}")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        QTimer.singleShot(900, self._check_startup)

    def _check_startup(self):
        if self._process is None:
            return
        code = self._process.poll()
        if code is not None:
            self._log(f"[HIBA] A scrcpy kilépett, hibakód: {code}")
            if self._process.stderr:
                try:
                    error = self._process.stderr.read().strip()
                    if error:
                        self._log(error)
                except Exception:
                    pass
            self.status_label.setText(f"A scrcpy leállt (hibakód: {code}).")
            self._process = None
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def _poll_process(self):
        if self._process is not None and self._process.poll() is not None:
            process = self._process
            code = process.returncode
            self._log(f"[HIBA] A scrcpy folyamat befejeződött (hibakód: {code}).")
            # A valódi scrcpy-hibaüzenetet is kiírjuk. Eddig a poll-ág
            # csak a hibakódot naplózta, ezért az ok rejtve maradt.
            if process.stderr:
                try:
                    error = process.stderr.read().strip()
                    if error:
                        self._log("[SCRCPY HIBAÜZENET]")
                        self._log(error)
                except Exception as exc:
                    self._log(f"[NAPLÓZÁSI HIBA] A scrcpy kimenete nem olvasható: {exc}")
            self._process = None
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.status_label.setText(f"A scrcpy leállt (hibakód: {code}); nézd meg a naplót.")

    def stop_mirroring(self):
        if self._process is None or self._process.poll() is not None:
            self.status_label.setText("Nincs futó, innen indított scrcpy-folyamat.")
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._log("[INFO] A scrcpy leállítva.")
        self._process = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("A tükrözés leállt.")

    def closeEvent(self, event):
        # A tükrözőablak bezárásakor a modul által indított scrcpy is leáll.
        if self._process is not None and self._process.poll() is None:
            answer = QMessageBox.question(
                self, "Tükrözés bezárása",
                "A modul által indított scrcpy is álljon le az ablak bezárásakor?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.stop_mirroring()
        event.accept()


def open_module(parent=None, settings: Optional[dict] = None):
    """A modult külön ablakban nyitja meg, főprogramból is meghívható."""
    window = QMainWindow(parent)
    window.setWindowTitle("Redmi Control Center — Képernyőtükrözés")
    window.resize(680, 500)
    window.setMinimumSize(560, 380)
    window.setCentralWidget(ScreenMirroringWidget(window, settings))
    window.setAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_DeleteOnClose, True)
    if parent is not None:
        if not hasattr(parent, "_module_windows"):
            parent._module_windows = []
        parent._module_windows.append(window)

        def remove_window(*_):
            windows = getattr(parent, "_module_windows", [])
            if window in windows:
                windows.remove(window)

        window.destroyed.connect(remove_window)
    window.show()
    return window


def main():
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Redmi Control Center — Képernyőtükrözés")
    window.resize(680, 500)
    window.setCentralWidget(ScreenMirroringWidget(settings={}))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
