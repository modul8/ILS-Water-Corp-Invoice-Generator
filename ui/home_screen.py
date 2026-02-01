from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

from services.settings_store import SettingsStore


class HomeScreen(QWidget):
    def __init__(self, store: SettingsStore, open_settings: Callable[[], None], reload_all: Callable[[], None]) -> None:
        super().__init__()
        self.store = store
        self.open_settings = open_settings
        self.reload_all = reload_all

        root = QVBoxLayout(self)

        title = QLabel("Home")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #222;")
        root.addWidget(title)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#444; font-size: 14px;")
        root.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_settings = QPushButton("Open Settings…")
        btn_settings.clicked.connect(self.open_settings)

        btn_reload = QPushButton("Reload Data")
        btn_reload.clicked.connect(self.reload_all)

        btn_row.addWidget(btn_settings)
        btn_row.addWidget(btn_reload)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        root.addStretch(1)
        self.reload_from_settings()

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
