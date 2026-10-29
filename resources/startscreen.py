#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center — kezdőképernyő

Önálló PyQt6-modul. Egyelőre a main.py-be történő integrálás nélkül is
importálható és futtatható tesztablakként.

Tervezett funkciók:
- USB-eszközök áttekintése (lsusb)
- ADB elérhetőség és eszközállapot (adb devices -l)
- Fastboot elérhetőség és eszközállapot (fastboot devices)
- Szükséges programok ellenőrzése
- Kézi frissítés
- Részletes parancskimenet megtekintése
- Ugrás a rendszerfüggőségek panelre, ha a főprogram átad egy callbacket

A rendszerellenőrzés háttérszálon fut, hogy a grafikus felület ne fagyjon le.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QDialog,
)


APP_NAME = "Redmi Control Center"

# Standalone indításkor is elérhetővé tesszük a projekt gyökérkönyvtárát,
# hogy a resources/ mappából futtatva megtalálja a modules/ csomagot.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PHONE_HINTS = (
    "android", "xiaomi", "redmi", "google", "samsung", "oneplus",
    "huawei", "motorola", "qualcomm", "mediatek", "fastboot",
)


@dataclass
class CheckResult:
    key: str
    title: str
    status: str
    summary: str
    details: str = ""
    severity: str = "neutral"  # ok, warning, error, neutral


