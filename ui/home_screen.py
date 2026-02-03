from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
import sys

from services.settings_store import SettingsStore


class HomeScreen(QWidget):
    def __init__(self, store: SettingsStore, open_settings: Callable[[], None], reload_all: Callable[[], None]) -> None:
        super().__init__()
        self.store = store
        self.open_settings = open_settings
        self.reload_all = reload_all

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_path = self._asset_path("ILS LOGO.png")
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                logo.setPixmap(pix.scaledToWidth(360, Qt.SmoothTransformation))
        root.addWidget(logo, 0, Qt.AlignCenter)

        title = QLabel("Water Corporation Job Planning and Invoicing")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #111;")
        root.addWidget(title)

        subtitle = QLabel("ILS Water Corp Invoice Console")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 14px; color: #666;")
        root.addWidget(subtitle)

        powered = QLabel("Powered by")
        powered.setAlignment(Qt.AlignCenter)
        powered.setStyleSheet("font-size: 12px; color: #777; margin-top: 8px;")
        root.addWidget(powered)

        powered_logo = QLabel()
        powered_logo.setAlignment(Qt.AlignCenter)
        powered_logo_path = self._asset_path("RealTVSoftware.png")
        if powered_logo_path.exists():
            pix = QPixmap(str(powered_logo_path))
            if not pix.isNull():
                powered_logo.setPixmap(pix.scaledToWidth(220, Qt.SmoothTransformation))
        root.addWidget(powered_logo, 0, Qt.AlignCenter)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#444; font-size: 13px;")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        root.addStretch(1)
        self.reload_from_settings()

    def _asset_path(self, filename: str) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        return base / "assets" / filename

    def reload_from_settings(self) -> None:
        s = self.store.load_settings()
        work = (s.get("work_list_path") or "").strip()
        spray = (s.get("spray_list_path") or "").strip()
        api_ok = "YES" if self.store.get_api_key().strip() else "NO"

        work_ok = "YES" if work and Path(work).exists() else "NO"
        spray_ok = "YES" if spray and Path(spray).exists() else "NO"

        self.status.setText(
            "Configuration status:\n"
            f"• Work List set: {work_ok}\n"
            f"• Spray List set: {spray_ok}\n"
            f"• API key set: {api_ok}\n\n"
            "Tip: set Work List + API key first, then modules will populate."
        )
