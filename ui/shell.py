from __future__ import annotations

from ui.wizard import SetupWizard
from pathlib import Path
import sys
import os
import tempfile
import threading
import subprocess
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QStackedWidget,
    QLabel,
    QPushButton,
    QMessageBox,
)
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtCore import Qt, Signal, QTimer

from services.settings_store import SettingsStore
from services.update_service import (
    fetch_update_info,
    fetch_update_info_with_error,
    is_newer,
    UpdateInfo,
)
from ui.drain_screen import DrainSprayingScreen
from ui.home_screen import HomeScreen
from ui.invoice_screen import InvoiceScreen
from ui.work_sheet_screen import WorkSheetScreen


class AppShell(QMainWindow):
    update_ready = Signal(object)
    update_launch = Signal(str)

    def __init__(self, store: SettingsStore, *, app_version: str) -> None:
        super().__init__()
        self.store = store
        self.app_version = app_version
        self.setWindowTitle(f"ILS Water Corp Invoice Console v{app_version}")
        self.setMinimumSize(1100, 720)

        settings = self.store.load_settings()
        self._theme = (settings.get("theme") or "dark").strip().lower()
        if self._theme not in ("dark", "light"):
            self._theme = "dark"

        self._apply_theme()

        root = QWidget()
        self.setCentralWidget(root)

        main = QHBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        # Sidebar
        sidebar = QWidget()
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)

        side_header = QWidget()
        side_header.setObjectName("SideHeader")
        side_header_layout = QHBoxLayout(side_header)
        side_header_layout.setContentsMargins(12, 12, 12, 12)
        side_header_layout.setSpacing(10)

        self.logo_label = QLabel()
        logo_path = self._asset_path("ILS_WC.png")
        if logo_path and logo_path.exists():
            pix = QPixmap(str(logo_path)).scaled(
                42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.logo_label.setPixmap(pix)
        else:
            self.logo_label.setText("ILS")
            self.logo_label.setObjectName("LogoFallback")

        title_box = QVBoxLayout()
        title = QLabel("ILS Water Corp")
        title.setObjectName("SideTitle")
        subtitle = QLabel("Invoice Console")
        subtitle.setObjectName("SideSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        side_header_layout.addWidget(self.logo_label)
        side_header_layout.addLayout(title_box, 1)

        self.nav = QListWidget()
        self.nav.addItems(
            [
                "Home",
                "Drain spraying",
                "Noxious weeds",
                "Track maintenance",
                "Fire zones",
                "Invoicing",
            ]
        )
        self.nav.setFixedWidth(220)
        self.nav.setObjectName("NavList")

        side_layout.addWidget(side_header)
        side_layout.addWidget(self.nav, 1)

        # Right side
        right = QVBoxLayout()
        right.setSpacing(10)

        # Top bar
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(10, 6, 10, 6)
        top_layout.setSpacing(8)

        btn_settings = QPushButton("Settings…")
        btn_settings.clicked.connect(self._open_settings)

        btn_reload = QPushButton("Reload")
        btn_reload.clicked.connect(self._reload_settings)

        self.btn_update = QPushButton("Check for\nUpdates")
        self.btn_update.setObjectName("UpdateCheck")
        self.btn_update.clicked.connect(self._check_updates_manual)

        self.btn_theme = QPushButton("Light mode" if self._theme == "dark" else "Dark mode")
        self.btn_theme.setObjectName("ThemeToggle")
        self.btn_theme.clicked.connect(self._toggle_theme)

        top_layout.addStretch(1)
        top_layout.addWidget(btn_reload)
        top_layout.addWidget(self.btn_update)
        top_layout.addWidget(self.btn_theme)
        top_layout.addWidget(btn_settings)

        # Pages
        self.stack = QStackedWidget()
        self.pages = [
            HomeScreen(store, self._open_settings, self._reload_settings),
            # Spray drains module (already built)
            DrainSprayingScreen(store),
            # Work list based modules (qty entered when completed)
            WorkSheetScreen(
                store, module_id="weeds", sheet_name="Noxious Weeds", unit="hour"
            ),
            WorkSheetScreen(
                store, module_id="tracks", sheet_name="Mtn Access Tracks", unit="km"
            ),
            WorkSheetScreen(
                store, module_id="fire", sheet_name="Fire Zone", unit="each"
            ),
            # Invoicing (reads shared state)
            InvoiceScreen(store),
        ]
        for p in self.pages:
            self.stack.addWidget(p)

        right.addWidget(topbar)
        right.addWidget(self.stack, 1)

        main.addWidget(sidebar)
        main.addLayout(right, 1)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        logo_icon = self._asset_path("ILS_WC.png")
        if logo_icon and logo_icon.exists():
            self.setWindowIcon(QIcon(str(logo_icon)))

        self.update_ready.connect(self._prompt_update)
        self.update_launch.connect(self._launch_installer)
        QTimer.singleShot(1500, self._check_updates_async)

    def _asset_path(self, filename: str) -> Path | None:
        try:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            return base / "assets" / filename
        except Exception:
            return None

    def _apply_theme(self) -> None:
        if self._theme == "light":
            self.setStyleSheet(
                """
            QWidget {
                background: #f5f6f8;
                color: #1f1f1f;
                font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
            }
            QWidget#TopBar, QWidget#SideHeader {
                background: #ffffff;
                border: 1px solid #e1e4ea;
                border-radius: 14px;
            }
            QListWidget#NavList {
                background: #ffffff;
                color: #1f1f1f;
                border: 1px solid #e1e4ea;
                border-radius: 14px;
                padding: 6px;
                font-size: 14px;
            }
            QListWidget#NavList::item {
                padding: 10px;
                border-radius: 10px;
            }
            QListWidget#NavList::item:selected {
                background: #e8f0fe;
                color: #1b4fbf;
                font-weight: 700;
            }
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #eef2f7;
                gridline-color: #e1e4ea;
                color: #1f1f1f;
            }
            QHeaderView::section {
                background: #f7f8fa;
                color: #1f1f1f;
                border: 1px solid #e1e4ea;
                padding: 4px 6px;
            }
            QTableWidget::item {
                background: #ffffff;
                color: #1f1f1f;
            }
            QTableWidget::item:alternate {
                background: #eef2f7;
                color: #1f1f1f;
            }
            QTableWidget::item:selected {
                background: #dbe7ff;
                color: #0b2f6b;
            }
            QTableWidget::indicator {
                width: 20px;
                height: 20px;
            }
            QTableWidget::indicator:unchecked {
                border: 2px solid #9aa3b2;
                background: #ffffff;
            }
            QTableWidget::indicator:checked {
                border: 2px solid #1b66d1;
                background: #1b66d1;
            }
            QPushButton {
                background: #1b66d1;
                color: #ffffff;
                padding: 8px 12px;
                border-radius: 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1557b0; }
            QLabel#InfoLabel { color: #3a3f46; font-size: 13px; }
            QLabel#TopLogo { background: transparent; }
            QLabel#SideTitle { font-weight: 700; font-size: 14px; }
            QLabel#SideSubtitle { color: #6b7280; font-size: 11px; }
            """
            )
        else:
            self.setStyleSheet(
                """
            QWidget {
                background: #121316;
                color: #e7e7ea;
                font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
            }
            QWidget#TopBar, QWidget#SideHeader {
                background: #1c1f26;
                border: 1px solid #2b2f3a;
                border-radius: 14px;
            }
            QListWidget#NavList {
                background: #1a1c22;
                color: #e7e7ea;
                border: 1px solid #2b2f3a;
                border-radius: 14px;
                padding: 6px;
                font-size: 14px;
            }
            QListWidget#NavList::item {
                padding: 10px;
                border-radius: 10px;
            }
            QListWidget#NavList::item:selected {
                background: #2b3f6b;
                color: #ffffff;
                font-weight: 700;
            }
            QTableWidget::indicator {
                width: 20px;
                height: 20px;
            }
            QTableWidget::indicator:unchecked {
                border: 2px solid #5f6b7a;
                background: #171a20;
            }
            QTableWidget::indicator:checked {
                border: 2px solid #4c8bf5;
                background: #4c8bf5;
            }
            QPushButton {
                background: #4c8bf5;
                color: #ffffff;
                padding: 8px 12px;
                border-radius: 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #3a6fd8; }
            QLabel#InfoLabel { color: #c9d1d9; font-size: 13px; }
            QLabel#TopLogo { background: transparent; }
            QLabel#SideTitle { font-weight: 700; font-size: 14px; }
            QLabel#SideSubtitle { color: #9aa3b2; font-size: 11px; }
            """
            )

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        settings = self.store.load_settings()
        settings["theme"] = self._theme
        self.store.save_settings(settings)
        self.btn_theme.setText("Light mode" if self._theme == "dark" else "Dark mode")
        self._apply_theme()

    def _check_updates_async(self) -> None:
        def _worker():
            info = fetch_update_info(
                "https://modul8.github.io/ILS-Water-Corp-Invoice-Generator/updates/latest.json"
            )
            if not info:
                return
            if is_newer(info.version, self.app_version):
                self.update_ready.emit(info)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_updates_manual(self) -> None:
        def _worker():
            info, err = fetch_update_info_with_error(
                "https://modul8.github.io/ILS-Water-Corp-Invoice-Generator/updates/latest.json"
            )
            if not info:
                self.update_ready.emit(UpdateInfo(version="", url="", notes=f"__error__:{err}"))
                return
            if is_newer(info.version, self.app_version):
                self.update_ready.emit(info)
            else:
                self.update_ready.emit(UpdateInfo(version="", url="", notes="__uptodate__"))

        threading.Thread(target=_worker, daemon=True).start()

    def _prompt_update(self, info: UpdateInfo) -> None:
        if info.notes.startswith("__error__"):
            err = info.notes.split(":", 1)[1] if ":" in info.notes else ""
            QMessageBox.warning(
                self,
                "Update check failed",
                f"Could not reach the update server.\n{err}",
            )
            return
        if info.notes == "__uptodate__":
            QMessageBox.information(
                self, "Up to date", "You already have the latest version."
            )
            return
        text = f"Version {info.version} is available.\n\nUpdate now?"
        if info.notes:
            text = f"Version {info.version} is available.\n\n{info.notes}\n\nUpdate now?"
        res = QMessageBox.question(self, "Update available", text)
        if res != QMessageBox.Yes:
            return
        QMessageBox.information(
            self,
            "Updating",
            "Downloading update. The installer will start and the app will close.",
        )
        threading.Thread(target=self._download_and_run, args=(info.url,), daemon=True).start()

    def _download_and_run(self, url: str) -> None:
        try:
            import requests
            import certifi

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            }
            try:
                r = requests.get(url, stream=True, timeout=20, verify=certifi.where(), headers=headers)
            except Exception:
                r = requests.get(url, stream=True, timeout=20, verify=False, headers=headers)
            if r.status_code >= 300:
                return
            fd, path = tempfile.mkstemp(suffix="_setup.exe")
            os.close(fd)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)
            self.update_launch.emit(path)
        except Exception:
            return

    def _launch_installer(self, path: str) -> None:
        try:
            subprocess.Popen([path], shell=False)
        except Exception:
            QMessageBox.warning(self, "Update failed", "Could not launch installer.")
            return
        QTimer.singleShot(500, self.close)

    def _reload_settings(self) -> None:
        w = self.stack.currentWidget()
        if hasattr(w, "reload_from_settings"):
            try:
                w.reload_from_settings()
            except Exception as e:
                QMessageBox.warning(self, "Reload failed", str(e))

        # Some screens need a refresh to re-read state from disk
        w = self.stack.currentWidget()
        if hasattr(w, "refresh"):
            try:
                w.refresh()
            except Exception:
                pass

    def _open_settings(self) -> None:
        wiz = SetupWizard(self.store)
        wiz.exec()
        self._reload_settings()

    def _on_nav_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        w = self.stack.currentWidget()
        if hasattr(w, "refresh"):
            try:
                w.refresh()
            except Exception:
                pass
