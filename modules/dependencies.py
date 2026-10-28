#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACC – Android Control Center: rendszerfüggőség-ellenőrző Debian/Ubuntu-alapú rendszerekhez."""
from __future__ import annotations
import importlib.util, json, os, shlex, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

CONFIG_DIR = Path.home() / ".config" / "android-control-center"
SETTINGS_FILE = CONFIG_DIR / "dependencies.json"

@dataclass(frozen=True)
class Dependency:
    name: str
    description: str
    package: str
    command: str | None = None
    python_module: str | None = None
    required: bool = True

DEPENDENCIES = [
    Dependency("Python 3", "A program futtatókörnyezete.", "python3", "python3"),
    Dependency("PyQt6", "A grafikus felület megjelenítése.", "python3-pyqt6", python_module="PyQt6"),
    Dependency("ADB", "Android Debug Bridge: kapcsolat, eszközadatok, fájl- és alkalmazásműveletek.", "adb", "adb"),
    Dependency("scrcpy", "Android-képernyőtükrözés és – engedélyezett esetben – vezérlés. Frissítése a hivatalos GitHub-kiadásból történik.", "", "scrcpy"),
    Dependency("Fastboot", "Bootloader/fastboot módban lévő készülékek kezelése.", "fastboot", "fastboot", required=False),
    Dependency("sudo", "Rendszergazdai jogosultság kérése a terminálban.", "sudo", "sudo"),
    Dependency("pkill", "Folyamatok leállítása, például scrcpy leállításakor.", "procps", "pkill"),
    Dependency("xdg-open", "Fájlok és hivatkozások megnyitása az asztali környezetben.", "xdg-utils", "xdg-open", required=False),
    Dependency("lsusb", "USB-eszközök diagnosztikai listázása.", "usbutils", "lsusb", required=False),
    Dependency("aapt", "APK-csomagok erőforrásainak és metaadatainak vizsgálata.", "aapt", "aapt", required=False),
    Dependency("aapt2", "APK-erőforrások vizsgálata; alternatíva egyes eszközöknél.", "aapt", "aapt2", required=False),
    Dependency("send2trash", "Python-könyvtár: fájlok kukába helyezése végleges törlés helyett.", "python3-send2trash", python_module="send2trash", required=False),
]
TERMINALS = {
    "x-terminal-emulator": ["-e"], "xterm": ["-e"], "xfce4-terminal": ["--command"],
    "mate-terminal": ["--"], "gnome-terminal": ["--"], "konsole": ["-e"],
    "lxterminal": ["-e"], "kitty": ["-e"], "tilix": ["-e"],
}

def detect_terminal() -> str:
    for name in TERMINALS:
        if shutil.which(name): return name
    return "x-terminal-emulator"

def load_settings() -> dict:
    settings = {"terminal": detect_terminal()}
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict): settings.update(data)
    except (OSError, json.JSONDecodeError): pass
    return settings

