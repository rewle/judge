"""Дешёвый JSON-кэш по хэшу контента — переиспользуется гейтом 03 (semantic-
дубли) и гейтом 07 (токен-бюджет цепочки), чтобы не пересчитывать judge/
count_tokens-вызовы при неизменных текстах скиллов между прогонами. Не
инвалидируется по времени — только по содержимому (hash меняется вместе с
текстом), поэтому staleness не проблема."""
import hashlib
import json
from pathlib import Path


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
