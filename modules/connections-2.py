#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi Control Center — Kapcsolatok modul

Elhelyezés:
    redmi-control-center/modules/connections.py

A főprogramból:
    import modules.connections
    modules.connections.open_module(main_window, settings)

Önálló indítás:
    python3 modules/connections.py

Automatizálás:
- ADB szerver ellenőrzése és indítása
- USB-n csatlakozó ADB-eszközök felismerése
- USB-s eszközön a telefon IPv4-címeinek felderítése
- Linuxos hotspot/útvonal információk begyűjtése
- A telefon által használt címek javaslata; az átjárót nem tekinti automatikusan telefonnak
- Wi-Fi ADB csatlakozás alapértelmezett 5555-ös porttal
- Kapcsolat ellenőrzése tényleges adb shell paranccsal
- ADB szerver leállítása/újraindítása
- Háttérszálas parancsvégrehajtás és részletes napló

Biztonsági megjegyzés:
A modul nem próbálja megkerülni az Android párosítási vagy hibakeresési engedélyeit.
A TCP/IP ADB 5555-ös portjára csak felhasználói művelet után csatlakozik.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QComboBox,
    QLineEdit, QPlainTextEdit, QProgressBar, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QMessageBox, QCheckBox, QTabWidget, QScrollArea,
    QGridLayout, QSizePolicy
)


DEFAULT_PORT = 5555


@dataclass
class AdbDevice:
    serial: str
    state: str
    details: str = ""

    @property
    def connection_type(self) -> str:
        if re.fullmatch(r".+:\d+", self.serial):
            return "Wi-Fi / TCP"
        if self.serial.startswith("emulator-"):
            return "Emulátor"
        return "USB / helyi"


@dataclass
class NetworkAddress:
    interface: str
    address: str
    prefix: int

    @property
    def cidr(self) -> str:
        return f"{self.address}/{self.prefix}"


