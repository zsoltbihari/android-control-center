#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redmi Control Center – hálózati diagnosztika (PyQt6, ADB)."""
from __future__ import annotations
import json, os, re, shutil, socket, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QPainter, QPen, QColor
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QFrame, QInputDialog, QMessageBox, QMenu, QGridLayout, QLineEdit,
    QSpinBox, QFileDialog, QFormLayout, QGroupBox, QSplitter)

class Adb:
    def __init__(self, path=None): self.path = path or shutil.which('adb') or '/usr/bin/adb'
    def run(self, args, timeout=12):
        try:
            p = subprocess.run([self.path, *args], capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError: raise RuntimeError('Az adb parancs nem található. Telepítsd az Android platform-tools csomagot.')
        except subprocess.TimeoutExpired: raise RuntimeError(f'Időtúllépés: {" ".join(args)}')
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    def phone(self, serial, *args, timeout=10):
        rc, out, err = self.run(['-s', serial, 'shell', *args], timeout)
        if rc: raise RuntimeError(err or out or f'ADB-hiba ({rc}): {" ".join(args)}')
        return out

class LineGraph(QWidget):
    """Könnyű, Qt-natív idősoros grafikon külső grafikus csomag nélkül."""
    def __init__(self, title, unit, parent=None):
        super().__init__(parent); self.title=title; self.unit=unit; self.series=[]; self.names=[]
        self.setMinimumHeight(190); self.setMinimumWidth(280)
    def set_series(self, series, names): self.series=series; self.names=names; self.update()
    @staticmethod
    def _format_axis(value):
        if value >= 1024 * 1024: return f"{value / (1024 * 1024):.1f} MB/s"
        if value >= 1024: return f"{value / 1024:.1f} KB/s"
        return f"{value:.0f} B/s"
    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#151a20')); p.setPen(QColor('#e7edf5')); p.setFont(QFont('Sans Serif', 10, QFont.Weight.DemiBold)); p.drawText(12,22,self.title)
        area=self.rect().adjusted(44,38,-14,-28); p.setPen(QPen(QColor('#2b3540'),1))
        for i in range(5):
            y=area.top()+i*area.height()/4; p.drawLine(area.left(),int(y),area.right(),int(y))
        maxv=max((max(s) for s in self.series if s),default=1.0); maxv=max(maxv,0.001)
        p.setFont(QFont('Sans Serif',8)); p.setPen(QColor('#9caaba'))
        for i in range(5):
            v=maxv*(4-i)/4; y=area.top()+i*area.height()/4; p.drawText(2,int(y+4),self._format_axis(v))
        colors=['#55a8ff','#45d6a0','#f0b65b','#d78cff']
        for si, values in enumerate(self.series):
            if not values: continue
            pen=QPen(QColor(colors[si%len(colors)]),2); p.setPen(pen)
            n=len(values)
            if n==1: p.drawPoint(int(area.right()), int(area.bottom()-values[0]/maxv*area.height())); continue
            points=[]
            for i,v in enumerate(values): points.append((area.left()+i*area.width()/(n-1),area.bottom()-v/maxv*area.height()))
            for a,b in zip(points,points[1:]): p.drawLine(int(a[0]),int(a[1]),int(b[0]),int(b[1]))
        p.setPen(QColor('#9caaba')); p.drawText(area.left(),self.height()-7,self.unit)
        x=area.left()+100
        for i,name in enumerate(self.names):
            p.setPen(QPen(QColor(colors[i%len(colors)]),3)); p.drawLine(int(x),self.height()-12,int(x+15),self.height()-12)
            p.setPen(QColor('#cbd5e1')); p.drawText(int(x+20),self.height()-8,name); x+=105

class SnapshotWorker(QThread):
    done=pyqtSignal(dict); failed=pyqtSignal(str)
    def __init__(self, adb, serial): super().__init__(); self.adb=adb; self.serial=serial
    def run(self):
        d={'Készülék':self.serial,'Vizsgálat időpontja':datetime.now().isoformat(timespec='seconds')}; errors=[]
        commands={
            'Aktív interfészek':('ip','-brief','addr'),'IPv4 útvonalak':('ip','-4','route'),'IPv6 útvonalak':('ip','-6','route'),
            'Wi-Fi részletek':('dumpsys','wifi'),'Kapcsolati állapot':('dumpsys','connectivity'),
            'DNS 1':('getprop','net.dns1'),'DNS 2':('getprop','net.dns2'),'DNS 3':('getprop','net.dns3'),
            'Wi-Fi parancs állapota':('cmd','wifi','status'),'Interfészforgalom /proc/net/dev':('cat','/proc/net/dev'),
            'Telefon tulajdonságai':('getprop','ro.build.version.release')}
        for key,args in commands.items():
            try: d[key]=self.adb.phone(self.serial,*args,timeout=14) or 'Nincs adat'
            except Exception as e: d[key]=f'Nem érhető el: {e}'; errors.append(f'{key}: {e}')
        ips=[]
        for line in d.get('Aktív interfészek','').splitlines():
            for token in line.split():
                if '/' in token and ('.' in token or ':' in token): ips.append(token.split('/')[0])
        d['IP-címek']=', '.join(dict.fromkeys(ips)) or 'Nem található'
        gateways=[]
        for line in (d.get('IPv4 útvonalak','')+'\n'+d.get('IPv6 útvonalak','')).splitlines():
            m=re.search(r'\bdefault\s+via\s+(\S+)',line)
            if m: gateways.append(m.group(1))
        d['Alapértelmezett átjáró']=', '.join(dict.fromkeys(gateways)) or 'Nem található'
        for label,target in [('Külső IP ping','1.1.1.1'),('DNS/domain teszt','connectivitycheck.gstatic.com')]:
            try:
                out=self.adb.phone(self.serial,'ping','-c','1','-W','3',target,timeout=7)
                m=re.search(r'time[=<]([\d.]+)\s*ms',out)
                d[label+' eredmény']='Elérhető' if ('bytes from' in out or '1 received' in out or '1 packets received' in out) else 'Nem igazolt'
                d[label+' válaszidő ms']=float(m.group(1)) if m else None; d[label+' kimenet']=out
            except Exception as e: d[label+' eredmény']='Nem igazolt'; d[label+' válaszidő ms']=None; d[label+' kimenet']=str(e)
        wifi_raw = d.get('Wi-Fi részletek', '')
        wifi_patterns = [
            ('Wi-Fi SSID', r'(?im)^.*?SSID[:= ]+([^,\n]+)'),
            ('Wi-Fi BSSID', r'(?im)^.*?BSSID[:= ]+([^,\n]+)'),
            ('Wi-Fi jelerősség (RSSI)', r'(?im)^.*?RSSI[:= ]+(-?\\d+)'),
            ('Wi-Fi frekvencia (MHz)', r'(?im)^.*?frequency[:= ]+(\\d+)'),
            ('Wi-Fi kapcsolati sebesség (Mbps)', r'(?im)^.*?(?:linkSpeed|Link speed)[:= ]+(\\d+)'),
        ]
        for label, pattern in wifi_patterns:
            match = re.search(pattern, wifi_raw)
            if match:
                d[label] = match.group(1).strip()
            else:
                d[label] = 'Nem érhető el ezen az Android-verzión / ADB-jogosultsággal'
        d['_hibák']=errors; self.done.emit(d)

class TrafficWorker(QThread):
    sample=pyqtSignal(float,float,str); message=pyqtSignal(str)
    def __init__(self,adb,serial,interval=1.0): super().__init__(); self.adb=adb; self.serial=serial; self.interval=interval; self._stop=False; self.prev={}
    def stop(self): self._stop=True
    def run(self):
        self.message.emit('Forgalommérés fut. A telefon interfészszámlálóiból számol, nem külön sebességteszt.')
        while not self._stop:
            try:
                raw=self.adb.phone(self.serial,'cat','/proc/net/dev',timeout=5); counters={}
                for line in raw.splitlines()[2:]:
                    if ':' not in line: continue
                    iface,vals=line.split(':',1); nums=vals.split()
                    if len(nums)>=9:
                        try: counters[iface.strip()]=(int(nums[0]),int(nums[8]))
                        except ValueError: pass

                # A virtuális/VPN-interfészek összegzése duplikálhat forgalmat.
                # Először az alapértelmezett útvonal interfészét keressük; ha nem
                # olvasható, az adott mintában ténylegesen változó interfészek közül
                # választjuk a legnagyobb forgalmút.
                excluded_exact = {"lo", "dummy0"}
                excluded_prefixes = ("tun", "tap", "gre", "gretap", "ip_vti", "ip6_vti", "sit", "veth", "br-", "docker", "ifb")
                deltas = {}
                for iface, (r, t) in counters.items():
                    if iface in excluded_exact or iface.startswith(excluded_prefixes):
                        continue
                    if iface in self.prev:
                        dr = max(0, r - self.prev[iface][0])
                        dt = max(0, t - self.prev[iface][1])
                        deltas[iface] = (dr, dt, dr + dt)

                route_iface = None
                try:
                    routes = self.adb.phone(self.serial, 'ip', 'route', 'show', 'default', timeout=3)
                    for route_line in routes.splitlines():
                        match = re.search(r'\bdev\s+(\S+)', route_line)
                        if match and match.group(1) in counters:
                            route_iface = match.group(1)
                            break
                except Exception:
                    pass

                active = [name for name, delta in deltas.items() if delta[2] > 0]
                if route_iface and route_iface in active:
                    selected = route_iface
                elif active:
                    selected = max(active, key=lambda name: deltas[name][2])
                elif route_iface:
                    selected = route_iface
                elif getattr(self, "selected_iface", None) in counters:
                    selected = self.selected_iface
                else:
                    preferred = [name for name in ("wlan0", "wlan1", "ap0") if name in counters]
                    cellular = [name for name in counters if name.startswith(("rmnet", "ccmni"))]
                    candidates = preferred or cellular or [name for name in counters if name not in excluded_exact and not name.startswith(excluded_prefixes)]
                    selected = candidates[0] if candidates else None

                if selected and selected in deltas:
                    rx, tx = deltas[selected][0], deltas[selected][1]
                else:
                    rx = tx = 0
                self.selected_iface = selected
                self.prev = counters
                self.sample.emit(rx / self.interval, tx / self.interval, selected or 'Nem azonosítható aktív interfész')
            except Exception as e: self.message.emit(f'Forgalommérés hiba: {e}')
            for _ in range(int(self.interval*10)):
                if self._stop: break
                time.sleep(.1)

class ProbeWorker(QThread):
    sample=pyqtSignal(float,float,str); message=pyqtSignal(str)
    def __init__(self,adb,serial,target,interval=2): super().__init__(); self.adb=adb; self.serial=serial; self.target=target; self.interval=interval; self._stop=False
    def stop(self): self._stop=True
    def run(self):
        while not self._stop:
            try:
                out=self.adb.phone(self.serial,'ping','-c','1','-W','2',self.target,timeout=5)
                m=re.search(r'time[=<]([\d.]+)\s*ms',out); ok=('bytes from' in out or '1 received' in out or '1 packets received' in out)
                self.sample.emit(float(m.group(1)) if m else 0.0,1.0 if ok else 0.0, 'Válasz' if ok else 'Nincs válasz')
            except Exception as e: self.sample.emit(0.0,0.0,str(e))
            for _ in range(self.interval*10):
                if self._stop: break
                time.sleep(.1)

class NetworkDiagnosticsWidget(QWidget):
    def __init__(self,parent=None,settings=None):
        super().__init__(parent); self.settings=dict(settings or {}); self.adb=Adb(self.settings.get('adb_path')); self.serial=None; self.devices=[]; self.worker=None; self.traffic_worker=None; self.probe_worker=None; self.data={}; self.rx_hist=[]; self.tx_hist=[]; self.ping_hist=[]; self.loss_hist=[]; self.max_points=90
        self.setStyleSheet('''QWidget{background:#171a1f;color:#e6e9ef;font-size:10pt} QPushButton{background:#263746;border:1px solid #405568;border-radius:6px;padding:7px 11px} QPushButton:hover{background:#344b60;border-color:#5b9cf5} QPushButton:disabled{color:#78818b;background:#20252b} QComboBox,QLineEdit,QSpinBox,QTableWidget,QTextEdit{background:#101419;color:#e6e9ef;border:1px solid #35414d;border-radius:5px;padding:5px;selection-background-color:#315477} QHeaderView::section{background:#252e38;color:#cbd6e2;padding:8px;border:0} QTabWidget::pane{border:1px solid #35414d;border-radius:5px} QTabBar::tab{background:#222a33;color:#bdc7d2;padding:9px 13px;margin-right:3px} QTabBar::tab:selected{background:#30465b;color:white;border-bottom:2px solid #5b9cf5} QFrame#Card,QGroupBox{background:#20262e;border:1px solid #394653;border-radius:8px} QLabel#PageTitle{font-size:15pt;font-weight:700}''')
        root=QVBoxLayout(self); root.setContentsMargins(12,12,12,12); root.setSpacing(9)
        header=QHBoxLayout(); title=QLabel('Hálózati diagnosztika'); title.setObjectName('PageTitle'); header.addWidget(title); header.addStretch(); header.addWidget(QLabel('Telefon:')); self.device_combo=QComboBox(); self.device_combo.setMinimumWidth(190); self.device_combo.currentIndexChanged.connect(self._selection_changed); header.addWidget(self.device_combo); b=QPushButton('Eszközök'); b.clicked.connect(self.refresh_devices); header.addWidget(b); self.run_button=QPushButton('Alapvizsgálat'); self.run_button.clicked.connect(self.start_diagnostics); header.addWidget(self.run_button); root.addLayout(header)
        cards=QHBoxLayout(); self.connection_value=self._card(cards,'ADB-KAPCSOLAT','Nincs telefon'); self.ip_value=self._card(cards,'IP-CÍMEK','—'); self.gateway_value=self._card(cards,'ÁTJÁRÓ','—'); self.internet_value=self._card(cards,'KÜLSŐ ELÉRÉS','Nincs vizsgálat'); root.addLayout(cards)
        self.tabs=QTabWidget(); root.addWidget(self.tabs,1); self.tabs.currentChanged.connect(self._tab_changed)
        # User-friendly overview
        page=QWidget(); l=QVBoxLayout(page); self.verdict=QLabel('Indítsd el az alapvizsgálatot.'); self.verdict.setWordWrap(True); self.verdict.setStyleSheet('font-size:14pt;font-weight:700;padding:8px'); l.addWidget(self.verdict); self.friendly=QTableWidget(0,3); self.friendly.setHorizontalHeaderLabels(['Ellenőrzés','Eredmény','Mit jelent?']); self._configure_table(self.friendly); self.friendly.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch); l.addWidget(self.friendly); self.tabs.addTab(page,'Áttekintés')
        # Live traffic graphs
        page=QWidget(); l=QVBoxLayout(page); row=QHBoxLayout(); self.traffic_state=QLabel('A grafikon indítás után gyűjti az adatokat.'); self.traffic_state.setWordWrap(True); self.traffic_state.setMinimumWidth(0); self.traffic_state.setMaximumWidth(520); self.traffic_state.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); row.addWidget(self.traffic_state,1); self.traffic_start=QPushButton('Forgalom figyelése'); self.traffic_start.clicked.connect(self.toggle_traffic); row.addWidget(self.traffic_start); l.addLayout(row); stats=QHBoxLayout(); self.rx_value=self._metric(stats,'LETÖLTÉS','—'); self.tx_value=self._metric(stats,'FELTÖLTÉS','—'); self.rx_total=self._metric(stats,'MÉRT LETÖLTÉS','0 B'); self.tx_total=self._metric(stats,'MÉRT FELTÖLTÉS','0 B'); l.addLayout(stats); self.traffic_stats=QLabel('Átlag: — · Csúcs: —'); self.traffic_stats.setStyleSheet('color:#aebccc;padding:3px 4px'); l.addWidget(self.traffic_stats); graphs=QHBoxLayout(); self.rx_graph=LineGraph('Letöltési forgalom','B/s  •  utolsó 90 másodperc'); self.tx_graph=LineGraph('Feltöltési forgalom','B/s  •  utolsó 90 másodperc'); graphs.addWidget(self.rx_graph,1); graphs.addWidget(self.tx_graph,1); l.addLayout(graphs); note=QLabel('A sebesség a kiválasztott Android-interfész számlálóinak változásából készül. Ez nem feltétlenül ugyanaz az interfész, amelyet a számítógépen futó Conky mér; összehasonlításhoz azonos kapcsolatot és időszakot használj.'); note.setWordWrap(True); note.setStyleSheet('color:#9caaba'); l.addWidget(note); self.tabs.addTab(page,'Élő forgalom')
        # Ping / DNS graph
        page=QWidget(); l=QVBoxLayout(page); form=QHBoxLayout(); form.addWidget(QLabel('Vizsgált cím:')); self.probe_target=QLineEdit('1.1.1.1'); self.probe_target.setMaximumWidth(220); form.addWidget(self.probe_target); form.addWidget(QLabel('DNS-teszt külön is elérhető az alapvizsgálatban.')); form.addStretch(); self.probe_start=QPushButton('Pingmonitor indítása'); self.probe_start.clicked.connect(self.toggle_probe); form.addWidget(self.probe_start); l.addLayout(form); self.ping_value=self._metric(l,'UTOLSÓ VÁLASZIDŐ','—'); self.loss_value=self._metric(l,'SIKERES VÁLASZOK','—'); self.ping_graph=LineGraph('Ping válaszidő','ms  •  sikertelen próba = 0 az elérhetőségi sorozatban'); l.addWidget(self.ping_graph); self.probe_status=QLabel('A pingmonitor külön, időszakosan pingeli a megadott címet.'); l.addWidget(self.probe_status); self.tabs.addTab(page,'Ping és DNS')
        # Friendly details + technical raw
        page=QWidget(); l=QVBoxLayout(page); self.details=QTableWidget(0,2); self.details.setHorizontalHeaderLabels(['Adat','Érték']); self._configure_table(self.details); l.addWidget(self.details); self.tabs.addTab(page,'Wi-Fi és interfészek')
        # Advanced TCP and route tests
        page=QWidget(); l=QVBoxLayout(page); group=QGroupBox('TCP-portteszt'); f=QFormLayout(group); self.host_input=QLineEdit('example.com'); self.port_input=QSpinBox(); self.port_input.setRange(1,65535); self.port_input.setValue(443); self.tcp_button=QPushButton('Port ellenőrzése'); self.tcp_button.clicked.connect(self.test_tcp); self.tcp_result=QLabel('Adjon meg egy gazdagépnevet vagy IP-címet és portot.'); self.tcp_result.setWordWrap(True); f.addRow('Kiszolgáló:',self.host_input); f.addRow('Port:',self.port_input); f.addRow(self.tcp_button); f.addRow('Eredmény:',self.tcp_result); l.addWidget(group)
        group=QGroupBox('Útvonalvizsgálat'); f=QFormLayout(group); self.route_target=QLineEdit('1.1.1.1'); self.route_button=QPushButton('Útvonalvizsgálat indítása'); self.route_button.clicked.connect(self.test_route); self.route_result=QTextEdit(); self.route_result.setReadOnly(True); self.route_result.setMaximumHeight(180); f.addRow('Célpont:',self.route_target); f.addRow(self.route_button); f.addRow(self.route_result); l.addWidget(group); l.addStretch(); self.tabs.addTab(page,'Haladó eszközök')
        # Raw output and report
        page=QWidget(); l=QVBoxLayout(page); row=QHBoxLayout(); row.addWidget(QLabel('Teljes, technikai kimenet – haladó hibakereséshez.'),1); self.raw_copy=QPushButton('Másolás'); self.raw_copy.clicked.connect(self.copy_raw); row.addWidget(self.raw_copy); l.addLayout(row); self.raw=QTextEdit(); self.raw.setReadOnly(True); l.addWidget(self.raw); self.tabs.addTab(page,'Nyers adatok')
        page=QWidget(); l=QVBoxLayout(page); l.addWidget(QLabel('A jelentés tartalmazza az alapvizsgálat eredményeit, a forgalmi és pingmintákat, valamint a haladó tesztek eredményeit.')) ; self.report_preview=QTextEdit(); self.report_preview.setReadOnly(True); l.addWidget(self.report_preview); row=QHBoxLayout(); self.report_copy=QPushButton('Jelentés másolása'); self.report_copy.clicked.connect(self.copy_report); self.report_save=QPushButton('Jelentés mentése…'); self.report_save.clicked.connect(self.save_report); row.addWidget(self.report_copy); row.addWidget(self.report_save); row.addStretch(); l.addLayout(row); self.tabs.addTab(page,'Jelentés exportálása')
        footer=QHBoxLayout(); self.status=QLabel('Csatlakoztass telefont, majd indítsd el a vizsgálatot.'); self.status.setWordWrap(True); footer.addWidget(self.status,1); self.copy_all=QPushButton('Összes eredmény másolása'); self.copy_all.clicked.connect(self.copy_report); footer.addWidget(self.copy_all); root.addLayout(footer)
        self.refresh_devices(initial=True)
    def _card(self,layout,heading,value):
        c=QFrame(); c.setObjectName('Card'); v=QVBoxLayout(c); lab=QLabel(heading); lab.setStyleSheet('color:#aeb9c7;font-size:9pt;font-weight:700'); val=QLabel(value); val.setWordWrap(True); val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); val.setStyleSheet('font-size:11pt;font-weight:650'); v.addWidget(lab); v.addWidget(val); layout.addWidget(c,1); return val
    def _metric(self,layout,heading,value):
        c=QFrame(); c.setObjectName('Card'); v=QVBoxLayout(c); lab=QLabel(heading); lab.setStyleSheet('color:#aeb9c7;font-size:8pt;font-weight:700'); val=QLabel(value); val.setStyleSheet('font-size:14pt;font-weight:700'); val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); v.addWidget(lab); v.addWidget(val); layout.addWidget(c,1); return val
    def _configure_table(self,t):
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); t.setAlternatingRowColors(True); t.verticalHeader().setVisible(False); t.setWordWrap(True); t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); t.customContextMenuRequested.connect(lambda pos,table=t:self._table_menu(table,pos))
    def _table_menu(self,table,pos):
        item=table.itemAt(pos)
        if not item:return
        menu=QMenu(self); a=menu.addAction('Kijelölt cella másolása'); b=menu.addAction('Sor másolása'); chosen=menu.exec(table.viewport().mapToGlobal(pos))
        if chosen==a: QApplication.clipboard().setText(item.text())
        elif chosen==b:
            row=item.row(); QApplication.clipboard().setText(' | '.join(table.item(row,c).text() if table.item(row,c) else '' for c in range(table.columnCount())))
    def refresh_devices(self,initial=False):
        previous=self.serial
        try:
            rc,out,err=self.adb.run(['devices','-l'],10)
            if rc: raise RuntimeError(err or out or f'ADB-hiba: {rc}')
            dev=[]
            for line in out.splitlines()[1:]:
                b=line.split()
                if len(b)>=2 and b[1]=='device': dev.append((b[0],' '.join(b[2:])))
            self.devices=dev
        except Exception as e:
            self.devices=[]; self.serial=None; self.device_combo.clear(); self.connection_value.setText('ADB nem érhető el'); self.status.setText(str(e)); return
        self.device_combo.blockSignals(True); self.device_combo.clear()
        for s,info in self.devices:self.device_combo.addItem(f'{s} — {info}' if info else s,s)
        if not dev:
            self.serial=None; self.connection_value.setText('Nincs telefon'); self.device_combo.blockSignals(False); self.run_button.setEnabled(False); self.status.setText('Nincs engedélyezett ADB-eszköz.'); return
        serials=[s for s,_ in dev]; preferred=previous if previous in serials else self.settings.get('serial')
        if len(dev)>1:
            labels=[f'{s} — {info}' if info else s for s,info in dev]; current=serials.index(preferred) if preferred in serials else 0
            label,ok=QInputDialog.getItem(self,'ADB-eszköz kiválasztása','Vizsgálandó telefon:',labels,current,False)
            if ok and label in labels: preferred=dev[labels.index(label)][0]
            elif preferred not in serials: self.serial=None; self.device_combo.blockSignals(False); self.run_button.setEnabled(False); self.status.setText('Nem történt eszközválasztás.'); return
        self.serial=preferred or dev[0][0]; self.device_combo.setCurrentIndex(self.device_combo.findData(self.serial)); self.device_combo.blockSignals(False); self.connection_value.setText('Csatlakoztatva'); self.run_button.setEnabled(True); self.status.setText(f'Kiválasztott telefon: {self.serial}')
    def _selection_changed(self,index):
        self.serial=self.device_combo.itemData(index) if index>=0 else None; self.run_button.setEnabled(bool(self.serial))
        if self.serial:self.connection_value.setText('Csatlakoztatva')
    def start_diagnostics(self):
        if not self.serial: QMessageBox.information(self,'Hálózati diagnosztika','Előbb válassz ki egy ADB-vel engedélyezett telefont.'); return
        if self.worker and self.worker.isRunning():return
        self.run_button.setEnabled(False); self.status.setText('Az alapvizsgálat fut…'); self.worker=SnapshotWorker(self.adb,self.serial); self.worker.done.connect(self.show_snapshot); self.worker.finished.connect(lambda:self.run_button.setEnabled(bool(self.serial))); self.worker.start()
    def show_snapshot(self,d):
        self.data=d; ext=d.get('Külső IP ping eredmény','Nem igazolt'); dns=d.get('DNS/domain teszt eredmény','Nem igazolt')
        if ext=='Elérhető' and dns=='Elérhető': self.verdict.setText('A tesztelt külső IP és domain is válaszolt.'); self.verdict.setStyleSheet('font-size:14pt;font-weight:700;color:#45d6a0;padding:8px')
        elif ext=='Elérhető' or dns=='Elérhető': self.verdict.setText('Részleges eredmény: legalább egy külső próba sikerült.'); self.verdict.setStyleSheet('font-size:14pt;font-weight:700;color:#e7b65c;padding:8px')
        else:self.verdict.setText('A külső elérés nem igazolt. Ez önmagában nem bizonyít internetkimaradást.'); self.verdict.setStyleSheet('font-size:14pt;font-weight:700;color:#e7b65c;padding:8px')
        rows=[('Telefon IP-címe',d.get('IP-címek'),'A telefon címe a helyi hálózaton.'),('Router / átjáró',d.get('Alapértelmezett átjáró'),'A forgalmat más hálózatok felé továbbító eszköz.'),('DNS-kiszolgáló',d.get('DNS 1'),'A webcímek IP-címre fordításában segít.'),('Külső IP ping',ext,'A pinget egyes hálózatok blokkolhatják.'),('DNS/domain teszt',dns,'A domainnév és a ping együtt ellenőrződik.'),('Aktív interfészek','Részletek a Wi-Fi és interfészek fülön','Wi-Fi, mobil és egyéb hálózati csatolók.')]
        self.friendly.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,v in enumerate(row):self.friendly.setItem(r,c,QTableWidgetItem(str(v or 'Nincs adat')))
        self.friendly.resizeRowsToContents(); keys=['Készülék','Vizsgálat időpontja','IP-címek','Alapértelmezett átjáró','DNS 1','DNS 2','DNS 3','Külső IP ping eredmény','Külső IP ping válaszidő ms','DNS/domain teszt eredmény','DNS/domain teszt válaszidő ms']
        self.details.setRowCount(len(d)); self.details.setColumnCount(2); self.details.setHorizontalHeaderLabels(['Adat','Érték'])
        for r,(k,v) in enumerate(d.items()): self.details.setItem(r,0,QTableWidgetItem(k)); self.details.setItem(r,1,QTableWidgetItem(str(v)))
        self.details.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents); self.details.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.ip_value.setText(str(d.get('IP-címek','—'))); self.gateway_value.setText(str(d.get('Alapértelmezett átjáró','—'))); self.internet_value.setText(f'IP: {ext} · DNS: {dns}')
        self.raw.setPlainText('\n\n'.join(f'=== {k} ===\n{v}' for k,v in d.items() if not k.startswith('_'))); self.status.setText('Alapvizsgálat kész. A részletek és export a megfelelő füleken érhetők el.'); self.refresh_report()
    def toggle_traffic(self):
        if self.traffic_worker and self.traffic_worker.isRunning(): self.traffic_worker.stop(); self.traffic_start.setText('Forgalom figyelése'); self.traffic_state.setText('Forgalommérés leállítva.'); return
        if not self.serial:self.status.setText('Nincs kiválasztott telefon.'); return
        self.rx_hist=[]; self.tx_hist=[]; self.rx_total_bytes=0; self.tx_total_bytes=0; self.traffic_worker=TrafficWorker(self.adb,self.serial); self.traffic_worker.sample.connect(self.add_traffic); self.traffic_worker.message.connect(self.traffic_state.setText); self.traffic_worker.start(); self.traffic_start.setText('Mérés leállítása')
    def add_traffic(self,rx,tx,interfaces):
        self.rx_hist.append(rx); self.tx_hist.append(tx); self.rx_hist=self.rx_hist[-self.max_points:]; self.tx_hist=self.tx_hist[-self.max_points:]; self.rx_total_bytes=getattr(self,'rx_total_bytes',0)+rx; self.tx_total_bytes=getattr(self,'tx_total_bytes',0)+tx
        self.rx_value.setText(self.fmt_rate(rx)); self.tx_value.setText(self.fmt_rate(tx)); self.rx_total.setText(self.fmt_bytes(self.rx_total_bytes)); self.tx_total.setText(self.fmt_bytes(self.tx_total_bytes)); self.rx_graph.set_series([self.rx_hist],[]); self.tx_graph.set_series([self.tx_hist],[]); avg_rx=sum(self.rx_hist)/len(self.rx_hist) if self.rx_hist else 0; avg_tx=sum(self.tx_hist)/len(self.tx_hist) if self.tx_hist else 0; peak_rx=max(self.rx_hist,default=0); peak_tx=max(self.tx_hist,default=0); self.traffic_stats.setText(f'Átlag — letöltés: {self.fmt_rate(avg_rx)} · feltöltés: {self.fmt_rate(avg_tx)}    |    Csúcs — letöltés: {self.fmt_rate(peak_rx)} · feltöltés: {self.fmt_rate(peak_tx)}'); self.traffic_state.setText('Aktív mérési interfész: ' + (', '.join(x.strip() for x in (interfaces or '').split(',') if x.strip()) or 'azonosítás folyamatban') + ' · Az Android által jelentett interfészforgalom; VPN esetén eltérhet a PC-s Conky mérésétől.')
    def toggle_probe(self):
        if self.probe_worker and self.probe_worker.isRunning(): self.probe_worker.stop(); self.probe_start.setText('Pingmonitor indítása'); self.probe_status.setText('Pingmonitor leállítva.'); return
        target=self.probe_target.text().strip()
        if not self.serial:self.status.setText('Nincs kiválasztott telefon.'); return
        if not target:self.status.setText('Adj meg egy IP-címet vagy domainnevet.'); return
        self.ping_hist=[]; self.loss_hist=[]; self.probe_worker=ProbeWorker(self.adb,self.serial,target); self.probe_worker.sample.connect(self.add_probe); self.probe_worker.message.connect(self.probe_status.setText); self.probe_worker.start(); self.probe_start.setText('Pingmonitor leállítása'); self.probe_status.setText('Pingelés: '+target)
    def add_probe(self,ms,ok,message):
        self.ping_hist.append(ms if ok else 0); self.loss_hist.append(ok); self.ping_hist=self.ping_hist[-self.max_points:]; self.loss_hist=self.loss_hist[-self.max_points:]; self.ping_graph.set_series([self.ping_hist],['Ping']); self.ping_value.setText(f'{ms:.1f} ms' if ok else 'Nincs válasz'); rate=100*sum(self.loss_hist)/len(self.loss_hist); self.loss_value.setText(f'{rate:.0f}% siker ({len(self.loss_hist)} próba)'); self.probe_status.setText(message); self.refresh_report()
    def test_tcp(self):
        host=self.host_input.text().strip(); port=self.port_input.value()
        if not host:self.tcp_result.setText('Adj meg egy kiszolgálónevet vagy IP-címet.'); return
        self.tcp_button.setEnabled(False); self.tcp_result.setText('TCP-kapcsolat ellenőrzése…'); QApplication.processEvents()
        # The test runs from the computer running the GUI, not from the Android phone.
        try:
            start=time.monotonic(); sock=socket.create_connection((host,port),timeout=4); sock.close(); ms=(time.monotonic()-start)*1000; self.tcp_result.setText(f'SIKER: {host}:{port} elérhető a számítógépről. Kapcsolatfelépítés: {ms:.1f} ms. Ez nem a telefon hálózatáról végzett teszt.')
            self.data['TCP-portteszt']=self.tcp_result.text()
        except Exception as e:self.tcp_result.setText(f'NEM SIKERÜLT: {host}:{port} – {e}. A teszt a számítógépről indult, nem a telefonról.'); self.data['TCP-portteszt']=self.tcp_result.text()
        self.tcp_button.setEnabled(True); self.refresh_report()
    def test_route(self):
        if not self.serial:self.route_result.setPlainText('Nincs kiválasztott telefon.'); return
        target=self.route_target.text().strip()
        if not target:self.route_result.setPlainText('Adj meg célpontot.'); return
        self.route_button.setEnabled(False); self.route_result.setPlainText('Útvonalvizsgálat fut…'); QApplication.processEvents()
        try:
            # Androidon a traceroute hiányozhat vagy korlátozott lehet.
            try: out=self.adb.phone(self.serial,'traceroute','-m','12','-w','1',target,timeout=18)
            except Exception: out=self.adb.phone(self.serial,'toybox','traceroute','-m','12','-w','1',target,timeout=18)
            self.route_result.setPlainText(out or 'A parancs nem adott kimenetet.'); self.data['Útvonalvizsgálat']=out or 'Nincs kimenet'
        except Exception as e:
            msg=f'Az útvonalvizsgálat nem futtatható ezen a telefonon: {e}\nEgyes Android-verziókból hiányzik a traceroute, vagy a hálózat nem válaszol az útvonalvizsgálati csomagokra.'; self.route_result.setPlainText(msg); self.data['Útvonalvizsgálat']=msg
        self.route_button.setEnabled(True); self.refresh_report()
    @staticmethod
    def fmt_rate(v):
        units=['B/s','KB/s','MB/s','GB/s']; i=0; v=float(v)
        while v>=1000 and i<len(units)-1:v/=1000;i+=1
        return f'{v:.1f} {units[i]}'
    @staticmethod
    def fmt_bytes(v):
        units=['B','KB','MB','GB']; i=0; v=float(v)
        while v>=1024 and i<len(units)-1:v/=1024;i+=1
        return f'{v:.1f} {units[i]}'
    def build_report(self):
        out={'alkalmazás':'Redmi Control Center – Hálózati diagnosztika','készült':datetime.now().isoformat(timespec='seconds'),'telefon':self.serial,'alapvizsgálat':{k:v for k,v in self.data.items() if not k.startswith('_')},'forgalmi_minták':{'interfész':self.traffic_state.text(),'rx_B_s':self.rx_hist,'tx_B_s':self.tx_hist,'letöltés_átlag_B_s':(sum(self.rx_hist)/len(self.rx_hist) if self.rx_hist else 0),'feltöltés_átlag_B_s':(sum(self.tx_hist)/len(self.tx_hist) if self.tx_hist else 0),'letöltés_csúcs_B_s':max(self.rx_hist,default=0),'feltöltés_csúcs_B_s':max(self.tx_hist,default=0),'összes_mért_rx_B':getattr(self,'rx_total_bytes',0),'összes_mért_tx_B':getattr(self,'tx_total_bytes',0)},'ping_minták':{'célpont':self.probe_target.text().strip(),'válaszidő_ms_0_jelenti_a_sikertelent':self.ping_hist,'sikeres_próbák':self.loss_hist},'tcp_portteszt':self.data.get('TCP-portteszt'),'útvonalvizsgálat':self.data.get('Útvonalvizsgálat')}
        return out
    def _tab_changed(self, index):
        # A teljes jelentés nagy dumpsys-kimeneteket is tartalmazhat.
        # Csak akkor állítsuk elő az előnézetet, amikor a felhasználó megnyitja a jelentésfület.
        if self.tabs.tabText(index) == "Jelentés exportálása":
            self.refresh_report()

    def refresh_report(self):
        try:
            obj = self.build_report()
            # Tömör előnézet: a teljes technikai tartalom exportáláskor kerül a fájlba.
            preview = {
                "alkalmazás": obj.get("alkalmazás"),
                "készült": obj.get("készült"),
                "telefon": obj.get("telefon"),
                "alapvizsgálat_mezőinek_száma": len(obj.get("alapvizsgálat", {})),
                "forgalmi_minták_pontjai": {
                    "letöltés": len(obj.get("forgalmi_minták", {}).get("rx_B_s", [])),
                    "feltöltés": len(obj.get("forgalmi_minták", {}).get("tx_B_s", [])),
                },
                "ping_minták_pontjai": len(obj.get("ping_minták", {}).get("válaszidő_ms_0_jelenti_a_sikertelent", [])),
                "megjegyzés": "A teljes technikai adatok a Jelentés mentése gombbal exportálhatók."
            }
            self.report_preview.setPlainText(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
        except Exception as exc:
            self.report_preview.setPlainText(f"A jelentés előnézete nem készíthető el: {exc}")

    def copy_report(self):
        try:
            payload = json.dumps(self.build_report(), ensure_ascii=False, indent=2, default=str)
            QApplication.clipboard().setText(payload)
            self.status.setText("A teljes diagnosztikai jelentés a vágólapra került.")
        except Exception as exc:
            self.status.setText(f"A jelentés összeállítása sikertelen: {exc}")

    def save_report(self):
        name=f'halozati-diagnosztika-{datetime.now():%Y%m%d-%H%M%S}.json'
        path,_=QFileDialog.getSaveFileName(self,'Diagnosztikai jelentés mentése',str(Path.home()/name),'JSON jelentés (*.json);;Szövegfájl (*.txt)')
        if not path:return
        try:
            obj = self.build_report()
            content = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
            if path.lower().endswith('.txt'):
                sections = [
                    'REDMI CONTROL CENTER – HÁLÓZATI DIAGNOSZTIKAI JELENTÉS',
                    f"Készült: {obj.get('készült')}",
                    f"Telefon: {obj.get('telefon')}",
                    '',
                    'ALAPVIZSGÁLAT',
                ]
                sections.extend(f"- {key}: {value}" for key, value in obj.get('alapvizsgálat', {}).items())
                traffic = obj.get('forgalmi_minták', {})
                sections += ['', 'FORGALOMFIGYELÉS',
                    f"- Interfész: {traffic.get('interfész', 'Nincs adat')}",
                    f"- Átlagos letöltés: {traffic.get('letöltés_átlag_B_s', 0):.1f} B/s",
                    f"- Átlagos feltöltés: {traffic.get('feltöltés_átlag_B_s', 0):.1f} B/s",
                    f"- Letöltési csúcs: {traffic.get('letöltés_csúcs_B_s', 0):.1f} B/s",
                    f"- Feltöltési csúcs: {traffic.get('feltöltés_csúcs_B_s', 0):.1f} B/s",
                    f"- Mért letöltés összesen: {traffic.get('összes_mért_rx_B', 0)} bájt",
                    f"- Mért feltöltés összesen: {traffic.get('összes_mért_tx_B', 0)} bájt",
                    '', 'PINGMINTÁK',
                    json.dumps(obj.get('ping_minták', {}), ensure_ascii=False, indent=2),
                    '', 'HALADÓ TESZTEK',
                    f"- TCP-portteszt: {obj.get('tcp_portteszt') or 'Nem futott'}",
                    f"- Útvonalvizsgálat: {obj.get('útvonalvizsgálat') or 'Nem futott'}",
                ]
                content = '\n'.join(sections)
            Path(path).write_text(content,encoding='utf-8')
            self.status.setText(f'Jelentés mentve: {path}')
        except Exception as e:
            QMessageBox.critical(self,'Mentési hiba',f'A jelentést nem sikerült menteni:\\n{e}')
    def copy_raw(self):QApplication.clipboard().setText(self.raw.toPlainText()); self.status.setText('A nyers adatok a vágólapra kerültek.')
    def closeEvent(self,event):
        for w in (self.traffic_worker,self.probe_worker):
            if w and w.isRunning():w.stop(); w.wait(2000)
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,'Vizsgálat folyamatban','Várd meg az alapvizsgálat végét.'); event.ignore(); return
        super().closeEvent(event)

def open_module(parent=None,settings=None):return NetworkDiagnosticsWidget(parent=parent,settings=settings)
def main():
    app=QApplication.instance() or QApplication(sys.argv); win=QWidget(); lay=QVBoxLayout(win); w=NetworkDiagnosticsWidget(win); lay.addWidget(w); win.setWindowTitle('Redmi Control Center — Hálózati diagnosztika'); win.resize(1180,820); win.show(); return app.exec()
if __name__=='__main__':raise SystemExit(main())
