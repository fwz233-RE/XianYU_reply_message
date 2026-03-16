import json
import os
import threading
from typing import List, Dict
from project_paths import get_data_file_path, resolve_project_path


class QuickReplyStore:
    def __init__(self, path: str | None = None):
        default_path = get_data_file_path("quick_replies.json")
        self.path = str(resolve_project_path(path or str(default_path)))
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        folder = os.path.dirname(self.path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        if not os.path.exists(self.path):
            self._write(
                [
                    {"id": "qr1", "text": "你好，在的，商品还在。"},
                    {"id": "qr2", "text": "可以小刀，您心里价位多少？"},
                    {"id": "qr3", "text": "支持同城自提，也可发快递。"},
                ]
            )

    def _read(self) -> List[Dict]:
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        for row in data:
            if isinstance(row, dict) and "id" in row and "text" in row:
                result.append({"id": str(row["id"]), "text": str(row["text"])})
        return result

    def _write(self, data: List[Dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list(self) -> List[Dict]:
        with self._lock:
            return self._read()

    def replace(self, items: List[Dict]) -> List[Dict]:
        normalized = []
        for idx, row in enumerate(items):
            text = str(row.get("text", "")).strip() if isinstance(row, dict) else ""
            if not text:
                continue
            rid = str(row.get("id", f"qr{idx+1}")) if isinstance(row, dict) else f"qr{idx+1}"
            normalized.append({"id": rid, "text": text})
        with self._lock:
            self._write(normalized)
            return normalized
