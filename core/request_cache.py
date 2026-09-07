"""Content-addressed, success-only response cache and atomic JSON storage."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def fingerprint(value) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, default=str)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RequestCache:
    def __init__(self, directory: str, stage: str):
        self.directory = Path(directory) / "responses" / stage if directory else None

    def load(self, key: str):
        if self.directory is None:
            return None
        try:
            data = json.loads((self.directory / f"{key}.json").read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("fingerprint") == key:
                return data.get("result")
        except (OSError, ValueError):
            pass
        return None

    def save(self, key: str, result) -> None:
        if self.directory is not None:
            write_json(self.directory / f"{key}.json", {"fingerprint": key, "result": result})
