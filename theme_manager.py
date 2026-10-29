#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Témakezelő a Redmi Control Centerhez.

Önálló modul, a projekt gyökérkönyvtárába (a main.py mellé) szánt fájl.
A beépített témák a main.py jelenlegi színvilágát követik.
A saját témákat külön fájlba menti:
    ~/.config/redmi-control-center/themes.json

A modul nem olvassa, nem írja át és nem helyezi át a meglévő settings.json-t.
"""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QGridLayout,
)

CONFIG_DIR = Path.home() / ".config" / "redmi-control-center"
THEMES_FILE = CONFIG_DIR / "themes.json"

# Színkulcsok: a QSS-generálás ezeket használja.
BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "Alapértelmezett": {
        "background": "#f0f0f0", "surface": "#fafafa",
        "text": "#202020", "border": "#b9b9b9",
        "button": "#f8f8f8", "hover": "#e4e4e4",
        "selected": "#d7d7d7", "accent": "#3978b8",
    },
    "Matrix": {
        "background": "#07120b", "surface": "#020704",
        "text": "#72ff8b", "border": "#238b45",
        "button": "#0b2112", "hover": "#12391d",
        "selected": "#174d27", "accent": "#39ff68",
    },
    "Zombie": {
        "background": "#202522", "surface": "#171b18",
        "text": "#d1d8c6", "border": "#68775f",
        "button": "#303a31", "hover": "#465342",
        "selected": "#465342", "accent": "#a5bd78",
    },
    "Vampire": {
        "background": "#171018", "surface": "#0d080e",
        "text": "#f0dce8", "border": "#842b49",
        "button": "#29131f", "hover": "#4a1c31",
        "selected": "#4a1c31", "accent": "#e04c78",
    },
    "Barbie": {
        "background": "#fff0f7", "surface": "#fff9fc",
        "text": "#651044", "border": "#df78b0",
        "button": "#ffd8ed", "hover": "#ffb7dc",
        "selected": "#ffb7dc", "accent": "#d63384",
    },
    "Pink pussycat": {
        "background": "#2a1426", "surface": "#1b0c19",
        "text": "#ffd9f1", "border": "#b64d94",
        "button": "#46203e", "hover": "#69305e",
        "selected": "#69305e", "accent": "#ff70c8",
    },
}

COLOR_LABELS = {
    "background": "Ablak háttere",
    "surface": "Munkaterület háttere",
    "text": "Szöveg",
    "border": "Keretek",
    "button": "Gombok háttere",
    "hover": "Egér fölötti állapot",
    "selected": "Kijelölt elem",
    "accent": "Kiemelőszín",
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _valid_color(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.fullmatch(value))


def _normalise_theme(theme: dict[str, Any]) -> dict[str, str]:
    """Kitölti a hiányzó színeket az alapértelmezett téma értékeivel."""
    base = BUILTIN_THEMES["Alapértelmezett"]
    result: dict[str, str] = {}
    for key, fallback in base.items():
        value = theme.get(key, fallback)
        result[key] = value if _valid_color(value) else fallback
    return result


def load_custom_themes(path: Path | str = THEMES_FILE) -> dict[str, dict[str, str]]:
    """Saját témák betöltése; hibás vagy hiányzó fájlnál üres szótár."""
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for name, colors in raw.items():
        if (
            isinstance(name, str)
            and name.strip()
            and name not in BUILTIN_THEMES
            and isinstance(colors, dict)
        ):
            result[name.strip()] = _normalise_theme(colors)
    return result


def save_custom_themes(
    themes: dict[str, dict[str, str]], path: Path | str = THEMES_FILE
) -> None:
    """Saját témák atomikus mentése külön JSON-fájlba."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    clean: dict[str, dict[str, str]] = {}
    for name, colors in themes.items():
        name = str(name).strip()
        if not name or name in BUILTIN_THEMES:
            continue
        clean[name] = _normalise_theme(colors)
    temp = file_path.with_suffix(file_path.suffix + ".tmp")
    temp.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Meglévő témák esetén biztonsági másolatot tartunk fenn.
    if file_path.exists():
        backup = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, backup)
    temp.replace(file_path)


def available_themes(path: Path | str = THEMES_FILE) -> dict[str, dict[str, str]]:
    """Beépített és saját témák egyesített, másolható szótára."""
    themes = deepcopy(BUILTIN_THEMES)
    themes.update(load_custom_themes(path))
    return themes


