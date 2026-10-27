#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRP-diagnosztika és fióklekérdezések. Önálló indítás: python3 google_accounts.py"""
from __future__ import annotations
import re, shutil, subprocess, sys
from datetime import datetime
from typing import Optional
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent, QTimer
from PyQt6.QtGui import QFont, QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QTextEdit, QProgressBar, QPushButton, QLineEdit, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QHeaderView, QGroupBox,
    QFileDialog, QCheckBox)

APP_TITLE = "FRP-diagnosztika és fióklekérdezések"

class Adb:
    def __init__(self): self.executable = shutil.which("adb")
    def run(self, *args, timeout=15):
        if not self.executable: return False, "", "Az adb nem található a PATH útvonalon."
        try:
            p = subprocess.run([self.executable, *args], capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired: return False, "", "Az ADB-parancs időtúllépés miatt leállt."
        except OSError as exc: return False, "", f"Az ADB nem indítható: {exc}"
        out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
        return p.returncode == 0, out, err or ("" if p.returncode == 0 else f"ADB hibakód: {p.returncode}")
    def devices(self): return self.run("devices", "-l")

class ScanWorker(QThread):
    log_line = pyqtSignal(str)
    completed = pyqtSignal(dict)
    def __init__(self, adb, serial): super().__init__(); self.adb, self.serial = adb, serial
    def run(self):
        r = {"accounts": [], "properties": {}, "frp": "Nem állapítható meg", "errors": [], "serial": self.serial,
             "commands_total": 0, "commands_ok": 0, "commands_failed": 0, "timeouts": 0}
        ok, devices, err = self.adb.devices()
        if not ok:
            r["errors"].append(err); self.completed.emit(r); return
        rows = [x.strip() for x in devices.splitlines()[1:] if x.strip()]
        if not any(x.split()[0] == self.serial and len(x.split()) > 1 and x.split()[1] == "device" for x in rows):
            r["errors"].append("A kiválasztott eszköz nem engedélyezett ADB-állapotú.")
            r["devices"] = devices; self.completed.emit(r); return
        r["devices"] = devices
        def shell(*args):
            # Ugyanúgy adja át az argumentumokat, mint a régi Redmi Control Center.
            command = " ".join(args)
            self.log_line.emit(f"Lekérdezés: adb -s {self.serial} shell {command}")
            r["commands_total"] += 1
            ok, out, error = self.adb.run("-s", self.serial, "shell", *args, timeout=15)
            if ok:
                r["commands_ok"] += 1
            else:
                r["commands_failed"] += 1
                if "időtúllépés" in (error or "").lower():
                    r["timeouts"] += 1
                message = f"{command}: {error}"
                r["errors"].append(message)
                self.log_line.emit("FIGYELMEZTETÉS: " + message)
            return out if ok else ""
        for label, cmd in [("Modell", "getprop ro.product.model"), ("Gyártó", "getprop ro.product.manufacturer"),
                           ("Android-verzió", "getprop ro.build.version.release"), ("SDK-szint", "getprop ro.build.version.sdk"),
                           ("Build-azonosító", "getprop ro.build.display.id")]:
            self.log_line.emit(f"Eszközadat: {label}")
            r["properties"][label] = shell(*cmd.split()) or "Nem állapítható meg"
        pkg = shell("dumpsys", "package", "com.google.android.gms")
        r["properties"]["Google Play-szolgáltatások"] = "Telepítve" if "versionName=" in pkg else ("Nem észlelhető" if pkg else "Nem állapítható meg")
        self.log_line.emit("Google-fiókok keresése…")
        dump = shell("dumpsys", "account")
        # A dumpsys account névmezőjét olvassuk ki, nem egy általános
        # e-mail-regex találatát. Az általános regex az Android által maszkolt
        # "tengerikajak***fo@gmail.com" szövegből tévesen csak "fo@gmail.com"
        # részt adta vissza. A forrásban szereplő név itt változatlan marad.
        accounts = []
        seen_accounts = set()
        for line in dump.splitlines():
            match = re.search(r"Account \{name=(.*?), type=([^}]+)\}", line)
            if not match:
                continue
            name = match.group(1).strip()
            account_type = match.group(2).strip()
            key = (name, account_type)
            if name and key not in seen_accounts:
                seen_accounts.add(key)
                accounts.append({"name": name, "type": account_type})
        r["accounts"] = accounts
        r["account_parse_status"] = ("Fiókbejegyzések felismerve" if accounts else
                                     "A kimenetből nem volt felismerhető fiókbejegyzés")
        r["account_dump"] = dump
        r["account_note"] = (
            "A táblázat a dumpsys account által kiadott névmezőt mutatja változtatás nélkül. "
            "Ha az Android már csillagokkal maszkolta a nevet, a program nem próbálja kitalálni "
            "a rejtett karaktereket."
        )
        # Az eredeti Redmi Control Centerben működő, négy lekérdezést használó
        # diagnosztikát tartjuk meg. A teljes nyers kimenetet is megőrizzük.
        self.log_line.emit("Az eredeti Google / FRP diagnosztika futtatása…")
        checks = [
            ("Android-fiókok", ["dumpsys", "account"]),
            ("Beállításvarázsló", ["settings", "get", "global", "device_provisioned"]),
            ("Felhasználó beállítva", ["settings", "get", "secure", "user_setup_complete"]),
            ("FRP / OOBE jelzők (getprop)", ["getprop"]),
        ]
        diagnostic_sections = []
        for label, args in checks:
            command = "adb -s " + self.serial + " shell " + " ".join(args)
            self.log_line.emit("Lekérdezés: " + command)
            r["commands_total"] += 1
            ok, out, error = self.adb.run("-s", self.serial, "shell", *args, timeout=20)
            if ok:
                r["commands_ok"] += 1
            else:
                r["commands_failed"] += 1
                if "időtúllépés" in (error or "").lower():
                    r["timeouts"] += 1
            if args == ["getprop"] and ok:
                matching = [line for line in out.splitlines()
                            if any(token in line.lower() for token in ("frp", "oobe", "provision", "setup"))]
                out = "\n".join(matching) if matching else "Nem találtam releváns, olvasható getprop jelzőt.\n\nTeljes getprop kimenet nem jelenik meg ebben a szakaszban."
            if not ok:
                out = "Nem olvasható: " + (error or "ismeretlen ADB-hiba")
                r["errors"].append(f"{label}: {error or 'ADB-hiba'}")
                self.log_line.emit(f"FIGYELMEZTETÉS: {label}: {error or 'ADB-hiba'}")
            diagnostic_sections.append(f"=== {label} ===\n{out or '(üres kimenet)'}")
        diagnostic_sections.append(
            "ÉRTÉKELÉS: ez a lekérdezés nem tudja minden Xiaomi/HyperOS készüléken bizonyítani az FRP-zár hiányát. "
            "Ha a beállításkor korábbi Google-fiókot kér, az előző tulajdonos szabályos közreműködése szükséges."
        )
        r["diagnostics"] = "\n\n".join(diagnostic_sections)
        # A régi program ezekből az értékekből nem állított biztos FRP-státuszt;
        # ezért a felületen is csak a nyers diagnosztika eredményét közöljük.
        provisioned = next((section.splitlines()[-1].strip() for section in diagnostic_sections
                            if section.startswith("=== Beállításvarázsló ===")), "")
        setup_complete = next((section.splitlines()[-1].strip() for section in diagnostic_sections
                               if section.startswith("=== Felhasználó beállítva ===")), "")
        r["device_provisioned"] = provisioned or "Nem olvasható"
        r["user_setup_complete"] = setup_complete or "Nem olvasható"
        if provisioned == "1" and setup_complete == "1":
            r["frp"] = "FRP-állapot: nem ellenőrizhető"
            r["frp_detail"] = "A két Android-beállításjelző szerint a kezdeti beállítás befejeződött. Ez önmagában nem bizonyítja az FRP-zár meglétét vagy hiányát."
        else:
            r["frp"] = "FRP-állapot: nem ellenőrizhető"
            r["frp_detail"] = "A beállításjelzők nem igazolják egyértelműen a beállítás befejezését. Az FRP tényleges állapota ebből a lekérdezésből nem állapítható meg."
        self.log_line.emit(
            f"Vizsgálat befejezve. ADB-parancsok: {r['commands_ok']} sikeres / "
            f"{r['commands_failed']} hibás; időtúllépések: {r['timeouts']}; "
            f"felismert fiókbejegyzések: {len(accounts)}."
        )
        self.completed.emit(r)

class GoogleAccountsWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None, settings=None):
        super().__init__(parent); self.settings = settings; self.adb = Adb(); self.worker = None
        root = QVBoxLayout(self); root.setContentsMargins(14,14,14,14); root.setSpacing(10)
        head = QHBoxLayout(); titles = QVBoxLayout()
        title = QLabel(APP_TITLE); title.setFont(QFont("Sans Serif", 16, QFont.Weight.Bold))
        sub = QLabel("A program ADB-kapcsolaton keresztül lekérdezi az Android által hozzáférhető fiókadatokat, rendszerjellemzőket és beállításjelzőket. A telefon beállításait nem módosítja."); sub.setWordWrap(True); sub.setStyleSheet("color: palette(mid);")
        titles.addWidget(title); titles.addWidget(sub); head.addLayout(titles); head.addStretch(1)
        self.device_combo = QComboBox(); self.device_combo.setMinimumWidth(240)
        self.btn_refresh = QPushButton("Eszközök frissítése"); self.btn_refresh.clicked.connect(self.refresh_devices)
        self.btn_scan = QPushButton("Vizsgálat indítása"); self.btn_scan.clicked.connect(self.start_scan)
        head.addWidget(self.device_combo); head.addWidget(self.btn_refresh); head.addWidget(self.btn_scan); root.addLayout(head)
        self.status = QLabel("ADB-kapcsolat ellenőrzése…"); self.status.setWordWrap(True); root.addWidget(self.status)
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.setFont(QFont("Sans Serif", 10))

        # 0. Áttekintés: rövid, egy pillantással értelmezhető összefoglaló.
        tab = QWidget(); lay = QVBoxLayout(tab)
        self.overview_title = QLabel("Még nincs vizsgálati eredmény")
        self.overview_title.setFont(QFont("Sans Serif", 15, QFont.Weight.Bold)); lay.addWidget(self.overview_title)
        self.overview_summary = QLabel("Csatlakoztass egy engedélyezett ADB-eszközt, majd indíts vizsgálatot.")
        self.overview_summary.setWordWrap(True); lay.addWidget(self.overview_summary)
        self.overview_table = QTableWidget(0, 2); self.overview_table.setHorizontalHeaderLabels(["Ellenőrzés", "Eredmény"])
        self.overview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents); self.overview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.overview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.overview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.overview_table.setWordWrap(True)
        self.overview_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.overview_table, 1)
        self.tabs.addTab(tab, "0. Áttekintés")

        # 1. Fiókok.
        tab = QWidget(); lay = QVBoxLayout(tab)
        self.account_summary = QLabel("Még nem történt vizsgálat."); self.account_summary.setWordWrap(True); lay.addWidget(self.account_summary)
        self.account_table = QTableWidget(0, 2); self.account_table.setHorizontalHeaderLabels(["Android-fiók neve (a forrás szerinti alakban)", "Fióktípus"])
        self.account_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.account_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.account_table.setWordWrap(True)
        self.account_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.account_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.account_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.account_table, 1)
        self.account_dump_group = QGroupBox("Nyers Android-fióklista (ellenőrzéshez) — kattints a kibontáshoz")
        self.account_dump_group.setCheckable(True)
        self.account_dump_group.setChecked(False)
        dump_layout = QVBoxLayout(self.account_dump_group)
        self.account_dump_view = QPlainTextEdit()
        self.account_dump_view.setReadOnly(True)
        self.account_dump_view.setPlaceholderText("A dumpsys account nyers kimenete")
        self.account_dump_view.setMaximumHeight(230)
        dump_layout.addWidget(self.account_dump_view)
        self.account_dump_group.toggled.connect(self.account_dump_view.setVisible)
        self.account_dump_view.setVisible(False)
        lay.addWidget(self.account_dump_group)
        self.tabs.addTab(tab, "1. Fiókok")

        # 2. FRP és aktiválás — tömör, tényszerű állapotpanel.
        tab = QWidget(); lay = QVBoxLayout(tab); lay.setSpacing(12)
        lay.setContentsMargins(14, 14, 14, 14)

        frp_card = QFrame(); frp_card.setFrameShape(QFrame.Shape.StyledPanel)
        frp_card_layout = QVBoxLayout(frp_card)
        self.frp_status = QLabel("FRP-zár: az ADB-ből nem ellenőrizhető")
        self.frp_status.setFont(QFont("Sans Serif", 14, QFont.Weight.Bold))
        self.frp_status.setWordWrap(True)
        self.frp_detail = QLabel(
            "A telefon tényleges FRP-állapotát ez a vizsgálat nem tudja kiolvasni. "
            "A beállításjelzők csak a beállítási folyamat állapotát mutatják."
        )
        self.frp_detail.setWordWrap(True)
        frp_card_layout.addWidget(self.frp_status)
        frp_card_layout.addWidget(self.frp_detail)
        lay.addWidget(frp_card)

        state_card = QFrame(); state_card.setFrameShape(QFrame.Shape.StyledPanel)
        state_layout = QVBoxLayout(state_card)
        state_layout.addWidget(QLabel("Lekérdezett készülékállapot"))
        def add_state_row(caption):
            row = QHBoxLayout()
            name = QLabel(caption); name.setMinimumWidth(210)
            value = QLabel("A vizsgálat még nem futott le")
            value.setWordWrap(True)
            row.addWidget(name); row.addWidget(value, 1)
            state_layout.addLayout(row)
            return value
        self.frp_provisioned_value = add_state_row("Kezdeti készülékbeállítás")
        self.frp_setup_value = add_state_row("Felhasználói beállítás")
        self.frp_account_value = add_state_row("Android-fiók a lekérdezésben")
        lay.addWidget(state_card)

        action_card = QFrame(); action_card.setFrameShape(QFrame.Shape.StyledPanel)
        action_layout = QVBoxLayout(action_card)
        action_layout.addWidget(QLabel("Ha a telefon a korábbi tulajdonos Google-fiókját kéri"))
        action_text = QLabel(
            "A korábbi tulajdonosnak kell eltávolítania a készüléket a fiókjából, "
            "vagy a hivatalos támogatástól kell segítséget kérni. Ez a program nem oldja fel az FRP-zárat."
        )
        action_text.setWordWrap(True)
        action_layout.addWidget(action_text)
        action_buttons = QHBoxLayout()
        self.btn_frp_google_help = QPushButton("Google-fiók helyreállítási oldal megnyitása")
        self.btn_frp_google_help.setToolTip(
            "A Google helyreállítási oldala nyílik meg az alapértelmezett böngészőben. "
            "A fiók helyreállítása jelszóváltoztatással járhat, de önmagában nem oldja fel az FRP-zárat."
        )
        self.btn_frp_google_help.clicked.connect(
            lambda: self.open_help_url(
                "https://accounts.google.com/signin/recovery", "Google-fiók helyreállítása"
            )
        )
        self.btn_frp_xiaomi_help = QPushButton("Xiaomi hivatalos támogatásának megnyitása")
        self.btn_frp_xiaomi_help.setToolTip(
            "A Xiaomi hivatalos támogatási oldala nyílik meg az alapértelmezett böngészőben. "
            "A gomb nem módosítja a telefon beállításait."
        )
        self.btn_frp_xiaomi_help.clicked.connect(
            lambda: self.open_help_url(
                "https://www.mi.com/global/support/", "Xiaomi hivatalos támogatása"
            )
        )
        action_buttons.addWidget(self.btn_frp_google_help)
        action_buttons.addWidget(self.btn_frp_xiaomi_help)
        action_buttons.addStretch(1)
        action_layout.addLayout(action_buttons)
        action_note = QLabel(
            "Mindkét gomb csak egy weboldalt nyit meg a böngészőben. Nem futtat ADB-parancsot, "
            "nem töröl adatot, és nem oldja fel közvetlenül a telefon FRP-zárát."
        )
        action_note.setWordWrap(True)
        action_note.setStyleSheet("color: palette(mid);")
        action_layout.addWidget(action_note)
        lay.addWidget(action_card)
        lay.addStretch(1)
        self.tabs.addTab(tab, "2. FRP és aktiválás")

        # Rendszeradatok az Áttekintés fülön, a korábbi prop_table megtartásával.
        lay_overview = self.tabs.widget(0).layout()
        lay_overview.addWidget(QLabel("Részletes rendszeradatok"))
        self.prop_table = QTableWidget(0, 2)
        self.prop_table.setHorizontalHeaderLabels(["Tulajdonság", "Érték"])
        self.prop_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents); self.prop_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.prop_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.prop_table.setWordWrap(True)
        self.prop_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        lay_overview.addWidget(self.prop_table, 1)

        # 6. Előzmények: a dumpsys account által ténylegesen kiadott részletek.
        tab = QWidget(); lay = QVBoxLayout(tab)
        self.history_summary = QLabel("Vizsgálat után itt jelennek meg a kinyerhető fiókelőzmények."); self.history_summary.setWordWrap(True); lay.addWidget(self.history_summary)
        self.history_view = QPlainTextEdit(); self.history_view.setReadOnly(True); self.history_view.setPlaceholderText("Nincs feldolgozott fiókelőzmény."); lay.addWidget(self.history_view, 1)
        self.tabs.addTab(tab, "3. Előzmények")

        # Google-fiók karbantartása: biztonsági tanácsok és hivatalos linkek együtt.
        tab = QWidget(); lay = QVBoxLayout(tab)
        intro = QLabel("Google-fiók karbantartása: ellenőrizd a helyreállítási adatokat, a kétlépcsős azonosítást, "
                       "az aktív eszközöket, a külső alkalmazások hozzáférését és a mentett jelszavakat. "
                       "A linkek a rendszer alapértelmezett böngészőjében nyílnak meg.")
        intro.setWordWrap(True); lay.addWidget(intro)
        self.link_search = QLineEdit(); self.link_search.setPlaceholderText("Keresés a Google-felületek és leírások között…")
        self.link_search.textChanged.connect(self.filter_links); lay.addWidget(self.link_search)
        self.links_table = QTableWidget(0, 3)
        self.links_table.setHorizontalHeaderLabels(["Felület", "Mire szolgál?", "URL"])
        self.links_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.links_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.links_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.links_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.links_table.setColumnWidth(2, 330)
        self.links_table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.links_table.setWordWrap(True)
        self.links_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.links_table.setWordWrap(True)
        header.sectionResized.connect(self.schedule_link_row_resize)
        self.links_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.links_table.verticalHeader().setMinimumSectionSize(28)
        self.links_table.verticalHeader().setDefaultSectionSize(42)
        self.links_table.cellDoubleClicked.connect(lambda row, _col: self.open_google_link(row))
        self.links_table.installEventFilter(self)
        lay.addWidget(self.links_table, 1)
        link_buttons = QHBoxLayout()
        self.btn_open_link = QPushButton("Kiválasztott oldal megnyitása")
        self.btn_open_link.clicked.connect(lambda: self.open_google_link())
        self.btn_open_home = QPushButton("Google-fiók főoldala")
        self.btn_open_home.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://myaccount.google.com/")))
        link_buttons.addWidget(self.btn_open_link); link_buttons.addWidget(self.btn_open_home)
        link_buttons.addStretch(1); lay.addLayout(link_buttons)
        self.google_links = [
            ("Google-fiók főoldala", "Akkor indulj innen, ha nem tudod, melyik beállítás okozza a problémát, vagy általánosan szeretnéd átnézni a fiókodat. Innen eléred a személyes adatokat, a biztonságot, az adatvédelmet és a többi fiókkezelési részt.", "https://myaccount.google.com/"),
            ("Biztonsági ellenőrzés", "Ezt nyisd meg, ha gyanús bejelentkezést észleltél, elveszhetett a fiókod biztonsága, vagy végig szeretnél menni a Google által javasolt védelmi lépéseken. Áttekinti a fiók helyreállítási lehetőségeit, az eszközöket és a hozzáféréseket, majd személyre szabott teendőket ajánl.", "https://myaccount.google.com/security-checkup"),
            ("Biztonság és bejelentkezés", "Akkor használd, ha ismeretlen biztonsági értesítést kaptál, valaki hozzáférhetett a fiókodhoz, vagy meg akarod erősíteni a bejelentkezést. Itt nézheted át a biztonsági eseményeket, a bejelentkezési módszereket, a kétlépcsős azonosítást és a fiókvédelmi beállításokat.", "https://myaccount.google.com/security"),
            ("Helyreállítási adatok", "Akkor ellenőrizd, ha nem tudsz belépni, elvesztetted a telefonodat, megváltozott az e-mail-címed vagy a telefonszámod, illetve szeretnéd elkerülni a későbbi kizáródást. Ellenőrizd, hogy a helyreállítási telefonszám és e-mail-cím valóban a tiéd és naprakész-e. A Google újraazonosítást kérhet.", "https://myaccount.google.com/signinoptions/rescuephone"),
            ("Kétlépcsős azonosítás", "Akkor nyisd meg, ha csak jelszóval véded a fiókot, vagy új telefont/azonosító eszközt állítasz be. Itt kezelheted a második lépést, az elérhető hitelesítő módszereket és a biztonsági kulcsokat. Módosítás előtt gondoskodj tartalék belépési lehetőségről, hogy ne zárd ki magad.", "https://myaccount.google.com/signinoptions/two-step-verification"),
            ("Eszközök és munkamenetek", "Ezt ellenőrizd, ha elveszett a telefonod, idegen eszközről érkező értesítést látsz, vagy eladtál/lecseréltél egy készüléket. Nézd át a fiókhoz kapcsolódó eszközöket és munkameneteket; az ismeretlen vagy már nem használt eszközökről jelentkezz ki. Az eszközlista önmagában nem mindig bizonyítja, ki használta a fiókot.", "https://myaccount.google.com/device-activity"),
            ("Külső alkalmazások hozzáférése", "Akkor nézd át, ha olyan alkalmazást használtál, amely Google-fiókos belépést kért, vagy már nem bízol egy korábban engedélyezett szolgáltatásban. Ellenőrizd, milyen adatokhoz férhet hozzá, és vond vissza a hozzáférést azoknál, amelyeket már nem használsz vagy nem ismersz.", "https://myaccount.google.com/permissions"),
            ("Jelszóellenőrzés", "Akkor használd, ha ugyanazt a jelszót több helyen használod, adatvédelmi incidensről hallottál, vagy szeretnéd felmérni a mentett jelszavaid kockázatát. A Google ellenőrzése jelezheti a gyenge, újrahasznált vagy ismert adatszivárgásban szereplő jelszavakat; a jelzések alapján cseréld le az érintett jelszavakat.", "https://passwords.google.com/checkup"),
            ("Google Jelszókezelő", "Akkor menj ide, ha nem találod egy webhely jelszavát, meg szeretnéd nézni a mentett bejelentkezéseket, vagy hozzáférési kulcsokat kezelnél. Csak a Google Jelszókezelőbe korábban elmentett adatokat mutatja; a telefonon lévő Google-fiók jelszavát nem olvassa ki az ADB.", "https://passwords.google.com/"),
            ("Személyes adatok", "Akkor ellenőrizd, ha a fiókban hibás név, elavult telefonszám vagy régi elérhetőség szerepel. Itt kezelheted a személyes adatokat és egyes kapcsolattartási adatokat. A módosításokhoz a Google kérheti a személyazonosságod újbóli igazolását.", "https://myaccount.google.com/personal-info"),
            ("Adatok és adatvédelem", "Akkor nyisd meg, ha szabályozni szeretnéd, milyen tevékenységeket őrizzen meg a Google, milyen adatokat használjon személyre szabáshoz, vagy mely előzményeket törölnéd. Itt találod a tevékenységvezérlőket, az adatmegőrzési és törlési lehetőségeket.", "https://myaccount.google.com/data-and-privacy"),
            ("Adatvédelmi ellenőrzés", "Akkor használd, ha nem vagy biztos benne, milyen adatokat osztasz meg, vagy egy helyen szeretnéd áttekinteni az adatvédelmi beállításokat. A Google lépésről lépésre végigvezet az elérhető vezérlőkön; a felkínált pontok a fiók használatától függhetnek.", "https://myaccount.google.com/privacycheckup"),
            ("Google-tárhely kezelése", "Akkor nyisd meg, ha a Gmail nem fogad levelet, a Drive nem enged feltölteni, vagy a Fotók biztonsági mentése megállt tárhelyhiány miatt. Itt áttekintheted a közös tárhelyhasználatot, és a felkínált eszközökkel helyet szabadíthatsz fel. Törlés előtt ellenőrizd, hogy nincs-e szükséged az adott fájlra.", "https://one.google.com/storage/management"),
            ("Google Takeout – adatok exportálása", "Akkor használd, ha biztonsági másolatot szeretnél a Google által tárolt adataidról, szolgáltatást váltasz, vagy a fiók megszüntetése előtt lementenéd az adataidat. Kiválaszthatod az exportálandó szolgáltatásokat; az export elkészítése időt vehet igénybe, és a letöltési hivatkozás nem feltétlenül azonnal készül el.", "https://takeout.google.com/"),
            ("Fiók-helyreállítás", "Akkor kezdd itt, ha elfelejtetted a jelszavad, nem férsz hozzá a második azonosítási módhoz, vagy a Google kizárt a fiókból. Válaszolj a Google kérdéseire, és lehetőleg korábban használt eszközről, megszokott helyről próbálkozz. A folyamat nem garantálja a hozzáférés visszaállítását.", "https://accounts.google.com/signin/recovery"),
            ("Inaktív fiók kezelője", "Akkor érdemes beállítani, ha hosszabb kiesés esetére szeretnéd meghatározni, mi történjen a fiókoddal és bizonyos adataiddal. Megadhatsz inaktivitási időt, értesítési módokat és – a Google által kínált lehetőségek szerint – megbízható kapcsolattartókat vagy adatmegosztást.", "https://myaccount.google.com/inactive"),
            ("Google-fiók súgó – biztonság", "Akkor használd, ha nem érted valamelyik biztonsági beállítást, figyelmeztetést vagy fiókvédelmi lépést. A hivatalos súgó magyarázatokat és további eljárásokat ad; ha a fiókhoz nem férsz hozzá, a helyreállítási oldalt használd.", "https://support.google.com/accounts/answer/46526"),
            ("Tárhely felszabadítása – súgó", "Akkor olvasd el, ha megtelt a Google-tárhelyed, és nem világos, mit lehet biztonságosan törölni. Útmutatást ad a tárhelyet foglaló fájlok és a lomtárak ellenőrzéséhez. A végleges törlés előtt nézd át a kijelölt elemeket, mert a törölt fájlok visszaállítása korlátozott lehet.", "https://support.google.com/drive/answer/6374270"),
            ("Google Play Protect – súgó", "Akkor nyisd meg, ha gyanús alkalmazásra figyelmeztet a telefon, ismeretlen forrásból telepítettél appot, vagy ellenőriznéd az alkalmazások védelmét. A súgó ismerteti a Play Protect működését és a vizsgálatok kezelését. Ez nem helyettesíti az Android és az alkalmazások frissítését.", "https://support.google.com/googleplay/answer/2812853"),
        ]
        for name, desc, url in self.google_links:
            row = self.links_table.rowCount(); self.links_table.insertRow(row)
            for col, value in enumerate((name, desc, url)):
                self.links_table.setItem(row, col, QTableWidgetItem(value))
        self.links_table.resizeRowsToContents()
        self.tabs.addTab(tab, "4. Biztonság és Google-linkek")

        # Facebook/Meta-fiók és Facebook Login fejlesztői hivatkozások.
        tab = QWidget(); lay = QVBoxLayout(tab)
        fb_intro = QLabel(
            "Facebook-fiók karbantartása és Facebook Login Joomla-integrációhoz. "
            "A fiókkezelési oldalakhoz be kell jelentkezned; a fejlesztői oldalakhoz Meta-fejlesztői hozzáférés is szükséges lehet."
        )
        fb_intro.setWordWrap(True)
        lay.addWidget(fb_intro)
        self.facebook_search = QLineEdit()
        self.facebook_search.setPlaceholderText("Keresés a Facebook/Meta-felületek és leírások között…")
        self.facebook_search.textChanged.connect(self.filter_facebook_links)
        lay.addWidget(self.facebook_search)
        self.facebook_table = QTableWidget(0, 3)
        self.facebook_table.setHorizontalHeaderLabels(["Felület", "Mire szolgál / mikor használd?", "URL"])
        self.facebook_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.facebook_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.facebook_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        fb_header = self.facebook_table.horizontalHeader()
        fb_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        fb_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        fb_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.facebook_table.setColumnWidth(2, 330)
        self.facebook_table.setWordWrap(True)
        self.facebook_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        fb_header.sectionResized.connect(self.schedule_link_row_resize)
        self.facebook_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.facebook_table.verticalHeader().setMinimumSectionSize(28)
        self.facebook_table.verticalHeader().setDefaultSectionSize(42)
        self.facebook_table.cellDoubleClicked.connect(lambda row, _col: self.open_facebook_link(row))
        self.facebook_table.installEventFilter(self)
        lay.addWidget(self.facebook_table, 1)
        fb_buttons = QHBoxLayout()
        self.btn_open_facebook_link = QPushButton("Kiválasztott oldal megnyitása")
        self.btn_open_facebook_link.clicked.connect(lambda: self.open_facebook_link())
        self.btn_open_facebook_home = QPushButton("Facebook főoldala")
        self.btn_open_facebook_home.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.facebook.com/"))
        )
        fb_buttons.addWidget(self.btn_open_facebook_link)
        fb_buttons.addWidget(self.btn_open_facebook_home)
        fb_buttons.addStretch(1)
        lay.addLayout(fb_buttons)

        self.facebook_links = [
            ("Facebook főoldala", "Akkor nyisd meg, ha be szeretnél jelentkezni, ellenőriznéd, hogy működik-e a fiókod, vagy a Facebook felületéről szeretnéd elérni a beállításokat.", "https://www.facebook.com/"),
            ("Feltört fiók helyreállítása", "Ha valaki átvette a fiókodat, megváltoztatta a jelszavadat, vagy ismeretlen műveleteket végez, ezt használd. Lehetőleg olyan eszközről nyisd meg, amelyről korábban már bejelentkeztél.", "https://www.facebook.com/hacked"),
            ("Fiók-helyreállítás", "Ha nem tudsz belépni, elfelejtetted a jelszót, vagy nem férsz hozzá a bejelentkezési e-mailhez/telefonszámhoz, indítsd el a hivatalos helyreállítást.", "https://www.facebook.com/login/identify"),
            ("Fiókbiztonság – súgó", "Akkor használd, ha gyanús bejelentkezést, adathalász üzenetet vagy más biztonsági problémát észlelsz. A hivatalos súgó a fiókvédelem és a biztonsági beállítások áttekintéséhez ad útmutatást.", "https://www.facebook.com/help/235353253505947"),
            ("Jelszó és biztonság – Accounts Center", "Jelszócsere, kétlépcsős azonosítás, bejelentkezési figyelmeztetések és más Meta-fiókvédelmi beállítások kezelése. Akkor nyisd meg, ha megerősítenéd a védelmet vagy gyanús hozzáférést vizsgálsz.", "https://accountscenter.facebook.com/password_and_security"),
            ("Bejelentkezési helyek / eszközök", "Ellenőrizd, milyen eszközökön és munkamenetekben van bejelentkezve a fiók. Ismeretlen vagy már nem használt munkamenetből jelentkezz ki.", "https://www.facebook.com/settings?tab=security"),
            ("Adatvédelmi ellenőrzés", "Akkor nézd át, ha módosítanád, ki láthatja a bejegyzéseidet, személyes adataidat vagy hogyan találhatnak meg mások.", "https://www.facebook.com/privacy/checkup"),
            ("Tevékenységnapló", "Korábbi bejegyzések, reakciók és egyéb fióktevékenységek áttekintésére, illetve egyes tartalmak kezelésére szolgál.", "https://www.facebook.com/me/allactivity"),
            ("Alkalmazások és webhelyek", "Nézd át, mely külső alkalmazásokhoz vagy webhelyekhez kapcsoltad a Facebook-fiókodat. Vond vissza azok hozzáférését, amelyeket már nem használsz vagy nem ismersz.", "https://www.facebook.com/settings?tab=applications"),
            ("Meta Business Suite", "Facebook-oldalak és üzleti jelenlét kezeléséhez használd. Ha oldaladminisztrációval, üzleti jogosultságokkal vagy tartalomkezeléssel van gond, itt érdemes kezdeni.", "https://business.facebook.com/"),
            ("Meta for Developers – alkalmazások", "Joomla Facebook Login beállításához itt hozhatsz létre vagy választhatsz Meta-alkalmazást. Az alkalmazás beállításaiban találod az App ID-t, az engedélyezett OAuth-átirányításokat és az alkalmazás állapotát.", "https://developers.facebook.com/apps/"),
            ("Facebook Login – fejlesztői dokumentáció", "A Facebook Login OAuth-folyamatának, engedélykéréseinek és átirányításainak hivatalos dokumentációja. Akkor használd, ha a Joomla-bővítmény beállításakor callback URL, jogosultság vagy belépési hiba jelentkezik.", "https://developers.facebook.com/docs/facebook-login/"),
            ("Meta alkalmazásbeállítások", "Az alkalmazás alapadatai, platformjai, domainjei és egyéb integrációs beállításai. Joomla esetén ellenőrizd, hogy a webhely domainje és a használt átirányítási/callback cím pontosan egyezzen a bővítmény konfigurációjával.", "https://developers.facebook.com/apps/"),
            ("Meta App Review és engedélyek", "Akkor lehet szükséges, ha a Joomla-bővítmény a nyilvános használathoz külön Meta-jogosultságot kér. Ellenőrizd, milyen engedélyeket kér a bővítmény, és hogy azokhoz szükséges-e alkalmazás-felülvizsgálat.", "https://developers.facebook.com/docs/app-review/"),
            ("Joomla – bővítmények kezelése", "A Joomla adminisztrációban itt találod a telepített bővítmények kezelését. Facebook-bejelentkezéshez ellenőrizd a használt social-login vagy authentication bővítmény saját dokumentációját, támogatott Joomla-verzióját és beállításait.", "https://docs.joomla.org/Administration_of_a_Plugin_in_Joomla"),
            ("Joomla – hitelesítési bővítmény fejlesztése", "Fejlesztőknek szóló Joomla-dokumentáció arról, hogyan működik a hitelesítési plugin. Nem kész Facebook-integrációs recept, de hasznos, ha egy egyedi bővítmény fejlesztése vagy hibakeresése a feladat.", "https://docs.joomla.org/J4.x:Creating_an_Authentication_Plugin_for_Joomla/en"),
            ("Joomla – bejelentkezési gombok és WebAuthn", "Joomla dokumentáció a bejelentkezési felületeken megjelenő további hitelesítési gombokról és a bővítmények együttműködéséről. Akkor lehet hasznos, ha a Facebook Login gomb nem jelenik meg a sablonban vagy a belépési űrlapon.", "https://docs.joomla.org/Special:MyLanguage/WebAuthn_Passwordless_Login"),
        ]
        for name, desc, url in self.facebook_links:
            row = self.facebook_table.rowCount()
            self.facebook_table.insertRow(row)
            for col, value in enumerate((name, desc, url)):
                self.facebook_table.setItem(row, col, QTableWidgetItem(value))
        self.facebook_table.resizeRowsToContents()
        self.tabs.addTab(tab, "5. Facebook és Joomla Login")

        # Xiaomi-fiók, készüléktámogatás és fejlesztői hivatkozások.
        tab = QWidget(); lay = QVBoxLayout(tab)
        xiaomi_intro = QLabel(
            "Hivatalos Xiaomi-felületek fiók-helyreállításhoz, készüléktámogatáshoz, "
            "adatvédelemhez és rendszerrel kapcsolatos ügyekhez. A bootloader-feloldás "
            "nem általános javítási lépés: adatvesztéssel és biztonsági kockázattal járhat."
        )
        xiaomi_intro.setWordWrap(True)
        lay.addWidget(xiaomi_intro)
        self.xiaomi_search = QLineEdit()
        self.xiaomi_search.setPlaceholderText("Keresés a Xiaomi-linkek és leírások között…")
        self.xiaomi_search.textChanged.connect(self.filter_xiaomi_links)
        lay.addWidget(self.xiaomi_search)
        self.xiaomi_table = QTableWidget(0, 3)
        self.xiaomi_table.setHorizontalHeaderLabels(["Felület", "Mire szolgál / mikor használd?", "URL"])
        self.xiaomi_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.xiaomi_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.xiaomi_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        xiaomi_header = self.xiaomi_table.horizontalHeader()
        xiaomi_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        xiaomi_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        xiaomi_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.xiaomi_table.setColumnWidth(2, 330)
        self.xiaomi_table.setWordWrap(True)
        self.xiaomi_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        xiaomi_header.sectionResized.connect(self.schedule_link_row_resize)
        self.xiaomi_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.xiaomi_table.verticalHeader().setMinimumSectionSize(28)
        self.xiaomi_table.verticalHeader().setDefaultSectionSize(42)
        self.xiaomi_table.cellDoubleClicked.connect(lambda row, _col: self.open_xiaomi_link(row))
        self.xiaomi_table.installEventFilter(self)
        lay.addWidget(self.xiaomi_table, 1)
        xiaomi_buttons = QHBoxLayout()
        self.btn_open_xiaomi_link = QPushButton("Kiválasztott oldal megnyitása")
        self.btn_open_xiaomi_link.clicked.connect(lambda: self.open_xiaomi_link())
        self.btn_open_xiaomi_home = QPushButton("Xiaomi főoldala")
        self.btn_open_xiaomi_home.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.mi.com/global/"))
        )
        xiaomi_buttons.addWidget(self.btn_open_xiaomi_link)
        xiaomi_buttons.addWidget(self.btn_open_xiaomi_home)
        xiaomi_buttons.addStretch(1)
        lay.addLayout(xiaomi_buttons)

        self.xiaomi_links = [
            ("Xiaomi Global – főoldal", "A nemzetközi Xiaomi-oldal termékekhez, szolgáltatásokhoz és a támogatási felületek eléréséhez.", "https://www.mi.com/global/"),
            ("Xiaomi Account – fiókkezelés", "A Xiaomi-fiók eléréséhez és a fiókhoz tartozó adatok ellenőrzéséhez. Ha a telefon Xiaomi-fiókhoz van kötve, ez segíthet tisztázni, melyik fiókot kell használni; nem oldja fel a Google FRP-zárat.", "https://account.xiaomi.com/"),
            ("Xiaomi Account – jelszó-helyreállítás", "Ha a Xiaomi-fiók jelszava nem ismert, vagy a fiókba nem tudsz belépni, a hivatalos jelszó-visszaállítási folyamatot itt lehet elindítani.", "https://account.xiaomi.com/pass/forgetPassword"),
            ("Xiaomi Cloud – eszközkeresés", "A Xiaomi-fiókhoz kapcsolt készülékek és a Find device funkció eléréséhez. Csak a saját fiókodhoz tartozó eszközöket kezeld; ez nem Google-fiók-helyreállító oldal.", "https://i.mi.com/"),
            ("Xiaomi Global – támogatás és kapcsolat", "Hivatalos ügyfélszolgálati és támogatási elérhetőségek. Garanciális, aktiválási vagy fiókhoz kötött készülékproblémánál ezt érdemes használni.", "https://www.mi.com/global/contact/"),
            ("Xiaomi Magyarország – szervizkapcsolat", "A Xiaomi hivatalos kapcsolatoldalán Magyarországhoz megadott ügyfélszolgálati elérhetőségek. Készülékazonosítóval és vásárlási bizonylattal készülj, ha rendelkezésre áll.", "https://www.mi.com/global/contact/"),
            ("Xiaomi – bootloader-zár ismertető", "A hivatalos tájékoztató a bootloader szerepét, feloldását és kockázatait ismerteti. Feloldás adatvesztést, biztonsági kockázatot és a garanciális feltételek változását okozhatja; a normál fiók- vagy FRP-problémát nem oldja meg automatikusan.", "https://www.mi.com/global/support/faq/details/KA-07238/"),
            ("Xiaomi Community – globális közösség", "Hivatalos közösségi fórum HyperOS/MIUI-hírekhez, eszközspecifikus útmutatókhoz és bootloaderrel kapcsolatos közleményekhez. A fórumon talált eljárásokat mindig vesd össze a készüléked pontos modelljével és régiójával.", "https://new.c.mi.com/global/"),
            ("HyperOS bootloader-feloldási szabályok", "A Xiaomi Community hivatalos közleménye a HyperOS alatti feloldási jogosultságokról. A feltételek régiótól és fiókjogosultságtól függhetnek, ezért ne tekintsd garantáltan elérhető funkciónak.", "https://new.c.mi.com/global/post/673501"),
            ("Xiaomi – adatvédelmi támogatás", "Adatvédelmi kérelmekhez, személyes adatokkal és adatkezeléssel kapcsolatos kérdésekhez használható hivatalos felület.", "https://privacy.mi.com/Privacy_Support/en_US"),
            ("Xiaomi – szoftverfrissítések és támogatás", "A támogatási oldalon a készülékhez elérhető útmutatók és támogatási információk kereshetők. A letöltés vagy frissítés előtt ellenőrizd a pontos modellt és a régiót.", "https://www.mi.com/global/support/"),
            ("Xiaomi – hivatalos termék- és modelloldalak", "A készülék modelljének, termékadatainak és régiójának ellenőrzéséhez. Ez hasznos lehet, ha a telefon névleges neve és az Android által jelentett modellszám eltér.", "https://www.mi.com/global/"),
        ]
        for name, desc, url in self.xiaomi_links:
            row = self.xiaomi_table.rowCount()
            self.xiaomi_table.insertRow(row)
            for col, value in enumerate((name, desc, url)):
                self.xiaomi_table.setItem(row, col, QTableWidgetItem(value))
        self.xiaomi_table.resizeRowsToContents()
        self.tabs.addTab(tab, "6. Xiaomi-linkek")

        # A diagnosztika nyers kimenete és az esemény-/lekérdezési napló külön fülre kerül.
        diagnostics_tab = QWidget(); diagnostics_layout = QVBoxLayout(diagnostics_tab)
        diagnostics_intro = QLabel(
            "A vizsgálat részletes, nyers diagnosztikai kimenete. Az itt látható adatok "
            "önmagukban nem bizonyítják az FRP-zár meglétét vagy hiányát."
        )
        diagnostics_intro.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_intro)
        self.diagnostics_view = QPlainTextEdit(); self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setPlaceholderText("Az ADB-diagnosztika teljes kimenete itt jelenik meg.")
        diagnostics_layout.addWidget(self.diagnostics_view, 1)
        diagnostics_actions = QHBoxLayout()
        self.masked_diagnostic_export = QCheckBox("Személyes adatok maszkolása mentéskor")
        self.masked_diagnostic_export.setChecked(True)
        self.masked_diagnostic_export.setToolTip(
            "A mentett másolatban e-mail-címek és felismerhető eszközazonosítók maszkolását kísérli meg. "
            "A képernyőn látható naplót nem módosítja."
        )
        diagnostics_actions.addWidget(self.masked_diagnostic_export)
        diagnostics_actions.addStretch(1)
        self.btn_copy_diagnostics = QPushButton("Másolás")
        self.btn_copy_diagnostics.clicked.connect(lambda: self.copy_log(self.diagnostics_view, "Diagnosztikai log"))
        self.btn_save_diagnostics = QPushButton("Mentés fájlba…")
        self.btn_save_diagnostics.clicked.connect(self.save_diagnostics_log)
        diagnostics_actions.addWidget(self.btn_copy_diagnostics)
        diagnostics_actions.addWidget(self.btn_save_diagnostics)
        diagnostics_layout.addLayout(diagnostics_actions)
        self.tabs.addTab(diagnostics_tab, "Diagnosztikai log")

        query_log_tab = QWidget(); query_log_layout = QVBoxLayout(query_log_tab)
        query_log_intro = QLabel(
            "A program futási eseményei, az elküldött ADB-lekérdezések és az esetleges figyelmeztetések. "
            "A napló mentése személyes vagy készülékazonosító adatokat is tartalmazhat."
        )
        query_log_intro.setWordWrap(True)
        query_log_layout.addWidget(query_log_intro)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setPlaceholderText("A lekérdezések és futási események itt jelennek meg.")
        query_log_layout.addWidget(self.log, 1)
        query_actions = QHBoxLayout(); query_actions.addStretch(1)
        self.btn_copy_query_log = QPushButton("Másolás")
        self.btn_copy_query_log.clicked.connect(lambda: self.copy_log(self.log, "Lekérdezési napló"))
        self.btn_save_query_log = QPushButton("Mentés fájlba…")
        self.btn_save_query_log.clicked.connect(lambda: self.save_log(self.log, "Lekérdezési napló", masked=False))
        query_actions.addWidget(self.btn_copy_query_log); query_actions.addWidget(self.btn_save_query_log)
        query_log_layout.addLayout(query_actions)
        self.tabs.addTab(query_log_tab, "Napló (Lekérdezési log)")

        # Fülrend: Áttekintés, Fiókok, FRP, Előzmények, Google, Facebook/Joomla, Xiaomi,
        # Diagnosztikai log, Napló (Lekérdezési log).
        self.tabs.setTabText(3, "3. Előzmények")
        self.tabs.setTabText(4, "4. Biztonság és Google-linkek")
        self.tabs.setTabText(5, "5. Facebook és Joomla Login")
        self.tabs.setTabText(6, "6. Xiaomi-linkek")
        self.tabs.setTabText(7, "Diagnosztikai log")
        self.tabs.setTabText(8, "Napló (Lekérdezési log)")
        self.progress = QProgressBar(); self.progress.setRange(0,1); self.progress.setValue(0); self.progress.setTextVisible(False); root.addWidget(self.progress)
        self.refresh_devices()
    @staticmethod
    def _describe_setup_flag(value):
        value = str(value).strip()
        if value == "1":
            return "Befejezett (érték: 1)"
        if value == "0":
            return "Nincs befejezve (érték: 0)"
        if value in ("", "None", "Nem olvasható"):
            return "Nem olvasható"
        return f"Visszaadott érték: {value}"

    def open_help_url(self, url, label):
        """Hivatalos súgóoldal megnyitása; a művelet nem küld parancsot a telefonra."""
        opened = QDesktopServices.openUrl(QUrl(url))
        if opened:
            self.log_line(f"{label}: weboldal megnyitásának kérése az alapértelmezett böngészőben: {url}")
        else:
            QMessageBox.warning(
                self,
                "A weboldal nem nyitható meg",
                f"A rendszer nem tudta megnyitni ezt a webcímet:\n{url}\n\n"
                "Ellenőrizd az alapértelmezett böngésző beállítását, majd próbáld újra."
            )
            self.log_line(f"HIBA: nem sikerült megnyitni a weboldalt: {url}")

    def log_line(self, message): self.log.appendPlainText(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
    def refresh_devices(self):
        if self.worker and self.worker.isRunning():
            self.log_line("Eszközfrissítés kihagyva: a vizsgálat még fut.")
            return
        self.device_combo.clear(); ok, out, err = self.adb.devices()
        if not ok: self.status.setText(f"ADB-hiba: {err}"); self.btn_scan.setEnabled(False); self.log_line(err); return
        for line in out.splitlines()[1:]:
            p = line.split()
            if len(p) >= 2: self.device_combo.addItem(f"{p[0]} — {p[1]}", p[0])
        online = any(self.device_combo.itemText(i).endswith("— device") for i in range(self.device_combo.count()))
        self.btn_scan.setEnabled(online); self.status.setText(f"ADB elérhető. Eszközök: {self.device_combo.count()}." if self.device_combo.count() else "ADB elérhető, de nincs csatlakoztatott eszköz.")
        self.log_line("Eszközlista frissítve.")
    def start_scan(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Vizsgálat folyamatban", "A jelenlegi vizsgálat még fut. Várd meg a befejezését az új vizsgálat indítása előtt.")
            return
        serial = self.device_combo.currentData()
        if not serial: QMessageBox.warning(self,"Nincs eszköz","Előbb csatlakoztass egy engedélyezett ADB-eszközt."); return
        self.worker = ScanWorker(self.adb, serial); self.worker.log_line.connect(self.log_line); self.worker.completed.connect(self.show_result)
        self.btn_scan.setEnabled(False); self.btn_refresh.setEnabled(False); self.device_combo.setEnabled(False); self.progress.setRange(0,0); self.status.setText(f"Vizsgálat folyamatban: {serial}"); self.log_line(f"Vizsgálat indítása: {serial}"); self.worker.start()
    def show_result(self, r):
        self.progress.setRange(0,1); self.progress.setValue(1); self.btn_refresh.setEnabled(True); self.device_combo.setEnabled(True); self.btn_scan.setEnabled(self.device_combo.currentIndex() >= 0)
        errors = r.get("errors",[]); self.status.setText("A vizsgálat befejeződött. Az ADB-n keresztül elérhető fiókadatokat, beállításjelzőket és rendszerjellemzőket feldolgoztuk. Az FRP-zár tényleges állapota ebből a vizsgálatból nem állapítható meg." if not errors else "A vizsgálat részben fejeződött be; egyes adatok nem voltak lekérdezhetők: " + " | ".join(errors))
        self.frp_status.setText("FRP-zár: az ADB-ből nem ellenőrizhető")
        self.frp_detail.setText(
            "A lekérdezés nem olvassa ki közvetlenül az FRP-zár állapotát. "
            "Az alábbi adatok a beállítási folyamatot és az Android-fióklista eredményét mutatják."
        )
        provisioned = str(r.get("device_provisioned", "Nem olvasható"))
        setup_complete = str(r.get("user_setup_complete", "Nem olvasható"))
        self.frp_provisioned_value.setText(self._describe_setup_flag(provisioned))
        self.frp_setup_value.setText(self._describe_setup_flag(setup_complete))
        accounts = r.get("accounts", [])
        if accounts:
            self.frp_account_value.setText(f"{len(accounts)} fiók észlelve a dumpsys account kimenetében")
        else:
            self.frp_account_value.setText("A lekérdezés nem talált kiolvasható fióknevet; ez nem bizonyítja, hogy nincs fiók.")
        self.diagnostics_view.setPlainText(r.get("diagnostics", "A diagnosztika nem adott kimenetet."))
        self.account_table.setRowCount(0)
        for account in r.get("accounts", []):
            row = self.account_table.rowCount()
            self.account_table.insertRow(row)
            self.account_table.setItem(row, 0, QTableWidgetItem(account["name"]))
            self.account_table.setItem(row, 1, QTableWidgetItem(account["type"]))
        if r.get("accounts"):
            self.account_summary.setText(
                f"{len(r['accounts'])} Android-fiók észlelve. A nevek a dumpsys account kimenetéből, "
                "változtatás nélkül szerepelnek; a csillagokkal maszkolt részeket nem csonkítjuk le."
            )
        else:
            self.account_summary.setText(
                "A felismerés eredménye: " + r.get("account_parse_status", "Nem ismert") + ". "
                "A kimenetből nem sikerült Account {name=…, type=…} sorokat kinyerni. "
                "A nyers Android-fióklista lent látható; ez nem bizonyítja, hogy nincs fiók.\n"
                + r.get("account_note", "")
            )
        # A nyers kimenet is megmarad, mert az Android/MIUI formátuma változhat.
        account_dump = r.get("account_dump", "")
        self.account_dump_view.setPlainText(account_dump)
        history_lines = []
        in_history = False
        for line in account_dump.splitlines():
            if "accounts history" in line.lower() or "account history" in line.lower():
                in_history = True
            if in_history:
                history_lines.append(line)
        if history_lines:
            self.history_summary.setText("A rendszer kimenetében talált fiókelőzmény-szakasz. Az események elérhetősége Android-verziónként eltérhet.")
            self.history_view.setPlainText("\n".join(history_lines))
        else:
            self.history_summary.setText("A dumpsys account kimenetében nem találtam felismerhető fiókelőzmény-szakaszt. Ez nem jelenti azt, hogy korábban nem történt fiókművelet.")
            self.history_view.setPlainText("Nincs felismerhető fiókelőzmény-szakasz.\n\nA teljes nyers kimenet a Fiókok fülön található.")
        overview_rows = [
            ("Kiválasztott eszköz", r.get("serial", "Nem ismert")),
            ("Kinyert Android-fiókok", str(len(r.get("accounts", [])))),
            ("FRP / aktiválás", r.get("frp", "Nem állapítható meg")),
            ("Google Play-szolgáltatások", r.get("properties", {}).get("Google Play-szolgáltatások", "Nem állapítható meg")),
            ("Android-verzió", r.get("properties", {}).get("Android-verzió", "Nem állapítható meg")),
            ("Sikeres ADB-parancsok", str(r.get("commands_ok", 0))),
            ("Sikertelen ADB-parancsok", str(r.get("commands_failed", len(errors)))),
            ("Időtúllépések", str(r.get("timeouts", 0))),
            ("Nem értékelhető adatok", str(sum(1 for v in r.get("properties", {}).values() if v == "Nem állapítható meg") + (1 if not r.get("accounts") else 0))),
            ("Vizsgálati hibák", str(len(errors))),
        ]
        self.overview_title.setText("Vizsgálati összefoglaló")
        self.overview_summary.setText(
            f"A vizsgálat {r.get('commands_ok', 0)} sikeres és {r.get('commands_failed', len(errors))} sikertelen ADB-parancsot rögzített, "
            f"ebből {r.get('timeouts', 0)} időtúllépés történt. A fiókbejegyzések felismerése a dumpsys kimenetének formátumától függ. "
            "Az FRP-jelzés nem tekinthető a zár hiányát bizonyító tesztnek."
        )
        self.overview_table.setRowCount(0)
        for key, val in overview_rows:
            row = self.overview_table.rowCount(); self.overview_table.insertRow(row)
            self.overview_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.overview_table.setItem(row, 1, QTableWidgetItem(str(val)))
        self.prop_table.setRowCount(0)
        for key,val in r.get("properties",{}).items():
            row=self.prop_table.rowCount(); self.prop_table.insertRow(row); self.prop_table.setItem(row,0,QTableWidgetItem(str(key))); self.prop_table.setItem(row,1,QTableWidgetItem(str(val)))
        self.account_table.resizeRowsToContents()
        self.prop_table.resizeRowsToContents()
        if r.get("devices"): self.log_line("adb devices -l:\n"+r["devices"])
        for e in errors: self.log_line("FIGYELMEZTETÉS: "+str(e))
        self.log_line("Eredmények megjelenítve.")
    def schedule_link_row_resize(self, *_args):
        """A leírásoszlop szélességének változásakor a sortördeléshez igazítja a sorokat."""
        QTimer.singleShot(0, self.resize_link_table_rows)

    def resize_link_table_rows(self):
        """Újraméri a többsoros linktáblák sorait az aktuális oszlopszélesség szerint."""
        for name in ("links_table", "facebook_table", "xiaomi_table"):
            table = getattr(self, name, None)
            if table is not None:
                table.resizeRowsToContents()

    def filter_links(self, query):
        query = query.casefold().strip()
        self.links_table.clearSelection()
        for row, (name, desc, url) in enumerate(self.google_links):
            self.links_table.setRowHidden(row, query not in (name + " " + desc + " " + url).casefold())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if obj is getattr(self, "links_table", None):
                self.open_google_link(); return True
            if obj is getattr(self, "facebook_table", None):
                self.open_facebook_link(); return True
            if obj is getattr(self, "xiaomi_table", None):
                self.open_xiaomi_link(); return True
        return super().eventFilter(obj, event)

    def open_google_link(self, row=None):
        if row is None:
            row = self.links_table.currentRow()
        if row < 0 or row >= len(self.google_links):
            QMessageBox.information(self, "Google-linkek", "Válassz ki egy sort a listából.")
            return
        url = self.google_links[row][2]
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Böngészőindítás", f"A rendszer nem tudta megnyitni ezt a címet:\n{url}\n\nEllenőrizd az alapértelmezett böngésző beállítását.")
        else:
            self.log_line(f"Google-oldal megnyitása az alapértelmezett böngészőben: {url}")

    def filter_facebook_links(self, query):
        query = query.casefold().strip()
        self.facebook_table.clearSelection()
        for row, (name, desc, url) in enumerate(self.facebook_links):
            self.facebook_table.setRowHidden(row, query not in (name + " " + desc + " " + url).casefold())

    def open_facebook_link(self, row=None):
        if row is None:
            row = self.facebook_table.currentRow()
        if row < 0 or row >= len(self.facebook_links):
            QMessageBox.information(self, "Facebook-linkek", "Válassz ki egy sort a listából.")
            return
        url = self.facebook_links[row][2]
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Böngészőindítás", f"A rendszer nem tudta megnyitni ezt a címet:\\n{url}\\n\\nEllenőrizd az alapértelmezett böngésző beállítását.")
        else:
            self.log_line(f"Facebook/Meta/Joomla oldal megnyitása az alapértelmezett böngészőben: {url}")

    def filter_xiaomi_links(self, query):
        query = query.casefold().strip()
        self.xiaomi_table.clearSelection()
        for row, (name, desc, url) in enumerate(self.xiaomi_links):
            self.xiaomi_table.setRowHidden(row, query not in (name + " " + desc + " " + url).casefold())

    def open_xiaomi_link(self, row=None):
        if row is None:
            row = self.xiaomi_table.currentRow()
        if row < 0 or row >= len(self.xiaomi_links):
            QMessageBox.information(self, "Xiaomi-linkek", "Válassz ki egy sort a listából.")
            return
        url = self.xiaomi_links[row][2]
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Böngészőindítás", f"A rendszer nem tudta megnyitni ezt a címet:\n{url}\n\nEllenőrizd az alapértelmezett böngésző beállítását.")
        else:
            self.log_line(f"Xiaomi-oldal megnyitása az alapértelmezett böngészőben: {url}")

    def copy_log(self, widget, label):
        text = widget.toPlainText()
        if not text.strip():
            QMessageBox.information(self, label, "A napló jelenleg üres.")
            return
        QApplication.clipboard().setText(text)
        self.log_line(f"{label}: a tartalom a vágólapra másolva.")

    @staticmethod
    def mask_sensitive_data(text):
        """Best-effort maszkolás kizárólag az exportált másolaton."""
        text = re.sub(r'(?i)\b[A-Z0-9._%+*\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b',
                      '[E-MAIL-MASZKOLVA]', text)
        text = re.sub(r'(?i)(serial(?:no)?|device[_ -]?id|android[_ -]?id|imei|meid|\bmac(?: address)?\b)\s*[:=]\s*([A-Z0-9._:-]+)',
                      r'\1=[AZONOSÍTÓ-MASZKOLVA]', text)
        # ADB devices -l serialjának és tipikus Android-azonosítóknak a maszkolása.
        text = re.sub(r'(?im)^(\s*List of devices attached\s*\n)?([A-F0-9]{12,20})(\s+device\b)',
                      lambda m: (m.group(1) or '') + '[SOROZATSZÁM-MASZKOLVA]' + m.group(3), text)
        text = re.sub(r'(?i)(android[_ -]?id\s*[:=]\s*)[a-f0-9]{8,32}', r'\1[AZONOSÍTÓ-MASZKOLVA]', text)
        return text

    def save_log(self, widget, label, masked=False):
        text = widget.toPlainText()
        if not text.strip():
            QMessageBox.information(self, label, "A napló jelenleg üres; nincs mit menteni.")
            return
        safe_name = re.sub(r'[^a-zA-Z0-9_-]+', '_', label.lower()).strip('_') or 'naplo'
        path, _ = QFileDialog.getSaveFileName(
            self, f"{label} mentése", f"{safe_name}.txt",
            "Szövegfájl (*.txt);;Minden fájl (*)"
        )
        if not path:
            return
        export_text = self.mask_sensitive_data(text) if masked else text
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(export_text)
                if not export_text.endswith("\n"):
                    handle.write("\n")
        except OSError as exc:
            QMessageBox.critical(self, "Mentési hiba", f"A fájl nem menthető:\n{exc}")
            self.log_line(f"MENTÉSI HIBA ({label}): {exc}")
            return
        self.log_line(f"{label}: napló mentve: {path}" + (" (maszkolás alkalmazva)" if masked else ""))
        QMessageBox.information(
            self, "Mentés befejezve",
            f"A napló mentése sikerült:\n{path}" +
            ("\n\nA személyes adatok maszkolása csak a mentett másolatban történt. A maszkolás nem garantálja minden személyes adat felismerését." if masked else
             "\n\nFigyelem: a fájl személyes vagy készülékazonosító adatokat tartalmazhat.")
        )

    def save_diagnostics_log(self):
        self.save_log(self.diagnostics_view, "Diagnosztikai log", self.masked_diagnostic_export.isChecked())

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self, "Vizsgálat folyamatban",
                "A vizsgálat még fut. A program bezárását a befejezésig elhalasztjuk, "
                "hogy a háttérszál biztonságosan leállhasson."
            )
            event.ignore()
            return
        event.accept()

def open_module(parent=None, settings=None): return GoogleAccountsWidget(parent=parent, settings=settings)
def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_TITLE); win=QMainWindow(); win.setWindowTitle(APP_TITLE); win.resize(1050,700); win.setCentralWidget(open_module()); win.show(); return app.exec()
if __name__ == "__main__": raise SystemExit(main())
