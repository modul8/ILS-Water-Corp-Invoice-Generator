from __future__ import annotations

from typing import Any, Dict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QFormLayout, QGroupBox, QMessageBox, QComboBox
)

from services.settings_store import SettingsStore
from services.dolibarr_client import DolibarrClient


class SetupWizard(QDialog):
    """
    Startup wizard:
    - Select Work List (.xlsx)
    - Optional Spray List (.xlsx)
    - Dolibarr Base URL
    - Dolibarr socid
    - API key (stored securely via keyring)
    """

    def __init__(self, store: SettingsStore) -> None:
        super().__init__()
        self.store = store

        self.setWindowTitle("DrainInvoiceApp V2 — Setup")
        self.setMinimumWidth(720)

        settings = self.store.load_settings()
        api_key = self.store.get_api_key()
        self._pending_socid = str(settings.get("dolibarr_socid", "")).strip()
        self._clients = []

        root = QVBoxLayout(self)

        title = QLabel("Setup")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("Choose your files and connection details. API key is stored securely on this PC.")
        subtitle.setStyleSheet("color: #666;")
        root.addWidget(title)
        root.addWidget(subtitle)

        # --- Files group ---
        files_group = QGroupBox("Files")
        files_layout = QFormLayout(files_group)

        self.work_list_edit = QLineEdit(settings.get("work_list_path", ""))
        self.spray_list_edit = QLineEdit(settings.get("spray_list_path", ""))
        self.export_dir_edit = QLineEdit(settings.get("invoice_export_dir", ""))

        btn_work_browse = QPushButton("Browse…")
        btn_work_browse.clicked.connect(lambda: self._browse_xlsx(self.work_list_edit, "Select Work List (.xlsx)"))

        btn_spray_browse = QPushButton("Browse…")
        btn_spray_browse.clicked.connect(lambda: self._browse_xlsx(self.spray_list_edit, "Select Spray List (.xlsx)"))

        btn_export_browse = QPushButton("Browse…")
        btn_export_browse.clicked.connect(lambda: self._browse_folder(self.export_dir_edit, "Select Export Folder"))

        work_row = QHBoxLayout()
        work_row.addWidget(self.work_list_edit)
        work_row.addWidget(btn_work_browse)

        spray_row = QHBoxLayout()
        spray_row.addWidget(self.spray_list_edit)
        spray_row.addWidget(btn_spray_browse)

        export_row = QHBoxLayout()
        export_row.addWidget(self.export_dir_edit)
        export_row.addWidget(btn_export_browse)

        files_layout.addRow("Work List:", self._wrap(work_row))
        files_layout.addRow("Spray List (optional):", self._wrap(spray_row))
        files_layout.addRow("Invoice export folder:", self._wrap(export_row))

        # --- Dolibarr group ---
        dol_group = QGroupBox("Dolibarr")
        dol_layout = QFormLayout(dol_group)

        self.base_url_edit = QLineEdit(settings.get("dolibarr_base_url", ""))
        self.client_combo = QComboBox()
        self.client_combo.setEnabled(False)
        if self._pending_socid:
            self.client_combo.addItem(f"Current customer ID: {self._pending_socid}", self._pending_socid)
        else:
            self.client_combo.addItem("Test connection to load clients…", "")
        self.api_key_edit = QLineEdit(api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)

        dol_layout.addRow("Base URL:", self.base_url_edit)
        dol_layout.addRow("Customer:", self.client_combo)
        dol_layout.addRow("API key:", self.api_key_edit)

        # --- Rates group ---
        rates_group = QGroupBox("Rates")
        rates_layout = QFormLayout(rates_group)

        self.rate_drain_edit = QLineEdit(str(settings.get("rate_drain", "")))
        self.rate_weeds_edit = QLineEdit(str(settings.get("rate_weeds", "")))
        self.rate_tracks_edit = QLineEdit(str(settings.get("rate_tracks", "")))
        self.rate_fire_edit = QLineEdit(str(settings.get("rate_fire", "")))

        rates_layout.addRow("Drain spraying rate ($/km):", self.rate_drain_edit)
        rates_layout.addRow("Noxious weeds rate ($/hour):", self.rate_weeds_edit)
        rates_layout.addRow("Track maintenance rate ($/km):", self.rate_tracks_edit)
        rates_layout.addRow("Fire zone rate ($/each):", self.rate_fire_edit)

        # --- Field sync group ---
        sync_group = QGroupBox("Field Sync (Mobile)")
        sync_layout = QFormLayout(sync_group)

        self.field_api_base_edit = QLineEdit(settings.get("field_api_base", ""))
        self.field_api_key_edit = QLineEdit(settings.get("field_api_key", ""))
        self.field_api_key_edit.setEchoMode(QLineEdit.Password)

        sync_layout.addRow("API Base URL:", self.field_api_base_edit)
        sync_layout.addRow("API Key:", self.field_api_key_edit)

        root.addWidget(files_group)
        root.addWidget(dol_group)
        root.addWidget(rates_group)
        root.addWidget(sync_group)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_test = QPushButton("Test connection")
        self.btn_test.clicked.connect(self._test_connection)

        self.btn_reset_state = QPushButton("Reset local state")
        self.btn_reset_state.clicked.connect(self._reset_local_state)

        self.btn_save = QPushButton("Save and Continue")
        self.btn_save.clicked.connect(self._save_and_continue)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_test)
        btn_row.addWidget(self.btn_reset_state)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(btn_cancel)

        root.addLayout(btn_row)

        # Light styling
        self.setStyleSheet("""
            QGroupBox { font-weight: 600; margin-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QLineEdit { padding: 6px; }
            QPushButton { padding: 8px 12px; }
        """)

        if self.base_url_edit.text().strip() and self.api_key_edit.text().strip():
            self._load_clients()

    def _wrap(self, layout: QHBoxLayout):
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        w.setLayout(layout)
        return w

    def _browse_xlsx(self, target_edit: QLineEdit, title: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, "", "Excel Files (*.xlsx)")
        if path:
            target_edit.setText(path)

    def _browse_folder(self, target_edit: QLineEdit, title: str) -> None:
        path = QFileDialog.getExistingDirectory(self, title)
        if path:
            target_edit.setText(path)

    def _test_connection(self) -> None:
        base_url = self.base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()

        if not base_url or not api_key:
            QMessageBox.warning(self, "Missing info", "Please enter Base URL and API key first.")
            return

        client = DolibarrClient(base_url, api_key)
        ok, msg = client.test_connection()
        if ok:
            ok_list, clients, list_msg = client.list_clients()
            if ok_list:
                self._clients = clients
                self._populate_clients()
                QMessageBox.information(self, "Connection OK", "Successfully connected to Dolibarr.")
            else:
                QMessageBox.warning(self, "Connected, but failed to load clients", list_msg)
        else:
            QMessageBox.critical(self, "Connection failed", msg)

    def _load_clients(self) -> None:
        base_url = self.base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()
        if not base_url or not api_key:
            return
        client = DolibarrClient(base_url, api_key)
        ok_list, clients, _ = client.list_clients()
        if ok_list:
            self._clients = clients
            self._populate_clients()

    def _populate_clients(self) -> None:
        self.client_combo.clear()
        self.client_combo.addItem("Select a customer…", "")
        for c in self._clients:
            cid = str(c.get("id", "")).strip()
            name = (c.get("name") or c.get("nom") or c.get("label") or "").strip()
            if not cid or not name:
                continue
            self.client_combo.addItem(f"{name} (ID {cid})", cid)

        if self._pending_socid:
            idx = self.client_combo.findData(self._pending_socid)
            if idx >= 0:
                self.client_combo.setCurrentIndex(idx)

        self.client_combo.setEnabled(self.client_combo.count() > 1)

    def _save_and_continue(self) -> None:
        settings = self.store.load_settings()

        work_list = self.work_list_edit.text().strip()
        spray_list = self.spray_list_edit.text().strip()
        export_dir = self.export_dir_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        socid = str(self.client_combo.currentData() or self._pending_socid or "").strip()
        api_key = self.api_key_edit.text().strip()
        rate_drain = self.rate_drain_edit.text().strip()
        rate_weeds = self.rate_weeds_edit.text().strip()
        rate_tracks = self.rate_tracks_edit.text().strip()
        rate_fire = self.rate_fire_edit.text().strip()
        field_api_base = self.field_api_base_edit.text().strip()
        field_api_key = self.field_api_key_edit.text().strip()

        # Validate required
        if not work_list or not Path(work_list).exists():
            QMessageBox.warning(self, "Work List missing", "Please choose a valid Work List .xlsx file.")
            return
        if not base_url:
            QMessageBox.warning(self, "Base URL missing", "Please enter the Dolibarr Base URL.")
            return
        if not socid:
            QMessageBox.warning(self, "Customer missing", "Please select a customer (use Test connection to load clients).")
            return
        if not api_key:
            QMessageBox.warning(self, "API key missing", "Please enter the Dolibarr API key.")
            return

        settings["work_list_path"] = work_list
        settings["spray_list_path"] = spray_list
        settings["invoice_export_dir"] = export_dir
        settings["dolibarr_base_url"] = base_url
        settings["dolibarr_socid"] = socid

        if rate_drain:
            try:
                settings["rate_drain"] = float(rate_drain)
            except Exception:
                QMessageBox.warning(self, "Invalid rate", "Drain spraying rate must be a number.")
                return
        else:
            settings.pop("rate_drain", None)

        if rate_weeds:
            try:
                settings["rate_weeds"] = float(rate_weeds)
            except Exception:
                QMessageBox.warning(self, "Invalid rate", "Noxious weeds rate must be a number.")
                return
        else:
            settings.pop("rate_weeds", None)

        if rate_tracks:
            try:
                settings["rate_tracks"] = float(rate_tracks)
            except Exception:
                QMessageBox.warning(self, "Invalid rate", "Track maintenance rate must be a number.")
                return
        else:
            settings.pop("rate_tracks", None)

        if rate_fire:
            try:
                settings["rate_fire"] = float(rate_fire)
            except Exception:
                QMessageBox.warning(self, "Invalid rate", "Fire zone rate must be a number.")
                return
        else:
            settings.pop("rate_fire", None)

        if field_api_base:
            if not field_api_base.startswith(("http://", "https://")):
                QMessageBox.warning(
                    self,
                    "Invalid Field API URL",
                    "Field API Base URL must start with http:// or https://",
                )
                return
            settings["field_api_base"] = field_api_base
        else:
            settings.pop("field_api_base", None)

        if field_api_key:
            settings["field_api_key"] = field_api_key
        else:
            settings.pop("field_api_key", None)

        self.store.save_settings(settings)
        self.store.set_api_key(api_key)

        self.accept()

    def _reset_local_state(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Reset local state?",
            "This will clear all locally stored Completed/Invoiced flags on this PC.\n"
            "It will NOT change settings or API keys.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        state_path = self.store.app_dir / "v2_state.json"
        try:
            if state_path.exists():
                state_path.unlink()
            QMessageBox.information(self, "Local state reset", "Local state has been cleared.")
        except Exception as e:
            QMessageBox.warning(self, "Reset failed", str(e))
