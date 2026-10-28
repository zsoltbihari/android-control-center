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
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QScrollArea, QDialog, QDialogButtonBox,
    QLabel, QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QGroupBox, QFormLayout, QSpinBox, QCheckBox, QMessageBox,
    QInputDialog, QFrame, QSizePolicy
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
QWidget { font-size: 10pt; }
QFrame#connectionPanel, QGroupBox { border: 1px solid #3984a8; border-radius: 7px; margin-top: 8px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #54b8df; font-weight: 600; }
QFrame#connectionPanel { background: rgba(45, 125, 160, 0.08); }
QLabel#heading { font-size: 15pt; font-weight: 700; }
QLabel#muted { color: palette(mid); }
QListWidget, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { border: 1px solid palette(mid); border-radius: 5px; padding: 5px; }
QListWidget::item { padding: 6px 5px; border-radius: 4px; min-height: 48px; }
QListWidget::item:selected { background: #245f7a; color: white; }
QPushButton { border: 1px solid #54839a; border-radius: 5px; padding: 7px 11px; }
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
        self.apps: list[dict] = []
        self._icon_cache: dict[str, QIcon] = {}
        self.profiles = [dict(x) for x in DEFAULT_PROFILES]
        self.app_settings: dict[str, dict] = {}
        self.favorites: set[str] = set()
        self._loading = False
        self._build_ui()
        self._load_config()
        self._refresh_profile_combo()
        self.refresh_devices()
        self.refresh_apps()
        self._set_status("Készen áll. Válassz alkalmazást és indítási profilt.")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)
        title = QLabel(APP_TITLE); title.setObjectName("heading")
        root.addWidget(title)
        subtitle = QLabel("Alkalmazásonként rendeld hozzá a virtuális kijelző méretét.")
        subtitle.setObjectName("muted"); subtitle.setWordWrap(True); root.addWidget(subtitle)

        conn = QFrame(); conn.setObjectName("connectionPanel")
        ch = QHBoxLayout(conn); ch.setContentsMargins(9, 7, 9, 7)
        ch.addWidget(QLabel("Telefonkapcsolat:"))
        self.device_combo = QComboBox(); self.device_combo.setMinimumWidth(220); ch.addWidget(self.device_combo, 1)
        self.connection_status = QLabel("Nincs ellenőrizve"); ch.addWidget(self.connection_status)
        refresh_dev = QPushButton("Frissítés"); refresh_dev.clicked.connect(self.refresh_devices); ch.addWidget(refresh_dev)
        root.addWidget(conn)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Keresés név vagy csomagnév szerint…")
        self.search_edit.textChanged.connect(self._filter_apps); filters.addWidget(self.search_edit, 3)
        self.app_type_filter = QComboBox()
        self.app_type_filter.addItem("Minden alkalmazás", "all")
        self.app_type_filter.addItem("Felhasználói", "user")
        self.app_type_filter.addItem("Rendszer", "system")
        self.app_type_filter.currentIndexChanged.connect(self._filter_apps); filters.addWidget(self.app_type_filter, 1)
        self.favorites_only = QCheckBox("Csak kedvencek"); self.favorites_only.toggled.connect(self._filter_apps); filters.addWidget(self.favorites_only)
        reload_apps = QPushButton("Lista frissítése"); reload_apps.clicked.connect(self.refresh_apps); filters.addWidget(reload_apps)
        root.addLayout(filters)

        # Rejtett kompatibilitási lista: a meglévő kiválasztási és parancslogika ezt használja.
        self.app_list = QListWidget(self); self.app_list.hide()
        self.app_list.currentItemChanged.connect(self._app_selected)
        self.cards_area = QScrollArea(); self.cards_area.setWidgetResizable(True); self.cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget(); self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(2, 2, 2, 2); self.cards_layout.setSpacing(8)
        self.cards_area.setWidget(self.cards_host); root.addWidget(self.cards_area, 1)

        # Beállítások és indítási parancs: külön modal ablakban jelenik meg.
        self.profile_combo = QComboBox()
        self.profile_name = QLineEdit(); self.profile_name.setPlaceholderText("Profil neve")
        self.width_spin = QSpinBox(); self.width_spin.setRange(240, 4096); self.width_spin.setSingleStep(80)
        self.height_spin = QSpinBox(); self.height_spin.setRange(320, 4096); self.height_spin.setSingleStep(80)
        self.dpi_spin = QSpinBox(); self.dpi_spin.setRange(72, 640); self.dpi_spin.setValue(240)
        self.crop_enabled = QCheckBox("Kivágás bekapcsolása")
        self.crop_w = QSpinBox(); self.crop_w.setRange(1,4096); self.crop_w.setValue(720)
        self.crop_h = QSpinBox(); self.crop_h.setRange(1,4096); self.crop_h.setValue(1200)
        self.crop_x = QSpinBox(); self.crop_x.setRange(0,4096)
        self.crop_y = QSpinBox(); self.crop_y.setRange(0,4096)
        self.extra_args = QLineEdit(); self.extra_args.setPlaceholderText("További scrcpy-paraméterek, pl. --always-on-top")
        self.command_edit = QPlainTextEdit(); self.command_edit.setPlaceholderText("Indítási parancs"); self.command_edit.setMinimumHeight(65)
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        self.crop_enabled.toggled.connect(self._update_command)
        for spin in (self.crop_w, self.crop_h, self.crop_x, self.crop_y): spin.valueChanged.connect(self._update_command)
        self.extra_args.textChanged.connect(self._update_command); self.command_edit.textChanged.connect(self._command_edited)

        actions = QHBoxLayout()
        self.launch_btn = QPushButton("▶  Alkalmazás indítása"); self.launch_btn.setObjectName("primary"); self.launch_btn.clicked.connect(self.launch_app); actions.addWidget(self.launch_btn)
        stop_btn = QPushButton("scrcpy leállítása"); stop_btn.setObjectName("danger"); stop_btn.clicked.connect(self.stop_scrcpy); actions.addWidget(stop_btn)
        save_btn = QPushButton("Beállítások mentése"); save_btn.setObjectName("save"); save_btn.clicked.connect(lambda: self.save_config()); actions.addWidget(save_btn)
        root.addLayout(actions)
        self.status_label = QLabel(" "); self.status_label.setWordWrap(True); self.status_label.setObjectName("muted"); root.addWidget(self.status_label)

    def _show_profile_dialog(self, package):
        item = next((self.app_list.item(i) for i in range(self.app_list.count()) if self.app_list.item(i).data(Qt.ItemDataRole.UserRole) == package), None)
        if item is not None: self.app_list.setCurrentItem(item)
        dialog = QDialog(self); dialog.setWindowTitle(f"Profil beállításai — {KNOWN_APPS.get(package, package)}")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("Profil:", self.profile_combo)
        form.addRow("Profil neve:", self.profile_name)
        dimensions = QHBoxLayout(); dimensions.addWidget(QLabel("Szélesség")); dimensions.addWidget(self.width_spin); dimensions.addWidget(QLabel("Magasság")); dimensions.addWidget(self.height_spin)
        form.addRow("Méret:", dimensions); form.addRow("DPI:", self.dpi_spin)
        form.addRow(self.crop_enabled)
        crop = QGridLayout(); crop.addWidget(QLabel("Szélesség"),0,0); crop.addWidget(self.crop_w,0,1); crop.addWidget(QLabel("Magasság"),0,2); crop.addWidget(self.crop_h,0,3); crop.addWidget(QLabel("X"),1,0); crop.addWidget(self.crop_x,1,1); crop.addWidget(QLabel("Y"),1,2); crop.addWidget(self.crop_y,1,3)
        form.addRow("Kivágás:", crop); form.addRow("Extra paraméterek:", self.extra_args)
        layout.addLayout(form); layout.addWidget(QLabel("Indítási parancs:")); layout.addWidget(self.command_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Mentés")
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()
        self._store_current_app_settings(); self._render_app_cards()

    def _render_app_cards(self):
        while self.cards_layout.count():
            child = self.cards_layout.takeAt(0); w = child.widget()
            if w: w.deleteLater()
        for i in range(self.app_list.count()):
            item = self.app_list.item(i); package = item.data(Qt.ItemDataRole.UserRole)
            app = next((a for a in self.apps if a["package"] == package), None)
            if not app: continue
            card = QFrame(); card.setObjectName("appCard"); card.setStyleSheet("QFrame#appCard { border: 1px solid #315b70; border-radius: 8px; background: rgba(20,32,40,0.7); }")
            row = QHBoxLayout(card); row.setContentsMargins(10,8,10,8); row.setSpacing(12)
            icon_label = QLabel(); icon_label.setFixedSize(48,48); icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = self._application_icon(package)
            if not icon.isNull(): icon_label.setPixmap(icon.pixmap(44,44))
            else: icon_label.setPixmap(self.style().standardIcon(self.style().StandardPixmap.SP_DesktopIcon).pixmap(36,36))
            row.addWidget(icon_label)
            info = QVBoxLayout(); name = QLabel(app["name"]); name.setStyleSheet("font-weight: 700; font-size: 11pt;")
            kind = "Felhasználói alkalmazás" if app["user"] else "Rendszeralkalmazás"
            meta = QLabel(f"{package}  ·  {kind}" + ("  ·  ★ Kedvenc" if package in self.favorites else "")); meta.setObjectName("muted"); meta.setWordWrap(True)
            info.addWidget(name); info.addWidget(meta); row.addLayout(info, 3)
            data = self.app_settings.get(package, {})
            profile_name = data.get("profile")
            prof = next((p for p in self.profiles if p["name"] == profile_name), None)
            if not prof and package == "org.opencpn.opencpn": prof = next((p for p in self.profiles if p["name"] == "OpenCPN – 1920×1080"), None)
            preview = QVBoxLayout(); preview.setSpacing(2)
            if prof:
                label = QLabel(f"{prof['width']} × {prof['height']}  ·  {prof.get('dpi',240)} DPI"); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setStyleSheet("font-weight:600; color:#80cfee;")
                box = QWidget(); box.setMinimumSize(110,42); box.setMaximumSize(130,48)
                bl = QVBoxLayout(box); bl.setContentsMargins(0,0,0,0); bl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                shape = QFrame(); shape.setStyleSheet("background:#203b49; border:1px solid #55b9ed; border-radius:3px;")
                ratio = prof['width'] / max(1, prof['height']); maxw,maxh=104,34
                if ratio >= maxw/maxh: w,h=maxw,max(1,int(maxw/ratio))
                else: h,w=maxh,max(1,int(maxh*ratio))
                shape.setFixedSize(w,h); bl.addWidget(shape); preview.addWidget(label); preview.addWidget(box)
            else:
                label = QLabel("Nincs ablakprofil"); label.setStyleSheet("color:#a9b9c2;"); preview.addWidget(label)
            row.addLayout(preview, 2)
            if prof:
                edit = QPushButton("⚙"); edit.setToolTip("A profil beállításai"); edit.setFixedWidth(38); edit.clicked.connect(lambda checked=False,p=package: self._show_profile_dialog(p)); row.addWidget(edit)
            else:
                create = QPushButton("＋ Profil létrehozása"); create.setObjectName("save"); create.clicked.connect(lambda checked=False,p=package: self._create_app_profile(p)); row.addWidget(create)
            fav = QPushButton("★" if package in self.favorites else "☆"); fav.setToolTip("Kedvenc ki/be"); fav.setFixedWidth(38); fav.clicked.connect(lambda checked=False,p=package: self._toggle_favorite_for(p)); row.addWidget(fav)
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

    def _set_status(self, text: str, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #e57373;" if error else "")

    def refresh_devices(self):
        self.device_combo.clear()
        if not shutil.which("adb") and self.adb == "/usr/bin/adb" and not Path(self.adb).exists():
            self.connection_status.setText("ADB nem található")
            self._set_status("Nem találom az adb parancsot. Telepítsd, vagy ellenőrizd a PATH változót.", True)
            return
        try:
            result = _run([self.adb, "devices"], 5)
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
        """Android-ikon lekérése és gyorsítótárazása APK-erőforrásból.

        ADB-vel lehúzza a base.apk-t, az aapt/aapt2 segítségével megkeresi
        az ikon útvonalát, majd a képet a helyi cache-be menti. Ha az ikon
        vektoros/adaptív, vagy nincs aapt, a Qt alapértelmezett ikonja marad.
        """
        if package in self._icon_cache:
            return self._icon_cache[package]
        cached = ICON_CACHE_DIR / f"{package.replace('/', '_')}.png"
        if cached.is_file():
            icon = QIcon(str(cached))
            if not icon.isNull():
                self._icon_cache[package] = icon
                return icon

        icon = QIcon()
        tools = [shutil.which("aapt"), shutil.which("aapt2")]
        tool = next((t for t in tools if t), None)
        if tool and self.device_combo.currentData():
            try:
                prefix = self._adb_prefix()
                paths = _run(prefix + ["shell", "pm", "path", package], 6)
                apk_path = next((line.split(":", 1)[1].strip() for line in paths.stdout.splitlines()
                                 if line.startswith("package:") and ":" in line), None)
                if apk_path:
                    with tempfile.TemporaryDirectory(prefix="rcc-icon-") as tmp:
                        local_apk = Path(tmp) / "base.apk"
                        pulled = _run(prefix + ["pull", apk_path, str(local_apk)], 20)
                        if pulled.returncode == 0 and local_apk.is_file():
                            if Path(tool).name == "aapt2":
                                badging = _run([tool, "dump", "badging", str(local_apk)], 10)
                            else:
                                badging = _run([tool, "dump", "badging", str(local_apk)], 10)
                            icon_path = None
                            for line in badging.stdout.splitlines():
                                if line.startswith("application:") and " icon='" in line:
                                    icon_path = line.split(" icon='", 1)[1].split("'", 1)[0]
                                    break
                            if icon_path:
                                with zipfile.ZipFile(local_apk) as archive:
                                    try:
                                        data = archive.read(icon_path)
                                    except KeyError:
                                        data = b""
                                pixmap = QPixmap()
                                if data and pixmap.loadFromData(data) and not pixmap.isNull():
                                    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                                    pixmap.save(str(cached), "PNG")
                                    icon = QIcon(pixmap)
            except (OSError, subprocess.SubprocessError, zipfile.BadZipFile, StopIteration):
                pass
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
        self.crop_w.setValue(int(data.get("crop_w", 720)))
        self.crop_h.setValue(int(data.get("crop_h", 1200)))
        self.crop_x.setValue(int(data.get("crop_x", 0)))
        self.crop_y.setValue(int(data.get("crop_y", 0)))
        if "width" in data:
            self.width_spin.setValue(int(data.get("width", self.width_spin.value())))
            self.height_spin.setValue(int(data.get("height", self.height_spin.value())))
            self.dpi_spin.setValue(int(data.get("dpi", self.dpi_spin.value())))
        self._loading = False
        saved_command = data.get("command")
        if saved_command:
            self.command_edit.blockSignals(True)
            self.command_edit.setPlainText(saved_command)
            self.command_edit.blockSignals(False)
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

    def _update_command(self, *_):
        if self._loading or not hasattr(self, "command_edit"):
            return
        package = self.current_package()
        if not package:
            self.command_edit.setPlainText("")
            return
        profile = self.profiles[self.profile_combo.currentIndex()] if 0 <= self.profile_combo.currentIndex() < len(self.profiles) else DEFAULT_PROFILES[0]
        args = [self.scrcpy, f"--new-display={self.width_spin.value()}x{self.height_spin.value()}/{self.dpi_spin.value()}", f"--start-app={package}"]
        if self.crop_enabled.isChecked():
            # scrcpy 5.x: --crop=WIDTH:HEIGHT:X:Y
            args.append(f"--crop={self.crop_w.value()}:{self.crop_h.value()}:{self.crop_x.value()}:{self.crop_y.value()}")
        try:
            args.extend(shlex.split(self.extra_args.text()))
        except ValueError:
            pass
        self.command_edit.blockSignals(True)
        self.command_edit.setPlainText(shlex.join(args))
        self.command_edit.blockSignals(False)
        self._store_current_app_settings()

    def _store_current_app_settings(self):
        package = self.current_package()
        if not package or not hasattr(self, "extra_args"):
            return
        self.app_settings[package] = {
            "extra_args": self.extra_args.text(),
            "crop_enabled": self.crop_enabled.isChecked(),
            "crop_w": self.crop_w.value(), "crop_h": self.crop_h.value(),
            "crop_x": self.crop_x.value(), "crop_y": self.crop_y.value(),
            "profile": self.profile_combo.currentText(),
            "width": self.width_spin.value(), "height": self.height_spin.value(),
            "dpi": self.dpi_spin.value(),
            "command": self.command_edit.toPlainText(),
        }

    def _command_edited(self):
        if not self._loading:
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
        # Ne használjunk shell=True: a szerkeszthető parancs argumentumlistaként fut.
        self._store_current_app_settings()
        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._read_process_output(p))
        proc.errorOccurred.connect(lambda err, p=proc: self._set_status(f"scrcpy indítási hiba: {p.errorString()}", True))
        proc.finished.connect(lambda code, status, p=proc: self._process_finished(p, code))
        proc.start()
        self.processes.append(proc)
        self._set_status(f"Indítási kérés elküldve: {package} — {text}")

    def _read_process_output(self, proc):
        try:
            output = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
            if output:
                self._set_status(output[-1000:])
        except Exception:
            pass

    def _process_finished(self, proc, code):
        self._set_status(f"scrcpy folyamat befejeződött (kilépési kód: {code}).")
        if proc in self.processes:
            self.processes.remove(proc)
        proc.deleteLater()

    def stop_scrcpy(self):
        stopped = 0
        for proc in list(self.processes):
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(1200):
                    proc.kill()
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
            if isinstance(data.get("profiles"), list) and data["profiles"]:
                self.profiles = data["profiles"]
            # A korábbi konfigurációkba is bekerül az OpenCPN alapprofil, ha hiányzik.
            if not any(p.get("name") == "OpenCPN – 1920×1080" for p in self.profiles):
                self.profiles.append({"name": "OpenCPN – 1920×1080", "width": 1920, "height": 1080, "dpi": 240})
            self.favorites = set(data.get("favorites", []))
            self.app_settings = data.get("apps", {})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            QTimer.singleShot(0, lambda: self._set_status(f"A mentett beállítások nem olvashatók: {exc}", True))


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
