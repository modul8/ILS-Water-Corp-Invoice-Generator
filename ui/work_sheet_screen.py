from __future__ import annotations

from typing import List
import json
import logging
from datetime import date
import time

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QInputDialog,
    QAbstractItemView,
)
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtGui import QDesktopServices

from services.settings_store import SettingsStore
import sys
from services.field_sync import FieldSyncClient
import threading
from services.state_store import StateStore
from data.data_loader import load_work_list_sheet, WorkListRow


def row_key(module_id: str, sheet_name: str, wo: str) -> str:
    return f"{module_id}:{sheet_name}:{wo}"


class WorkSheetScreen(QWidget):
    """
    Generic screen for Work List sheets.
    Qty is entered when marking a job as completed.
    """

    sync_status = Signal(str)

    def __init__(
        self, store: SettingsStore, *, module_id: str, sheet_name: str, unit: str
    ) -> None:
        super().__init__()
        self.store = store
        self.state_store = StateStore(store)
        self._log = logging.getLogger(__name__)
        self._sync_warned = False
        self._last_sync_attempt = 0.0
        self._min_sync_interval = 2.0
        self._full_sync_interval = 300.0
        self._dirty_sync_pending = False

        self.module_id = module_id
        self.sheet_name = sheet_name
        self.unit = unit

        self.rows: List[WorkListRow] = []

        root = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel(sheet_name)
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        header.addWidget(title)
        header.addStretch(1)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh)
        header.addWidget(btn_refresh)

        self.btn_hide_completed = QPushButton("Show Completed")
        self.btn_hide_completed.setCheckable(True)
        self.btn_hide_completed.toggled.connect(self._on_filter_toggle)
        header.addWidget(self.btn_hide_completed)

        self.btn_hide_missing = QPushButton("Hide Missing PO/WO")
        self.btn_hide_missing.setCheckable(True)
        self.btn_hide_missing.toggled.connect(self._on_filter_toggle)
        header.addWidget(self.btn_hide_missing)

        root.addLayout(header)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "WO",
                "Location",
                "Call Date",
                "Qty",
                "Unit",
                "Completed",
                "Invoiced",
                "Current",
                "Pin",
                "Edit",
                "Key",
            ]
        )
        self.table.setColumnHidden(10, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_click)
        self.table.cellDoubleClicked.connect(self._on_double_click)

        root.addWidget(self.table, 1)

        self._load_filter_settings()
        self._update_filter_labels()

        hint = QLabel(
            "Click Completed to mark job done (enter Qty). Click Qty column to edit."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#e0e0e0;")
        root.addWidget(hint)

        self.sync_label = QLabel("Sync: idle")
        self.sync_label.setStyleSheet("color:#888; font-size: 12px;")
        root.addWidget(self.sync_label)

        self.sync_status.connect(self._set_sync_status)

        self.refresh()

    def reload_from_settings(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.state_store = StateStore(self.store)

        work_list_path = (
            self.store.load_settings().get("work_list_path") or ""
        ).strip()
        if not work_list_path:
            self.table.setRowCount(0)
            return

        # Prefer server list if configured
        client = self._get_sync_client()
        if client:
            try:
                jobs = client.list_jobs(module=self.module_id, completed=None, limit=2000)
            except Exception:
                jobs = []
            if jobs:
                self._load_from_server_jobs(jobs)
                self._sync_with_server_async()
                self._populate()
                return

        self.rows = load_work_list_sheet(work_list_path, self.sheet_name)
        for r in self.rows:
            k = row_key(self.module_id, self.sheet_name, r.wo)
            rec = self.state_store.get(k)
            if rec:
                continue
            self.state_store.upsert(
                k,
                {
                    "module": self.module_id,
                    "completed": False,
                    "invoiced": False,
                    "current_work": False,
                    "unit": self.unit,
                    "meta": {
                        "sheet": self.sheet_name,
                        "work_order": r.wo or "",
                        "location": r.location,
                        "call_date": r.call_date,
                        "po": r.po or "",
                    },
                },
            )
        self._sync_with_server_async()
        self._populate()

    def _asset_path(self, filename: str) -> Path | None:
        try:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            return base / "assets" / filename
        except Exception:
            return None

    def _load_from_server_jobs(self, jobs: list[dict]) -> None:
        self.rows = []
        for j in jobs:
            wo = str(j.get("work_order") or "").strip()
            if not wo:
                continue
            meta = {}
            raw_meta = j.get("meta")
            if isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    parsed = json.loads(raw_meta)
                    if isinstance(parsed, dict):
                        meta = parsed
                except Exception:
                    meta = {}

            r = WorkListRow(
                sheet=str(j.get("sheet") or self.sheet_name),
                mp="",
                wo=wo,
                location=str(j.get("item") or meta.get("location") or ""),
                call_date=str(meta.get("call_date") or ""),
                po=str(j.get("po") or ""),
            )
            self.rows.append(r)

            key = j.get("job_key") or row_key(self.module_id, self.sheet_name, wo)
            existing = self.state_store.get(key) or {}
            if existing.get("_dirty"):
                # Preserve local changes until they are synced.
                existing.setdefault("module", self.module_id)
                existing["meta"] = {**meta, **(existing.get("meta") or {})}
                self.state_store.upsert(key, existing)
                continue

            rec = existing
            rec["module"] = self.module_id
            rec["completed"] = bool(int(j.get("completed") or 0))
            rec["invoiced"] = bool(int(j.get("invoiced") or 0))
            if j.get("completed_at"):
                rec["completed_at"] = j.get("completed_at")
            else:
                rec.pop("completed_at", None)
            if j.get("invoiced_at"):
                rec["invoiced_at"] = j.get("invoiced_at")
            rec["qty"] = j.get("qty")
            rec["unit"] = j.get("unit") or self.unit
            rec["current_work"] = bool(int(j.get("current_work") or 0))
            if j.get("lat") is not None:
                rec["lat"] = j.get("lat")
                meta["lat"] = j.get("lat")
            if j.get("lon") is not None:
                rec["lon"] = j.get("lon")
                meta["lon"] = j.get("lon")
            rec["meta"] = meta
            self.state_store.upsert(key, rec)

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        show_completed = self.btn_hide_completed.isChecked()
        hide_missing = self.btn_hide_missing.isChecked()

        for r in self.rows:
            k = row_key(self.module_id, self.sheet_name, r.wo)
            rec = self.state_store.get(k) or {}
            completed = bool(rec.get("completed", False))
            invoiced = bool(rec.get("invoiced", False))
            qty = rec.get("qty", "")
            current_work = bool(rec.get("current_work", False))
            wo = r.wo or ""
            po = r.po or ""

            if show_completed and not completed:
                continue
            if not show_completed and completed:
                continue
            if hide_missing and not wo and not po:
                continue

            qty_disp = ""
            try:
                if qty not in ("", None):
                    qty_disp = f"{float(qty):.2f}".rstrip("0").rstrip(".")
            except Exception:
                qty_disp = ""

            row = self.table.rowCount()
            self.table.insertRow(row)

            lat = rec.get("lat")
            lon = rec.get("lon")
            if lat in ("", None):
                lat = (rec.get("meta") or {}).get("lat")
            if lon in ("", None):
                lon = (rec.get("meta") or {}).get("lon")
            pin_text = ""
            try:
                if lat not in (None, "") and lon not in (None, ""):
                    pin_text = f"{float(lat):.6f}, {float(lon):.6f}"
            except Exception:
                pin_text = ""

            values = [
                wo,
                r.location,
                r.call_date,
                qty_disp,
                self.unit,
                "YES" if completed else "NO",
                "YES" if invoiced else "NO",
                "",
                pin_text,
                "",
                k,
            ]

            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == 5 and completed:
                    item.setForeground(Qt.green)
                if c == 6 and invoiced:
                    item.setForeground(Qt.cyan)
                if c in (5, 6, 7, 8, 9):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, c, item)

            if pin_text:
                pin_item = QTableWidgetItem("")
                icon_path = self._asset_path("gps.png")
                if icon_path and icon_path.exists():
                    pin_item.setIcon(QIcon(str(icon_path)))
                else:
                    pin_item.setText("📍")
                pin_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                pin_item.setTextAlignment(Qt.AlignCenter)
                pin_item.setToolTip(f"{lat}, {lon}")
                pin_item.setData(Qt.UserRole, f"{r.location}|{lat},{lon}")
                self.table.setItem(row, 8, pin_item)

            current_item = QTableWidgetItem()
            current_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            current_item.setCheckState(Qt.Checked if current_work else Qt.Unchecked)
            current_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 7, current_item)

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _=False, key=k: self._edit_pin(key))
            self.table.setCellWidget(row, 9, edit_btn)

        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)

    def _filter_settings_key(self, name: str) -> str:
        return f"filters_{self.module_id}_{name}"

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
            self.btn_hide_completed.setText("Show Not Completed")

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
        if col == 3:
            self._edit_qty(row)
            return
        if col == 8:
            pin_item = self.table.item(row, 8)
            if pin_item:
                data = pin_item.data(Qt.UserRole) or ""
                if isinstance(data, str) and "|" in data:
                    label, coords = data.split("|", 1)
                    if "," in coords:
                        lat_val, lon_val = [v.strip() for v in coords.split(",", 1)]
                        if lat_val and lon_val:
                            q = f"{label} {lat_val},{lon_val}".strip()
                            QDesktopServices.openUrl(QUrl(f"https://maps.google.com/?q={q}"))
            return

    def _on_double_click(self, row: int, col: int) -> None:
        if col == 5:
            self._toggle_completed(row)
            return

    def _toggle_completed(self, row: int) -> None:
        k = self.table.item(row, 10).text()
        current = self.state_store.get(k) or {}
        completed_now = bool(current.get("completed", False))

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
            current["completed"] = False
            current["qty"] = 0
            current["unit"] = self.unit
            current["module"] = self.module_id
            current.pop("completed_at", None)
            self.state_store.upsert(k, current)
            self.state_store.set_dirty(k, True)
            self._sync_completion_async(k, False, 0, "")
            self._populate()
            return

        default_qty = 1.0 if self.unit == "each" else 0.0
        qty, ok = QInputDialog.getDouble(
            self,
            "Enter quantity",
            f"Enter qty ({self.unit}):",
            default_qty,
            0.0,
            1000000.0,
            2,
        )
        if not ok or qty <= 0:
            return

        current["completed"] = True
        current["qty"] = float(qty)
        current["unit"] = self.unit
        current["module"] = self.module_id
        completed_at = date.today().isoformat()
        current["completed_at"] = completed_at

        meta = current.get("meta") or {}
        meta.update(
            {
                "sheet": self.sheet_name,
                "work_order": self.table.item(row, 0).text(),
                "location": self.table.item(row, 1).text(),
                "call_date": self.table.item(row, 2).text(),
                "po": self.rows[row].po if row < len(self.rows) else "",
            }
        )
        current["meta"] = meta

        self.state_store.upsert(k, current)
        self.state_store.set_dirty(k, True)
        self._sync_completion_async(k, True, float(qty), completed_at)
        self._populate()

    def _edit_qty(self, row: int) -> None:
        k = self.table.item(row, 10).text()
        rec = self.state_store.get(k) or {}
        if not bool(rec.get("completed", False)):
            QMessageBox.information(
                self, "Not completed", "Mark the job completed first."
            )
            return

        current_qty = float(rec.get("qty", 0) or 0)
        qty, ok = QInputDialog.getDouble(
            self,
            "Edit quantity",
            f"Qty ({self.unit}):",
            current_qty,
            0.0,
            1000000.0,
            2,
        )
        if not ok or qty <= 0:
            return

        rec["qty"] = float(qty)
        rec["unit"] = self.unit
        rec["module"] = self.module_id
        self.state_store.upsert(k, rec)
        self.state_store.set_dirty(k, True)
        self._populate()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 7:
            return
        key_item = self.table.item(item.row(), 10)
        if not key_item:
            return
        k = key_item.text()
        rec = self.state_store.get(k) or {}
        rec["current_work"] = item.checkState() == Qt.Checked
        rec["module"] = rec.get("module") or self.module_id
        self.state_store.upsert(k, rec)
        self.state_store.set_dirty(k, True)
        self._sync_dirty_async()

    def _get_sync_client(self) -> FieldSyncClient | None:
        settings = self.store.load_settings()
        base = (settings.get("field_api_base") or "").strip()
        key = (settings.get("field_api_key") or "").strip()
        if not base or not key:
            return None
        return FieldSyncClient(base, key)

    def _full_sync_key(self) -> str:
        return f"field_sync_last_full_{self.module_id}"

    def _change_sync_key(self) -> str:
        return f"field_sync_last_change_{self.module_id}"

    def _get_last_full_sync(self) -> float:
        settings = self.store.load_settings()
        try:
            return float(settings.get(self._full_sync_key(), 0) or 0)
        except Exception:
            return 0.0

    def _set_last_full_sync(self, ts: float) -> None:
        settings = self.store.load_settings()
        settings[self._full_sync_key()] = int(ts)
        self.store.save_settings(settings)

    def _get_last_change_sync(self) -> str:
        settings = self.store.load_settings()
        return str(settings.get(self._change_sync_key(), "1970-01-01 00:00:00"))

    def _set_last_change_sync(self, stamp: str) -> None:
        settings = self.store.load_settings()
        settings[self._change_sync_key()] = stamp
        self.store.save_settings(settings)

    def _can_sync_now(self) -> bool:
        now = time.time()
        if (now - self._last_sync_attempt) < self._min_sync_interval:
            return False
        self._last_sync_attempt = now
        return True

    def _build_payload(self, r, rec: dict) -> dict:
        lat = rec.get("lat")
        lon = rec.get("lon")
        if lat in (None, ""):
            lat = (rec.get("meta") or {}).get("lat")
        if lon in (None, ""):
            lon = (rec.get("meta") or {}).get("lon")
        return {
            "job_key": row_key(self.module_id, self.sheet_name, r.wo),
            "module": self.module_id,
            "job_type": self.sheet_name,
            "sheet": self.sheet_name,
            "item": r.location,
            "lat": lat,
            "lon": lon,
            "work_order": r.wo,
            "po": r.po,
            "unit": self.unit,
            "qty_default": None,
            "completed": 1 if rec.get("completed") is True else 0,
            "completed_at": rec.get("completed_at"),
            "invoiced": 1 if rec.get("invoiced") is True else 0,
            "invoiced_at": rec.get("invoiced_at"),
            "qty": rec.get("qty"),
            "current_work": 1 if rec.get("current_work") is True else 0,
            "meta": {
                "location": r.location,
                "call_date": r.call_date,
                "sheet": self.sheet_name,
                "lat": lat,
                "lon": lon,
            },
        }

    def _edit_pin(self, key: str) -> None:
        rec = self.state_store.get(key) or {}
        meta = rec.get("meta") or {}

        lat_val = rec.get("lat", meta.get("lat", ""))
        lon_val = rec.get("lon", meta.get("lon", ""))

        lat_str, ok = QInputDialog.getText(self, "Edit Pin", "Lat:", text=str(lat_val or ""))
        if not ok:
            return
        lon_str, ok = QInputDialog.getText(self, "Edit Pin", "Lon:", text=str(lon_val or ""))
        if not ok:
            return

        rec["lat"] = lat_str.strip()
        rec["lon"] = lon_str.strip()
        meta["lat"] = rec["lat"]
        meta["lon"] = rec["lon"]
        rec["meta"] = meta
        rec["module"] = rec.get("module") or self.module_id

        self.state_store.upsert(key, rec)
        self.state_store.set_dirty(key, True)
        self._sync_dirty_async()
        self._populate()

    def _sync_with_server(self) -> None:
        client = self._get_sync_client()
        if not client:
            return
        if not self._can_sync_now():
            return
        try:
            self._log.info("sync_start module=%s", self.module_id)
            self.sync_status.emit("Syncing...")
            now = time.time()
            since = self._get_last_change_sync()
            changed_jobs = client.changes(since=since)
            self._log.info("sync_changes module=%s since=%s count=%s", self.module_id, since, len(changed_jobs))
            max_updated = ""
            for j in changed_jobs:
                if j.get("module") != self.module_id:
                    continue
                key = j.get("job_key") or ""
                if not key:
                    continue
                rec = self.state_store.get(key) or {}
                if rec.get("_dirty"):
                    continue
                completed_val = j.get("completed")
                if completed_val is not None:
                    rec["completed"] = bool(int(completed_val))
                if j.get("invoiced") is not None:
                    rec["invoiced"] = bool(int(j.get("invoiced")))
                if j.get("invoiced_at"):
                    rec["invoiced_at"] = j.get("invoiced_at")
                if j.get("current_work") is not None:
                    rec["current_work"] = bool(int(j.get("current_work")))
                qty = j.get("qty")
                if qty is not None:
                    try:
                        rec["qty"] = float(qty)
                    except Exception:
                        pass
                rec["unit"] = self.unit
                rec["module"] = self.module_id
                if j.get("completed_at"):
                    rec["completed_at"] = j.get("completed_at")
                elif completed_val is not None and not bool(int(completed_val)):
                    rec.pop("completed_at", None)
                self.state_store.upsert(key, rec)
                updated_at = j.get("updated_at") or ""
                if isinstance(updated_at, str) and updated_at > max_updated:
                    max_updated = updated_at
            if max_updated:
                self._set_last_change_sync(max_updated)
            elif changed_jobs:
                self._set_last_change_sync(client.utc_now_mysql())

            jobs_payload = []
            dirty_keys = set()
            for r in self.rows:
                key = row_key(self.module_id, self.sheet_name, r.wo)
                rec = self.state_store.get(key) or {}
                if rec.get("_dirty"):
                    jobs_payload.append(self._build_payload(r, rec))
                    if rec.get("_dirty"):
                        dirty_keys.add(key)

            if jobs_payload:
                self._log.info(
                    "sync_push module=%s count=%s dirty=%s",
                    self.module_id,
                    len(jobs_payload),
                    len(dirty_keys),
                )
                result = client.sync_jobs(jobs_payload)
                if result.get("ok"):
                    for k in dirty_keys:
                        self.state_store.clear_dirty(k)
            from datetime import datetime
            self.sync_status.emit(f"Last sync: {datetime.now().strftime('%H:%M')}")
            self._log.info("sync_done module=%s", self.module_id)
        except Exception:
            self.sync_status.emit("Sync failed")
            self._log.exception("sync_failed module=%s", self.module_id)

    def _sync_completion(self, key: str, completed: bool, qty: float, completed_at: str) -> None:
        self._sync_dirty()

    def _sync_dirty(self) -> None:
        client = self._get_sync_client()
        if not client:
            return
        if not self._can_sync_now():
            self._schedule_dirty_sync()
            return
        try:
            jobs_payload = []
            dirty_keys = set()
            for r in self.rows:
                key = row_key(self.module_id, self.sheet_name, r.wo)
                rec = self.state_store.get(key) or {}
                if rec.get("_dirty"):
                    jobs_payload.append(self._build_payload(r, rec))
                    dirty_keys.add(key)
            if not jobs_payload:
                return
            self.sync_status.emit("Syncing...")
            result = client.sync_jobs(jobs_payload)
            if result.get("ok"):
                for k in dirty_keys:
                    self.state_store.clear_dirty(k)
            from datetime import datetime
            self.sync_status.emit(f"Last sync: {datetime.now().strftime('%H:%M')}")
        except Exception:
            self.sync_status.emit("Sync failed")

    def _schedule_dirty_sync(self) -> None:
        if self._dirty_sync_pending:
            return
        self._dirty_sync_pending = True

        def _run() -> None:
            self._dirty_sync_pending = False
            self._sync_dirty()

        t = threading.Timer(self._min_sync_interval, _run)
        t.daemon = True
        t.start()

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

    def _sync_dirty_async(self) -> None:
        t = threading.Thread(target=self._sync_dirty, daemon=True)
        t.start()

    def _set_sync_status(self, text: str) -> None:
        self.sync_label.setText(f"Sync: {text}")
