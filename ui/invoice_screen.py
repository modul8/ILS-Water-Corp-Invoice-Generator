from __future__ import annotations

from typing import Any, Dict, List, Tuple
from pathlib import Path
from datetime import date
import re
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialog, QDateEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QInputDialog,
    QRadioButton
)

from services.settings_store import SettingsStore
from services.field_sync import FieldSyncClient
from services.state_store import StateStore
from services.dolibarr_client import DolibarrClient
from PySide6.QtCore import Qt


DEFAULT_GST_RATE = 10.0


def _line_desc(module: str, meta: Dict[str, Any]) -> str:
    # Drain module (matches your V1 style)
    if module == "drain":
        drain = meta.get("drain", "")
        wo = (meta.get("work_order") or "").strip()
        if wo:
            return f"Drain Spraying {drain}\nW/O: {wo}"
        return f"Drain Spraying {drain}"

    # Placeholder for future modules
    item = meta.get("item") or meta.get("location") or meta.get("name") or "Work Item"
    wo = (meta.get("work_order") or "").strip()
    if wo:
        return f"{module.title()} {item}\nW/O: {wo}"
    return f"{module.title()} {item}"


class InvoiceScreen(QWidget):
    """
    Lists completed but not invoiced items from ALL modules (as they are implemented).
    Creates draft invoices in Dolibarr for selected items and then marks them invoiced locally.
    """

    def __init__(self, store: SettingsStore) -> None:
        super().__init__()
        self.store = store
        self.state_store = StateStore(store)
        
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("Invoicing")
        title.setStyleSheet("font-size: 20px; font-weight: 800;")

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)

        self.btn_mark_invoiced = QPushButton("Mark Selected\nas Invoiced")
        self.btn_mark_invoiced.clicked.connect(self.mark_selected_invoiced)

        self.btn_undo_invoiced = QPushButton("Undo Invoiced")
        self.btn_undo_invoiced.clicked.connect(self.undo_selected_invoiced)

        self.btn_create = QPushButton("Create Draft\nInvoice")
        self.btn_create.clicked.connect(self.create_draft_invoices)

        self.btn_export_range = QPushButton("Export TXT\n(Date Range)")
        self.btn_export_range.clicked.connect(self.export_txt_date_range)

        self.btn_show_invoiced = QPushButton("Show Invoiced")
        self.btn_show_invoiced.clicked.connect(self.toggle_show_invoiced)

        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self.select_all)

        self.btn_select_none = QPushButton("Select None")
        self.btn_select_none.clicked.connect(self.select_none)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_show_invoiced)
        header.addWidget(self.btn_select_all)
        header.addWidget(self.btn_select_none)
        header.addWidget(self.btn_mark_invoiced)
        header.addWidget(self.btn_undo_invoiced)
        header.addWidget(self.btn_create)
        header.addWidget(self.btn_export_range)
        

        root.addLayout(header)

        self.totals_label = QLabel("")
        self.totals_label.setStyleSheet("color:#9aa3b2; font-size: 12px;")
        self.totals_label.setWordWrap(True)
        root.addWidget(self.totals_label)

        self.table = QTableWidget(0, 10)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels([
            "Select", "Module", "Sheet/Area", "Item", "Qty", "Amount (ex GST)", "WO", "PO", "Draft ID", "Key"
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setColumnHidden(9, True)  # hide key
        self.table.setColumnWidth(0, 70)
        self.table.setStyleSheet("""
        QTableWidget::indicator {
            width: 18px;
            height: 18px;
        }
        QTableWidget::indicator:unchecked {
            border: 2px solid #bdbdbd;
            background: #1e1e1e;
        }
        QTableWidget::indicator:checked {
            border: 2px solid #4c8bf5;
            background: #4c8bf5;
        }
        """)
       
        root.addWidget(self.table, 1)

        hint = QLabel("Select rows and click Create Draft Invoice. Items must be Completed and not Invoiced.")
        hint.setStyleSheet("color:#aaa;")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.hint = hint

        self.show_invoiced = False

        self.refresh()

    def reload_from_settings(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._sync_changes()
        self.state_store = StateStore(self.store)

        if self.show_invoiced:
            records = [(k, rec) for k, rec in self.state_store.iter_records() if rec.get("invoiced") is True]
        else:
            records = self.state_store.pending_to_invoice()
        seen_keys: set[str] = set()
        totals_by_po: Dict[str, float] = {}

        self.table.setRowCount(0)
        for key, rec in records:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            meta = rec.get("meta") or {}
            module = rec.get("module", "unknown")

            sheet_area = meta.get("sheet") or meta.get("catchment") or meta.get("area") or ""
            item = meta.get("drain") or meta.get("location") or meta.get("item") or ""
            qty = meta.get("qty_km") or rec.get("qty") or meta.get("qty") or ""
            unit = self._unit_for_record(rec)
            unit_suffix = "km" if unit == "km" else "hr" if unit == "hour" else "ea"
            qty_disp = ""
            try:
                qty_f = float(qty)
                qty_disp = f"{qty_f:.2f}".rstrip("0").rstrip(".")
                qty_disp = f"{qty_disp} {unit_suffix}"
            except Exception:
                qty_disp = ""

            rate = self._get_rate_for_module(module, unit, self.store.load_settings())
            amount_disp = ""
            try:
                qty_f = float(qty)
                if rate > 0:
                    amount_disp = f"${qty_f * rate:.2f}"
                    po_key = (meta.get("po") or "").strip()
                    totals_by_po[po_key] = totals_by_po.get(po_key, 0.0) + (qty_f * rate)
            except Exception:
                amount_disp = ""
            wo = meta.get("work_order") or ""
            po = meta.get("po") or ""

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Column 0: checkbox
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check_item)
            draft_id = rec.get("draft_invoice_id", "")
            if not draft_id or int(draft_id) <= 0:
                draft_id = ""

            vals = [module, str(sheet_area), str(item), str(qty_disp), str(amount_disp), str(wo), str(po), str(draft_id), key]
            # Put these into columns 1..9
            for c, v in enumerate(vals, start=1):
                item_widget = QTableWidgetItem(str(v))
                if c == 5:
                    item_widget.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, c, item_widget)

        self.table.resizeColumnsToContents()
        self._update_totals_label(totals_by_po)
        self._sync_view_state()

    def _change_sync_key(self) -> str:
        return "invoice_change_sync"

    def _get_last_change_sync(self) -> str:
        settings = self.store.load_settings()
        return str(settings.get(self._change_sync_key(), "1970-01-01 00:00:00"))

    def _set_last_change_sync(self, stamp: str) -> None:
        settings = self.store.load_settings()
        settings[self._change_sync_key()] = stamp
        self.store.save_settings(settings)

    def _get_sync_client(self) -> FieldSyncClient | None:
        settings = self.store.load_settings()
        base = (settings.get("field_api_base") or "").strip()
        key = (settings.get("field_api_key") or "").strip()
        if not base or not key:
            return None
        return FieldSyncClient(base, key)

    def _sync_changes(self) -> None:
        client = self._get_sync_client()
        if not client:
            return
        since = self._get_last_change_sync()
        try:
            changed_jobs = client.changes(since=since)
        except Exception:
            return
        max_updated = ""
        for j in changed_jobs:
            job_key = j.get("job_key") or ""
            if not job_key:
                continue
            rec = self.state_store.get(job_key) or {}
            if rec.get("_dirty"):
                continue
            completed_val = j.get("completed")
            if completed_val is not None:
                rec["completed"] = bool(int(completed_val))
            if j.get("completed_at"):
                rec["completed_at"] = j.get("completed_at")
            elif completed_val is not None and not bool(int(completed_val)):
                rec.pop("completed_at", None)
            server_invoiced_val = j.get("invoiced")
            local_invoiced = bool(rec.get("invoiced"))
            if server_invoiced_val is not None:
                server_invoiced = bool(int(server_invoiced_val))
                if server_invoiced or not local_invoiced:
                    rec["invoiced"] = server_invoiced
                elif local_invoiced and not server_invoiced:
                    # Server missed the invoiced update; re-assert it.
                    try:
                        today = date.today().isoformat()
                        client.set_invoiced(
                            job_key=job_key,
                            invoiced=True,
                            invoiced_at=rec.get("invoiced_at") or today,
                        )
                    except Exception:
                        pass
            if j.get("invoiced_at"):
                rec["invoiced_at"] = j.get("invoiced_at")
            qty = j.get("qty")
            if qty is not None:
                try:
                    rec["qty"] = float(qty)
                except Exception:
                    rec["qty"] = qty
            if j.get("qty_default") is not None:
                rec["qty_default"] = j.get("qty_default")
            if j.get("unit"):
                rec["unit"] = j.get("unit")
            if j.get("module"):
                rec["module"] = j.get("module")
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
                    "area": meta.get("area") or "",
                    "drain": j.get("item") or meta.get("drain") or "",
                    "location": j.get("item") or meta.get("location") or "",
                    "item": j.get("item") or meta.get("item") or "",
                    "work_order": j.get("work_order") or meta.get("work_order") or "",
                    "po": j.get("po") or meta.get("po") or "",
                    "qty_km": j.get("qty_default") or rec.get("qty") or meta.get("qty_km") or meta.get("qty") or 0,
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

    def _sync_view_state(self) -> None:
        if self.show_invoiced:
            self.btn_show_invoiced.setText("Show Pending")
            self.btn_mark_invoiced.setEnabled(False)
            self.btn_create.setEnabled(False)
            self.hint.setText("Showing invoiced items.")
        else:
            self.btn_show_invoiced.setText("Show Invoiced")
            self.btn_mark_invoiced.setEnabled(True)
            self.btn_create.setEnabled(True)
            self.hint.setText("Select rows and click Create Draft Invoice. Items must be Completed and not Invoiced.")

    def toggle_show_invoiced(self) -> None:
        self.show_invoiced = not self.show_invoiced
        self.refresh()

    def _update_totals_label(self, totals_by_po: Dict[str, float]) -> None:
        if not totals_by_po:
            self.totals_label.setText("Totals per PO (ex GST): none")
            return

        parts = []
        for po in sorted(totals_by_po.keys(), key=lambda x: (x == "", x)):
            label = po if po else "No PO"
            parts.append(f"{label}: ${totals_by_po[po]:.2f}")
        self.totals_label.setText("Totals per PO (ex GST): " + "  |  ".join(parts))

    def _selected_keys(self) -> List[str]:
        keys: List[str] = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)  # checkbox column
            if item and item.checkState() == Qt.Checked:
                key_item = self.table.item(r, 9)  # hidden key column
                if key_item:
                    keys.append(key_item.text())
        return keys
    
    def select_all(self) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked)

    def select_none(self) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Unchecked)


    def mark_selected_invoiced(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(self, "Nothing selected", "Select one or more rows first.")
            return

        self._sync_invoiced(keys, True)
        for k in keys:
            self.state_store.set_flags(k, invoiced=True)

        self.refresh()

    def _rate_settings_key(self, module: str) -> str:
        m = (module or "").strip().lower()
        if m == "drain":
            return "rate_drain"
        if m == "weeds":
            return "rate_weeds"
        if m == "tracks":
            return "rate_tracks"
        if m == "fire":
            return "rate_fire"
        return "rate_default"

    def _rate_label(self, module: str, unit: str) -> str:
        m = (module or "").strip().lower()
        if m == "drain":
            return "Drain spraying rate"
        if m == "weeds":
            return "Noxious weeds rate"
        if m == "tracks":
            return "Track maintenance rate"
        if m == "fire":
            return "Fire zone rate"
        return "Rate"

    def _rate_prompt_default(self, unit: str) -> float:
        u = (unit or "").strip().lower()
        if u == "km":
            return 2.50
        return 1.00

    def _get_rate_for_module(self, module: str, unit: str, settings: Dict[str, Any]) -> float:
        key = self._rate_settings_key(module)
        try:
            r = float(settings.get(key, 0))
            if r > 0:
                return r
        except Exception:
            pass

        rate, ok = QInputDialog.getDouble(
            self,
            self._rate_label(module, unit),
            f"Enter rate for {module or 'item'} ({unit or 'each'}) ex GST:",
            self._rate_prompt_default(unit),
            0.01,
            9999.0,
            2,
        )
        if not ok or rate <= 0:
            return 0.0

        settings[key] = float(rate)
        self.store.save_settings(settings)
        return float(rate)

    def _unit_for_record(self, rec: Dict[str, Any]) -> str:
        unit = (rec.get("unit") or "").strip().lower()
        if unit:
            return unit
        meta = rec.get("meta") or {}
        if meta.get("qty_km") is not None:
            return "km"
        return "each"

    def _safe_filename(self, name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "invoice_item"

    def _next_available_path(self, base: Path) -> Path:
        if not base.exists():
            return base
        stem = base.stem
        suffix = base.suffix or ".txt"
        n = 2
        while True:
            candidate = base.with_name(f"{stem}_{n}{suffix}")
            if not candidate.exists():
                return candidate
            n += 1

    def _job_type_label(self, module: str) -> str:
        m = (module or "").strip().lower()
        if m == "drain":
            return "Spray Drains"
        if m == "weeds":
            return "Noxious Weeds"
        if m == "tracks":
            return "Mtn Access Tracks"
        if m == "fire":
            return "Fire Zones"
        return module or "Work Item"

    def _write_export_files(self, selected_recs: List[Tuple[str, Dict[str, Any]]], export_dir: str) -> None:
        out_dir = Path(export_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

        settings = self.store.load_settings()
        photo_base = (settings.get("field_api_base") or "").strip()
        api_key = (settings.get("field_api_key") or "").strip()
        if photo_base.endswith("/api/index.php"):
            photo_base = photo_base[: -len("/api/index.php")]
        photo_base = photo_base.rstrip("/")

        def photos_link(job_key: str) -> str:
            if not photo_base or not api_key or not job_key:
                return ""
            return f"{photo_base}/photos.php?job_key={job_key}&key={api_key}"

        lines: List[str] = []
        html_rows: List[str] = []
        for key, rec in selected_recs:
            meta = rec.get("meta") or {}
            module = rec.get("module", "unknown")
            item = meta.get("drain") or meta.get("location") or meta.get("item") or ""
            wo = meta.get("work_order") or ""
            po = meta.get("po") or ""
            completed_at = rec.get("completed_at") or meta.get("completed_at") or ""
            if not completed_at:
                completed_at = date.today().isoformat()
            photo_url = photos_link(key)

            block = [
                f"Job Type: {self._job_type_label(module)}",
                f"Item: {item}",
                f"WO: {wo}",
                f"PO: {po}",
                f"Completed: {completed_at}",
                f"Photos: {photo_url}" if photo_url else "Photos: (none)",
                "",
            ]
            lines.extend(block)

            photo_cell = f'<a href="{photo_url}">Photos</a>' if photo_url else "(none)"
            html_rows.append(
                "<tr>"
                f"<td>{self._job_type_label(module)}</td>"
                f"<td>{item}</td>"
                f"<td>{wo}</td>"
                f"<td>{po}</td>"
                f"<td>{completed_at}</td>"
                f"<td>{photo_cell}</td>"
                "</tr>"
            )

        stamp = date.today().isoformat()
        stem = self._safe_filename(f"invoiced_jobs_{stamp}")
        path = self._next_available_path(out_dir / f"{stem}.txt")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>body{font-family:Arial,sans-serif;}table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ddd;padding:8px;text-align:left;}th{background:#f5f5f5;}</style>"
            "</head><body>"
            f"<h2>Invoiced Jobs {stamp}</h2>"
            "<table><thead><tr>"
            "<th>Job Type</th><th>Item</th><th>WO</th><th>PO</th><th>Completed</th><th>Photos</th>"
            "</tr></thead><tbody>"
            + "".join(html_rows)
            + "</tbody></table></body></html>"
        )
        html_path = self._next_available_path(out_dir / f"{stem}.html")
        html_path.write_text(html, encoding="utf-8")

    def _parse_completed_date(self, rec: Dict[str, Any]) -> date | None:
        meta = rec.get("meta") or {}
        raw = rec.get("completed_at") or meta.get("completed_at") or ""
        try:
            if raw:
                return date.fromisoformat(str(raw))
        except Exception:
            return None
        return None

    def export_txt_date_range(self) -> None:
        settings = self.store.load_settings()
        export_dir = (settings.get("invoice_export_dir") or "").strip()
        if not export_dir:
            QMessageBox.warning(self, "Missing export folder", "Set the invoice export folder in Settings.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Export TXT (Date Range)")
        layout = QVBoxLayout(dlg)

        row = QHBoxLayout()
        lbl_from = QLabel("From:")
        lbl_to = QLabel("To:")
        date_from = QDateEdit()
        date_from.setCalendarPopup(True)
        date_from.setDate(date.today())
        date_to = QDateEdit()
        date_to.setCalendarPopup(True)
        date_to.setDate(date.today())

        row.addWidget(lbl_from)
        row.addWidget(date_from)
        row.addWidget(lbl_to)
        row.addWidget(date_to)
        layout.addLayout(row)

        filter_row = QHBoxLayout()
        rb_invoiced = QRadioButton("Invoiced")
        rb_not_invoiced = QRadioButton("Completed, not invoiced")
        rb_invoiced.setChecked(True)
        filter_row.addWidget(rb_invoiced)
        filter_row.addWidget(rb_not_invoiced)
        layout.addLayout(filter_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_ok = QPushButton("Export")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        start = date_from.date().toPython()
        end = date_to.date().toPython()
        if end < start:
            QMessageBox.warning(self, "Invalid range", "End date must be on or after start date.")
            return

        selected: List[Tuple[str, Dict[str, Any]]] = []
        for k, rec in self.state_store.iter_records():
            if rec.get("completed") is not True:
                continue
            if rb_invoiced.isChecked():
                if rec.get("invoiced") is not True:
                    continue
            else:
                if rec.get("invoiced") is True:
                    continue
            d = self._parse_completed_date(rec)
            if not d:
                continue
            if start <= d <= end:
                selected.append((k, rec))

        if not selected:
            QMessageBox.information(self, "No matches", "No completed jobs found in that date range.")
            return

        try:
            self._write_export_files(selected, export_dir)
        except Exception as e:
            QMessageBox.warning(self, "Export files failed", str(e))
            return

        QMessageBox.information(self, "Exported", "TXT file created for the selected date range.")

    def undo_selected_invoiced(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(self, "Nothing selected", "Select one or more rows first.")
            return

        self._sync_invoiced(keys, False)
        for k in keys:
            # Mark not invoiced; keep completed state so it re-appears in pending list
            rec = self.state_store.get(k) or {}
            if rec.get("completed") is True:
                # Clear invoiced flags and any draft link
                self.state_store.set_flags(k, invoiced=False, draft_invoice_id=0)

                # Clean up the key if you prefer it absent rather than 0:
                # If you want that, tell me and I'll adjust StateStore to support clearing.
        self.refresh()

    def create_draft_invoices(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(self, "Nothing selected", "Select one or more rows first.")
            return

        settings = self.store.load_settings()
        base_url = (settings.get("dolibarr_base_url") or "").strip()
        socid = (settings.get("dolibarr_socid") or "").strip()
        api_key = self.store.get_api_key().strip()
        export_dir = (settings.get("invoice_export_dir") or "").strip()

        if not base_url or not socid or not api_key:
            QMessageBox.warning(self, "Missing settings", "Set Dolibarr Base URL, Customer ID (socid), and API key in Settings.")
            return
        if not export_dir:
            QMessageBox.warning(self, "Missing export folder", "Set the invoice export folder in Settings.")
            return

        client = DolibarrClient(base_url, api_key)

        # Collect records
        selected_recs: List[Tuple[str, Dict[str, Any]]] = []
        for k in keys:
            rec = self.state_store.get(k)
            if not rec:
                continue
            if rec.get("completed") is True and rec.get("invoiced") is not True:
                selected_recs.append((k, rec))

        if not selected_recs:
            QMessageBox.information(self, "Nothing to invoice", "Selected rows are not in a Completed + not Invoiced state.")
            return

        # Resolve required rates by module before creating any invoices
        rate_by_module: Dict[str, float] = {}
        for _, rec in selected_recs:
            module = rec.get("module", "unknown")
            if module in rate_by_module:
                continue
            unit = self._unit_for_record(rec)
            r = self._get_rate_for_module(module, unit, settings)
            if r <= 0:
                QMessageBox.warning(self, "Missing rate", f"Rate for module '{module}' is required.")
                return
            rate_by_module[module] = r

        # Group by PO (one invoice per PO)
        groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for k, rec in selected_recs:
            meta = rec.get("meta") or {}
            po = (meta.get("po") or "").strip()
            groups.setdefault(po, []).append((k, rec))

        created: List[str] = []

        try:
            for po, items in groups.items():
                invoice_id = client.create_draft_invoice(
                    socid=str(socid),
                    ref_customer=str(po or ""),
                )

                # Add one line per item
                for k, rec in items:
                    module = rec.get("module", "unknown")
                    meta = rec.get("meta") or {}

                    qty = meta.get("qty_km") or rec.get("qty") or meta.get("qty") or 0
                    try:
                        qty_f = float(qty)
                    except Exception:
                        qty_f = 0.0

                    if qty_f <= 0:
                        raise RuntimeError(f"Bad qty for key {k}: {qty}")

                    desc = _line_desc(module, meta)
                    unit = self._unit_for_record(rec)
                    rate = rate_by_module.get(module, 0.0)
                    if rate <= 0:
                        raise RuntimeError(f"Missing rate for module '{module}'")

                    client.add_invoice_line(
                        invoice_id=invoice_id,
                        desc=desc,
                        qty=qty_f,
                        subprice=rate,
                        tva_tx=DEFAULT_GST_RATE,
                        product_type="1",
                    )

                # Mark as invoiced locally
                for k, _ in items:
                    self.state_store.set_flags(k, invoiced=True, draft_invoice_id=invoice_id)

                created.append(f"Draft invoice ID {invoice_id} ({'PO ' + po if po else 'no PO'})")

        except Exception as e:
            QMessageBox.critical(self, "Invoice creation failed", str(e))
            # We may have created an invoice before failing on a later line.
            # Items will only be marked invoiced after a full invoice finishes.
            self.refresh()
            return

        try:
            self._write_export_files(selected_recs, export_dir)
        except Exception as e:
            QMessageBox.warning(self, "Export files failed", str(e))

        self.refresh()

    def _sync_invoiced(self, keys: List[str], invoiced: bool) -> None:
        settings = self.store.load_settings()
        base = (settings.get("field_api_base") or "").strip()
        key = (settings.get("field_api_key") or "").strip()
        if not base or not key:
            return
        client = FieldSyncClient(base, key)
        from datetime import date

        today = date.today().isoformat()
        jobs_payload: List[Dict[str, Any]] = []
        for k in keys:
            rec = self.state_store.get(k)
            if not rec:
                continue
            meta = rec.get("meta") or {}
            module = rec.get("module", "")
            jobs_payload.append(
                {
                    "job_key": k,
                    "module": module,
                    "job_type": self._job_type_label(module),
                    "sheet": meta.get("sheet") or meta.get("catchment") or "",
                    "item": meta.get("drain") or meta.get("location") or meta.get("item") or "",
                    "work_order": meta.get("work_order") or "",
                    "po": meta.get("po") or "",
                    "unit": rec.get("unit") or "",
                    "qty_default": meta.get("qty_km") or rec.get("qty") or meta.get("qty") or "",
                    "completed": 1 if rec.get("completed") is True else 0,
                    "completed_at": rec.get("completed_at") or meta.get("completed_at") or "",
                    "invoiced": 1 if invoiced else 0,
                    "invoiced_at": today if invoiced else "",
                    "qty": rec.get("qty") or meta.get("qty") or "",
                    "meta": meta,
                }
            )

        if jobs_payload:
            try:
                client.sync_jobs(jobs_payload)
            except Exception:
                pass

        for k in keys:
            try:
                client.set_invoiced(job_key=k, invoiced=invoiced, invoiced_at=today)
            except Exception:
                pass
