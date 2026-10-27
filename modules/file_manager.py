#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kétpaneles fájlkezelő a Redmi Control Centerhez.

Főprogramból: from modules.file_manager import open_module
Önállóan: python3 file_manager.py

Az eredeti redmi-control-center-4(7).py fájl fájlkezelő-funkcióiból kiemelve.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QInputDialog, QTabWidget
)

FILE_HISTORY_PATH = Path.home() / ".config" / "redmi-control-center" / "paths.json"
SETTINGS_PATH = Path.home() / ".config" / "redmi-control-center" / "settings.json"


class Adb:
    def __init__(self):
        self.path = shutil.which("adb") or "/usr/bin/adb"

    def run(self, args, timeout=15):
        try:
            p = subprocess.run([self.path, *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("Az adb parancs nem található. Telepítsd az Android platform-tools csomagot.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Az adb parancs időtúllépés miatt leállt: {' '.join(args)}")
        return p.returncode, p.stdout.strip(), p.stderr.strip()


class FileManagerWindow(QMainWindow):
    def __init__(self, parent=None, settings=None, initial_remote_path=None, serial=None):
        super().__init__(parent)
        self.settings = dict(settings or {})
        self.adb = Adb()
        self.serial = serial or None
        self.initial_remote_path = initial_remote_path
        self.fm_panels = []
        self.fm_active = 0
        self.setWindowTitle("Redmi Control Center — Fájlkezelő")
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#171a1f; color:#e6e9ef; }
            QPushButton { background:#28313b; border:1px solid #465565; border-radius:5px; padding:6px 9px; }
            QPushButton:hover { background:#354454; }
            QComboBox, QTableWidget { background:#101317; color:#e6e9ef; border:1px solid #39434e; padding:4px; }
            QHeaderView::section { background:#252c34; color:#e6e9ef; padding:5px; border:0; }
        """)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        top = QHBoxLayout()
        title = QLabel("KÉTPANELES FÁJLKEZELŐ")
        title.setFont(QFont("Sans Serif", 14, QFont.Weight.Bold))
        top.addWidget(title)
        top.addStretch()
        self.device_label = QLabel("ADB: telefon ellenőrzése…")
        top.addWidget(self.device_label)
        refresh = QPushButton("ADB frissítése")
        refresh.clicked.connect(self.refresh_adb_devices)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.make_file_manager()
        self.refresh_adb_devices()
        if self.initial_remote_path and self.serial:
            self.open_remote_target(self.initial_remote_path)

    def read_settings(self):
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.settings.update(data)
        except Exception:
            pass
        return self.settings

    def log_msg(self, message):
        if hasattr(self, "fm_status"):
            self.fm_status.setText(str(message))
        self.statusBar().showMessage(str(message), 6000)

    def refresh_adb_devices(self):
        """Frissíti az ADB állapotát; telefonhiány esetén nem szakítja meg a fájlkezelést."""
        try:
            rc, out, err = self.adb.run(["devices", "-l"], timeout=12)
            if rc:
                raise RuntimeError(err or out or f"adb devices hibakód: {rc}")

            devices = []
            unauthorized = []
            offline = []
            for line in out.splitlines()[1:]:
                bits = line.split()
                if len(bits) < 2:
                    continue
                if bits[1] == "device":
                    devices.append((bits[0], " ".join(bits[2:])))
                elif bits[1] == "unauthorized":
                    unauthorized.append(bits[0])
                elif bits[1] == "offline":
                    offline.append(bits[0])

            available_serials = [item[0] for item in devices]
            if self.serial not in available_serials:
                preferred = self.settings.get("serial")
                self.serial = preferred if preferred in available_serials else (
                    devices[0][0] if devices else None
                )

            if len(devices) > 1:
                labels = [f"{serial} — {extra}" for serial, extra in devices]
                current = next((i for i, item in enumerate(devices)
                                if item[0] == self.serial), 0)
                label, ok = QInputDialog.getItem(
                    self, "ADB-eszköz", "Aktív telefon:", labels, current, False
                )
                if ok:
                    self.serial = devices[labels.index(label)][0]

            if self.serial:
                self.device_label.setText(f"ADB: csatlakoztatva ({self.serial})")
                self.fm_refresh("remote")
            else:
                if unauthorized:
                    status = "ADB: telefon csatlakoztatva, engedélyezés szükséges"
                elif offline:
                    status = "ADB: telefon offline állapotban"
                else:
                    status = "ADB: nincs csatlakoztatott telefon"
                self.device_label.setText(status)
                # A telefonpanel információs állapotot mutat; a helyi panel
                # használatát és a modul indulását nem akadályozzuk.
                remote = self._fm_panel("remote")
                remote["table"].setRowCount(0)
                remote["path"].setCurrentText("/sdcard/DCIM")
                self.log_msg(status)
        except Exception as exc:
            # Az ADB hiánya nem végzetes: a Linux-panel továbbra is használható.
            self.serial = None
            self.device_label.setText("ADB nem érhető el")
            remote = self._fm_panel("remote")
            remote["table"].setRowCount(0)
            self.log_msg(f"ADB nem érhető el: {exc}")

    def require_device(self):
        if not self.serial:
            QMessageBox.warning(
                self,
                "Android-művelet nem érhető el",
                "Ehhez a művelethez nincs elérhető ADB-eszköz. "
                "Csatlakoztasd a telefont, engedélyezd az USB-/Wi-Fi-hibakeresést, "
                "majd nyomd meg az „ADB frissítése” gombot. "
                "A Linux-panel ettől függetlenül használható."
            )
            return False
        return True

    def adb_args(self, *args):
        return ["-s", self.serial, *args] if self.serial else list(args)

    def shell(self, *args, timeout=15):
        if not self.require_device():
            raise RuntimeError("Nincs kiválasztott telefon.")
        rc, out, err = self.adb.run(self.adb_args("shell", *args), timeout=timeout)
        if rc:
            raise RuntimeError(err or out or f"ADB exit code: {rc}")
        return out

    def make_file_manager(self):
        page = QWidget(); outer = QVBoxLayout(page)
        outer.addWidget(QLabel("Kétpaneles fájlkezelő · Telefon ↔ Linux · MC-stílusú funkcióbillentyűk"))
        split = QHBoxLayout()
        self.fm_panels = []
        for side, title in (("local", "Linux / számítógép"), ("remote", "Telefon / Android")):
            col = QVBoxLayout()
            col.addWidget(QLabel(title))
            pathbox = QComboBox(); pathbox.setEditable(True); pathbox.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
            toolbar = QHBoxLayout()
            nav_buttons = []
            for label, action in (("←", "back"), ("→", "forward"), ("↑ Fel", "up"), ("⌂ Home", "home"), ("＋", "zoom_in"), ("－", "zoom_out"), ("Lista", "list"), ("Részletek", "details")):
                btn = QPushButton(label); btn.setToolTip({"back":"Előző könyvtár az előzményekben", "forward":"Következő könyvtár az előzményekben", "up":"Egy szinttel feljebb", "home":"Linux: saját könyvtár · Telefon: /sdcard", "zoom_in":"Nagyobb betűk és sorok", "zoom_out":"Kisebb betűk és sorok", "list":"Egyszerű listanézet", "details":"Részletes nézet"}[action])
                btn.clicked.connect(lambda _checked=False, a=action, sd=side: self.fm_toolbar_action(sd, a))
                toolbar.addWidget(btn); nav_buttons.append(btn)
            table = QTableWidget(0, 3); table.setHorizontalHeaderLabels(["Név", "Típus", "Méret"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
            table.setAlternatingRowColors(True)
            pathbox.activated.connect(lambda _idx, s=side: self.fm_navigate(s))
            pathbox.lineEdit().returnPressed.connect(lambda s=side: self.fm_navigate(s))
            table.cellDoubleClicked.connect(lambda row, _col, s=side: self.fm_enter(s, row))
            col.addLayout(toolbar); col.addWidget(pathbox); col.addWidget(table, 1)
            split.addLayout(col, 1)
            self.fm_panels.append({"side":side,"path":pathbox,"table":table,"nav_history":[],"nav_index":-1,"zoom":10,"view":"details"})
        outer.addLayout(split, 1)
        self.fm_status = QLabel("Kattints egy panelre, majd használhatod a funkcióbillentyűket.")
        outer.addWidget(self.fm_status)
        keys = QHBoxLayout()
        actions = [("F1 Súgó", self.fm_help), ("F2", None), ("F3 Megnyitás", self.fm_open),
                   ("F4", None), ("F5 Másolás", self.fm_copy), ("F6 Áthelyezés", self.fm_move),
                   ("F7 Új könyvtár", self.fm_mkdir), ("F8 Törlés", self.fm_delete),
                   ("F9", None), ("F10", None)]
        for label, fn in actions:
            b=QPushButton(label)
            if fn is None: b.setEnabled(False)
            else: b.clicked.connect(fn)
            keys.addWidget(b)
        self.fm_rename_hint = QLabel("Shift+F6: átnevezés")
        keys.addWidget(self.fm_rename_hint)
        outer.addLayout(keys)
        self.fm_active = 0
        for i, panel in enumerate(self.fm_panels):
            panel["table"].installEventFilter(self)
            panel["table"].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            panel["table"].setToolTip("Tab: váltás a másik panelre · F5: másolás · F6: áthelyezés · Shift+F6: átnevezés")
            panel["table"].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.fm_load_history()
        self.tabs.addTab(page, "Fájlkezelő")
        self.fm_refresh("local")
        if self.serial:
            self.fm_refresh("remote")
        else:
            # Telefon nélkül a távoli panel inicializálása nem jeleníthet meg modális ablakot.
            remote = self._fm_panel("remote")
            remote["table"].setRowCount(0)
            remote["path"].setCurrentText("/sdcard/DCIM")

    def _fm_panel(self, side=None):
        if side is None: side = self.fm_panels[self.fm_active]["side"]
        return next(p for p in self.fm_panels if p["side"]==side)

    def _fm_set_active(self, side):
        """Set active pane and make it visually unmistakable."""
        self.fm_active=0 if side=="local" else 1
        for p in self.fm_panels:
            if p["side"] == side:
                p["table"].setStyleSheet(
                    "QTableWidget { border: 3px solid #39ff14; gridline-color: #176b2b; }"
                    "QTableWidget::item:selected { background: #145c25; color: #d8ffe0; }"
                )
            else:
                p["table"].setStyleSheet(
                    "QTableWidget { border: 1px solid #39434e; }"
                    "QTableWidget::item:selected { background: #23658a; }"
                )
        self.fm_status.setText(f"Aktív panel: {'Linux / számítógép' if side=='local' else 'Telefon / Android'} · Tab: panelváltás · F5 másolás · F6 áthelyezés · Shift+F6 átnevezés · F7 új könyvtár · F8 törlés")

    def _fm_path(self, panel): return panel["path"].currentText().strip()

    def _fm_history_add(self, side, path):
        try:
            prefs=self.read_settings()
            if not prefs.get("save_history", True): return
            data=json.loads(FILE_HISTORY_PATH.read_text()) if FILE_HISTORY_PATH.exists() else {"local":[],"remote":[]}
            hist=data.setdefault(side,[])
            if path in hist: hist.remove(path)
            hist.insert(0,path); data[side]=hist[:40]
            FILE_HISTORY_PATH.parent.mkdir(parents=True,exist_ok=True)
            FILE_HISTORY_PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2))
            box=self._fm_panel(side)["path"]; current=box.currentText(); box.blockSignals(True); box.clear(); box.addItems(hist); box.setCurrentText(current or path); box.blockSignals(False)
        except Exception as e: self.log_msg("Útvonalelőzmény mentése sikertelen: "+str(e))

    def fm_load_history(self):
        try: data=json.loads(FILE_HISTORY_PATH.read_text()) if FILE_HISTORY_PATH.exists() else {}
        except Exception: data={}
        prefs=self.read_settings()
        defaults={"local":prefs.get("local_root", str(Path.home())), "remote":prefs.get("remote_root", "/sdcard/DCIM")}
        for side in ("local","remote"):
            box=self._fm_panel(side)["path"]; hist=data.get(side,[])
            box.addItems(hist or [defaults[side]])
            box.setCurrentText((hist or [defaults[side]])[0])

    def fm_navigate(self, side):
        panel=self._fm_panel(side); path=self._fm_path(panel)
        if not path: return
        self._fm_set_active(side)
        self.fm_refresh(side, path)

    def fm_toolbar_action(self, side, action):
        panel=self._fm_panel(side); self._fm_set_active(side)
        path=self._fm_path(panel)
        if action == "back":
            if panel["nav_index"] > 0:
                panel["nav_index"] -= 1
                self.fm_refresh(side, panel["nav_history"][panel["nav_index"]], record_history=False)
            return
        if action == "forward":
            if panel["nav_index"] + 1 < len(panel["nav_history"]):
                panel["nav_index"] += 1
                self.fm_refresh(side, panel["nav_history"][panel["nav_index"]], record_history=False)
            return
        if action == "up":
            if side == "local": new_path=str(Path(path).expanduser().resolve().parent)
            else: new_path=path.rstrip("/").rsplit("/",1)[0] or "/"
            self.fm_refresh(side,new_path); return
        if action == "home":
            new_path=str(Path.home()) if side == "local" else "/sdcard"
            self.fm_refresh(side,new_path); return
        if action in ("zoom_in","zoom_out"):
            panel["zoom"] = max(7, min(24, panel["zoom"] + (1 if action=="zoom_in" else -1)))
            panel["table"].setStyleSheet(panel["table"].styleSheet() + f" QTableWidget {{ font-size: {panel['zoom']}pt; }}")
            return
        if action == "list":
            panel["view"]="list"; panel["table"].setColumnHidden(1,True); panel["table"].setColumnHidden(2,True); return
        if action == "details":
            panel["view"]="details"; panel["table"].setColumnHidden(1,False); panel["table"].setColumnHidden(2,False); return

    def fm_refresh(self, side, path=None, record_history=True):
        panel=self._fm_panel(side); path=path or self._fm_path(panel)
        if not path: return
        try:
            rows=[]
            if side=="local":
                base=Path(path).expanduser().resolve()
                if not base.is_dir(): raise RuntimeError("A helyi útvonal nem könyvtár.")
                rows.append(("..","Könyvtár","—")) if base.parent!=base else None
                for item in sorted(base.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
                    try: size=str(item.stat().st_size) if item.is_file() else "—"
                    except OSError: size="—"
                    rows.append((item.name,"Könyvtár" if item.is_dir() else "Fájl",size))
                path=str(base)
            else:
                # A távoli panel kezdeti frissítése nem kérhet felugró
                # engedélykérést. Telefon nélkül csak üres/inaktív panelt mutatunk.
                if not self.serial:
                    table = panel["table"]
                    table.setRowCount(0)
                    panel["path"].setCurrentText(path or "/sdcard/DCIM")
                    self.device_label.setText("ADB: nincs csatlakoztatott/engedélyezett telefon")
                    return
                if not path.startswith("/"): raise RuntimeError("A telefon útvonala abszolút legyen.")
                out=self.shell("ls","-la",path,timeout=20)
                for line in out.splitlines():
                    bits=line.split(None,7)
                    if len(bits)<7 or bits[0].startswith("total"): continue
                    name=bits[7] if len(bits)>7 else bits[-1]
                    if name in (".",): continue
                    if name==".." and path=="/": continue
                    rows.append((name,"Könyvtár" if bits[0].startswith("d") else "Fájl",bits[4] if len(bits)>4 else "—"))
                if not any(r[0]==".." for r in rows) and path!="/": rows.insert(0,("..","Könyvtár","—"))
            panel["path"].setCurrentText(path); self._fm_history_add(side,path)
            if record_history and (not panel["nav_history"] or panel["nav_history"][panel["nav_index"]] != path):
                panel["nav_history"] = panel["nav_history"][:panel["nav_index"]+1]
                panel["nav_history"].append(path)
                if len(panel["nav_history"]) > 100: panel["nav_history"].pop(0)
                panel["nav_index"] = len(panel["nav_history"])-1
            table=panel["table"]; table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,val in enumerate(row): table.setItem(i,j,QTableWidgetItem(val))
            self._fm_set_active(side)
        except Exception as e:
            QMessageBox.warning(self,"Könyvtár megnyitása",str(e))

    def fm_enter(self, side, row):
        self._fm_set_active(side); panel=self._fm_panel(side); item=panel["table"].item(row,0)
        if not item:return
        name=item.text(); path=self._fm_path(panel)
        if name == "..":
            new=str(Path(path).expanduser().resolve().parent) if side=="local" else (path.rstrip("/").rsplit("/",1)[0] or "/")
            if new == path: return
            self.fm_refresh(side,new); return
        if item.text()=="..": new=str(Path(path).parent) if side=="local" else (path.rstrip("/").rsplit("/",1)[0] or "/")
        elif panel["table"].item(row,1).text()=="Könyvtár": new=str(Path(path)/name) if side=="local" else path.rstrip("/")+"/"+name
        else: self.fm_open(); return
        self.fm_refresh(side,new)

    def _fm_selected(self, side=None):
        panel=self._fm_panel(side); table=panel["table"]
        rows=sorted({idx.row() for idx in table.selectionModel().selectedRows()})
        if not rows and table.currentRow()>=0: rows=[table.currentRow()]
        names=[]
        for r in rows:
            it=table.item(r,0)
            if it and it.text()!="..": names.append(it.text())
        return panel,names

    def _fm_local_path(self, panel, name): return Path(self._fm_path(panel))/name

    def _fm_remote_path(self, panel, name): return self._fm_path(panel).rstrip("/")+"/"+name

    def fm_copy(self): self._fm_transfer(move=False)

    def fm_move(self): self._fm_transfer(move=True)

    def _fm_transfer(self, move=False):
        src=self.fm_panels[self.fm_active]; dst=self.fm_panels[1-self.fm_active]
        _,names=self._fm_selected(src["side"])
        if not names: QMessageBox.information(self,"Másolás","Jelölj ki legalább egy elemet."); return
        dest=Path(self._fm_path(dst)) if dst["side"]=="local" else self._fm_path(dst)
        # Collision policy is chosen before transfer; each collision can be resolved individually.
        for name in names:
            source=(self._fm_local_path(src,name) if src["side"]=="local" else self._fm_remote_path(src,name))
            target=(dest/name if dst["side"]=="local" else dest.rstrip("/")+"/"+name)
            exists=target.exists() if dst["side"]=="local" else self._remote_exists(target)
            if exists:
                box=QMessageBox(self); box.setWindowTitle("Fájlütközés"); box.setText(f"A célban már létezik:\\n{name}\n\nMit tegyen a program?")
                overwrite=box.addButton("Felülírás",QMessageBox.ButtonRole.AcceptRole); rename=box.addButton("Átnevezés",QMessageBox.ButtonRole.ActionRole); skip=box.addButton("Kihagyás",QMessageBox.ButtonRole.DestructiveRole); box.exec()
                if box.clickedButton()==skip: continue
                if box.clickedButton()==rename:
                    new,ok=QInputDialog.getText(self,"Átnevezés","Új célfájlnév:",text=name+"_másolat")
                    if not ok or not new.strip(): continue
                    name_target=new.strip(); target=(dest/name_target if dst["side"]=="local" else dest.rstrip("/")+"/"+name_target)
                    if (target.exists() if dst["side"]=="local" else self._remote_exists(target)):
                        QMessageBox.warning(self,"Ütközés","Az új célfájlnév is létezik. A művelet kimarad."); continue
                elif box.clickedButton()==overwrite:
                    if dst["side"]=="local":
                        import shutil as _shutil
                        if target.is_dir(): _shutil.rmtree(target)
                        else: target.unlink()
                    else: self.shell("rm","-rf",target,timeout=30)
            try:
                if src["side"]=="local" and dst["side"]=="remote":
                    rc,out,err=self.adb.run(self.adb_args("push",str(source),str(target)),timeout=600)
                elif src["side"]=="remote" and dst["side"]=="local":
                    rc,out,err=self.adb.run(self.adb_args("pull",str(source),str(target)),timeout=600)
                elif src["side"]=="local" and dst["side"]=="local":
                    import shutil as _shutil
                    if source.is_dir(): _shutil.copytree(source,target,dirs_exist_ok=True)
                    else: _shutil.copy2(source,target)
                    rc,out,err=0,"",""
                else:
                    # Remote-to-remote uses a temporary local staging path.
                    with tempfile.TemporaryDirectory(prefix="redmi-fm-") as tmp:
                        staged=Path(tmp)/Path(name).name
                        rc,out,err=self.adb.run(self.adb_args("pull",str(source),str(staged)),timeout=600)
                        if rc==0: rc,out,err=self.adb.run(self.adb_args("push",str(staged),str(target)),timeout=600)
                if rc: raise RuntimeError(err or out or f"ADB hibakód: {rc}")
                if move:
                    # Never delete the source unless the destination transfer succeeded.
                    if src["side"]=="local":
                        import shutil as _shutil
                        if source.is_dir(): _shutil.rmtree(source)
                        else: source.unlink()
                    else: self.shell("rm","-rf",str(source),timeout=30)
                self.log_msg(f"{'Áthelyezve' if move else 'Másolva'}: {source} → {target}")
            except Exception as e: QMessageBox.warning(self,"Másolási hiba",f"{name}: {e}")
        self.fm_refresh(src["side"]); self.fm_refresh(dst["side"])

    def _remote_exists(self,path):
        try: self.shell("sh","-c",f"test -e {shlex.quote(path)}") ; return True
        except Exception:return False

    def fm_rename(self):
        panel,names=self._fm_selected()
        if len(names)!=1: QMessageBox.information(self,"Átnevezés","Egyszerre egy fájlt vagy könyvtárat jelölj ki."); return
        old=names[0]; new,ok=QInputDialog.getText(self,"Átnevezés","Új név:",text=old)
        if not ok or not new.strip() or new.strip() in (".",".."): return
        try:
            if panel["side"]=="local":
                source=self._fm_local_path(panel,old); target=source.with_name(new.strip())
                if target.exists(): raise RuntimeError("Ilyen nevű cél már létezik.")
                source.rename(target)
            else:
                source=self._fm_remote_path(panel,old); target=self._fm_path(panel).rstrip("/")+"/"+new.strip()
                if self._remote_exists(target): raise RuntimeError("Ilyen nevű cél már létezik.")
                self.shell("mv",source,target)
            self.log_msg(f"Átnevezve: {old} → {new.strip()}"); self.fm_refresh(panel["side"])
        except Exception as e: QMessageBox.warning(self,"Átnevezési hiba",str(e))

    def fm_mkdir(self):
        panel=self._fm_panel(); name,ok=QInputDialog.getText(self,"Új könyvtár","Könyvtár neve:")
        if not ok or not name.strip() or "/" in name: return
        try:
            if panel["side"]=="local": (Path(self._fm_path(panel))/name.strip()).mkdir()
            else: self.shell("mkdir",self._fm_path(panel).rstrip("/")+"/"+name.strip())
            self.fm_refresh(panel["side"])
        except Exception as e: QMessageBox.warning(self,"Könyvtárlétrehozási hiba",str(e))

    def fm_delete(self):
        panel,names=self._fm_selected()
        if not names: return
        path=self._fm_path(panel)
        if panel["side"]=="remote":
            protected=("/system","/vendor","/product","/data","/proc","/sys","/dev","/apex","/metadata","/odm")
            full=[path.rstrip("/")+"/"+n for n in names]
            dangerous=any(any(x==p or x.startswith(p+"/") for p in protected) for x in full)
            if dangerous:
                QMessageBox.critical(self,"VÉDETT RENDSZERÚTVONAL", "A kijelölt elem rendszerkönyvtárban vagy védett útvonalon található. A fájlkezelő ezt a törlést letiltja.\n\n"+"\n".join(full)); return
        answer=QMessageBox.question(self,"Törlés megerősítése",f"Biztosan törlöd a kijelölt {len(names)} elemet?\n\n"+"\n".join(names[:10]),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        try:
            for name in names:
                if panel["side"]=="local":
                    import shutil as _shutil
                    p=self._fm_local_path(panel,name)
                    if p.is_dir(): _shutil.rmtree(p)
                    else:p.unlink()
                else:self.shell("rm","-rf",self._fm_remote_path(panel,name),timeout=60)
            self.log_msg(f"Törölve: {len(names)} elem ({panel['side']})"); self.fm_refresh(panel["side"])
        except Exception as e: QMessageBox.warning(self,"Törlési hiba",str(e))

    def fm_open(self):
        panel,names=self._fm_selected()
        if len(names)!=1:return
        name=names[0]
        try:
            if panel["side"]=="local": path=str(self._fm_local_path(panel,name))
            else:
                if self._fm_panel(panel["side"])["table"].item(self._fm_panel(panel["side"])["table"].currentRow(),1).text()=="Könyvtár": self.fm_enter(panel["side"],panel["table"].currentRow()); return
                tmp=Path(tempfile.gettempdir())/"redmi-control-center-preview"; tmp.mkdir(exist_ok=True)
                path=str(tmp/name); rc,out,err=self.adb.run(self.adb_args("pull",self._fm_remote_path(panel,name),path),timeout=180)
                if rc: raise RuntimeError(err or out)
            if Path(path).is_dir():
                if panel["side"]=="local": self.fm_refresh("local",path)
                return
            exe=shutil.which("mirage")
            if not exe: raise RuntimeError("A Mirage nem található. Telepítsd, vagy nyisd meg a fájlt másik képnézegetővel.")
            subprocess.Popen([exe,path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except Exception as e: QMessageBox.warning(self,"Megnyitás",str(e))

    def open_remote_target(self, target_path):
        """Open the Android directory containing target_path and focus its exact row."""
        target = str(target_path or "").strip()
        if not target.startswith("/"):
            self.log_msg(f"Érvénytelen Android-útvonal: {target}")
            return
        target = target.rstrip("/") or "/"
        # If the requested target is a directory, navigate into it; otherwise show its parent and select it.
        try:
            is_dir = self.shell("sh", "-c", "test -d " + shlex.quote(target) + " && echo yes || echo no")
            if is_dir.strip() == "yes":
                folder, wanted_name = target, None
            else:
                folder, wanted_name = target.rsplit("/", 1)
                folder = folder or "/"
            self.fm_refresh("remote", folder)
            panel = self._fm_panel("remote")
            table = panel["table"]
            if wanted_name:
                for row in range(table.rowCount()):
                    item = table.item(row, 0)
                    if item and item.text() == wanted_name:
                        table.setCurrentCell(row, 0)
                        table.selectRow(row)
                        table.setFocus(Qt.FocusReason.OtherFocusReason)
                        self._fm_set_active("remote")
                        self.log_msg(f"Kijelölve a telefonon: {target}")
                        return
                self.log_msg(f"A fájlkezelő megnyílt, de a célfájl nem található a listában: {target}")
            else:
                table.setFocus(Qt.FocusReason.OtherFocusReason)
                self._fm_set_active("remote")
        except Exception as exc:
            QMessageBox.warning(self, "Fájl megnyitása a fájlkezelőben", str(exc))

    def fm_help(self):
        QMessageBox.information(self,"Redmi Control Center – fájlkezelő súgó",
            "Kétpaneles fájlkezelő: Linux és telefon között mindkét irányban másolhatsz.\n\n"
            "F1 Súgó · F3 megnyitás Mirage képnézegetővel · F5 másolás · F6 áthelyezés · Shift+F6 átnevezés · F7 új könyvtár · F8 törlés.\\n"
            "F2, F4, F9 és F10 szándékosan üres. A másolásnál felülírás, átnevezés vagy kihagyás választható.\n\n"
            "Névjegy\\nFejlesztő: Bihari Zsolt\\ninfo@tengerikajak.net")

    def eventFilter(self, obj, event):
        if hasattr(self, "fm_panels"):
            pane = next((p for p in self.fm_panels if obj is p["table"]), None)
            if pane is not None:
                etype = event.type()
                if etype == event.Type.MouseButtonPress:
                    self._fm_set_active(pane["side"])
                elif etype == event.Type.FocusIn:
                    self._fm_set_active(pane["side"])
                elif etype == event.Type.KeyPress:
                    key=event.key(); mods=event.modifiers()
                    if key == Qt.Key.Key_Tab and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                        other = "remote" if pane["side"] == "local" else "local"
                        self._fm_set_active(other)
                        target = self._fm_panel(other)["table"]
                        target.setFocus(Qt.FocusReason.TabFocusReason)
                        if target.rowCount() and target.currentRow() < 0:
                            target.selectRow(0)
                        return True
                    self._fm_set_active(pane["side"])
                    if key==Qt.Key.Key_F1: self.fm_help(); return True
                    if key==Qt.Key.Key_F3: self.fm_open(); return True
                    if key==Qt.Key.Key_F5: self.fm_copy(); return True
                    if key==Qt.Key.Key_F6 and mods & Qt.KeyboardModifier.ShiftModifier: self.fm_rename(); return True
                    if key==Qt.Key.Key_F6: self.fm_move(); return True
                    if key==Qt.Key.Key_F7: self.fm_mkdir(); return True
                    if key==Qt.Key.Key_F8: self.fm_delete(); return True
                    # F2, F4, F9, F10 intentionally do nothing.
        return super().eventFilter(obj,event)

def open_module(parent=None, settings=None):
    """Belépési pont a Redmi Control Center modulbetöltőjéhez."""
    return FileManagerWindow(parent=parent, settings=settings)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = FileManagerWindow()
    window.show()
    if QApplication.instance() is app:
        return app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
