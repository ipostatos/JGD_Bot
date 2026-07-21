"""Append-only audit log: JSONL с sha256-цепочкой.

Каждая запись хранит hash предыдущей — модификация истории ломает цепочку,
verify() это обнаруживает. Каталог audit/ в .gitignore (реальные данные).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

GENESIS = "0" * 64


class Audit:
    def __init__(self, path: Path | None = None):
        self.path = path or (ROOT / "audit" / "audit.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = None
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last)["hash"] if last else GENESIS

    def log(self, event: str, data: dict, actor: str = "hermes") -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "event": event,
            "data": data,
            "prev": self._last_hash(),
        }
        payload = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
        entry["hash"] = hashlib.sha256(payload.encode()).hexdigest()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return entry["hash"]

    def verify(self) -> tuple[bool, int]:
        """Проверка целостности цепочки. -> (ok, количество записей)."""
        prev, n = GENESIS, 0
        if not self.path.exists():
            return True, 0
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                stored_hash = entry.pop("hash")
                if entry["prev"] != prev:
                    return False, n
                payload = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
                if hashlib.sha256(payload.encode()).hexdigest() != stored_hash:
                    return False, n
                prev, n = stored_hash, n + 1
        return True, n
