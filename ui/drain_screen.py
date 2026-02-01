from __future__ import annotations
from services.state_store import StateStore
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView
)
from datetime import date

from services.settings_store import SettingsStore
from services.field_sync import FieldSyncClient
import threading
import json

# Your copied V1 loader goes here:
from data.data_loader import load_spray_list, load_spray_drains_mapping, attach_work_info, norm


def row_key(r) -> str:
    sheet = norm(getattr(r, "sheet", ""))
    catchment = norm(getattr(r, "catchment", "") or "")
    drain = norm(getattr(r, "drain", ""))
    return "|".join([sheet, catchment, drain])


class DrainSprayingScreen(QWidget):
    """
    V2 screen: Drain spraying.
    - Loads spray rows from Spray List (optional)
    - Uses Work List to attach WO/PO info
    - Simple table view
    - "Completed" toggle stored locally in settings folder (v2_state.json) for now
      (You can replace with SQLite later.)
    """

    sync_status = Signal(str)

    def __init__(self, store: SettingsStore) -> None:
        super().__init__()
        self.store = store

        self.state_store = StateStore(self.store)
        self._sync_warned = False

        self.rows = []  # list of SprayRow-like objects

        root = QVBoxLayout(self)
        header = QHBoxLayout()

        title = QLabel("Drain spraying")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.btn_refresh)

        self.btn_hide_completed = QPushButton("Hide Completed")
        self.btn_hide_completed.setCheckable(True)
        self.btn_hide_completed.toggled.connect(self._on_filter_toggle)
        header.addWidget(self.btn_hide_completed)

        self.btn_hide_missing = QPushButton("Hide Missing PO/WO")
        self.btn_hide_missing.setCheckable(True)
        self.btn_hide_missing.toggled.connect(self._on_filter_toggle)
        header.addWidget(self.btn_hide_missing)

        root.addLayout(header)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Sheet", "Catchment", "Drain", "KM", "WO", "PO", "Completed", "Invoiced", "Current"
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_click)
        self.table.cellDoubleClicked.connect(self._on_double_click)

        root.addWidget(self.table, 1)

        self._load_filter_settings()
        self._update_filter_labels()

        hint = QLabel("Tip: click the Completed column to toggle.")
        hint.setStyleSheet("color:#e0e0e0;")
        root.addWidget(hint)

        self.sync_label = QLabel("Sync: idle")
        self.sync_label.setStyleSheet("color:#888; font-size: 12px;")
        root.addWidget(self.sync_label)
        self.sync_status.connect(self._set_sync_status)

        # Auto-load at first open
        self.refresh()

    def reload_from_settings(self) -> None:
        self.refresh()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        if self.state_path.exists():
            try:
                import json
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def _save_state(self) -> None:
        import json
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def refresh(self) -> None:
        settings = self.store.load_settings()
        work_list = (settings.get("work_list_path") or "").strip()
        spray_list = (settings.get("spray_list_path") or "").strip()

        if not work_list or not Path(work_list).exists():
            # No popup spam — just show empty table and stop.
            self.rows = []
            self._populate()
            return


        self.rows = []

        # Spray list is optional, but drain spraying is most useful with it
        if spray_list and Path(spray_list).exists():
            self.rows = load_spray_list(spray_list)

            # Try to attach WO/PO from work list
            # You can change sheet_name if your work list uses a specific tab
            sheet_name = settings.get("work_list_sheet_name", "") or "Sheet1"
            try:
                po, mapping = load_spray_drains_mapping(work_list, sheet_name=sheet_name)
                attach_work_info(self.rows, po=po, mapping=mapping)
            except Exception:
                # If mapping fails, still show raw rows
                pass

            # Apply completed state
            for r in self.rows:
                k = row_key(r)
                st = self.state_store.get(k)
                if st:
                    r.completed = bool(st.get("completed", False))
                    r.invoiced = bool(st.get("invoiced", False))
                else:
                    # ensure a base record exists so invoicing screen can see it later
                    self.state_store.upsert(k, {
                        "module": "drain",
                        "completed": False,
                        "invoiced": False,
                        "meta": {
                            "sheet": getattr(r, "sheet", ""),
                            "catchment": getattr(r, "catchment", "") or "",
                            "drain": getattr(r, "drain", ""),
                            "qty_km": float(getattr(r, "qty_km", 0.0)),
                            "work_order": getattr(r, "work_order", "") or "",
                            "po": getattr(r, "po", "") or "",
                        }
                    })

        else:
            self.rows = []
            self._populate()
            return
        
        self._sync_with_server_async()
        self._populate()

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        hide_completed = self.btn_hide_completed.isChecked()
        hide_missing = self.btn_hide_missing.isChecked()

        for r in self.rows:
            completed = bool(getattr(r, "completed", False))
            wo = getattr(r, "work_order", "") or ""
            po = getattr(r, "po", "") or ""
            if hide_completed and completed:
                continue
            if hide_missing and not wo and not po:
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)

            k = row_key(r)
            rec = self.state_store.get(k) or {}
            current_work = bool(rec.get("current_work", False))

            sheet = getattr(r, "sheet", "")
            catchment = getattr(r, "catchment", "") or ""
            drain = getattr(r, "drain", "")
            km = float(getattr(r, "qty_km", 0.0))
            invoiced = bool(getattr(r, "invoiced", False))
            values = [sheet, catchment, drain, f"{km:.2f}", wo, po,
                    "YES" if completed else "NO",
                    "YES" if invoiced else "NO",
                    ""]


            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == 6 and completed:
                    item.setForeground(Qt.darkGreen)
                if c in (6, 7, 8):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, c, item)

            current_item = QTableWidgetItem()
            current_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            current_item.setCheckState(Qt.Checked if current_work else Qt.Unchecked)
            current_item.setTextAlignment(Qt.AlignCenter)
            current_item.setData(Qt.UserRole, k)
            self.table.setItem(row, 8, current_item)

        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 8:
            return
        k = item.data(Qt.UserRole)
        if not k:
            return
        rec = self.state_store.get(k) or {"module": "drain", "invoiced": False, "meta": {}}
        rec.setdefault("module", "drain")
        rec.setdefault("meta", {})
        rec["current_work"] = item.checkState() == Qt.Checked
        self.state_store.upsert(k, rec)
        self._sync_with_server_async()

    def _filter_settings_key(self, name: str) -> str:
        return f"filters_drain_{name}"

    def _load_filter_settings(self) -> None:
        settings = self.store.load_settings()
        self.btn_hide_completed.setChecked(
            bool(settings.get(self._filter_settings_key("hide_completed"), False))
        )
        self.btn_hide_missing.setChecked(
            bool(settings.get(self._filter_settings_key("hide_missing"), False))
        )

    def _save_filter_settings(self) -> None:
        settings = self.store.load_settings()
        settings[self._filter_settings_key("hide_completed")] = self.btn_hide_completed.isChecked()
        settings[self._filter_settings_key("hide_missing")] = self.btn_hide_missing.isChecked()
        self.store.save_settings(settings)

    def _update_filter_labels(self) -> None:
        if self.btn_hide_completed.isChecked():
            self.btn_hide_completed.setText("Show Completed")
        else:
            self.btn_hide_completed.setText("Hide Completed")

        if self.btn_hide_missing.isChecked():
            self.btn_hide_missing.setText("Show Missing PO/WO")
        else:
            self.btn_hide_missing.setText("Hide Missing PO/WO")

    def _apply_filters(self) -> None:
        self._populate()

    def _on_filter_toggle(self) -> None:
        self._update_filter_labels()
        self._save_filter_settings()
        self._apply_filters()

    def _on_click(self, row: int, col: int) -> None:
        return

    def _on_double_click(self, row: int, col: int) -> None:
        if col == 6:
            self._toggle_completed(row)
            return

    def _toggle_completed(self, row: int) -> None:
        if row < 0 or row >= len(self.rows):
            return
        r = self.rows[row]
        completed_now = bool(getattr(r, "completed", False))
        if completed_now:
            confirm = QMessageBox.question(
                self,
                "Un-complete job?",
                "This job is currently marked Completed. Do you want to mark it as NOT completed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        r.completed = not completed_now
        
        k = row_key(r)
        qty_km = float(getattr(r, "qty_km", 0.0))

        # Preserve existing record and meta, just flip completed
        existing = self.state_store.get(k) or {"module": "drain", "invoiced": False, "meta": {}}
        existing.setdefault("module", "drain")
        existing.setdefault("invoiced", False)
        existing.setdefault("meta", {})
        existing["completed"] = bool(r.completed)
        if not existing["completed"]:
            existing["qty"] = 0
            existing["unit"] = "km"
            existing.pop("completed_at", None)
            completed_at = ""
        else:
            existing["qty"] = qty_km
            existing["unit"] = "km"
            completed_at = date.today().isoformat()
            existing["completed_at"] = completed_at

        # keep meta up to date
        existing["meta"].update({
            "sheet": getattr(r, "sheet", ""),
            "catchment": getattr(r, "catchment", "") or "",
            "drain": getattr(r, "drain", ""),
            "qty_km": qty_km,
            "work_order": getattr(r, "work_order", "") or "",
            "po": getattr(r, "po", "") or "",
        })

        self.state_store.upsert(k, existing)
        self._sync_completion_async(k, existing["completed"], existing.get("qty", 0), completed_at)
        self._populate()

    def _get_sync_client(self) -> FieldSyncClient | None:
        settings = self.store.load_settings()
        base = (settings.get("field_api_base") or "").strip()
        key = (settings.get("field_api_key") or "").strip()
        if not base or not key:
            return None
        return FieldSyncClient(base, key)

    def _sync_with_server(self) -> None:
        client = self._get_sync_client()
        if not client:
            return
        try:
            self.sync_status.emit("Syncing...")
            completed_jobs = client.list_jobs(module="drain", completed=True)
            for j in completed_jobs:
                job_key = j.get("job_key") or ""
                if not job_key:
                    continue
                rec = self.state_store.get(job_key) or {}
                rec["completed"] = True
                if j.get("invoiced") is not None:
                    server_invoiced = bool(int(j.get("invoiced")))
                    if server_invoiced or not bool(rec.get("invoiced")):
                        rec["invoiced"] = server_invoiced
                if j.get("current_work") is not None:
                    rec["current_work"] = bool(int(j.get("current_work")))
                if j.get("invoiced_at"):
                    rec["invoiced_at"] = j.get("invoiced_at")
                qty = j.get("qty")
                if qty is not None:
                    try:
                        rec["qty"] = float(qty)
                    except Exception:
                        pass
                rec["unit"] = "km"
                rec["module"] = "drain"
                if j.get("completed_at"):
                    rec["completed_at"] = j.get("completed_at")
                meta = rec.get("meta") or {}
                raw_meta = j.get("meta")
                if isinstance(raw_meta, str) and raw_meta.strip():
                    try:
                        parsed = json.loads(raw_meta)
                        if isinstance(parsed, dict):
                            meta.update(parsed)
                    except Exception:
                        pass
                meta.update(
                    {
                        "sheet": j.get("sheet") or meta.get("sheet") or "",
                        "catchment": meta.get("catchment") or "",
                        "drain": j.get("item") or meta.get("drain") or "",
                        "work_order": j.get("work_order") or meta.get("work_order") or "",
                        "po": j.get("po") or meta.get("po") or "",
                        "qty_km": j.get("qty_default") or rec.get("qty") or meta.get("qty_km") or 0,
                    }
                )
                rec["meta"] = meta
                self.state_store.upsert(job_key, rec)

            jobs_payload = []
            for r in self.rows:
                key = row_key(r)
                rec = self.state_store.get(key) or {}
                jobs_payload.append(
                    {
                        "job_key": key,
                        "module": "drain",
                        "job_type": "Drain spraying",
                        "sheet": getattr(r, "sheet", ""),
                        "item": getattr(r, "drain", ""),
                        "work_order": getattr(r, "work_order", "") or "",
                        "po": getattr(r, "po", "") or "",
                        "unit": "km",
                        "qty_default": float(getattr(r, "qty_km", 0.0)),
                        "completed": 1 if rec.get("completed") is True else 0,
                        "completed_at": rec.get("completed_at"),
                        "invoiced": 1 if rec.get("invoiced") is True else 0,
                        "invoiced_at": rec.get("invoiced_at"),
                        "qty": rec.get("qty"),
                        "current_work": 1 if rec.get("current_work") is True else 0,
                        "meta": {
                            "sheet": getattr(r, "sheet", ""),
                            "catchment": getattr(r, "catchment", "") or "",
                            "drain": getattr(r, "drain", ""),
                            "qty_km": float(getattr(r, "qty_km", 0.0)),
                            "work_order": getattr(r, "work_order", "") or "",
                            "po": getattr(r, "po", "") or "",
                        },
                    }
                )
            client.sync_jobs(jobs_payload)
            from datetime import datetime
            self.sync_status.emit(f"Last sync: {datetime.now().strftime('%H:%M')}")
        except Exception:
            self.sync_status.emit("Sync failed")

    def _sync_completion(self, key: str, completed: bool, qty: float, completed_at: str) -> None:
        client = self._get_sync_client()
        if not client:
            return
        try:
            client.mark_completed(
                job_key=key, completed=completed, qty=qty, completed_at=completed_at
            )
            from datetime import datetime
            self.sync_status.emit(f"Last sync: {datetime.now().strftime('%H:%M')}")
        except Exception:
            self.sync_status.emit("Sync failed")

    def _sync_with_server_async(self) -> None:
        t = threading.Thread(target=self._sync_with_server, daemon=True)
        t.start()

    def _sync_completion_async(self, key: str, completed: bool, qty: float, completed_at: str) -> None:
        t = threading.Thread(
            target=self._sync_completion,
            args=(key, completed, qty, completed_at),
            daemon=True,
        )
        t.start()

    def _set_sync_status(self, text: str) -> None:
        self.sync_label.setText(f"Sync: {text}")