def _resolve_executable(name: str, configured_path: Optional[str] = None) -> Optional[str]:
    """Visszaadja a megadott vagy PATH-on található végrehajtható fájlt."""
    candidates = []
    if configured_path:
        candidates.append(os.path.expanduser(configured_path))
    found = shutil.which(name)
    if found:
        candidates.append(found)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run_command(args: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    """Parancs futtatása időkorláttal; nem dob kivételt hiányzó programnál."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"A program nem található: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"Időtúllépés ({timeout:g} másodperc): {' '.join(args)}"
    except PermissionError:
        return 126, "", f"Nincs jogosultság a program futtatásához: {args[0]}"
    except OSError as exc:
        return 125, "", str(exc)


class _CheckWorker(QObject):
    finished = pyqtSignal(object)

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings or {}

    def run(self):
        results: list[CheckResult] = []
        logs: list[str] = []
        self_phone_hint_count = 0

        # Függőségek: a hiányzó opcionális eszköz nem jelenti automatikusan
        # az egész alkalmazás működésképtelenségét.
        configured_adb = self.settings.get("adb_path") or self.settings.get("adb")
        configured_scrcpy = self.settings.get("scrcpy_path") or self.settings.get("scrcpy")
        adb = _resolve_executable("adb", configured_adb)
        scrcpy = _resolve_executable("scrcpy", configured_scrcpy)
        fastboot = _resolve_executable("fastboot")
        lsusb = _resolve_executable("lsusb")

        for key, title, executable, required in (
            ("adb", "ADB", adb, True),
            ("scrcpy", "scrcpy", scrcpy, True),
            ("fastboot", "Fastboot", fastboot, False),
            ("lsusb", "lsusb", lsusb, False),
        ):
            if executable:
                results.append(CheckResult(
                    key, title, "Elérhető", executable,
                    severity="ok",
                ))
                logs.append(f"[OK] {title}: {executable}")
            else:
                results.append(CheckResult(
                    key, title, "Nem található",
                    "A program nincs a PATH-ban, vagy a beállított útvonal hibás.",
                    severity="error" if required else "warning",
                ))
                logs.append(f"[HIÁNYZIK] {title}: nem található végrehajtható fájl.")

        # lsusb: a Linux látja-e a csatlakoztatott USB-eszközöket?
        if lsusb:
            code, stdout, stderr = _run_command([lsusb], timeout=3.0)
            detail = stdout or stderr or "(Nincs kimenet.)"
            logs.extend(["", "$ " + lsusb, detail])
            if code == 0:
                lines = [line for line in stdout.splitlines() if line.strip()]
                likely_phone = [
                    line for line in lines
                    if any(hint in line.lower() for hint in PHONE_HINTS)
                ]
                if likely_phone:
                    summary = f"{len(lines)} USB-eszköz; telefonra utaló bejegyzés: {len(likely_phone)}"
                    severity = "ok"
                elif lines:
                    summary = f"{len(lines)} USB-eszköz látható; a telefon nem azonosítható biztosan a leírásból."
                    severity = "neutral"
                else:
                    summary = "Az lsusb nem sorolt fel USB-eszközt."
                    severity = "warning"
                results.append(CheckResult(
                    "usb", "USB-eszközök", "Lekérdezve", summary,
                    details=detail, severity=severity,
                ))
            else:
                results.append(CheckResult(
                    "usb", "USB-eszközök", "Hiba",
                    stderr or "Az lsusb parancs nem futott le sikeresen.",
                    details=detail, severity="warning",
                ))
        else:
            results.append(CheckResult(
                "usb", "USB-eszközök", "Nem ellenőrizhető",
                "Az lsusb program hiányzik; telepítéséhez általában az usbutils csomag szükséges.",
                severity="warning",
            ))

        # ADB: külön választjuk a program elérhetőségét és a telefon állapotát.
        if adb:
            code, stdout, stderr = _run_command([adb, "devices", "-l"], timeout=5.0)
            detail = stdout or stderr or "(Nincs kimenet.)"
            logs.extend(["", "$ " + adb + " devices -l", detail])
            device_lines = [
                line.strip() for line in stdout.splitlines()
                if line.strip() and not line.lower().startswith("list of devices")
            ]
            if code != 0:
                summary = stderr or f"Az adb hibakóddal tért vissza: {code}."
                status, severity = "Hiba", "error"
            elif any("\tno permissions" in line or "no permissions" in line.lower() for line in device_lines):
                summary = "USB-jogosultsági probléma. Ellenőrizni kell az udev-szabályokat és a felhasználói csoportokat."
                status, severity = "Jogosultsági hiba", "error"
            elif any(line.endswith("\tunauthorized") or "\tunauthorized " in line for line in device_lines):
                summary = "A telefon látható, de az USB-hibakeresési engedély nincs jóváhagyva a telefonon."
                status, severity = "Engedélyezés szükséges", "warning"
            elif any("\toffline" in line for line in device_lines):
                summary = "Van ADB-bejegyzés, de legalább egy eszköz offline állapotú."
                status, severity = "Offline", "warning"
            else:
                ready = [
                    line for line in device_lines
                    if "\tdevice" in line or line.endswith(" device")
                ]
                if ready:
                    summary = "ADB-kapcsolat aktív: " + ", ".join(line.split()[0] for line in ready)
                    status, severity = "Csatlakoztatva", "ok"
                else:
                    if self_phone_hint_count:
                        summary = (
                            "Az USB-listában telefonra utaló eszköz látható, "
                            "de az ADB nem listáz engedélyezett telefont. "
                            "Ellenőrizd az USB-hibakeresést és a telefonon "
                            "megjelenő RSA-engedélykérést."
                        )
                    else:
                        summary = (
                            "Az ADB működik, de nem listáz engedélyezett eszközt. "
                            "Ellenőrizd az USB-kábelt, az USB-módot és a "
                            "telefonon az USB-hibakeresést."
                        )
                    status, severity = "Nincs ADB-eszköz", "neutral"
            results.append(CheckResult(
                "adb-device", "ADB-kapcsolat", status, summary,
                details=detail, severity=severity,
            ))
        else:
            results.append(CheckResult(
                "adb-device", "ADB-kapcsolat", "Nem ellenőrizhető",
                "Az ADB program nem található, ezért az eszközállapot sem kérdezhető le.",
                severity="error",
            ))

        # Fastboot: opcionális; üres lista normális, ha a telefon nincs fastboot módban.
        if fastboot:
            code, stdout, stderr = _run_command([fastboot, "devices"], timeout=4.0)
            detail = stdout or stderr or "(Nincs fastboot eszköz.)"
            logs.extend(["", "$ " + fastboot + " devices", detail])
            if code == 0 and stdout.strip():
                summary, status, severity = "Fastboot-eszköz található.", "Csatlakoztatva", "ok"
            elif code == 0:
                summary, status, severity = "Nincs fastboot módban lévő eszköz (ez normál állapot).", "Nincs eszköz", "neutral"
            else:
                summary, status, severity = stderr or f"Fastboot hibakód: {code}", "Hiba", "warning"
            results.append(CheckResult(
                "fastboot-device", "Fastboot-kapcsolat", status, summary,
                details=detail, severity=severity,
            ))
        else:
            results.append(CheckResult(
                "fastboot-device", "Fastboot-kapcsolat", "Nem ellenőrizhető",
                "A fastboot parancs nem található. Ez a funkció csak fastboot használatakor szükséges.",
                severity="warning",
            ))

        self.finished.emit({"results": results, "log": "\n".join(logs)})


CARD_HELP = {
    "adb": "Az Android Debug Bridge parancssori eszköz. Az ADB-n keresztüli vezérléshez ez szükséges.",
    "scrcpy": "A telefon képét tükrözi a számítógépre, és lehetővé teszi az egérrel/billentyűzettel történő vezérlést.",
    "fastboot": "A Fastboot az Android bootloader módjában használható. Nem ugyanaz, mint az ADB, és a telefon csak Fastboot módban jelenik meg itt.",
    "lsusb": "A Linux USB-eszközlistázó programja. Azt mutatja meg, hogy az operációs rendszer USB-szinten látja-e az eszközöket.",
    "usb": "Az USB-lista a fizikai USB-eszközökről ad képet. Önmagában nem bizonyítja, hogy az ADB-kapcsolat is működik.",
    "adb-device": "Az ADB-kapcsolat állapota. A „device” engedélyezett kapcsolatot jelent; az „unauthorized” esetén a telefonon jóvá kell hagyni az USB-hibakeresést.",
    "fastboot-device": "A Fastboot módban csatlakoztatott készülékek listája. Üres lista normális, ha a telefon Android-rendszerben indul, nem Fastboot módban.",
}


class StatusCard(QFrame):
    def __init__(self, result: CheckResult, parent=None):
        super().__init__(parent)
        self.setObjectName("statusCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip(CARD_HELP.get(result.key, result.summary))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        colors = {
            "ok": ("#2e9d67", "#eaf7ef"),
            "warning": ("#c28a21", "#fff7e5"),
            "error": ("#d64a4a", "#fff0f0"),
            "neutral": ("#718096", "#f1f4f8"),
        }
        accent, _ = colors.get(result.severity, colors["neutral"])
        self.setStyleSheet(f"""
            QFrame#statusCard {{
                background: palette(base);
                border: 1px solid palette(mid);
                border-left: 4px solid {accent};
                border-radius: 9px;
            }}
            QFrame#statusCard:hover {{
                border-top-color: #52758c;
                border-right-color: #52758c;
                border-bottom-color: #52758c;
                background: palette(alternate-base);
            }}
            QLabel#cardTitle {{ font-size: 14px; font-weight: 700; }}
            QLabel#cardStatus {{ color: {accent}; font-weight: 700; }}
            QLabel#cardSummary {{ color: palette(text); }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 11)
        layout.setSpacing(5)

        top = QHBoxLayout()
        title = QLabel(result.title)
        title.setObjectName("cardTitle")
        status = QLabel(result.status)
        status.setObjectName("cardStatus")
        status.setAlignment(
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignRight
            | __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignVCenter
        )
        top.addWidget(title, 1)
        top.addWidget(status)
        layout.addLayout(top)

        summary = QLabel(result.summary)
        summary.setObjectName("cardSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)


class StartScreen(QWidget):
    """
    Kezdőképernyő-widget.

    Paraméterek:
        parent: opcionális szülőwidget.
        settings: beállítások dict; az adb_path/adb és scrcpy_path/scrcpy
                  kulcsokat figyelembe veszi.
        open_dependencies: callback a főprogram rendszerfüggőségi paneljéhez.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        settings: Optional[dict] = None,
        open_dependencies: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.settings = settings or {}
        self.open_dependencies = open_dependencies
        self._thread: Optional[QThread] = None
        self._worker: Optional[_CheckWorker] = None
        self._last_log = ""

        self._build_ui()
        # QToolTip nem rendelkezik setStyleSheet() metódussal PyQt6-ban.
        # A tooltip megjelenését az alkalmazás stíluslapja kezeli.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app.styleSheet() + """
                QToolTip {
                    color: #e8f4ff;
                    background-color: #18232e;
                    border: 1px solid #62baff;
                    border-radius: 10px;
                    padding: 9px 12px;
                    font-size: 12px;
                }
            """)
        self.refresh_checks()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Fejléc: rövid állapotüzenet, fölösleges nagy cím nélkül.
        header = QHBoxLayout()
        intro = QVBoxLayout()
        self.headline = QLabel("Áttekintés")
        self.headline.setStyleSheet(
            "color: #78c8ff; font-size: 16px; font-weight: 700; "
            "letter-spacing: 0.4px;"
        )
        self.subtitle = QLabel("USB  /  ADB  /  scrcpy  /  Fastboot")
        self.subtitle.setStyleSheet("color: #9aa8b5; font-size: 11px;")
        intro.addWidget(self.headline)
        intro.addWidget(self.subtitle)
        header.addLayout(intro, 1)

        self.refresh_button = QPushButton("⟳  Frissítés")
        self.refresh_button.setMinimumHeight(36)
        self.refresh_button.setToolTip("Az eszközök és programfüggőségek újbóli ellenőrzése")
        self.refresh_button.clicked.connect(self.refresh_checks)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self.overall_label = QLabel("Állapotellenőrzés folyamatban…")
        self.overall_label.setWordWrap(True)
        self.overall_label.setStyleSheet(
            "padding: 9px 12px; border: 1px solid palette(mid); border-radius: 7px;"
        )
        root.addWidget(self.overall_label)

        content_row = QHBoxLayout()
        content_row.setSpacing(10)
        content_row.setContentsMargins(0, 0, 0, 0)

        self.cards_host = QWidget()
        self.cards_layout = QGridLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(10)
        self.cards_layout.setVerticalSpacing(10)
        self.cards_layout.setColumnStretch(0, 1)
        self.cards_layout.setColumnStretch(1, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.cards_host)
        content_row.addWidget(scroll, 7)

        self.actions_panel = QWidget()
        self.actions_panel.setMinimumWidth(175)
        self.actions_panel.setMinimumHeight(0)
        self.actions_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.actions_panel_layout = QVBoxLayout(self.actions_panel)
        self.actions_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_panel_layout.setSpacing(10)
        content_row.addWidget(self.actions_panel, 3)
        root.addLayout(content_row, 1)

        # Nyolcadik elem: teljes súgó a projekt README-fájljából.
        self.readme_card = QFrame()
        self.readme_card.setObjectName("readmeCard")
        self.readme_card.setCursor(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.CursorShape.PointingHandCursor)
        self.readme_card.setToolTip("Kattints a részletes súgó megnyitásához. A modul a projekt README-fájlját keresi.")
        self.readme_card.setStyleSheet("""
            QFrame#readmeCard {
                background: palette(base);
                border: 1px solid #4f8fc7;
                border-left: 4px solid #4f8fc7;
                border-radius: 9px;
            }
            QFrame#readmeCard:hover {
                border: 1px solid #76c2ff;
                border-left: 4px solid #76c2ff;
                background: palette(alternate-base);
            }
            QLabel { border: none; }
        """)
        readme_layout = QHBoxLayout(self.readme_card)
        readme_layout.setContentsMargins(13, 10, 13, 10)
        readme_text = QVBoxLayout()
        readme_title = QLabel("Részletes súgó")
        readme_title.setStyleSheet("font-weight: 700; font-size: 14px;")
        readme_desc = QLabel("A README-fájl megnyitása külön ablakban")
        readme_desc.setWordWrap(True)
        readme_text.addWidget(readme_title)
        readme_text.addWidget(readme_desc)
        readme_layout.addLayout(readme_text, 1)
        readme_action = QLabel("Megnyitás  →")
        readme_action.setStyleSheet("color: #62baff; font-weight: 700;")
        readme_layout.addWidget(readme_action)
        self.readme_card.mousePressEvent = lambda event: self.show_readme()

        # Harmadik oszlop: kiemelt, ténylegesen megjelenített gyorsindító kártyák.
        quick_actions = [
            (
                "ESZKÖZÖK",
                "Rendszerfüggőségek",
                "ADB, scrcpy és egyéb rendszerprogramok ellenőrzése",
                "Rendszerfüggőségek",
            ),
            (
                "TELEFON",
                "Kapcsolatok",
                "Telefonkapcsolat és csatlakozási módok",
                "Kapcsolatok",
            ),
            (
                "TELEFON",
                "Eszközadatok",
                "A csatlakoztatott telefon adatainak megtekintése",
                "Eszközadatok",
            ),
        ]
        for group_name, title_text, description, target in quick_actions:
            action = QFrame(self.actions_panel)
            action.setObjectName("quickActionCard")
            action.setCursor(Qt.CursorShape.PointingHandCursor)
            action.setToolTip(description)
            action.setMinimumHeight(112)
            action.setStyleSheet("""
                QFrame#quickActionCard {
                    background-color: #12344a;
                    border: 1px solid #287eae;
                    border-left: 4px solid #45b9ff;
                    border-radius: 9px;
                }
                QFrame#quickActionCard:hover {
                    background-color: #194762;
                    border: 1px solid #70ccff;
                    border-left: 4px solid #70ccff;
                }
                QLabel {
                    background: transparent;
                    border: none;
                    color: #e8f5ff;
                }
            """)
            action_layout = QVBoxLayout(action)
            action_layout.setContentsMargins(12, 10, 12, 10)
            action_layout.setSpacing(5)

            group_label = QLabel(group_name)
            group_label.setStyleSheet(
                "font-size: 10px; color: #8ed5ff; font-weight: 700;"
            )
            action_title = QLabel(title_text)
            action_title.setStyleSheet("font-size: 14px; font-weight: 700;")
            action_title.setWordWrap(True)
            action_description = QLabel(description)
            action_description.setStyleSheet("font-size: 11px;")
            action_description.setWordWrap(True)

            action_layout.addWidget(group_label)
            action_layout.addWidget(action_title)
            action_layout.addWidget(action_description)
            action_layout.addStretch(1)
            action.mousePressEvent = (
                lambda event, destination=target: self._open_target(destination)
            )
            self.actions_panel_layout.addWidget(action, 0)

        # A súgóterület kapja az oszlop fennmaradó magasságát.
        # A kártyák természetes méretűek maradnak, a súgó pedig görgethető.
        self.actions_panel_layout.addStretch(0)

        # Az alsó sávban csak a napló marad; a rendszerfüggőségek
        # gyorsindítója már a harmadik oszlopban található.
        footer = QHBoxLayout()
        self.details_button = QPushButton("Ellenőrzési napló")
        self.details_button.setMinimumHeight(36)
        self.details_button.clicked.connect(self.show_log)
        footer.addWidget(self.details_button)
        footer.addStretch(1)
        root.addLayout(footer)

        # A telefon-előkészítési súgó saját, önállóan görgethető területet kap.
        # A függőleges görgetősáv csak akkor látszik, ha a szöveg nem fér el.
        note_scroll = QScrollArea(self.actions_panel)
        note_scroll.setObjectName("phoneSetupScroll")
        note_scroll.setWidgetResizable(True)
        note_scroll.setFrameShape(QFrame.Shape.NoFrame)
        note_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        note_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        note_scroll.setMinimumHeight(100)
        note_scroll.setMaximumHeight(16777215)
        note_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        note_panel = QFrame()
        note_panel.setObjectName("phoneSetupInfo")
        note_panel.setStyleSheet("""
            QFrame#phoneSetupInfo {
                background-color: #102936;
                border: 1px solid #28536a;
                border-left: 4px solid #45b9ff;
                border-radius: 9px;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #c7dce8;
            }
            QLabel#phoneSetupTitle {
                color: #75caff;
                font-size: 13px;
                font-weight: 700;
            }
        """)
        note_layout = QVBoxLayout(note_panel)
        note_layout.setContentsMargins(12, 11, 12, 12)
        note_layout.setSpacing(7)

        note_title = QLabel("TELEFON ELŐKÉSZÍTÉSE")
        note_title.setObjectName("phoneSetupTitle")
        note_layout.addWidget(note_title)

        note = QLabel(
            "Az ADB-kapcsolathoz engedélyezd a telefonon a Fejlesztői "
            "beállításokat és az USB-hibakeresést. Csatlakoztatáskor oldd fel "
            "a telefont, majd fogadd el a számítógép RSA-ujjlenyomatára "
            "vonatkozó engedélykérést."
        )
        note.setWordWrap(True)
        note.setToolTip(
            "Hol találod?\n"
            "1. Nyisd meg a telefon Beállítások menüjét.\n"
            "2. A Telefonról résznél keresd a verzióadatokat, majd koppints "
            "többször a Buildszámra. Xiaomi készüléken a HyperOS-verzió "
            "megérintése is előhívhatja a fejlesztői módot.\n"
            "3. Lépj a További beállítások → Fejlesztői beállítások menübe.\n"
            "4. Kapcsold be az USB-hibakeresést.\n"
            "5. Csatlakoztatáskor oldd fel a telefont, és fogadd el az "
            "USB-hibakeresési engedélykérést (RSA-ujjlenyomat).\n\n"
            "Az ADB-kapcsolathoz általában ezek szükségesek. Ha a telefon "
            "külön USB-biztonsági engedélyt kér, azt csak akkor engedélyezd, "
            "ha az adott funkció valóban igényli."
        )
        note.setToolTipDuration(20000)
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px; line-height: 1.25;")
        note_layout.addWidget(note)

        note_hint = QLabel("Részletekért vidd az egérmutatót erre a panelre.")
        note_hint.setStyleSheet("color: #7fa7ba; font-size: 10px;")
        note_hint.setWordWrap(True)
        note_layout.addWidget(note_hint)
        note_panel.setToolTip(note.toolTip())
        note_scroll.setWidget(note_panel)
        self.actions_panel_layout.addWidget(note_scroll, 1)

    def _clear_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is self.readme_card:
                continue
            if widget is not None:
                widget.deleteLater()

    def refresh_checks(self):
        if self._thread is not None and self._thread.isRunning():
            return

        self.refresh_button.setEnabled(False)
        self.overall_label.setText("Állapotellenőrzés folyamatban…")
        self._clear_cards()

        thread = QThread(self)
        worker = _CheckWorker(self.settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._checks_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _thread_finished(self):
        self.refresh_button.setEnabled(True)
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _checks_finished(self, payload):
        results: list[CheckResult] = payload.get("results", [])
        self._last_log = payload.get("log", "")
        self._clear_cards()

        for index, result in enumerate(results):
            card = StatusCard(result, self.cards_host)
            self.cards_layout.addWidget(card, index // 2, index % 2)

        # A részletes súgó az ADB-kapcsolat kártya alá kerül (jobb oszlop).
        self.cards_layout.addWidget(self.readme_card, 3, 1)

        errors = sum(result.severity == "error" for result in results)
        warnings = sum(result.severity == "warning" for result in results)
        connected = any(
            result.key == "adb-device" and result.severity == "ok"
            for result in results
        )
        if connected:
            text = "A telefon ADB-n keresztül elérhető."
            color = "#2e9d67"
        elif errors:
            text = f"Az ellenőrzés elkészült: {errors} hiba és {warnings} figyelmeztetés."
            color = "#d64a4a"
        elif warnings:
            text = f"Az ellenőrzés elkészült: {warnings} figyelmeztetés. Ellenőrizd a hiányzó programokat és a kapcsolat állapotát."
            color = "#c28a21"
        else:
            text = "Az ellenőrzés elkészült. Jelenleg nem látható súlyos probléma."
            color = "#2e9d67"
        self.overall_label.setText(text)
        self.overall_label.setStyleSheet(
            f"padding: 9px 12px; border: 1px solid {color}; "
            f"border-radius: 7px; color: {color}; font-weight: 600;"
        )

    def _open_target(self, target: str):
        """A főprogram menüpontját használja; önálló futáskor a valódi modult nyitja meg."""
        main_window = self.window()
        if main_window is not self and callable(
            getattr(main_window, "open_module", None)
        ):
            main_window.open_module(target)
            return

        try:
            if target == "Eszközadatok":
                # DeviceInfoWindow QMainWindow, nem QWidget-tartalom.
                # Ezért önálló ablaként kell megjeleníteni, nem setCentralWidget()-be tenni.
                from modules.device_info import open_module as open_device_info
                device_window = open_device_info(parent=None, settings=self.settings)
                if not isinstance(device_window, QMainWindow):
                    raise TypeError(
                        "A modules.device_info.open_module() nem QMainWindow "
                        f"objektumot adott vissza: {type(device_window).__name__}"
                    )
                if not hasattr(self, "_standalone_module_windows"):
                    self._standalone_module_windows = []
                self._standalone_module_windows.append(device_window)

                def forget_device_window(*_args, ref=device_window):
                    if ref in self._standalone_module_windows:
                        self._standalone_module_windows.remove(ref)

                device_window.destroyed.connect(forget_device_window)
                device_window.show()
                device_window.raise_()
                device_window.activateWindow()
                return

            window = QMainWindow()
            window.setWindowTitle(f"Redmi Control Center — {target}")
            window.resize(1000, 700)
            if target == "Rendszerfüggőségek":
                from modules.dependencies import DependencyCheckerWidget
                widget = DependencyCheckerWidget(window, settings=self.settings)
            elif target == "Kapcsolatok":
                from modules.connections import ConnectionsWidget
                widget = ConnectionsWidget(window, self.settings)
            else:
                raise ValueError(f"Ismeretlen célmodul: {target}")

            if not isinstance(widget, QWidget):
                raise TypeError(
                    f"A(z) {target} modul nem QWidget felületet adott vissza "
                    f"(kapott típus: {type(widget).__name__})."
                )
            window.setCentralWidget(widget)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            if not hasattr(self, "_standalone_module_windows"):
                self._standalone_module_windows = []
            self._standalone_module_windows.append(window)

            def forget_window(*_args, ref=window):
                if ref in self._standalone_module_windows:
                    self._standalone_module_windows.remove(ref)

            window.destroyed.connect(forget_window)
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as exc:
            QMessageBox.critical(
                self,
                f"{target} — betöltési hiba",
                f"A modul nem nyitható meg.\n\n"
                f"{type(exc).__name__}: {exc}",
            )

    def _open_dependencies(self):
        if callable(self.open_dependencies):
            self.open_dependencies()
        else:
            QMessageBox.information(
                self,
                "Rendszerfüggőségek",
                "A rendszerfüggőségek panel a főprogramba történő integrálás után innen nyitható meg.",
            )

    def show_readme(self):
        """A README-fájl tartalmának megjelenítése modális párbeszédablakban."""
        base_dir = Path(__file__).resolve().parent
        candidates = (
            base_dir / "README.rd",
            base_dir / "readme.rd",
            base_dir / "README.md",
            base_dir / "readme.md",
            base_dir / "README.rst",
            base_dir / "readme.rst",
            base_dir.parent / "README.rd",
            base_dir.parent / "readme.rd",
            base_dir.parent / "README.md",
            base_dir.parent / "readme.md",
            base_dir.parent / "README.rst",
            base_dir.parent / "readme.rst",
        )
        readme_path = next((item for item in candidates if item.is_file()), None)

        dialog = QDialog(self)
        dialog.setWindowTitle("Redmi Control Center — súgó")
        dialog.resize(820, 620)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(dialog)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        if readme_path:
            try:
                editor.setPlainText(readme_path.read_text(encoding="utf-8"))
                dialog.setWindowTitle(f"Redmi Control Center — súgó ({readme_path.name})")
            except (OSError, UnicodeError) as exc:
                editor.setPlainText(f"A README-fájl nem olvasható.\n\n{exc}")
        else:
            editor.setPlainText(
                "Nem található README-fájl.\n\n"
                "A keresett fájlnevek: README.rd, README.md és README.rst "
                "(kisbetűs változatban is), a startscreen.py mappájában "
                "vagy annak szülőmappájában.\n\n"
                "Tedd a projekt README-fájlját a projekt gyökérkönyvtárába, "
                "és a súgó innen automatikusan be fogja olvasni."
            )

        layout.addWidget(editor)
        close_button = QPushButton("Bezárás")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def show_log(self):
        dialog = QMainWindow(self)
        dialog.setWindowTitle("Ellenőrzési napló")
        dialog.resize(760, 500)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(self._last_log or "Még nincs elérhető napló.")
        dialog.setCentralWidget(editor)
        dialog.show()
        # Erős referencia, hogy a nem modális ablak ne semmisüljön meg.
        if not hasattr(self, "_log_windows"):
            self._log_windows = []
        self._log_windows.append(dialog)
        dialog.destroyed.connect(lambda: self._log_windows.remove(dialog) if dialog in self._log_windows else None)


def main():
    """Önálló tesztindítás: python3 resources/startscreen.py"""
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = QMainWindow()
    window.setWindowTitle(f"{APP_NAME} — kezdőképernyő teszt")
    window.resize(900, 680)
    screen = StartScreen()
    window.setCentralWidget(screen)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
