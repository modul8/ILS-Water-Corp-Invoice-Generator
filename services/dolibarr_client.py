from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
import requests


def _parse_id_from_response(resp: requests.Response) -> int:
    txt = (resp.text or "").strip()

    try:
        j = resp.json()
        if isinstance(j, int):
            return int(j)
        if isinstance(j, str) and j.strip().isdigit():
            return int(j.strip())
        if isinstance(j, dict) and "id" in j and str(j["id"]).isdigit():
            return int(j["id"])
    except Exception:
        pass

    if txt.isdigit():
        return int(txt)

    raise RuntimeError(f"Could not parse ID from response:\n{txt[:500]}")


class DolibarrClient:
    """
    Dolibarr API helper.
    Uses form-encoded POSTs (data=payload) because that matches your known-working V1 behavior.
    Retries with query-param DOLAPIKEY if needed.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()

    def api_root(self) -> str:
        # Accept either:
        #  - https://host/dolibarr
        #  - https://host/dolibarr/api/index.php
        if self.base_url.lower().endswith("/api/index.php"):
            return self.base_url
        return f"{self.base_url}/api/index.php"

    def _headers(self) -> Dict[str, str]:
        return {
            "DOLAPIKEY": self.api_key,
            "Accept": "application/json",
            "User-Agent": "IlsWaterCorpV2/1.0",
        }

    def _request(self, method: str, url: str, *, data: Optional[Dict[str, Any]] = None,
                 timeout: int = 30) -> requests.Response:
        # First try: header auth
        r = requests.request(method, url, headers=self._headers(), data=data, timeout=timeout)

        # Some hosts/WAF setups behave differently with query-param auth
        if r.status_code in (401, 403):
            r2 = requests.request(
                method,
                url,
                headers=self._headers(),
                params={"DOLAPIKEY": self.api_key},
                data=data,
                timeout=timeout,
            )
            return r2

        return r

    def test_connection(self) -> Tuple[bool, str]:
        # Use an endpoint you already know works in your environment:
        url = f"{self.api_root()}/invoices?limit=1"
        try:
            r = self._request("GET", url, timeout=10)
            if r.status_code == 200:
                return True, "OK"
            return False, f"HTTP {r.status_code}: {(r.text or '')[:250]}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_clients(self, *, limit: int = 500) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch third parties (customers) from Dolibarr.
        Returns (ok, list, message).
        """
        url = f"{self.api_root()}/thirdparties"
        try:
            r = requests.request(
                "GET",
                url,
                headers=self._headers(),
                params={"limit": str(limit)},
                timeout=20,
            )
            if r.status_code in (401, 403):
                r = requests.request(
                    "GET",
                    url,
                    headers=self._headers(),
                    params={"DOLAPIKEY": self.api_key, "limit": str(limit)},
                    timeout=20,
                )
            if r.status_code != 200:
                return False, [], f"HTTP {r.status_code}: {(r.text or '')[:250]}"
            data = r.json()
            if isinstance(data, dict) and "data" in data:
                data = data.get("data")
            if not isinstance(data, list):
                return False, [], "Unexpected response format."
            return True, data, "OK"
        except Exception as e:
            return False, [], f"{type(e).__name__}: {e}"

    def create_draft_invoice(
        self,
        *,
        socid: str,
        note_public: str = "",
        ref_customer: str = "",
    ) -> int:
        """
        POST /invoices  (draft)
        Returns invoice_id (int)
        """
        url = f"{self.api_root()}/invoices"
        payload: Dict[str, Any] = {
            "socid": str(socid),
            "type": "0",
        }
        if note_public:
            payload["note_public"] = note_public
        if ref_customer:
            payload["ref_customer"] = ref_customer

        r = self._request("POST", url, data=payload, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Create invoice failed: HTTP {r.status_code}\n{(r.text or '')[:800]}")
        return _parse_id_from_response(r)

    def add_invoice_line(
        self,
        *,
        invoice_id: int,
        desc: str,
        qty: float,
        subprice: float,
        tva_tx: float,
        product_type: str = "1",
    ) -> None:
        """
        POST /invoices/{id}/lines
        """
        url = f"{self.api_root()}/invoices/{invoice_id}/lines"
        payload: Dict[str, Any] = {
            "desc": desc,
            "qty": f"{float(qty):.2f}",
            "subprice": f"{float(subprice):.2f}",
            "tva_tx": f"{float(tva_tx):.2f}",
            "product_type": product_type,
        }

        r = self._request("POST", url, data=payload, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Add line failed: HTTP {r.status_code}\n{(r.text or '')[:800]}")