def theme_stylesheet(theme: str | dict[str, Any], path: Path | str = THEMES_FILE) -> str:
    """QSS-stíluslap generálása témanévből vagy színszótárból."""
    if isinstance(theme, str):
        all_themes = available_themes(path)
        colors = all_themes.get(theme, all_themes["Alapértelmezett"])
    else:
        colors = _normalise_theme(theme)

    bg = colors["background"]
    surface = colors["surface"]
    fg = colors["text"]
    border = colors["border"]
    button = colors["button"]
    hover = colors["hover"]
    selected = colors["selected"]
    accent = colors["accent"]

    return f"""
        QWidget {{
            background-color: {bg};
            color: {fg};
        }}
        QMainWindow, QDialog {{
            background-color: {bg};
        }}
        QFrame#workspace, QGroupBox {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QGroupBox {{
            margin-top: 18px;
            padding: 12px 8px 8px 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            top: 0px;
            left: 10px;
            padding: 1px 6px;
            background-color: {surface};
        }}
        QPushButton {{
            padding: 7px 12px;
            border: 1px solid {border};
            border-radius: 5px;
            background-color: {button};
            color: {fg};
        }}
        QPushButton:hover {{
            background-color: {hover};
            border-color: {accent};
        }}
        QPushButton:pressed {{
            background-color: {selected};
        }}
        QPushButton:disabled {{
            color: {border};
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
        QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
            padding: 6px;
            border: 1px solid {border};
            border-radius: 5px;
            background-color: {surface};
            color: {fg};
            selection-background-color: {selected};
        }}
        QComboBox QAbstractItemView {{
            background-color: {surface};
            color: {fg};
            selection-background-color: {selected};
        }}
        QTabWidget::pane {{
            border: 1px solid {border};
            border-radius: 5px;
        }}
        QTabBar::tab {{
            background-color: {button};
            color: {fg};
            border: 1px solid {border};
            padding: 7px 12px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: {selected};
            border-bottom: 2px solid {accent};
        }}
        QMenuBar, QMenu {{
            background-color: {bg};
            color: {fg};
        }}
        QMenu::item:selected {{
            background-color: {selected};
        }}
        QCheckBox, QRadioButton {{
            spacing: 6px;
        }}
        QToolTip {{
            background-color: {surface};
            color: {fg};
            border: 1px solid {border};
            padding: 4px;
        }}
    """


