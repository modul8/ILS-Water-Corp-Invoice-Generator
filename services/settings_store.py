import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import keyring


class SettingsStore:
    """
    Stores non-secret settings in a JSON file under LocalAppData.
    Stores the API key securely using OS keyring (Windows Credential Manager).
    """

    SERVICE_NAME = "DrainInvoiceAppV2"
    API_KEY_ENTRY = "dolibarr_api_key"

    def __init__(self) -> None:
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home())
        self.app_dir = Path(base) / "DrainInvoiceAppV2"
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.app_dir / "settings.json"

    def load_settings(self) -> Dict[str, Any]:
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def save_settings(self, settings: Dict[str, Any]) -> None:
        self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def set_api_key(self, api_key: str) -> None:
        api_key = (api_key or "").strip()
        if not api_key:
            # If user clears it, remove it.
            try:
                keyring.delete_password(self.SERVICE_NAME, self.API_KEY_ENTRY)
            except Exception:
                pass
            return
        keyring.set_password(self.SERVICE_NAME, self.API_KEY_ENTRY, api_key)

    def get_api_key(self) -> str:
        try:
            return keyring.get_password(self.SERVICE_NAME, self.API_KEY_ENTRY) or ""
        except Exception:
            return ""

    def is_ready(self, settings: Dict[str, Any]) -> bool:
        """
        "Ready" means we have:
          - work_list_path
          - base_url
          - socid
          - api key in keyring
        """
        work_list = (settings.get("work_list_path") or "").strip()
        base_url = (settings.get("dolibarr_base_url") or "").strip()
        socid = (settings.get("dolibarr_socid") or "").strip()
        api_key = self.get_api_key().strip()

        if not work_list or not Path(work_list).exists():
            return False
        if not base_url:
            return False
        if not socid:
            return False
        if not api_key:
            return False
        return True
