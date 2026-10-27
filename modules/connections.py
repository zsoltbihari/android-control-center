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
    QFormLayout, QGroupBox, QMessageBox, QCheckBox, QTabWidget
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
        self._build_ui()
        self._log(f"ADB útvonalbeállítás: {self.adb.adb_path}")
        self.refresh_devices()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(6, 5, 6, 5)
        outer_layout.setSpacing(3)

        self.tabs = QTabWidget(self)
        outer_layout.addWidget(self.tabs)

        connections_page = QWidget(self.tabs)
        root = QVBoxLayout(connections_page)
        root.setContentsMargins(6, 5, 6, 5)
        root.setSpacing(3)
        self.tabs.addTab(connections_page, "Kapcsolatok és automatikus felderítés")

        log_page = QWidget(self.tabs)
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(6, 5, 6, 5)
        log_layout.setSpacing(4)
        self.tabs.addTab(log_page, "Log")

        title = QLabel("Kapcsolatok és automatikus felderítés")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        # Kompakt, kéthasábos folyamatjelző: a hét lépés két oszlopban jelenik meg.
        steps_group = QGroupBox("Kapcsolódás lépésről lépésre")
        steps_layout = QVBoxLayout(steps_group)
        steps_layout.setContentsMargins(6, 4, 6, 4)
        steps_layout.setSpacing(3)

        self.step_texts = [
            "1. ADB ellenőrzése és eszközlista frissítése",
            "2. Telefon kiválasztása és shellkapcsolat tesztelése",
            "3. Telefon IP-címeinek felderítése USB-n",
            "4. Laptop hálózatának felderítése",
            "5. TCP/IP ADB bekapcsolása USB-n",
            "6. Wi-Fi ADB-kapcsolat létrehozása és shellteszt",
            "7. USB-kábel nélkül a kapcsolat újbóli ellenőrzése",
        ]
        steps_columns = QHBoxLayout()
        steps_columns.setContentsMargins(0, 0, 0, 0)
        steps_columns.setSpacing(18)
        self.step_labels = []
        self._step_columns = [QVBoxLayout(), QVBoxLayout()]
        for column in self._step_columns:
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(1)
            steps_columns.addLayout(column, 1)

        # Három lépés a bal, négy a jobb oszlopban; a 7. lépés is
        # a jobb oldalra kerül, hogy a két oszlop magassága kiegyensúlyozott legyen.
        column_for_step = (0, 1, 0, 1, 0, 1, 1)
        for index, step_text in enumerate(self.step_texts):
            label = QLabel("○  " + step_text)
            label.setWordWrap(True)
            self._step_columns[column_for_step[index]].addWidget(label)
            self.step_labels.append(label)
        for column in self._step_columns:
            column.addStretch(1)
        steps_layout.addLayout(steps_columns)

        # A soron következő művelet gombja zöld hátteret kap; kattintás/futás után visszaáll.
        self._workflow_buttons = {}
        self._normal_button_style = ""
        self._active_button_style = (
            "QPushButton { background-color: #2e8b57; color: white; font-weight: 600; }"
            "QPushButton:disabled { background-color: #b8c9be; color: #f5f5f5; }"
        )
        self.step_status = QLabel(
            "Kezdés: nyomd meg az 1. Eszközlista frissítése gombot. "
            "Ezután kövesd a zölddel jelölt következő gombot."
        )
        self.step_status.setWordWrap(True)
        steps_layout.addWidget(self.step_status)
        root.addWidget(steps_group)
        # A részletes sorrend a lépésjelzőben jelenik meg; külön hosszú magyarázó sor nincs.

        status_group = QGroupBox("ADB-szerver")
        status_row = QHBoxLayout(status_group)
        self.status_label = QLabel("Ellenőrzés…")
        self.refresh_button = QPushButton("1. Eszközlista frissítése")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.start_server_button = QPushButton("ADB indítása")
        self.start_server_button.clicked.connect(self.start_server)
        self.restart_server_button = QPushButton("ADB újraindítása")
        self.restart_server_button.clicked.connect(self.restart_server)
        self.kill_server_button = QPushButton("ADB leállítása")
        self.kill_server_button.clicked.connect(self.kill_server)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.refresh_button)
        status_row.addWidget(self.start_server_button)
        status_row.addWidget(self.restart_server_button)
        status_row.addWidget(self.kill_server_button)
        root.addWidget(status_group)

        device_group = QGroupBox("Észlelt ADB-eszközök")
        device_layout = QVBoxLayout(device_group)
        self.device_combo = QComboBox()
        device_layout.addWidget(self.device_combo)
        dev_buttons = QHBoxLayout()
        self.test_button = QPushButton("2. Kapcsolat tesztelése")
        self.test_button.clicked.connect(self.test_selected_device)
        self.discover_button = QPushButton("3. Telefon IP-címeinek felderítése")
        self.discover_button.clicked.connect(self.discover_selected_ips)
        self.disconnect_button = QPushButton("Wi-Fi-eszköz leválasztása")
        self.disconnect_button.clicked.connect(self.disconnect_selected_device)
        dev_buttons.addWidget(self.test_button)
        dev_buttons.addWidget(self.discover_button)
        dev_buttons.addWidget(self.disconnect_button)
        self.verify_wireless_button = QPushButton("7. USB nélkül: Wi-Fi-kapcsolat ellenőrzése")
        self.verify_wireless_button.setToolTip("Előbb húzd ki az USB-kábelt, majd ezzel teszteld a Wi-Fi/TCP ADB-kapcsolatot.")
        self.verify_wireless_button.clicked.connect(self.verify_wireless_connection)
        device_layout.addLayout(dev_buttons)
        device_layout.addWidget(self.verify_wireless_button)
        root.addWidget(device_group)

        wifi_group = QGroupBox("Wi-Fi ADB")
        wifi_layout = QFormLayout(wifi_group)
        ip_row = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Telefon IP-címe (pl. 10.224.12.162)")
        self.port_input = QLineEdit(str(DEFAULT_PORT))
        self.port_input.setMaximumWidth(90)
        self.port_input.setToolTip("Alapértelmezett port: 5555")
        ip_row.addWidget(self.address_input, 1)
        ip_row.addWidget(QLabel("Port:"))
        ip_row.addWidget(self.port_input)
        self.connect_button = QPushButton("6. Wi-Fi ADB-kapcsolódás")
        self.connect_button.clicked.connect(self.connect_wifi)
        ip_row.addWidget(self.connect_button)
        wifi_layout.addRow("IP-cím:", ip_row)

        self.auto_discover_checkbox = QCheckBox("IP-címek felderítése USB-s ADB-kapcsolatból")
        self.auto_discover_checkbox.setChecked(True)
        wifi_layout.addRow("", self.auto_discover_checkbox)
        self.enable_tcpip_button = QPushButton("5. USB-n: TCP/IP ADB bekapcsolása (5555)")
        self.enable_tcpip_button.clicked.connect(self.enable_tcpip)
        wifi_layout.addRow("", self.enable_tcpip_button)
        note = QLabel("Az automatikus IP-felderítéshez USB-s ADB kell; a port önmagában nem engedélyezi a TCP/IP ADB-t.")
        note.setWordWrap(True)
        wifi_layout.addRow("", note)
        root.addWidget(wifi_group)

        linux_group = QGroupBox("Laptop hálózata")
        linux_row = QHBoxLayout(linux_group)
        self.linux_network_label = QLabel("A laptop hálózati adatainak lekérdezése.")
        self.linux_network_label.setWordWrap(True)
        self.linux_network_button = QPushButton("4. Laptop hálózatának felderítése")
        self.linux_network_button.clicked.connect(self.inspect_linux_network)
        linux_row.addWidget(self.linux_network_label, 1)
        linux_row.addWidget(self.linux_network_button)
        root.addWidget(linux_group)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        log_heading = QLabel("Kapcsolati napló")
        log_layout.addWidget(log_heading)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        log_layout.addWidget(self.log_view, 1)

        clear_button = QPushButton("Napló törlése")
        clear_button.clicked.connect(self.log_view.clear)
        log_layout.addWidget(clear_button)

        self._workflow_buttons = {
            1: [self.refresh_button],
            2: [self.test_button],
            3: [self.discover_button],
            4: [self.linux_network_button],
            5: [self.enable_tcpip_button],
            6: [self.connect_button],
            7: [self.verify_wireless_button],
        }
        self._highlight_workflow_button(1)
        self.device_combo.currentIndexChanged.connect(self._update_buttons)
        self._update_buttons()

    def _highlight_workflow_button(self, current: int):
        for buttons in self._workflow_buttons.values():
            for button in buttons:
                button.setStyleSheet(self._normal_button_style)
        for button in self._workflow_buttons.get(current, []):
            button.setStyleSheet(self._active_button_style)

    def _set_step(self, current: int, completed: tuple[int, ...] = (), message: str = ""):
        """Update the visible numbered workflow; indices are 1-based."""
        self._highlight_workflow_button(current)
        for index, label in enumerate(self.step_labels, start=1):
            if index in completed:
                marker = "✓"
                label.setStyleSheet("color: #2e8b57; font-weight: 600;")
            elif index == current:
                marker = "▶"
                label.setStyleSheet("font-weight: 700;")
            else:
                marker = "○"
                label.setStyleSheet("color: palette(mid);")
            label.setText(f"{marker}  {self.step_texts[index - 1]}")
        if message:
            self.step_status.setText(message)

    def _log(self, message: str):
        self.log_view.appendPlainText(message)

    def _is_busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def _set_busy(self, busy: bool, status: str = ""):
        self.progress.setVisible(busy)
        for widget in (
            self.refresh_button, self.start_server_button, self.restart_server_button,
            self.kill_server_button, self.connect_button, self.device_combo,
            self.discover_button, self.enable_tcpip_button, self.linux_network_button,
            self.verify_wireless_button,
        ):
            widget.setEnabled(not busy)
        self._update_buttons()
        if status:
            self.status_label.setText(status)

    def _update_buttons(self, *_):
        busy = self._is_busy()
        device = self._selected_device()
        self.test_button.setEnabled(not busy and device is not None)
        self.discover_button.setEnabled(not busy and device is not None)
        self.disconnect_button.setEnabled(
            not busy and device is not None and device.connection_type == "Wi-Fi / TCP"
        )
        self.verify_wireless_button.setEnabled(not busy)

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
        if "felderít" in low_label or "hálózat" in low_label or "ip-cím" in low_label:
            self._set_step(3, (1, 2), f"3/5 – {label}. A program a telefon és a laptop hálózati címeit vizsgálja.")
        elif "wifi adb kapcsolódás" in low_label or "wifi adb csatlakozás" in low_label:
            self._set_step(4, (1, 2, 3), f"4/5 – {label}. A kapcsolódás után shellteszt következik.")
        elif "shell-kapcsolat" in low_label:
            self._set_step(2, (1,), f"2/5 – {label}. A telefon válaszát ellenőrizzük.")
        elif "eszközlista" in low_label or "adb szerver" in low_label:
            self._set_step(1, (), f"1/5 – {label}. ADB-szerver és eszközlista ellenőrzése.")
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
            # _wireless_check_succeeded marks the final step.
            pass
        elif "wifi adb kapcsolódás" in task_label or "wifi adb csatlakozás" in task_label:
            self._set_step(
                7, (1, 2, 3, 4, 5, 6),
                "6/7 kész – A Wi-Fi ADB-kapcsolat és a shellteszt sikerült. "
                "Most húzd ki az USB-kábelt, majd nyomd meg a zöld 7. ellenőrző gombot."
            )
        elif "tcp/ip adb engedélyezése" in task_label or "tcp/ip adb bekapcsolása" in task_label:
            self._set_step(
                6, (1, 2, 3, 4, 5),
                "5/7 kész – A TCP/IP ADB engedélyezve van. Következik a 6. Wi-Fi ADB-kapcsolódás."
            )
        elif "telefon ip-címeinek felderítése" in task_label or "ip-felderítés" in task_label:
            self._set_step(
                4, (1, 2, 3),
                "3/7 kész – A telefon IP-címeit megtaláltuk. Következik a 4. laptop-hálózatfelderítés."
            )
        elif "laptop hálózati adatainak felderítése" in task_label or "hálózati adatok lekérdezése" in task_label:
            self._set_step(
                5, (1, 2, 3, 4),
                "4/7 kész – A laptop hálózati adatai megvannak. Következik az 5. TCP/IP ADB-lépés USB-n."
            )
        elif "shell-kapcsolat tesztelése" in task_label:
            self._set_step(
                3, (1, 2),
                "2/7 kész – A telefon válaszol. Következik a 3. telefon-IP-felderítés."
            )
        elif "eszközlista" in task_label:
            self._set_step(
                2, (1,),
                "1/7 kész – Az ADB és az eszközlista rendben. Következik a 2. kapcsolatellenőrzés."
            )
        self.status_label.setText("A művelet sikeres.")

    def _task_failed(self, error: str):
        self._log(f"[HIBA] {error}")
        self.status_label.setText("Hiba történt – a kapcsolat nincs igazolva.")
        self.step_status.setText("A lépés nem sikerült. Az előző lépések állapota megmarad; javítsd a hibát, majd próbáld újra.")
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
        self._set_step(5, (1, 2, 3, 4, 5),
                       f"5/5 kész – A Wi-Fi ADB-kapcsolat működik USB-kábel nélkül: {serial}")
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
            self._log("A telefonon nem sikerült külső IPv4-címet találni.")
            QMessageBox.information(
                self, "IP-felderítés",
                "Az ADB működik, de a telefon nem adott vissza használható IPv4-címet."
            )
            return
        self._log("Telefon IPv4-címei:")
        for address in addresses:
            self._log(f"  {address.interface}: {address.address}/{address.prefix}")
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
