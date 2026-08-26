"""Чистка транскрипта от слов-паразитов, эканья и повторов.

Правилами, а не моделью: чистка должна быть предсказуемой и не менять смысл.
Модель, которую попросили «убрать лишнее», заодно перепишет половину фраз.

Главная сложность в том, что почти каждое слово-паразит бывает и осмысленным:
«вот здесь» — указание, «ну и что» — вопрос, «так как» — союз. Поэтому есть
список сочетаний, в которых слово трогать нельзя.
"""

from __future__ import annotations

import re

# Мычание и эканье — эти звуки не значат ничего никогда.
HESITATIONS = {
    "э", "ээ", "эээ", "ээээ", "эм", "эмм", "эммм", "эх",
    "м", "мм", "ммм", "мммм", "мхм", "хм", "хмм",
    "а-а", "а-а-а", "э-э", "э-э-э", "м-м", "м-м-м",
    "ммда", "эээм", "ыы", "ы-ы",
}

# Слова-паразиты. Убираем, только если рядом нет сочетания из PROTECTED.
FILLERS = {
    "вот", "ну", "типа", "короче", "блин", "слушай", "смотри",
    "собственно", "допустим", "скажем", "значит",
}

# Устойчивые обороты-паразиты — вырезаются целиком.
FILLER_PHRASES = [
    "как бы", "это самое", "так сказать", "в общем-то", "в принципе",
    "по сути дела", "если честно", "честно говоря", "скажем так",
    "собственно говоря", "как говорится", "что называется",
    "я не знаю", "ну это", "вот это вот",
]

# Сочетания, где слово из FILLERS осмысленно и должно остаться.
PROTECTED = {
    ("вот", "здесь"), ("вот", "тут"), ("вот", "это"), ("вот", "этот"),
    ("вот", "эта"), ("вот", "эти"), ("вот", "так"), ("вот", "такой"),
    ("вот", "такая"), ("вот", "такие"), ("вот", "почему"), ("вот", "что"),
    ("вот", "он"), ("вот", "она"), ("вот", "они"),
    ("ну", "и"), ("ну", "да"), ("ну", "нет"), ("ну", "ладно"), ("ну", "хорошо"),
    ("ну", "что"), ("ну", "как"),
    ("значит", "что"), ("типа", "того"),
    ("смотри", "какой"), ("смотри", "как"),
}

_WORD = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+(?:-[А-Яа-яЁёA-Za-z0-9]+)*")


def _key(word: str) -> str:
    return word.lower().replace("ё", "е")


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Режет текст на пары «слово, хвост после него» — так проще собирать
    обратно, не теряя знаки препинания."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _WORD.finditer(text):
        if m.start() > pos and tokens:
            tokens[-1] = (tokens[-1][0], tokens[-1][1] + text[pos:m.start()])
        elif m.start() > pos:
            tokens.append(("", text[pos:m.start()]))
        tokens.append((m.group(), ""))
        pos = m.end()
    if pos < len(text) and tokens:
        tokens[-1] = (tokens[-1][0], tokens[-1][1] + text[pos:])
    return tokens


def _drop_phrases(text: str) -> str:
    for phrase in FILLER_PHRASES:
        pattern = r"(?<![А-Яа-яЁёA-Za-z])" + r"[\s,]+".join(
            re.escape(w) for w in phrase.split()
        ) + r"(?![А-Яа-яЁёA-Za-z])"
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return text


def _tidy(text: str) -> str:
    """Приводит в порядок то, что осталось после вырезания слов."""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?;:…])", r"\1", text)
    text = re.sub(r"([,;:])\s*(?=[,.;:!?])", "", text)
    text = re.sub(r"^[\s,;:—–-]+", "", text)
    text = re.sub(r"(?<=[.!?])\s*,", "", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Выброшенное слово могло стоять в начале предложения — возвращаем заглавную
    def upper_first(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()

    text = re.sub(r"(^|[.!?…]\s+)([а-яёa-z])", upper_first, text)
    return text


def strip_fillers(text: str, drop_repeats: bool = True) -> str:
    """Убирает эканье, слова-паразиты и повторы. Смысл не трогает."""
    if not text or not text.strip():
        return text

    working = _drop_phrases(text)
    tokens = _tokenize(working)
    words = [w for w, _ in tokens]
    keep: list[bool] = []

    for i, (word, _tail) in enumerate(tokens):
        if not word:
            keep.append(True)
            continue
        low = _key(word)

        if low in HESITATIONS:
            keep.append(False)
            continue

        if low in FILLERS:
            nxt = _key(words[i + 1]) if i + 1 < len(words) else ""
            prev = _key(words[i - 1]) if i > 0 else ""
            if (low, nxt) in PROTECTED or (prev, low) in PROTECTED:
                keep.append(True)
            else:
                keep.append(False)
            continue

        keep.append(True)

    if drop_repeats:
        previous = ""
        for i, (word, _tail) in enumerate(tokens):
            if not keep[i] or not word:
                continue
            low = _key(word)
            # «мы мы пойдём» → «мы пойдём»; числа и короткие союзы не трогаем
            if low == previous and len(low) > 1 and not low.isdigit():
                keep[i] = False
                continue
            previous = low

    rebuilt = "".join(
        (word + tail) if keep[i] else _keep_punctuation(tail)
        for i, (word, tail) in enumerate(tokens)
    )
    result = _tidy(rebuilt)
    # Если вычистили всё подчистую — лучше вернуть исходное, чем пустоту.
    return result if _WORD.search(result) else text.strip()


def _keep_punctuation(tail: str) -> str:
    """От выброшенного слова оставляем только точку и вопрос: они держат
    границу предложения. Запятая за паразитом уходит вместе с ним."""
    kept = "".join(ch for ch in tail if ch in ".!?…")
    return (kept + " ") if kept else " "


def clean_turns(turns, enabled: bool = True) -> None:
    """Проставляет репликам вычищенный текст, сохраняя исходный в ``raw``."""
    for turn in turns:
        raw = getattr(turn, "raw", None) or turn.text
        turn.raw = raw
        turn.text = strip_fillers(raw) if enabled else raw
