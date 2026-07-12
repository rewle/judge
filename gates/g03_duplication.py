"""Гейт 03 — дублирование. v0: эвристика по пересечению слов (детерминированная,
без внешних вызовов, всегда включена). v1: опциональный semantic-пас через
judge для пар, которые v0 не поймал (одна функция, разные слова) — только
если judge доступен (api или cli), иначе гейт остаётся v0-only, как раньше.
См. docs/03_duplication.md."""
import os
import re
from pathlib import Path

import yaml

from cache_store import content_hash, load_cache, save_cache
from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill
from judge_client import JudgeNotConfigured, get_client

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
CACHE_PATH = Path(__file__).parent.parent / "history" / "duplication_cache.json"
STOPWORDS = {"и", "в", "на", "с", "по", "для", "не", "из", "или", "как", "the", "a", "to", "of", "and"}


def _cache_key(model: str, hash_a: str, hash_b: str) -> str:
    # Симметрично относительно пары (verdict не зависит от того, кто A, кто
    # B — judge получает оба текста), model в ключе — разные модели могут
    # дать разный вердикт, сравнивать между ними некорректно (тот же
    # принцип, что backend в ключе истории гейта 04).
    a, b = sorted([hash_a, hash_b])
    return f"{model}:{a}:{b}"

_DUP_TOOL = {
    "name": "submit_duplication_verdict",
    "description": (
        "Оценить, решают ли два скилла одну и ту же задачу для пользователя "
        "по существу — даже если написаны разными словами, на разных языках "
        "или с разной структурой."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "functionally_duplicate": {"type": "boolean"},
            "similarity": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0 — про разное, 1 — решают одну и ту же задачу тем же способом",
            },
            "reason": {"type": "string"},
        },
        "required": ["functionally_duplicate", "similarity", "reason"],
    },
}

DUP_JUDGE_SYS = (
    "Ты проверяешь реестр skill-документов на функциональное дублирование. "
    "Тебя не обманывает разница в словах, языке или структуре текста — важно, "
    "решает ли скилл B ту же задачу пользователя, что и скилл A. Оцени честно."
)


def _tokenize(text: str) -> set:
    words = re.findall(r"[а-яa-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _judge_pair(client, model: str, own_text: str, other_text: str) -> dict:
    user = f"СКИЛЛ A:\n<<<\n{own_text}\n>>>\n\nСКИЛЛ B:\n<<<\n{other_text}\n>>>"
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=DUP_JUDGE_SYS,
        messages=[{"role": "user", "content": user}],
        tools=[_DUP_TOOL],
        tool_choice={"type": "tool", "name": "submit_duplication_verdict"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("judge не вернул tool_use блок с вердиктом о дублировании")


def check(skill_path: Path, registry_dir: Path = None) -> GateResult:
    frontmatter, body, text = _read_skill(skill_path)
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter", {})

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = config["thresholds"]
    lexical_threshold = thresholds["duplication_similarity"]
    prefilter = thresholds.get("duplication_semantic_prefilter", 0.05)
    semantic_threshold = thresholds.get("duplication_semantic_similarity", 0.8)
    registry_dir = registry_dir or (Path(__file__).parent.parent / config["paths"]["registry_dir"])

    own_tokens = _tokenize((frontmatter.get("description", "") or "") + " " + (body or ""))

    lexical_matches = []
    # Кандидаты на семантическую проверку: v0 их не поймал, но и не совсем
    # непохожи лексически — дешёвый способ не звать judge на явно чужие темы.
    semantic_candidates = []
    for other_dir in sorted(registry_dir.iterdir()):
        if not other_dir.is_dir() or other_dir.resolve() == skill_path.resolve():
            continue
        other_fm, other_body, other_text = _read_skill(other_dir)
        if other_fm is None:
            continue
        other_tokens = _tokenize((other_fm.get("description", "") or "") + " " + (other_body or ""))
        sim = _jaccard(own_tokens, other_tokens)
        if sim >= lexical_threshold:
            lexical_matches.append((other_dir.name, round(sim, 3)))
        elif sim >= prefilter:
            semantic_candidates.append((other_dir.name, other_text, round(sim, 3)))

    if lexical_matches:
        details_str = ", ".join(f"{name} (sim={sim})" for name, sim in lexical_matches)
        return GateResult(
            FAIL,
            f"похож на существующие скиллы: {details_str} (порог {lexical_threshold})",
            {"lexical_matches": lexical_matches},
        )

    details = {"lexical_matches": [], "semantic_checked": [c[0] for c in semantic_candidates]}

    if not semantic_candidates:
        return GateResult(
            PASS,
            "дублей выше лексического порога не найдено, семантических кандидатов нет",
            details,
        )

    try:
        client = get_client()
    except JudgeNotConfigured:
        details["note"] = "v1 (семантическая проверка) пропущена — judge не настроен"
        return GateResult(
            PASS,
            f"v0 чист; {len(semantic_candidates)} лексически-близких кандидатов не проверены "
            f"семантически (нет judge)",
            details,
        )

    judge_cfg = config.get("judge", {})
    model = os.environ.get("JUDGE_MODEL", judge_cfg.get("model", "claude-sonnet-5"))

    cache = load_cache(CACHE_PATH)
    own_hash = content_hash(text)
    cache_dirty = False

    semantic_matches = []
    verdicts = {}
    cache_hits = 0
    for name, other_text, lexical_sim in semantic_candidates:
        key = _cache_key(model, own_hash, content_hash(other_text))
        cached = cache.get(key)
        if cached is not None:
            verdict = dict(cached)
            cache_hits += 1
        else:
            fresh = _judge_pair(client, model, text, other_text)
            cache[key] = fresh
            cache_dirty = True
            verdict = dict(fresh)
        # lexical_sim — на копии, не на объекте в кэше: значение направленное
        # (зависит от того, кто A), а ключ кэша симметричный.
        verdict["lexical_sim"] = lexical_sim
        verdicts[name] = verdict
        if verdict["functionally_duplicate"] and verdict["similarity"] >= semantic_threshold:
            semantic_matches.append((name, verdict))

    if cache_dirty:
        save_cache(CACHE_PATH, cache)

    details["semantic_verdicts"] = verdicts
    details["cache_hits"] = cache_hits

    if semantic_matches:
        # reason судьи — прямо в сообщении, не только в details: иначе
        # человек, смотрящий на голый вывод run_gates.py, видит имя+число
        # без единого слова объяснения, почему это дубль (см. ревью).
        details_str = "; ".join(
            f"{name} (similarity={v['similarity']}): {v['reason']}" for name, v in semantic_matches
        )
        return GateResult(
            FAIL,
            f"функциональный дубль (v1, разные слова — одна задача): {details_str} "
            f"(порог {semantic_threshold})",
            details,
        )
    return GateResult(
        PASS,
        f"дублей не найдено (v0 + семантическая проверка v1, {model}, "
        f"{len(semantic_candidates)} кандидатов проверено, {cache_hits} из кэша)",
        details,
    )