class ThemeEditorDialog(QDialog):
    """Téma létrehozása vagy szerkesztése színválasztókkal."""

    def __init__(
        self,
        name: str = "",
        colors: dict[str, str] | None = None,
        parent: QWidget | None = None,
        title: str = "Téma szerkesztése",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(430)
        self.colors = _normalise_theme(colors or {})
        self._original_colors = deepcopy(self.colors)
        self._undo_stack: list[tuple[str, str]] = []
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("A téma neve")
        self.color_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Téma neve:", self.name_edit)
        group = QGroupBox("Színek")
        grid = QGridLayout(group)

        for row, (key, label) in enumerate(COLOR_LABELS.items()):
            grid.addWidget(QLabel(label), row, 0)
            button = QPushButton(self.colors[key].upper())
            button.setMinimumWidth(120)
            button.clicked.connect(lambda checked=False, k=key: self._choose_color(k))
            self.color_buttons[key] = button
            grid.addWidget(button, row, 1)
            self._paint_button(key)

        form.addRow(group)
        layout.addLayout(form)
        preview_group = QGroupBox("Élő előnézet")
        preview_group.setContentsMargins(8, 22, 8, 8)
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QLabel("Minta szöveg – kijelölés és gombstílus")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview)
        preview_row = QHBoxLayout()
        self.preview_button = QPushButton("Minta gomb")
        self.preview_input = QLineEdit("Minta beviteli mező")
        self.preview_combo = QComboBox()
        self.preview_combo.addItems(["Első elem", "Második elem"])
        self.preview_check = QCheckBox("Minta jelölőnégyzet")
        preview_row.addWidget(self.preview_button)
        preview_row.addWidget(self.preview_input, 1)
        preview_row.addWidget(self.preview_combo)
        preview_layout.addLayout(preview_row)
        preview_layout.addWidget(self.preview_check)
        layout.addWidget(preview_group)

        utility_row = QHBoxLayout()
        undo_button = QPushButton("Visszavonás")
        undo_button.clicked.connect(self.undo_color_change)
        reset_button = QPushButton("Mentett színek visszaállítása")
        reset_button.clicked.connect(self.restore_original_colors)
        defaults_button = QPushButton("Alapszínek")
        defaults_button.clicked.connect(self.restore_default_colors)
        utility_row.addWidget(undo_button)
        utility_row.addWidget(reset_button)
        utility_row.addWidget(defaults_button)
        layout.addLayout(utility_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_preview()

    def _paint_button(self, key: str) -> None:
        color = self.colors[key]
        text_color = "#111111" if QColor(color).lightness() > 145 else "#ffffff"
        self.color_buttons[key].setText(color.upper())
        self.color_buttons[key].setStyleSheet(f"""
            QPushButton {{
                background-color: {color}; color: {text_color};
                border: 1px solid #888888; border-radius: 4px;
                padding: 5px 8px; text-align: center;
            }}
            QPushButton:hover {{ border: 2px solid {self.colors['accent']}; }}
        """)

    def _choose_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self.colors[key]), self, COLOR_LABELS[key])
        if color.isValid() and color.name().lower() != self.colors[key].lower():
            self._undo_stack.append((key, self.colors[key]))
            self.colors[key] = color.name().lower()
            self._paint_button(key)
            self._update_preview()

    def undo_color_change(self) -> None:
        if not self._undo_stack:
            QMessageBox.information(self, "Visszavonás", "Nincs visszavonható színmódosítás.")
            return
        key, old_color = self._undo_stack.pop()
        self.colors[key] = old_color
        self._paint_button(key)
        self._update_preview()

    def restore_original_colors(self) -> None:
        self.colors = deepcopy(self._original_colors)
        self._undo_stack.clear()
        for key in self.colors:
            self._paint_button(key)
        self._update_preview()

    def restore_default_colors(self) -> None:
        answer = QMessageBox.question(
            self, "Alapszínek", "Visszaállítod az összes színt az alapértelmezett palettára?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.colors = deepcopy(BUILTIN_THEMES["Alapértelmezett"])
        self._undo_stack.clear()
        for key in self.colors:
            self._paint_button(key)
        self._update_preview()

    def _update_preview(self) -> None:
        qss = theme_stylesheet(self.colors)
        self.preview.parentWidget().setStyleSheet(qss)

    def _accept_if_valid(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Hiányzó név", "Adj nevet a témának.")
            return
        self.name_edit.setText(name)
        self.accept()

    def result_data(self) -> tuple[str, dict[str, str]]:
        return self.name_edit.text().strip(), deepcopy(self.colors)


class ThemeManagerWidget(QWidget):
    """Beágyazható témakezelő widget; később a Beállítások egyik füle lehet."""

    themeApplied = pyqtSignal(str, str)  # témaneve, QSS

    def __init__(
        self,
        parent: QWidget | None = None,
        themes_path: Path | str = THEMES_FILE,
        active_theme: str = "Alapértelmezett",
    ):
        super().__init__(parent)
        self.themes_path = Path(themes_path)
        self.custom_themes = load_custom_themes(self.themes_path)
        self._build_ui()
        self._refresh(active_theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        heading = QLabel("Témakezelő")
        heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(heading)
        root.addWidget(QLabel(
            "Válassz beépített témát, vagy készíts saját színösszeállítást. "
            "A saját témák külön themes.json fájlba kerülnek."
        ))

        row = QHBoxLayout()
        row.addWidget(QLabel("Aktív téma:"))
        self.theme_combo = QComboBox()
        self.theme_combo.currentTextChanged.connect(self._selection_changed)
        row.addWidget(self.theme_combo, 1)
        apply_btn = QPushButton("Alkalmazás")
        apply_btn.clicked.connect(self.apply_selected)
        row.addWidget(apply_btn)
        root.addLayout(row)

        self.preview_box = QGroupBox("Előnézet")
        self.preview_box.setContentsMargins(8, 22, 8, 8)
        preview_layout = QVBoxLayout(self.preview_box)
        self.preview = QLabel("Téma előnézete")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview)
        sample_row = QHBoxLayout()
        sample_row.addWidget(QPushButton("Minta gomb"))
        sample_row.addWidget(QLineEdit("Beviteli mező"), 1)
        sample_combo = QComboBox()
        sample_combo.addItems(["Listaelem", "Másik elem"])
        sample_row.addWidget(sample_combo)
        preview_layout.addLayout(sample_row)
        preview_layout.addWidget(QCheckBox("Minta jelölőnégyzet"))
        root.addWidget(self.preview_box)

        actions = QGridLayout()
        items = [
            ("Új téma", self.create_theme),
            ("Szerkesztés", self.edit_theme),
            ("Másolat készítése", self.duplicate_theme),
            ("Átnevezés", self.rename_theme),
            ("Törlés", self.delete_theme),
            ("Visszaállítás", self.reset_preview),
        ]
        for i, (label, callback) in enumerate(items):
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            actions.addWidget(btn, i // 3, i % 3)
        root.addLayout(actions)
        root.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    def _all(self) -> dict[str, dict[str, str]]:
        result = deepcopy(BUILTIN_THEMES)
        result.update(deepcopy(self.custom_themes))
        return result

    def _refresh(self, selected: str | None = None) -> None:
        name = selected or self.theme_combo.currentText() or "Alapértelmezett"
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItems(list(self._all().keys()))
        if self.theme_combo.findText(name) < 0:
            name = "Alapértelmezett"
        self.theme_combo.setCurrentText(name)
        self.theme_combo.blockSignals(False)
        self._selection_changed(name)

    def _selection_changed(self, name: str) -> None:
        colors = self._all().get(name)
        if colors:
            self.preview_box.setStyleSheet(theme_stylesheet(colors))
            self.preview.setText(
                f"{name}\nHáttér: {colors['background']}   "
                f"Szöveg: {colors['text']}   Kiemelés: {colors['accent']}"
            )

    def apply_selected(self) -> None:
        name = self.theme_combo.currentText()
        qss = theme_stylesheet(self._all().get(name, BUILTIN_THEMES["Alapértelmezett"]))
        self.themeApplied.emit(name, qss)
        self.status.setText(
            f"A(z) „{name}” téma alkalmazásra kész. "
            "A főablak tényleges átállításához a main.py integrációja szükséges."
        )

    def _save(self, selected: str) -> None:
        save_custom_themes(self.custom_themes, self.themes_path)
        self._refresh(selected)
        self.status.setText(f"Témák mentve: {self.themes_path}")

    def create_theme(self) -> None:
        dialog = ThemeEditorDialog(parent=self, title="Új téma létrehozása")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, colors = dialog.result_data()
        if name in self._all():
            QMessageBox.warning(self, "Már létezik", "Ilyen nevű téma már van.")
            return
        self.custom_themes[name] = colors
        self._save(name)

    def edit_theme(self) -> None:
        name = self.theme_combo.currentText()
        if name in BUILTIN_THEMES:
            QMessageBox.information(
                self, "Beépített téma",
                "A beépített témát közvetlenül nem módosítjuk. "
                "Készíts róla másolatot, és azt szerkeszd."
            )
            return
        dialog = ThemeEditorDialog(name, self.custom_themes[name], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name, colors = dialog.result_data()
        if new_name != name and new_name in self._all():
            QMessageBox.warning(self, "Már létezik", "Ilyen nevű téma már van.")
            return
        del self.custom_themes[name]
        self.custom_themes[new_name] = colors
        self._save(new_name)

    def duplicate_theme(self) -> None:
        source = self.theme_combo.currentText()
        base = f"{source} – saját"
        name = base
        index = 2
        while name in self._all():
            name = f"{base} {index}"
            index += 1
        self.custom_themes[name] = deepcopy(self._all()[source])
        self._save(name)

    def rename_theme(self) -> None:
        old = self.theme_combo.currentText()
        if old in BUILTIN_THEMES:
            QMessageBox.information(self, "Beépített téma", "Beépített téma nem nevezhető át.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Téma átnevezése")
        layout = QVBoxLayout(dialog)
        edit = QLineEdit(old)
        layout.addWidget(QLabel("Új név:"))
        layout.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = edit.text().strip()
        if not new or (new != old and new in self._all()):
            QMessageBox.warning(self, "Érvénytelen név", "A név üres vagy már foglalt.")
            return
        self.custom_themes[new] = self.custom_themes.pop(old)
        self._save(new)

    def delete_theme(self) -> None:
        name = self.theme_combo.currentText()
        if name in BUILTIN_THEMES:
            QMessageBox.information(self, "Beépített téma", "Beépített téma nem törölhető.")
            return
        answer = QMessageBox.question(
            self, "Téma törlése", f"Biztosan törlöd a(z) „{name}” témát?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.custom_themes[name]
        self._save("Alapértelmezett")

    def reset_preview(self) -> None:
        """Frissíti a listát a mentett állapotból; a beépített témák érintetlenek."""
        self.custom_themes = load_custom_themes(self.themes_path)
        self._refresh(self.theme_combo.currentText())
        self.status.setText("A témalista a lemezen mentett állapotból frissült.")


# Egyszerű önálló tesztindítás: python3 theme_manager.py
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = QDialog()
    window.setWindowTitle("Redmi Control Center – Témakezelő")
    layout = QVBoxLayout(window)
    manager = ThemeManagerWidget(active_theme="Matrix")
    manager.themeApplied.connect(lambda _name, qss: app.setStyleSheet(qss))
    layout.addWidget(manager)
    window.resize(760, 540)
    window.show()
    sys.exit(app.exec())