class AdbService:
    def __init__(self, adb_path: str = "adb", timeout: float = 8.0):
        self.adb_path = adb_path or "adb"
        self.timeout = timeout

    def executable(self) -> str:
        path = self.adb_path.strip()
        if "/" in path:
            if not (path.startswith("/") and shutil.which(path)):
                raise FileNotFoundError(f"Az ADB nem található: {path}")
            return path
        found = shutil.which(path)
        if not found:
            raise FileNotFoundError(f"Nem található az ADB parancs: {path}")
        return found

    def run(self, args: list[str], timeout: Optional[float] = None) -> tuple[str, str, int, float]:
        command = [self.executable(), *args]
        started = time.monotonic()
        limit = self.timeout if timeout is None else timeout
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                timeout=limit, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            raise TimeoutError(
                f"Időtúllépés {elapsed:.2f} s után: {' '.join(command)}"
                + (f"\nstdout: {out.strip()}" if out.strip() else "")
                + (f"\nstderr: {err.strip()}" if err.strip() else "")
            ) from exc
        return result.stdout.strip(), result.stderr.strip(), result.returncode, time.monotonic() - started

    def version(self) -> str:
        out, err, rc, elapsed = self.run(["version"], timeout=4)
        text = out or err
        if rc:
            raise RuntimeError(f"adb version hibakód: {rc}\n{text}")
        return f"{text.splitlines()[0] if text else 'ADB verzió ismeretlen'} ({elapsed:.2f} s)"

    def start_server(self) -> str:
        out, err, rc, elapsed = self.run(["start-server"], timeout=8)
        text = out or err or "ADB-szerver elindítva vagy már fut."
        if rc:
            raise RuntimeError(f"adb start-server hibakód: {rc}\n{text}")
        return f"{text} ({elapsed:.2f} s)"

    def kill_server(self) -> str:
        out, err, rc, elapsed = self.run(["kill-server"], timeout=8)
        text = out or err or "ADB-szerver leállítva."
        if rc:
            raise RuntimeError(f"adb kill-server hibakód: {rc}\n{text}")
        return f"{text} ({elapsed:.2f} s)"

    def list_devices(self) -> tuple[list[AdbDevice], str]:
        out, err, rc, elapsed = self.run(["devices", "-l"], timeout=8)
        if rc:
            raise RuntimeError(f"adb devices -l hibakód: {rc}\n{err or out}")
        devices: list[AdbDevice] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append(AdbDevice(parts[0], parts[1], " ".join(parts[2:])))
        return devices, f"adb devices -l lefutott ({elapsed:.2f} s)"

    def shell(self, serial: str, args: list[str], timeout: float = 8.0) -> str:
        out, err, rc, elapsed = self.run(["-s", serial, "shell", *args], timeout=timeout)
        if rc:
            raise RuntimeError(f"adb shell hibakód: {rc}\n{err or out}")
        return f"{out.strip()} ({elapsed:.2f} s)"

    def test_device(self, serial: str) -> str:
        out, err, rc, elapsed = self.run(
            ["-s", serial, "shell", "echo", "ADB_OK"], timeout=8
        )
        if rc:
            raise RuntimeError(f"adb shell hibakód: {rc}\n{err or out}")
        if "ADB_OK" not in out.split():
            raise RuntimeError(f"Nem érkezett meg az ADB_OK válasz. Kimenet: {out!r}")
        return f"{out.strip()} ({elapsed:.2f} s)"

    def connect(self, address: str) -> str:
        address = address.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:\d{1,5}", address):
            raise ValueError("Hibás cím. Példa: 192.168.1.25 vagy 192.168.1.25:5555")
        host, sep, port_text = address.rpartition(":")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("A portnak 1 és 65535 közé kell esnie.")
        out, err, rc, elapsed = self.run(["connect", address], timeout=10)
        text = out or err
        low = text.lower()
        if rc or "unable to connect" in low or "failed" in low:
            raise RuntimeError(f"adb connect hibakód: {rc}\n{text}")
        return f"{text or 'ADB connect lefutott'} ({elapsed:.2f} s)"

    def disconnect(self, serial: str) -> str:
        out, err, rc, elapsed = self.run(["disconnect", serial], timeout=6)
        text = out or err
        if rc:
            raise RuntimeError(f"adb disconnect hibakód: {rc}\n{text}")
        return f"{text or 'Leválasztási parancs lefutott'} ({elapsed:.2f} s)"

    def discover_phone_ipv4(self, serial: str) -> list[NetworkAddress]:
        # ip -o -4 addr show is available on most Android builds; fall back to ip addr.
        commands = [
            ["ip", "-o", "-4", "addr", "show"],
            ["ip", "-4", "addr", "show"],
        ]
        last_error = ""
        output = ""
        for command in commands:
            try:
                output = self.shell(serial, command, timeout=8)
                break
            except Exception as exc:
                last_error = str(exc)
        if not output:
            raise RuntimeError(
                "Nem sikerült lekérdezni a telefon IPv4-címeit. "
                "Lehet, hogy az Android nem engedi az ip parancsot.\n" + last_error
            )

        addresses: list[NetworkAddress] = []
        # Typical ip -o output: 12 wlan0 inet 192.168.1.1/24 ...
        for line in output.splitlines():
            match = re.search(r"\d+:\s*([\w.@-]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if not match:
                # Non -o format: "inet 192.168.1.1/24 ..."
                iface_match = re.search(r"^\d+:\s*([\w.@-]+):", line)
                addr_match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                if iface_match and addr_match:
                    iface, address, prefix = iface_match.group(1), addr_match.group(1), addr_match.group(2)
                else:
                    continue
            else:
                iface, address, prefix = match.group(1), match.group(2), match.group(3)

            try:
                parsed = ipaddress.IPv4Address(address)
            except ipaddress.AddressValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
                continue
            addresses.append(NetworkAddress(iface, str(parsed), int(prefix)))

        # De-duplicate while preserving order.
        seen = set()
        unique = []
        for item in addresses:
            key = (item.interface, item.address, item.prefix)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique


def discover_linux_network() -> tuple[str, list[str]]:
    """Read Linux-side route/interface data without assuming gateway == phone."""
    logs: list[str] = []
    ip_bin = shutil.which("ip")
    if not ip_bin:
        return "A Linux 'ip' parancs nem található.", logs
    try:
        route = subprocess.run(
            [ip_bin, "route", "show", "default"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3, check=False,
        )
        logs.append(f"Linux alapértelmezett útvonal: {route.stdout.strip() or '(nincs)'}")
        addr = subprocess.run(
            [ip_bin, "-o", "-4", "addr", "show"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3, check=False,
        )
        for line in addr.stdout.splitlines():
            logs.append(f"Linux interfész: {line.strip()}")
        return "Hálózati adatok lekérdezve.", logs
    except Exception as exc:
        return f"Hálózati lekérdezési hiba: {exc}", logs


class TaskThread(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, label: str, function: Callable, args=(), parent=None):
        super().__init__(parent)
        self.label = label
        self.function = function
        self.args = args

    def run(self):
        try:
            self.log_line.emit(f"[INDÍTÁS] {self.label}")
            result = self.function(*self.args)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.log_line.emit(f"[BEFEJEZÉS] {self.label}")


class ConnectionsWidget(QWidget):
    def __init__(self, parent=None, settings: Optional[dict] = None):
        super().__init__(parent)
        self.settings = settings or {}
        self.adb = AdbService(self.settings.get("adb_path", "adb"))
        self._task: Optional[TaskThread] = None
        self._pending_success: Optional[Callable[[Any], None]] = None
        self.completed_steps: set[int] = set()
        self.current_step = 1
        self._build_ui()
        self._log(f"ADB útvonalbeállítás: {self.adb.adb_path}")
        self.refresh_devices()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(6)

        self.tabs = QTabWidget(self)
        outer_layout.addWidget(self.tabs)

        # Főoldal görgethető tartalommal: kis kijelzőn sem tűnik el funkció.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "Telefoncsatlakozás")

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)
        self.tabs.addTab(log_page, "Kapcsolati napló")

        # Fejléc
        header = QGroupBox()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        title_col = QVBoxLayout()
        title = QLabel("ACC — Android Control Center")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        subtitle = QLabel("Telefoncsatlakozás és kapcsolatkezelés")
        subtitle.setStyleSheet("color: palette(mid); font-size: 12px;")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_layout.addLayout(title_col, 1)
        self.overall_status = QLabel("0 / 7 lépés kész")
        self.overall_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overall_status.setMinimumWidth(130)
        self.overall_status.setStyleSheet(
            "QLabel { padding: 8px 12px; border: 1px solid #7A8795; "
            "border-radius: 7px; font-weight: 600; }"
        )
        header_layout.addWidget(self.overall_status)
        root.addWidget(header)

        instruction = QLabel(
            "A csatlakozáshoz haladj sorrendben az 1–7. lépésen. "
            "A zöld gomb mutatja a következő teendőt; a pipa csak ellenőrzött siker után jelenik meg."
        )
        instruction.setWordWrap(True)
        instruction.setStyleSheet("padding: 4px 2px; color: palette(text);")
        root.addWidget(instruction)

        # A fő vezérlő: bal oldalt kattintható lépésgombok, jobb oldalt állapot.
        steps_group = QGroupBox("Csatlakozási folyamat — 7 kötelező lépés")
        steps_layout = QVBoxLayout(steps_group)
        steps_layout.setContentsMargins(8, 8, 8, 8)
        steps_layout.setSpacing(5)
        self.step_texts = [
            "ADB és eszközlista frissítése",
            "Telefonkapcsolat tesztelése",
            "Telefon IP-címeinek felderítése",
            "Laptop hálózatának felderítése",
            "TCP/IP ADB bekapcsolása USB-n",
            "Wi-Fi ADB-kapcsolódás és shellteszt",
            "Ellenőrzés USB-kábel nélkül",
        ]
        button_texts = [
            "1.  Eszközlista frissítése",
            "2.  Kapcsolat tesztelése",
            "3.  Telefon IP-címeinek felderítése",
            "4.  Laptop hálózatának felderítése",
            "5.  TCP/IP ADB bekapcsolása",
            "6.  Wi-Fi ADB-kapcsolódás",
            "7.  USB nélküli végső ellenőrzés",
        ]
        self.step_buttons: list[QPushButton] = []
        self.step_status_labels: list[QLabel] = []
        for index, (button_text, description) in enumerate(zip(button_texts, self.step_texts), start=1):
            row = QHBoxLayout()
            row.setSpacing(10)
            button = QPushButton(button_text)
            button.setMinimumHeight(38)
            button.setMinimumWidth(250)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(description)
            self.step_buttons.append(button)
            status = QLabel("○  Várakozik")
            status.setMinimumWidth(220)
            status.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.step_status_labels.append(status)
            row.addWidget(button, 3)
            row.addWidget(status, 2)
            steps_layout.addLayout(row)
        self.step_status = QLabel("Kezdés: nyomd meg az 1. Eszközlista frissítése gombot.")
        self.step_status.setWordWrap(True)
        self._set_message_style(error=False)
        steps_layout.addWidget(self.step_status)
        root.addWidget(steps_group)

        # A kapcsolódáshoz szükséges eszközválasztás és ellenőrzési eredmények.
        device_group = QGroupBox("Felismert telefon és kapcsolat állapota")
        device_layout = QVBoxLayout(device_group)
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("ADB-eszköz:"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(260)
        device_row.addWidget(self.device_combo, 1)
        self.status_label = QLabel("ADB ellenőrzése…")
        self.status_label.setWordWrap(True)
        self._set_status_label_style(error=False)
        device_row.addWidget(self.status_label, 2)
        device_layout.addLayout(device_row)
        self.device_details_label = QLabel("A felismert eszköz részletei itt jelennek meg.")
        self.device_details_label.setWordWrap(True)
        self.device_details_label.setStyleSheet("color: palette(mid); padding: 3px;")
        device_layout.addWidget(self.device_details_label)
        root.addWidget(device_group)

        # Telefon- és laptopoldali hálózati adatok.
        network_group = QGroupBox("Hálózati adatok")
        network_layout = QGridLayout(network_group)
        network_layout.setHorizontalSpacing(12)
        network_layout.setVerticalSpacing(6)
        network_layout.addWidget(QLabel("Telefon IP-címei:"), 0, 0)
        self.phone_network_label = QLabel("USB-s ADB-kapcsolat után kérdezhetők le.")
        self.phone_network_label.setWordWrap(True)
        network_layout.addWidget(self.phone_network_label, 0, 1)
        network_layout.addWidget(QLabel("Laptop hálózata:"), 1, 0)
        self.linux_network_label = QLabel("A laptop interfészei és útvonalai még nincsenek lekérdezve.")
        self.linux_network_label.setWordWrap(True)
        network_layout.addWidget(self.linux_network_label, 1, 1)
        root.addWidget(network_group)

        # Wi-Fi ADB beállításai: a meglévő mezők és automatikus címjavaslat megmaradnak.
        wifi_group = QGroupBox("Wi-Fi ADB beállításai")
        wifi_layout = QFormLayout(wifi_group)
        ip_row = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Telefon IP-címe (pl. 192.168.1.25)")
        self.port_input = QLineEdit(str(DEFAULT_PORT))
        self.port_input.setMaximumWidth(100)
        self.port_input.setToolTip("Alapértelmezett port: 5555")
        ip_row.addWidget(self.address_input, 1)
        ip_row.addWidget(QLabel("Port:"))
        ip_row.addWidget(self.port_input)
        wifi_layout.addRow("Célcím:", ip_row)
        self.auto_discover_checkbox = QCheckBox("IP-címek felderítése USB-s ADB-kapcsolatból")
        self.auto_discover_checkbox.setChecked(True)
        wifi_layout.addRow("Automatizálás:", self.auto_discover_checkbox)
        note = QLabel("Az IP-felderítéshez működő USB-s ADB kell. A port megadása önmagában nem kapcsolja be a TCP/IP ADB-t.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        wifi_layout.addRow("Fontos:", note)
        root.addWidget(wifi_group)

        # Haladó gombok külön panelen, a kötelező sorrendet nem zavarva.
        advanced_group = QGroupBox("Haladó ADB-műveletek  —  megnyitás / bezárás")
        advanced_group.setCheckable(True)
        advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(advanced_group)
        advanced_content = QWidget()
        advanced_content_layout = QVBoxLayout(advanced_content)
        advanced_content_layout.setContentsMargins(0, 0, 0, 0)
        advanced_buttons = QHBoxLayout()
        self.start_server_button = QPushButton("ADB indítása")
        self.start_server_button.clicked.connect(self.start_server)
        self.restart_server_button = QPushButton("ADB újraindítása")
        self.restart_server_button.clicked.connect(self.restart_server)
        self.kill_server_button = QPushButton("ADB leállítása")
        self.kill_server_button.clicked.connect(self.kill_server)
        self.disconnect_button = QPushButton("Wi-Fi-eszköz leválasztása")
        self.disconnect_button.clicked.connect(self.disconnect_selected_device)
        self.manual_refresh_button = QPushButton("Eszközlista frissítése")
        self.manual_refresh_button.clicked.connect(self.refresh_devices)
        for button in (self.start_server_button, self.restart_server_button, self.kill_server_button,
                       self.disconnect_button, self.manual_refresh_button):
            advanced_buttons.addWidget(button)
        advanced_content_layout.addLayout(advanced_buttons)
        advanced_layout.addWidget(advanced_content)
        advanced_content.setVisible(False)
        root.addWidget(advanced_group)
        advanced_group.toggled.connect(advanced_content.setVisible)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        root.addStretch(1)

        # Részletes napló külön fülön.
        log_heading = QLabel("Részletes ADB- és hálózati napló")
        log_heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        log_layout.addWidget(log_heading)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setPlaceholderText("Az ADB-parancsok, eredmények és hibák itt jelennek meg.")
        log_layout.addWidget(self.log_view, 1)
        clear_button = QPushButton("Napló törlése")
        clear_button.clicked.connect(self.log_view.clear)
        log_layout.addWidget(clear_button)

        # A hét lépés gombjai az eredeti műveletekhez kapcsolódnak.
        self.refresh_button = self.step_buttons[0]
        self.test_button = self.step_buttons[1]
        self.discover_button = self.step_buttons[2]
        self.linux_network_button = self.step_buttons[3]
        self.enable_tcpip_button = self.step_buttons[4]
        self.connect_button = self.step_buttons[5]
        self.verify_wireless_button = self.step_buttons[6]
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.test_button.clicked.connect(self.test_selected_device)
        self.discover_button.clicked.connect(self.discover_selected_ips)
        self.linux_network_button.clicked.connect(self.inspect_linux_network)
        self.enable_tcpip_button.clicked.connect(self.enable_tcpip)
        self.connect_button.clicked.connect(self.connect_wifi)
        self.verify_wireless_button.clicked.connect(self.verify_wireless_connection)
        self.device_combo.currentIndexChanged.connect(self._update_buttons)
        self._render_steps()
        self._update_buttons()

    def _set_message_style(self, error: bool):
        """Make workflow feedback visually unmistakable when an error is active."""
        if error:
            self.step_status.setStyleSheet(
                "QLabel { color: #FF6B6B; background: #2A1719; "
                "border: 2px solid #E53935; border-radius: 6px; "
                "padding: 7px; font-weight: 600; }"
            )
        else:
            self.step_status.setStyleSheet(
                "QLabel { color: palette(text); background: palette(alternate-base); "
                "border: 1px solid palette(mid); border-radius: 6px; padding: 7px; }"
            )

    def _set_status_label_style(self, error: bool):
        """Red while no authorized online ADB device exists; normal otherwise."""
        if error:
            self.status_label.setStyleSheet(
                "QLabel { color: #FF6B6B; font-weight: 700; padding: 3px; "
                "border: 1px solid #E53935; border-radius: 4px; }"
            )
        else:
            self.status_label.setStyleSheet("QLabel { color: palette(text); padding: 3px; }")

    def _set_status_error(self, status_text: str, message: str):
        self.status_label.setText(status_text)
        self._set_status_label_style(error=True)
        self.step_status.setText(message)
        self._set_message_style(error=True)

    def _set_status_normal(self, status_text: str):
        self.status_label.setText(status_text)
        self._set_status_label_style(error=False)
        self._set_message_style(error=False)

    def _highlight_workflow_button(self, current: int):
        self.current_step = current
        for index, button in enumerate(self.step_buttons, start=1):
            if index in self.completed_steps:
                button.setStyleSheet(
                    "QPushButton { background-color: #E7F5EC; color: #176B39; "
                    "border: 1px solid #82C99A; border-radius: 5px; padding: 7px; }"
                )
            elif index == current:
                button.setStyleSheet(
                    "QPushButton { background-color: #23854B; color: white; "
                    "font-weight: 700; border: 1px solid #176B39; border-radius: 5px; padding: 7px; }"
                    "QPushButton:disabled { background-color: #9CBDA8; color: white; }"
                )
            else:
                button.setStyleSheet("")

    def _render_steps(self):
        for index, label in enumerate(self.step_status_labels, start=1):
            if index in self.completed_steps:
                label.setText("✓  Sikeresen ellenőrizve")
                label.setStyleSheet("color: #219653; font-weight: 600; padding-left: 4px;")
            elif index == self.current_step:
                label.setText("▶  Következik / folyamatban")
                label.setStyleSheet("color: #23854B; font-weight: 700; padding-left: 4px;")
            else:
                label.setText("○  Várakozik")
                label.setStyleSheet("color: palette(mid); padding-left: 4px;")
        count = len(self.completed_steps)
        self.overall_status.setText(f"{count} / 7 lépés kész")
        if count == 7:
            self.overall_status.setText("✓  Kapcsolat kész")
            self.overall_status.setStyleSheet(
                "QLabel { padding: 8px 12px; border: 1px solid #219653; "
                "border-radius: 7px; color: #219653; font-weight: 700; }"
            )
        else:
            self.overall_status.setStyleSheet(
                "QLabel { padding: 8px 12px; border: 1px solid #7A8795; "
                "border-radius: 7px; font-weight: 600; }"
            )
        self._highlight_workflow_button(self.current_step)

    def _set_step(self, current: int, completed: tuple[int, ...] = (), message: str = ""):
        """Update the seven-step workflow; completed steps are 1-based."""
        self.current_step = current
        self.completed_steps.update(completed)
        self._render_steps()
        if message:
            self.step_status.setText(message)
            self._set_message_style(error=False)

    def _log(self, message: str):
        self.log_view.appendPlainText(message)

    def _is_busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def _set_busy(self, busy: bool, status: str = ""):
        self.progress.setVisible(busy)
        for widget in (
            *self.step_buttons, self.start_server_button, self.restart_server_button,
            self.kill_server_button, self.disconnect_button, self.manual_refresh_button,
            self.device_combo, self.address_input, self.port_input,
            self.auto_discover_checkbox,
        ):
            widget.setEnabled(not busy)
        self._update_buttons()
        if status:
            self.status_label.setText(status)

    def _update_buttons(self, *_):
        busy = self._is_busy()
        device = self._selected_device()
        self.test_button.setEnabled(not busy and device is not None and device.state == "device")
        self.discover_button.setEnabled(not busy and device is not None and device.state == "device")
        self.enable_tcpip_button.setEnabled(
            not busy and device is not None and device.connection_type == "USB / helyi" and device.state == "device"
        )
        self.connect_button.setEnabled(not busy)
        self.linux_network_button.setEnabled(not busy)
        self.verify_wireless_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(
            not busy and device is not None and device.connection_type == "Wi-Fi / TCP"
        )
        if device is None:
            self.device_details_label.setText("Nem található kiválasztott ADB-eszköz. Ellenőrizd az USB-kábelt és az USB-hibakeresést.")
        else:
            self.device_details_label.setText(
                f"Eszközazonosító: {device.serial}   |   Állapot: {device.state}   |   Kapcsolat: {device.connection_type}"
                + (f"   |   Részletek: {device.details}" if device.details else "")
            )

    def _start_task(self, label: str, function: Callable, args=(), on_success=None):
        if self._task is not None:
            if self._task.isRunning():
                self._log(f"[FIGYELMEZTETÉS] A(z) „{self._task.label}” művelet még fut. Új művelet nem indult.")
                self.step_status.setText("Várakozás: az előző művelet befejezéséig nem indítható új feladat.")
                return
            # A thread befejeződött, de a Qt finished esemény még nem takarította el.
            self._task.deleteLater()
            self._task = None
        self._pending_success = on_success
        task = TaskThread(label, function, args=args, parent=self)
        self._task = task
        task.log_line.connect(self._log)
        task.succeeded.connect(self._task_succeeded)
        task.failed.connect(self._task_failed)
        task.finished.connect(self._task_finished)
        self._set_busy(True, f"Folyamatban: {label}")
        low_label = label.lower()
        # Futás közben csak az aktuális lépést jelöljük; a pipákat kizárólag
        # a sikeres visszatéréskor állítjuk be.
        if "usb nélküli wi-fi adb ellenőrzése" in low_label:
            current = 7
        elif "wifi adb kapcsolódás" in low_label or "wifi adb csatlakozás" in low_label:
            current = 6
        elif "tcp/ip adb engedélyezése" in low_label or "tcp/ip adb bekapcsolása" in low_label:
            current = 5
        elif "laptop hálózati adatainak felderítése" in low_label or "hálózati adatok lekérdezése" in low_label:
            current = 4
        elif "telefon ip-címeinek felderítése" in low_label or "ip-felderítés" in low_label:
            current = 3
        elif "shell-kapcsolat tesztelése" in low_label:
            current = 2
        elif "eszközlista" in low_label or "adb szerver, verzió" in low_label:
            current = 1
        else:
            current = self.current_step
        self._set_step(current, (), f"{current}/7 – Folyamatban: {label}")
        task.start()

    def _task_succeeded(self, result):
        if isinstance(result, dict) and "devices" in result:
            self._populate_devices(result["devices"])
            for line in result.get("log", []):
                self._log(line)
        elif isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], list):
            self._populate_devices(result[0])
            self._log(result[1])
        elif isinstance(result, list) and all(isinstance(x, NetworkAddress) for x in result):
            self._show_phone_addresses(result)
        else:
            self._log(f"[OK] {result}")
        if self._pending_success:
            self._pending_success(result)
        task_label = self._task.label.lower() if self._task else ""
        if "usb nélküli wi-fi adb ellenőrzése" in task_label:
            # Only _wireless_check_succeeded may mark the final step complete.
            pass
        elif "adb szerver, verzió és eszközlista" in task_label or "eszközlista" in task_label:
            # A successful adb command is NOT proof that a phone is connected.
            # Require at least one authorized, online device before completing step 1.
            devices = result.get("devices", []) if isinstance(result, dict) else []
            online_devices = [device for device in devices if device.state == "device"]
            if not online_devices:
                # Clear stale progress: a previously connected phone may have been unplugged.
                self.completed_steps.clear()
                self.current_step = 1
                self._render_steps()
                self._set_status_error(
                    "Nincs engedélyezett ADB-eszköz.",
                    "Az ADB működik, de nincs csatlakoztatott és engedélyezett telefon. "
                    "Csatlakoztasd USB-n, oldd fel a telefont, engedélyezd az USB-hibakeresést, "
                    "fogadd el az RSA-engedélykérést, majd frissítsd az eszközlistát."
                )
                self._log("[ELLENŐRZÉS SIKERTELEN] Nincs online, engedélyezett ADB-eszköz; az 1. lépés nem kap pipát.")
                return
            self._set_step(
                2, (1,),
                "1/7 kész – Az ADB-eszközlista legalább egy engedélyezett, online eszközt tartalmaz. "
                "Következik a telefonkapcsolat tényleges tesztelése."
            )
        elif "wifi adb kapcsolódás" in task_label or "wifi adb csatlakozás" in task_label:
            # The connect worker already confirms that this exact TCP device is online
            # and answers a real shell command. Do not auto-complete earlier steps.
            self._set_step(
                7, (6,),
                "6. lépés sikeres – A Wi-Fi ADB-kapcsolat és a shellteszt működik. "
                "Húzd ki az USB-kábelt, majd végezd el a 7. ellenőrzést."
            )
        elif "tcp/ip adb engedélyezése" in task_label or "tcp/ip adb bekapcsolása" in task_label:
            self._set_step(
                6, (5,),
                "Az 5. lépés parancsa sikeresen lefutott. Következik a Wi-Fi ADB-kapcsolódás; "
                "a tényleges elérhetőséget a 6. lépés ellenőrzi."
            )
        elif "telefon ip-címeinek felderítése" in task_label or "ip-felderítés" in task_label:
            addresses = result if isinstance(result, list) else []
            if not addresses:
                self._set_status_error(
                    "Nem találtam használható telefon-IP-címet.",
                    "A 3. lépés nem sikerült: a telefon nem adott vissza használható IPv4-címet. "
                    "Ez a lépés nem kap pipát. Ellenőrizd a telefon ADB-kapcsolatát és a hálózatot."
                )
                self._log("[ELLENŐRZÉS SIKERTELEN] Nincs használható telefon-IP-cím; a 3. lépés nem kap pipát.")
                return
            self._set_step(
                4, (3,),
                "3. lépés sikeres – A telefon legalább egy használható IPv4-címét megtaláltuk. "
                "Következik a laptop hálózatának felderítése."
            )
        elif "laptop hálózati adatainak felderítése" in task_label or "hálózati adatok lekérdezése" in task_label:
            summary, logs = result if isinstance(result, tuple) and len(result) == 2 else ("", [])
            if not logs or "nem található" in summary.lower() or "hiba" in summary.lower():
                self._set_status_error(
                    "A laptop hálózati adatai nem ellenőrizhetők.",
                    "A 4. lépés nem igazolható: a hálózati lekérdezés nem adott használható adatokat."
                )
                return
            self._set_step(
                5, (4,),
                "4. lépés sikeres – A laptop hálózati adatait lekérdeztük. Következik az 5. TCP/IP ADB-lépés USB-n."
            )
        elif "shell-kapcsolat tesztelése" in task_label:
            self._set_step(
                3, (2,),
                "2. lépés sikeres – A telefon valódi ADB shell-parancsra válaszolt. Következik a telefon IP-felderítése."
            )
        # Step 1 is handled above with an explicit online-device check.
        self._set_status_normal("A művelet sikeres.")

    def _task_failed(self, error: str):
        self._log(f"[HIBA] {error}")
        self._set_status_error(
            "Hiba történt – a kapcsolat nincs igazolva.",
            "A lépés nem sikerült. Az előző lépések állapota megmarad; javítsd a hibát, majd próbáld újra."
        )
        QMessageBox.warning(self, "ADB-művelet sikertelen", error)

    def _task_finished(self):
        task = self._task
        self._task = None
        self._pending_success = None
        self._set_busy(False)
        if task:
            task.deleteLater()

    def refresh_devices(self):
        self.adb = AdbService(self.settings.get("adb_path", "adb"))
        self._start_task("ADB szerver, verzió és eszközlista", self._refresh_worker)

    def _refresh_worker(self):
        log = []
        # Starting the server is idempotent and often fixes a stopped server.
        try:
            log.append(f"[ADB] {self.adb.start_server()}")
        except Exception as exc:
            log.append(f"[FIGYELMEZTETÉS] start-server: {exc}")
        log.append(f"[ADB] {self.adb.version()}")
        devices, status = self.adb.list_devices()
        log.append(f"[ADB] {status}")
        if not devices:
            log.append("Az ADB nem listázott eszközt.")
        for device in devices:
            log.append(f"Eszköz: {device.serial} | állapot={device.state} | típus={device.connection_type}")
        return {"devices": devices, "log": log}

    def _populate_devices(self, devices: list[AdbDevice]):
        previous = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in devices:
            self.device_combo.addItem(
                f"{device.serial} — {device.state} [{device.connection_type}]",
                device.serial,
            )
            self.device_combo.setItemData(
                self.device_combo.count() - 1, device, Qt.ItemDataRole.UserRole + 1
            )
        if previous:
            index = self.device_combo.findData(previous)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
        self.device_combo.blockSignals(False)
        online_devices = [device for device in devices if device.state == "device"]
        if online_devices:
            self._set_status_normal(f"Engedélyezett ADB-eszköz: {len(online_devices)}")
        else:
            self._set_status_error(
                "Nincs engedélyezett ADB-eszköz.",
                "Az ADB nem talált engedélyezett, online telefont. Csatlakoztasd a készüléket, "
                "old fel a képernyőt, engedélyezd az USB-hibakeresést és fogadd el az RSA-kérést, "
                "majd frissítsd az eszközlistát."
            )
        self._update_buttons()

    def _selected_serial(self) -> str:
        return self.device_combo.currentData() or ""

    def _selected_device(self) -> Optional[AdbDevice]:
        index = self.device_combo.currentIndex()
        if index < 0:
            return None
        return self.device_combo.itemData(index, Qt.ItemDataRole.UserRole + 1)

    def test_selected_device(self):
        serial = self._selected_serial()
        device = self._selected_device()
        if not serial or not device:
            QMessageBox.information(self, "Kapcsolat tesztelése", "Nincs kiválasztott eszköz.")
            return
        if device.state != "device":
            QMessageBox.warning(self, "Kapcsolat tesztelése", f"Az eszköz állapota: {device.state}")
            return
        self._start_task(
            f"Shell-kapcsolat tesztelése: {serial}",
            self.adb.test_device, args=(serial,),
        )

    def verify_wireless_connection(self):
        """Explicit step 5: verify an already listed TCP device without restarting ADB."""
        tcp_device = None
        for index in range(self.device_combo.count()):
            item = self.device_combo.itemData(index, Qt.ItemDataRole.UserRole + 1)
            if item and item.connection_type == "Wi-Fi / TCP" and item.state == "device":
                tcp_device = item
                break
        if tcp_device is None:
            QMessageBox.information(
                self, "5. USB nélküli ellenőrzés",
                "Nem található működő Wi-Fi/TCP ADB-eszköz.\n\n"
                "1. Ha még csatlakozik az USB-kábel, az 5–6. lépésben hozd létre a Wi-Fi ADB-kapcsolatot.\n"
                "2. Húzd ki az USB-kábelt.\n"
                "3. Frissítsd az eszközlistát, majd próbáld újra az 5. lépést."
            )
            return
        self.device_combo.setCurrentIndex(self.device_combo.findData(tcp_device.serial))
        self._set_step(5, (1, 2, 3, 4),
                       f"5/5 – USB nélküli ellenőrzés folyamatban: {tcp_device.serial}")
        self._start_task(
            f"USB nélküli Wi-Fi ADB ellenőrzése: {tcp_device.serial}",
            self.adb.test_device, args=(tcp_device.serial,),
            on_success=lambda result: self._wireless_check_succeeded(tcp_device.serial, result),
        )

    def _wireless_check_succeeded(self, serial: str, result: Any):
        # This test proves step 7 only; it must not silently tick earlier steps.
        self._set_step(7, (7,),
                       f"A 7. lépés sikeres – A Wi-Fi ADB-kapcsolat USB-kábel nélkül működik: {serial}")
        QMessageBox.information(
            self, "Wi-Fi ADB – sikeres",
            f"A vezeték nélküli ADB-kapcsolat működik.\n\nEszköz: {serial}\nEredmény: {result}"
        )

    def discover_selected_ips(self):
        serial = self._selected_serial()
        device = self._selected_device()
        if not serial or not device:
            QMessageBox.information(self, "IP-felderítés", "Előbb válassz ki egy ADB-eszközt.")
            return
        if device.state != "device":
            QMessageBox.warning(self, "IP-felderítés", f"Az eszköz állapota: {device.state}")
            return
        self._start_task(
            f"Telefon IP-címeinek felderítése: {serial}",
            self.adb.discover_phone_ipv4,
            args=(serial,),
        )

    def _show_phone_addresses(self, addresses: list[NetworkAddress]):
        if not addresses:
            self.phone_network_label.setText("Nem található használható IPv4-cím.")
            self._log("A telefonon nem sikerült külső IPv4-címet találni.")
            QMessageBox.information(
                self, "IP-felderítés",
                "Az ADB működik, de a telefon nem adott vissza használható IPv4-címet."
            )
            return
        self._log("Telefon IPv4-címei:")
        for address in addresses:
            self._log(f"  {address.interface}: {address.address}/{address.prefix}")
        self.phone_network_label.setText(";  ".join(
            f"{address.interface}: {address.address}/{address.prefix}" for address in addresses
        ))
        self._suggest_addresses(addresses)

    def _suggest_addresses(self, addresses: list[NetworkAddress]):
        if not addresses:
            return
        # Prefer the phone-side Wi-Fi/hotspot interface, never cellular ccmni or USB rndis.
        wifi_candidates = [
            a for a in addresses
            if any(k in a.interface.lower() for k in ("wlan", "ap", "swlan", "wifi"))
            and not any(k in a.interface.lower() for k in ("rndis", "ccmni", "rmnet", "pdp"))
        ]
        preferred = wifi_candidates[0] if wifi_candidates else addresses[0]
        self.address_input.setText(preferred.address)
        self.port_input.setText(str(DEFAULT_PORT))
        self._log(
            f"Javasolt cím: {preferred.address}:{DEFAULT_PORT} "
            f"(interfész: {preferred.interface}). A cím használhatóságát külön tesztelni kell."
        )

    def _target_address(self) -> str:
        host = self.address_input.text().strip()
        port_text = self.port_input.text().strip() or str(DEFAULT_PORT)
        if not host:
            raise ValueError("Add meg az IP-címet, vagy előbb futtasd a Telefon IP-címeinek felderítését.")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # Also permit a host:port pasted into the IP field.
            if re.fullmatch(r"[A-Za-z0-9_.-]+:\d{1,5}", host):
                host, _, embedded_port = host.rpartition(":")
                port_text = embedded_port
                try:
                    ip = ipaddress.ip_address(host)
                except ValueError as exc:
                    raise ValueError("Érvénytelen IP-cím.") from exc
            else:
                raise ValueError("Érvénytelen IP-cím.")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("A portnak egész számnak kell lennie.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("A portnak 1 és 65535 közé kell esnie.")
        return f"{ip.compressed}:{port}"

    def connect_wifi(self):
        try:
            target = self._target_address()
        except ValueError as exc:
            QMessageBox.warning(self, "Wi-Fi ADB", str(exc))
            return
        self._start_task(
            f"Wi-Fi ADB kapcsolódás: {target}",
            self._connect_worker,
            args=(target,),
        )

    def _connect_worker(self, target: str):
        # Ha a TCP-végpont már elérhető, ne futtassuk le újra az adb connect parancsot.
        devices_before, _ = self.adb.list_devices()
        existing = next((item for item in devices_before if item.serial == target and item.state == "device"), None)
        if existing is not None:
            message = f"Már csatlakoztatva: {target}"
            devices = devices_before
        else:
            message = self.adb.connect(target)
            devices, _ = self.adb.list_devices()
        device = next((item for item in devices if item.serial == target), None)
        if device is None:
            raise RuntimeError(
                f"{message}\nA kapcsolódás után az eszköz nem jelent meg az ADB-listában: {target}"
            )
        if device.state != "device":
            raise RuntimeError(f"{message}\nAz eszköz ADB-állapota: {device.state}")
        shell = self.adb.test_device(target)
        return {"devices": devices, "log": [message, f"Valódi shell-teszt: {shell}"]}

    def disconnect_selected_device(self):
        serial = self._selected_serial()
        device = self._selected_device()
        if not serial or device is None:
            QMessageBox.information(self, "Leválasztás", "Nincs kiválasztott eszköz.")
            return
        if device.connection_type != "Wi-Fi / TCP":
            QMessageBox.information(self, "Leválasztás", "Ez nem Wi-Fi/TCP ADB-kapcsolat.")
            return
        answer = QMessageBox.question(
            self, "Wi-Fi ADB leválasztása", f"Leválasztod ezt az eszközt?\n{serial}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_task(
            f"Wi-Fi ADB leválasztása: {serial}",
            self._disconnect_worker, args=(serial,),
        )

    def _disconnect_worker(self, serial: str):
        message = self.adb.disconnect(serial)
        devices, _ = self.adb.list_devices()
        return {"devices": devices, "log": [message]}

    def enable_tcpip(self):
        serial = self._selected_serial()
        device = self._selected_device()
        if not serial or device is None:
            QMessageBox.information(self, "TCP/IP ADB", "Válassz ki egy USB-n csatlakozó telefont.")
            return
        if device.connection_type != "USB / helyi" or device.state != "device":
            QMessageBox.warning(self, "TCP/IP ADB", "Ehhez működő USB-s ADB-kapcsolat szükséges.")
            return
        answer = QMessageBox.question(
            self, "TCP/IP ADB engedélyezése",
            f"Engedélyezed a TCP/IP ADB-t a kiválasztott eszközön a {DEFAULT_PORT} porton?\n\n"
            "A telefon Android-verziójától függően ez a művelet újraindíthatja az ADB TCP-kapcsolatát.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_task(
            f"TCP/IP ADB engedélyezése: {serial}",
            self._enable_tcpip_worker, args=(serial,),
        )

    def _enable_tcpip_worker(self, serial: str):
        out, err, rc, elapsed = self.adb.run(["-s", serial, "tcpip", str(DEFAULT_PORT)], timeout=10)
        text = out or err
        if rc:
            raise RuntimeError(f"adb tcpip hibakód: {rc}\n{text}")
        return f"{text or 'TCP/IP ADB parancs elküldve'} ({elapsed:.2f} s). Ezután a telefon IP-címére kell csatlakozni."

    def start_server(self):
        self._start_task("ADB-szerver indítása", self.adb.start_server)

    def kill_server(self):
        answer = QMessageBox.question(
            self, "ADB-szerver leállítása",
            "Leállítod az ADB-szervert? Ez az összes ADB-kapcsolatot megszakítja.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_task("ADB-szerver leállítása", self.adb.kill_server)

    def restart_server(self):
        answer = QMessageBox.question(
            self, "ADB-szerver újraindítása",
            "Az ADB-szerver újraindítása megszakítja az összes aktív ADB-kapcsolatot. Folytatod?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_task("ADB-szerver újraindítása", self._restart_worker)

    def _restart_worker(self):
        try:
            stopped = self.adb.kill_server()
        except Exception as exc:
            stopped = f"Leállítási üzenet: {exc}"
        started = self.adb.start_server()
        devices, _ = self.adb.list_devices()
        return {"devices": devices, "log": [stopped, started, "Eszközlista frissítve."]}

    def inspect_linux_network(self):
        self._start_task("Laptop hálózati adatainak felderítése", discover_linux_network,
                         on_success=self._show_linux_network)

    def _show_linux_network(self, result):
        summary, logs = result
        self.linux_network_label.setText(summary)
        for line in logs:
            self._log(line)
        self._log(summary)

    def closeEvent(self, event):
        if self._task is not None and self._task.isRunning():
            QMessageBox.warning(
                self, "Művelet folyamatban",
                "Várd meg az aktuális ADB-művelet befejezését, mielőtt bezárod ezt az ablakot."
            )
            event.ignore()
            return
        event.accept()


def open_module(parent, settings: Optional[dict] = None):
    window = QMainWindow(parent)
    window.setWindowTitle("Redmi Control Center — Kapcsolatok")
    window.resize(1000, 590)
    window.setMinimumSize(800, 500)
    window.setCentralWidget(ConnectionsWidget(window, settings))
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
    app = QApplication([])
    window = QMainWindow()
    window.setWindowTitle("Redmi Control Center — Kapcsolatok")
    window.resize(1000, 590)
    window.setMinimumSize(800, 500)
    window.setCentralWidget(ConnectionsWidget(settings={}))
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
