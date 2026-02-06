from __future__ import annotations
from services.state_store import StateStore
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple
import logging
import time

from PySide6.QtCore import Qt, Signal, QUrl, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QInputDialog
)
from PySide6.QtGui import QDesktopServices, QIcon
from datetime import date

from services.settings_store import SettingsStore
import sys
from services.field_sync import FieldSyncClient
import threading
import json

# Your copied V1 loader goes here:
from data.data_loader import (
    SprayRow,
    load_spray_list,
    load_spray_drains_mapping,
    attach_work_info,
    norm,
)


def row_key(r) -> str:
    jk = getattr(r, "job_key", None)
    if jk:
        return str(jk)
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
        self._log = logging.getLogger(__name__)

        self.state_store = StateStore(self.store)
        self._sync_warned = False
        self._last_sync_attempt = 0.0
        self._min_sync_interval = 2.0
        self._full_sync_interval = 300.0
        self._dirty_sync_pending = False

        self.rows = []  # list of SprayRow-like objects
        self._display_rows: list = []

        root = QVBoxLayout(self)
        header = QHBoxLayout()

        title = QLabel("Drain spraying")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.btn_refresh)

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
        self.table.setHorizontalHeaderLabels([
            "Sheet", "Catchment", "Drain", "KM", "WO", "PO", "Completed", "Invoiced", "Current", "Pin", "Edit"
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(32, 32))

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

        # Prefer server list if configured
        client = self._get_sync_client()
        if client:
            try:
                jobs = client.list_jobs(module="drain", completed=None, limit=2000)
            except Exception:
                jobs = []
            if jobs:
                self._load_from_server_jobs(jobs)
                self._apply_spray_list_order(spray_list)
                self._populate()
                self._sync_with_server_async()
                return

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

    def _asset_path(self, filename: str) -> Path | None:
        try:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            return base / "assets" / filename
        except Exception:
            return None

    def _apply_spray_list_order(self, spray_list: str) -> None:
        if not spray_list or not Path(spray_list).exists():
            return
        try:
            ordered = load_spray_list(spray_list)
        except Exception:
            return

        order_map: Dict[str, int] = {}
        for idx, r in enumerate(ordered):
            order_map[row_key(r)] = idx

        def _sort_key(r: SprayRow) -> tuple[int, str]:
            k = row_key(r)
            if k in order_map:
                return (order_map[k], k)
            return (10**9, k)

        self.rows.sort(key=_sort_key)

    def _load_from_server_jobs(self, jobs: list[dict]) -> None:
        self.rows = []
        for j in jobs:
            drain = (j.get("item") or "").strip()
            if not drain:
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

            qty_default = j.get("qty_default")
            try:
                km = float(qty_default) if qty_default is not None else 0.0
            except Exception:
                km = 0.0

            if "qty_km" not in meta:
                meta["qty_km"] = round(km, 2)

            r = SprayRow(
                job_key=j.get("job_key") or None,
                sheet=str(j.get("sheet") or ""),
                catchment=meta.get("catchment") or "",
                drain=drain,
                distance_m=km * 1000.0,
                qty_km=round(km, 2),
                lat=j.get("lat") if j.get("lat") is not None else meta.get("lat"),
                lon=j.get("lon") if j.get("lon") is not None else meta.get("lon"),
            )
            r.work_order = j.get("work_order") or ""
            r.po = j.get("po") or ""
            r.completed = bool(int(j.get("completed") or 0))
            self.rows.append(r)

            key = j.get("job_key") or row_key(r)
            existing = self.state_store.get(key) or {}
            if existing.get("_dirty"):
                # Preserve local changes until they are synced.
                existing.setdefault("module", "drain")
                existing["meta"] = {**meta, **(existing.get("meta") or {})}
                self.state_store.upsert(key, existing)
                continue

            rec = existing
            rec["module"] = "drain"
            rec["completed"] = bool(int(j.get("completed") or 0))
            server_invoiced_val = j.get("invoiced")
            local_invoiced = bool(rec.get("invoiced"))
            if server_invoiced_val is not None:
                server_invoiced = bool(int(server_invoiced_val))
                if server_invoiced or not local_invoiced:
                    rec["invoiced"] = server_invoiced
            if j.get("completed_at"):
                rec["completed_at"] = j.get("completed_at")
            else:
                rec.pop("completed_at", None)
            if j.get("invoiced_at"):
                rec["invoiced_at"] = j.get("invoiced_at")
            rec_qty = j.get("qty")
            if rec_qty in (None, ""):
                rec_qty = round(km, 2)
            rec["qty"] = rec_qty
            rec["qty_default"] = qty_default
            rec["unit"] = j.get("unit") or "km"
            rec["current_work"] = bool(int(j.get("current_work") or 0))
            rec["meta"] = meta
            self.state_store.upsert(key, rec)

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._display_rows = []

        show_completed = self.btn_hide_completed.isChecked()
        hide_missing = self.btn_hide_missing.isChecked()

        for r in self.rows:
            k = row_key(r)
            rec = self.state_store.get(k) or {}
            completed = bool(rec.get("completed", False))
            wo = getattr(r, "work_order", "") or ""
            po = getattr(r, "po", "") or ""
            if show_completed and not completed:
                continue
            if not show_completed and completed:
                continue
            if hide_missing and not wo and not po:
                continue

            self._display_rows.append(r)
            row = self.table.rowCount()
            self.table.insertRow(row)

            current_work = bool(rec.get("current_work", False))

            sheet = getattr(r, "sheet", "")
            catchment = getattr(r, "catchment", "") or ""
            drain = getattr(r, "drain", "")
            km = float(getattr(r, "qty_km", 0.0))
            invoiced = bool(rec.get("invoiced", False))
            lat = rec.get("lat")
            lon = rec.get("lon")
            if lat in ("", None):
                lat = getattr(r, "lat", None)
            if lon in ("", None):
                lon = getattr(r, "lon", None)
            pin_text = ""
            try:
                if lat not in (None, "") and lon not in (None, ""):
                    pin_text = f"{float(lat):.6f}, {float(lon):.6f}"
            except Exception:
                pin_text = ""

            values = [sheet, catchment, drain, f"{km:.2f}", wo, po,
                    "YES" if completed else "NO",
                    "YES" if invoiced else "NO",
                    "",
                    pin_text,
                    ""]


            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == 6 and completed:
                    item.setForeground(Qt.darkGreen)
                if c in (6, 7, 8, 9, 10):
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
                pin_item.setData(Qt.UserRole, f"{drain}|{lat},{lon}")
                self.table.setItem(row, 9, pin_item)
                self.table.setRowHeight(row, 36)

            current_item = QTableWidgetItem()
            current_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            current_item.setCheckState(Qt.Checked if current_work else Qt.Unchecked)
            current_item.setTextAlignment(Qt.AlignCenter)
            current_item.setData(Qt.UserRole, k)
            self.table.setItem(row, 8, current_item)

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _=False, key=k: self._edit_pin(key))
            self.table.setCellWidget(row, 10, edit_btn)

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
        self.state_store.set_dirty(k, True)
        self._sync_dirty_async()

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
        if col == 9:
            pin_item = self.table.item(row, 9)
            if pin_item:
                data = pin_item.data(Qt.UserRole) or ""
                if isinstance(data, str) and "|" in data:
                    label, coords = data.split("|", 1)
                    if "," in coords:
                        lat_val, lon_val = [v.strip() for v in coords.split(",", 1)]
                        if lat_val and lon_val:
                            safe_label = QUrl.toPercentEncoding(label).data().decode("utf-8")
                            if safe_label:
                                query = f"loc:{lat_val},{lon_val} ({label})"
                            else:
                                query = f"loc:{lat_val},{lon_val}"
                            url = QUrl("https://maps.google.com/maps")
                            url.setQuery(f"q={QUrl.toPercentEncoding(query).data().decode('utf-8')}")
                            QDesktopServices.openUrl(url)
        return

    def _on_double_click(self, row: int, col: int) -> None:
        if col == 6:
            self._toggle_completed(row)
            return

    def _toggle_completed(self, row: int) -> None:
        if row < 0 or row >= len(self._display_rows):
            return
        r = self._display_rows[row]
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
            "lat": getattr(r, "lat", None),
            "lon": getattr(r, "lon", None),
        })

        self.state_store.upsert(k, existing)
        self.state_store.set_dirty(k, True)
        self._sync_dirty_async()
        self._populate()

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
        rec.setdefault("module", "drain")

        # Update in-memory row if present
        for r in self.rows:
            if row_key(r) == key:
                r.lat = rec["lat"]
                r.lon = rec["lon"]
                break

        self.state_store.upsert(key, rec)
        self.state_store.set_dirty(key, True)
        self._sync_dirty_async()
        self._populate()

    def _get_sync_client(self) -> FieldSyncClient | None:
        settings = self.store.load_settings()
        base = (settings.get("field_api_base") or "").strip()
        key = (settings.get("field_api_key") or "").strip()
        if not base or not key:
            return None
        return FieldSyncClient(base, key)

    def _full_sync_key(self) -> str:
        return "field_sync_last_full_drain"

    def _change_sync_key(self) -> str:
        return "field_sync_last_change_drain"

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

    def _build_payload(self, r, rec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "job_key": row_key(r),
            "module": "drain",
            "job_type": "Drain spraying",
            "sheet": getattr(r, "sheet", ""),
            "item": getattr(r, "drain", ""),
            "lat": getattr(r, "lat", None),
            "lon": getattr(r, "lon", None),
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
                "start_m": getattr(r, "start_m", None),
                "end_m": getattr(r, "end_m", None),
                "work_order": getattr(r, "work_order", "") or "",
                "po": getattr(r, "po", "") or "",
                "lat": getattr(r, "lat", None),
                "lon": getattr(r, "lon", None),
            },
        }

    def _sync_with_server(self) -> None:
        client = self._get_sync_client()
        if not client:
            return
        if not self._can_sync_now():
            return
        try:
            self._log.info("sync_start module=drain")
            self.sync_status.emit("Syncing...")
            now = time.time()
            since = self._get_last_change_sync()
            changed_jobs = client.changes(since=since)
            self._log.info("sync_changes module=drain since=%s count=%s", since, len(changed_jobs))
            max_updated = ""
            for j in changed_jobs:
                if j.get("module") != "drain":
                    continue
                job_key = j.get("job_key") or ""
                if not job_key:
                    continue
                rec = self.state_store.get(job_key) or {}
                if rec.get("_dirty"):
                    continue
                completed_val = j.get("completed")
                if completed_val is not None:
                    rec["completed"] = bool(int(completed_val))
                server_invoiced_val = j.get("invoiced")
                local_invoiced = bool(rec.get("invoiced"))
                if server_invoiced_val is not None:
                    server_invoiced = bool(int(server_invoiced_val))
                    if server_invoiced or not local_invoiced:
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
                elif completed_val is not None and not bool(int(completed_val)):
                    rec.pop("completed_at", None)
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
                        "lat": j.get("lat") if j.get("lat") is not None else meta.get("lat"),
                        "lon": j.get("lon") if j.get("lon") is not None else meta.get("lon"),
                    }
                )
                rec["meta"] = meta
                self.state_store.upsert(job_key, rec)
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
                key = row_key(r)
                rec = self.state_store.get(key) or {}
                if rec.get("_dirty"):
                    jobs_payload.append(self._build_payload(r, rec))
                    if rec.get("_dirty"):
                        dirty_keys.add(key)

            if jobs_payload:
                self._log.info("sync_push module=drain count=%s dirty=%s", len(jobs_payload), len(dirty_keys))
                result = client.sync_jobs(jobs_payload)
                if result.get("ok"):
                    for k in dirty_keys:
                        self.state_store.clear_dirty(k)
            from datetime import datetime
            self.sync_status.emit(f"Last sync: {datetime.now().strftime('%H:%M')}")
            self._log.info("sync_done module=drain")
        except Exception:
            self.sync_status.emit("Sync failed")
            self._log.exception("sync_failed module=drain")

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
                key = row_key(r)
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

    def _sync_dirty_async(self) -> None:
        t = threading.Thread(target=self._sync_dirty, daemon=True)
        t.start()

    def _set_sync_status(self, text: str) -> None:
        self.sync_label.setText(f"Sync: {text}")

