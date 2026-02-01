from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import requests
import certifi
from requests.exceptions import SSLError, RequestException


@dataclass
class UpdateInfo:
    version: str
    url: str
    notes: str = ""


def _parse_version(v: str) -> tuple[int, int, int]:
    parts = (v or "").strip().split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except Exception:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def fetch_update_info_with_error(url: str, *, timeout: int = 8) -> tuple[UpdateInfo | None, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, timeout=timeout, verify=certifi.where(), headers=headers)
    except SSLError:
        try:
            r = requests.get(url, timeout=timeout, verify=False, headers=headers)
        except RequestException as e:
            return None, str(e)
    except RequestException as e:
        return None, str(e)

    if r.status_code >= 300:
        return None, f"HTTP {r.status_code}"
    try:
        data: Dict[str, Any] = r.json()
    except Exception:
        return None, "Invalid JSON"

    version = str(data.get("version") or "").strip()
    dl = str(data.get("url") or "").strip()
    notes = str(data.get("notes") or "").strip()
    if not version or not dl:
        return None, "Missing version or url"
    return UpdateInfo(version=version, url=dl, notes=notes), ""


def fetch_update_info(url: str, *, timeout: int = 8) -> UpdateInfo | None:
    info, _ = fetch_update_info_with_error(url, timeout=timeout)
    return info
