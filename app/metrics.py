"""Насколько хорошо разобрана запись: ошибки в словах и ошибки в спикерах.

Числа из чужих статей про качество распознавания почти ничего не говорят о
конкретных созвонах: разброс между записями (микрофон, комната, сколько людей
перебивают друг друга) больше, чем разброс между моделями. Поэтому здесь
считаются две вещи на своих же записях:

* **WER** — доля ошибок в словах: вставки, пропуски и замены к числу слов
  эталона. Классическая мера распознавания.
* **WDER** — доля слов, которые распознаны верно, но подписаны не тем
  человеком. Именно это человек и замечает в расшифровке: текст правильный, а
  реплика приписана соседу.

Второе считается по словам, а не по времени, намеренно. Стандартная мера
диаризации (DER) считает секунды и щедро прощает короткие реплики — а в
расшифровке короткая реплика это целая строка с чужим именем.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import permutations

# Слова сравниваем без пунктуации и регистра: «Ирина,» и «ирина» — одно слово.
# «ё» сводим к «е» — в расшифровках она ставится через раз, и считать это
# ошибкой значило бы мерить не распознавание, а привычки набора.
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> list[str]:
    """Текст → список слов для сравнения."""
    low = unicodedata.normalize("NFKC", str(text or "")).lower().replace("ё", "е")
    low = _PUNCT.sub(" ", low)
    return [word for word in _SPACE.split(low) if word]


@dataclass
class Word:
    """Слово с именем сказавшего — минимальная единица сравнения."""
    text: str
    who: str


def words_of(turns) -> list[Word]:
    """Реплики → плоский список слов. Реплика может быть любой парой
    «кто сказал, что сказал»: и наша `Turn`, и строка эталона."""
    out: list[Word] = []
    for turn in turns:
        who = str(getattr(turn, "who", None) or getattr(turn, "speaker", "") or "")
        text = getattr(turn, "text", "") if not isinstance(turn, (list, tuple)) else turn[1]
        if isinstance(turn, (list, tuple)):
            who = str(turn[0])
        for word in normalise(text):
            out.append(Word(word, who))
    return out


def align(ref: list[Word], hyp: list[Word]) -> list[tuple[int | None, int | None]]:
    """Выравнивание Левенштейна по словам.

    Возвращает пары индексов: (i, j) — слово эталона i соответствует слову
    ответа j; (i, None) — пропущено; (None, j) — придумано. Таблица считается
    по строке, а путь восстанавливается по сохранённым ходам: для часовой
    записи это десятки тысяч слов, и держать в памяти всю матрицу расстояний
    незачем.
    """
    n, m = len(ref), len(hyp)
    # Ходы храним по одному биту смысла на клетку: откуда пришли.
    moves: list[bytearray] = []
    row = list(range(m + 1))
    for i in range(1, n + 1):
        prev, row = row, [i] + [0] * m
        line = bytearray(m + 1)
        line[0] = 2                                  # пришли сверху — пропуск
        for j in range(1, m + 1):
            same = ref[i - 1].text == hyp[j - 1].text
            best = prev[j - 1] + (0 if same else 1)  # совпадение или замена
            move = 0 if same else 1
            if prev[j] + 1 < best:
                best, move = prev[j] + 1, 2          # пропущено
            if row[j - 1] + 1 < best:
                best, move = row[j - 1] + 1, 3       # придумано
            row[j] = best
            line[j] = move
        moves.append(line)

    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 and j > 0:
        move = moves[i - 1][j]
        if move in (0, 1):
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif move == 2:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    while i > 0:
        pairs.append((i - 1, None))
        i -= 1
    while j > 0:
        pairs.append((None, j - 1))
        j -= 1
    pairs.reverse()
    return pairs


def _best_names(pairs, ref: list[Word], hyp: list[Word]) -> dict[str, str]:
    """Кто из ответа кем был в эталоне.

    Имена спикеров в ответе свои («S2»), в эталоне свои («Ирина»), и сравнивать
    их напрямую нельзя — сначала нужно понять, кто есть кто. Берём сочетание,
    при котором совпадает больше всего слов: для горстки голосов это перебор,
    для большего числа — жадный проход, чтобы не ждать факториал.
    """
    counts: dict[tuple[str, str], int] = {}
    for i, j in pairs:
        if i is None or j is None or ref[i].text != hyp[j].text:
            continue
        key = (hyp[j].who, ref[i].who)
        counts[key] = counts.get(key, 0) + 1
    theirs = sorted({k[0] for k in counts})
    ours = sorted({k[1] for k in counts})
    if not theirs or not ours:
        return {}
    if len(theirs) <= 7 and len(ours) <= 7:
        # Голосов в ответе может быть больше, чем в эталоне (разделение
        # раздробило человека) или меньше (двоих слило в одного). Недостающую
        # сторону добиваем пустышками: тогда любой голос ответа получает либо
        # имя, либо ничего, и оба перекоса считаются одинаково честно.
        names = ours + [f"\0{n}" for n in range(max(0, len(theirs) - len(ours)))]
        best, mapping = -1, {}
        for order in permutations(names, len(theirs)):
            pick = dict(zip(theirs, order))
            hits = sum(counts.get(pair, 0) for pair in pick.items())
            if hits > best:
                best, mapping = hits, pick
        return mapping
    # Много голосов: отдаём каждому самое частое совпадение, занятые пропускаем.
    mapping, taken = {}, set()
    for (theirs_name, ours_name), _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        if theirs_name in mapping or ours_name in taken:
            continue
        mapping[theirs_name] = ours_name
        taken.add(ours_name)
    return mapping


@dataclass
class Score:
    words: int          # слов в эталоне
    wrong: int          # замен
    missed: int         # пропущено
    invented: int       # придумано
    wrong_who: int      # слово верное, а подписано не тем
    matched: int        # слов совпало
    speakers_ref: int
    speakers_hyp: int
    names: dict         # кто из ответа кем оказался в эталоне

    @property
    def wer(self) -> float:
        return (self.wrong + self.missed + self.invented) / max(1, self.words)

    @property
    def wder(self) -> float:
        """Доля верно распознанных слов, подписанных не тем человеком."""
        return self.wrong_who / max(1, self.matched)

    def line(self) -> str:
        return (f"WER {self.wer * 100:5.1f}%  "
                f"WDER {self.wder * 100:5.1f}%  "
                f"замен {self.wrong}, пропущено {self.missed}, "
                f"придумано {self.invented}, "
                f"спикеров {self.speakers_hyp} против {self.speakers_ref}")


def score(reference, hypothesis) -> Score:
    """Сравнивает разбор с эталоном. На вход — две последовательности реплик."""
    ref = words_of(reference)
    hyp = words_of(hypothesis)
    pairs = align(ref, hyp)
    names = _best_names(pairs, ref, hyp)
    wrong = missed = invented = wrong_who = matched = 0
    for i, j in pairs:
        if i is None:
            invented += 1
        elif j is None:
            missed += 1
        elif ref[i].text != hyp[j].text:
            wrong += 1
        else:
            matched += 1
            if names.get(hyp[j].who, hyp[j].who) != ref[i].who:
                wrong_who += 1
    return Score(
        words=len(ref), wrong=wrong, missed=missed, invented=invented,
        wrong_who=wrong_who, matched=matched,
        speakers_ref=len({w.who for w in ref if w.who}),
        speakers_hyp=len({w.who for w in hyp if w.who}),
        names=names,
    )
