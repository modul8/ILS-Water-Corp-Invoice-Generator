import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from services.settings_store import SettingsStore


class StateStore:
    """
    Shared state for all modules:
      - completed
      - invoiced
      - draft_invoice_id (optional)
      - qty/unit/meta (for worklist-based modules)

    Stored in one JSON file in the app folder so the Invoicing screen can see everything.
    """

    def __init__(self, settings_store: SettingsStore) -> None:
        self.settings_store = settings_store
        self.path: Path = settings_store.app_dir / "v2_state.json"
        self._data: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self.path.exists():
            try:
                obj = json.loads(self.path.read_text(encoding="utf-8"))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _reload(self) -> None:
        self._data = self._load()

    def get(self, key: str) -> Dict[str, Any]:
        rec = self._data.get(key)
        return rec if isinstance(rec, dict) else {}

    def upsert(self, key: str, record: Dict[str, Any]) -> None:
        self._reload()
        self._data[key] = record
        self.save()

    def set_dirty(self, key: str, dirty: bool = True) -> None:
        self._reload()
        rec = self.get(key).copy()
        if dirty:
            rec["_dirty"] = True
        else:
            rec.pop("_dirty", None)
            rec.pop("_dirty_at", None)
        self._data[key] = rec
        self.save()

    def clear_dirty(self, key: str) -> None:
        self.set_dirty(key, False)

    def iter_dirty(self, *, module: str | None = None) -> Iterable[Tuple[str, Dict[str, Any]]]:
        for k, v in self._data.items():
            if not isinstance(v, dict):
                continue
            if not v.get("_dirty"):
                continue
            if module and v.get("module") != module:
                continue
            yield k, v

    def delete(self, key: str) -> None:
        self._reload()
        if key in self._data:
            self._data.pop(key, None)
            self.save()

    def set_flags(
        self,
        key: str,
        *,
        completed: bool | None = None,
        invoiced: bool | None = None,
        draft_invoice_id: int | None = None,
    ) -> None:
        self._reload()
        rec = self.get(key).copy()

        if completed is not None:
            rec["completed"] = bool(completed)

        if invoiced is not None:
            rec["invoiced"] = bool(invoiced)

        # draft_invoice_id behavior:
        # - None  => leave as-is
        # - <=0   => delete field
        # - >0    => set to that integer
        if draft_invoice_id is not None:
            if int(draft_invoice_id) <= 0:
                rec.pop("draft_invoice_id", None)
            else:
                rec["draft_invoice_id"] = int(draft_invoice_id)

        self._data[key] = rec
        self.save()

    def iter_records(self) -> Iterable[Tuple[str, Dict[str, Any]]]:
        for k, v in self._data.items():
            if isinstance(v, dict):
                yield k, v

    def pending_to_invoice(self) -> list[tuple[str, Dict[str, Any]]]:
        """
        Only show jobs that are:
          - completed
          - not invoiced
          - AND have qty > 0 (qty entered when job completed)
        """
        chosen: dict[tuple, tuple[str, Dict[str, Any]]] = {}
        for k, rec in self.iter_records():
            if not bool(rec.get("completed")):
                continue

            qty = rec.get("qty", 0) or 0
            try:
                if float(qty) <= 0:
                    continue
            except Exception:
                continue

            meta = rec.get("meta") or {}
            sig = (
                rec.get("module", ""),
                meta.get("sheet") or meta.get("catchment") or "",
                meta.get("drain") or meta.get("location") or meta.get("item") or "",
                meta.get("work_order") or "",
                meta.get("po") or "",
                str(qty),
                rec.get("unit") or "",
            )

            existing = chosen.get(sig)
            if not existing:
                chosen[sig] = (k, rec)
                continue

            _, existing_rec = existing
            if bool(existing_rec.get("invoiced")):
                # keep invoiced-true record
                continue
            if bool(rec.get("invoiced")):
                chosen[sig] = (k, rec)

        out: list[tuple[str, Dict[str, Any]]] = []
        for k, rec in chosen.values():
            if bool(rec.get("invoiced")):
                continue
            out.append((k, rec))
        return out