def save_settings(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

def is_installed(dep: Dependency) -> bool:
    if dep.python_module:
        try: return importlib.util.find_spec(dep.python_module) is not None
        except (ImportError, ValueError): return False
    return bool(dep.command and shutil.which(dep.command))

class PreferencesDialog(QDialog):
    def __init__(self, terminal: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ACC – Beállítások")
        self.setMinimumWidth(490)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.terminal_combo = QComboBox(); self.terminal_combo.setEditable(True)
        names = [n for n in TERMINALS if shutil.which(n)]
        if "x-terminal-emulator" not in names: names.insert(0, "x-terminal-emulator")
        if "xterm" not in names: names.append("xterm")
        self.terminal_combo.addItems(names); self.terminal_combo.setCurrentText(terminal)
        form.addRow("Alapértelmezett terminál:", self.terminal_combo); layout.addLayout(form)
        info = QLabel("A telepítés külön terminálablakban fut, így a sudo jelszókérése látható és beírható. Az xterm nincs minden Linuxon előre telepítve; a program a telepített terminálokat próbálja felismerni.")
        info.setWordWrap(True); layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def terminal(self) -> str: return self.terminal_combo.currentText().strip()

class DependencyChecker(QMainWindow):
    def __init__(self):
        super().__init__(); self.settings = load_settings()
        self.setWindowTitle("ACC – Rendszerfüggőségek ellenőrzése"); self.resize(1000, 600)
        central = QWidget(); root = QVBoxLayout(central)
        intro = QLabel("Ellenőrzi az ACC által használt rendszerprogramokat és Python-könyvtárakat. A telepítés és frissítés Debian/Ubuntu/AV Linux rendszereken az apt-get csomagkezelővel történik.")
        intro.setWordWrap(True); root.addWidget(intro)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["Összetevő", "Állapot", "Típus", "Mire való?"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((135, 105, 110)): self.table.setColumnWidth(col, width)
        root.addWidget(self.table)
        buttons = QHBoxLayout()
        refresh = QPushButton("Újraellenőrzés"); refresh.clicked.connect(self.refresh); buttons.addWidget(refresh); buttons.addStretch(1)
        prefs = QPushButton("⚙ Beállítások"); prefs.setToolTip("Alapértelmezett terminál kiválasztása"); prefs.clicked.connect(self.open_preferences); buttons.addWidget(prefs)
        self.update_button = QPushButton("Csomagok frissítése…"); self.update_button.setStyleSheet("QPushButton { background:#1769aa; color:white; font-weight:bold; padding:8px; }"); self.update_button.clicked.connect(self.update_packages); buttons.addWidget(self.update_button)
        self.install_button = QPushButton("Hiányzó csomagok telepítése…"); self.install_button.setStyleSheet("QPushButton { background:#247bce; color:white; font-weight:bold; padding:8px; }"); self.install_button.clicked.connect(self.install_missing); buttons.addWidget(self.install_button)
        root.addLayout(buttons); self.setCentralWidget(central); self.refresh()

    def open_preferences(self):
        dlg = PreferencesDialog(self.settings.get("terminal", detect_terminal()), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            terminal = dlg.terminal()
            if not terminal:
                QMessageBox.warning(self, "Hiányzó adat", "Adj meg egy terminálparancsot."); return
            self.settings["terminal"] = terminal
            try: save_settings(self.settings)
            except OSError as exc: QMessageBox.warning(self, "Mentési hiba", f"A beállítás nem menthető:\n{exc}"); return
            QMessageBox.information(self, "Beállítások", f"Terminál beállítva: {terminal}")

    def refresh(self):
        self.table.setRowCount(len(DEPENDENCIES))
        for row, dep in enumerate(DEPENDENCIES):
            ok = is_installed(dep); status = "Telepítve" if ok else ("Hiányzik" if dep.required else "Opcionális")
            vals = [dep.name, status, "Python" if dep.python_module else "Rendszerprogram", dep.description]
            for col, value in enumerate(vals):
                item = QTableWidgetItem(value); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 1: item.setForeground(Qt.GlobalColor.darkGreen if ok else (Qt.GlobalColor.red if dep.required else Qt.GlobalColor.darkYellow))
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        apt_ok = bool(shutil.which("apt-get"))
        self.install_button.setEnabled(apt_ok); self.update_button.setEnabled(apt_ok)
        if not apt_ok:
            self.install_button.setToolTip("Az apt-get nem található."); self.update_button.setToolTip("Az apt-get nem található.")

    def terminal_command(self, shell_command: str) -> list[str] | None:
        parts = shlex.split(self.settings.get("terminal", detect_terminal()))
        if not parts or not shutil.which(parts[0]): return None
        name = os.path.basename(parts[0]); option = TERMINALS.get(name, ["-e"])
        return parts + option + ["bash", "-lc", shell_command]

    def run_in_terminal(self, shell_command: str, title: str) -> None:
        cmd = self.terminal_command(shell_command)
        if cmd is None:
            QMessageBox.warning(self, "Terminál nem található", f"A beállított terminál nem indítható: {self.settings.get('terminal')}\n\nNyisd meg a Beállításokat, és válassz egy telepített terminált."); return
        try: subprocess.Popen(cmd, start_new_session=True)
        except OSError as exc: QMessageBox.critical(self, "Terminálindítási hiba", f"A terminál nem indítható:\n{exc}"); return
        self.statusBar().showMessage(title + " külön terminálban elindult.", 6000)

    def scrcpy_installer_path(self) -> Path | None:
        """Megkeresi a resources könyvtárban lévő scrcpy telepítőscriptet."""
        here = Path(__file__).resolve().parent
        candidates = (
            here / "resources" / "scrcpy-install.sh",
            here.parent / "resources" / "scrcpy-install.sh",
        )
        return next((path for path in candidates if path.is_file()), None)

    def append_scrcpy_installer(self, apt_command: str, label: str) -> str:
        installer = self.scrcpy_installer_path()
        finish = (
            f"; apt_rc=$?; echo; echo '{label} kilépési kód: '$apt_rc; "
            "if [ \"$apt_rc\" -eq 0 ]; then "
        )
        if installer is None:
            return (
                apt_command + finish
                + "echo 'HIBA: a resources/scrcpy-install.sh nem található.'; "
                + "else echo 'Az apt művelet hibázott; a scrcpy-frissítés kimarad.'; fi; "
                + "read -r -p 'Bezáráshoz Enter…' _"
            )
        return (
            apt_command + finish
            + "echo 'A hivatalos scrcpy-frissítő indítása…'; "
            + "bash " + shlex.quote(str(installer))
            + "; scrcpy_rc=$?; echo; echo 'scrcpy-frissítő kilépési kód: '$scrcpy_rc; "
            + "else echo 'Az apt művelet hibázott; a scrcpy-frissítés kimarad.'; fi; "
            + "read -r -p 'Bezáráshoz Enter…' _"
        )

    def install_missing(self):
        if not shutil.which("apt-get"):
            QMessageBox.warning(self, "Nem támogatott", "Az apt-get nem található; ez a funkció Debian/Ubuntu-alapú rendszerekhez készült."); return
        packages = list(dict.fromkeys(d.package for d in DEPENDENCIES if d.required and d.package and not is_installed(d)))
        if not packages:
            QMessageBox.information(self, "Nincs teendő", "Minden kötelező összetevő telepítve van."); return
        answer = QMessageBox.question(self, "Hiányzó csomagok", "Telepítendő kötelező csomagok:\n\n" + ", ".join(packages) + "\n\nA telepítés külön terminálban fut. Folytatod?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        pkg = " ".join(shlex.quote(p) for p in packages)
        apt_script = "sudo apt-get update && sudo apt-get install -y " + pkg
        script = self.append_scrcpy_installer(apt_script, "APT-telepítés")
        self.run_in_terminal(script, "A hiányzó csomagok telepítése")

    def update_packages(self):
        if not shutil.which("apt-get"):
            QMessageBox.warning(self, "Nem támogatott", "Az apt-get nem található; ez a funkció Debian/Ubuntu-alapú rendszerekhez készült."); return
        packages = list(dict.fromkeys(d.package for d in DEPENDENCIES if d.required and d.package and is_installed(d)))
        if not packages:
            QMessageBox.information(self, "Nincs frissíthető csomag", "A szükséges csomagok közül jelenleg nincs telepített csomag."); return
        answer = QMessageBox.question(self, "ACC-csomagok frissítése", "Csak az ACC által használt, már telepített kötelező csomagok frissülnek. A teljes rendszer nem frissül, a kernel és más alkalmazások nem kerülnek automatikusan frissítésre.\n\nA művelet előbb frissíti a csomaglistát, majd az alábbi csomagokra alkalmazza az apt-get upgrade műveletet:\n\n" + ", ".join(packages) + "\n\nFolytatod?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        pkg = " ".join(shlex.quote(p) for p in packages)
        apt_script = "sudo apt-get update && sudo apt-get install --only-upgrade -y " + pkg
        script = self.append_scrcpy_installer(apt_script, "APT-frissítés")
        self.run_in_terminal(script, "Az ACC-csomagok frissítése")


def main():
    app = QApplication(sys.argv); app.setApplicationName("Android Control Center")
    window = DependencyChecker(); window.show(); sys.exit(app.exec())

if __name__ == "__main__": main()
