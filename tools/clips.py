#!/usr/bin/env python3
"""Нарезка длинного эфира на самодостаточные клипы.

Идея взята у OpenMontage (их пайплайн `clip-factory`): из одного длинного
исходника получить N независимых коротких роликов. Код здесь свой, их
лицензия AGPL-3.0 нам не подходит.

Закон контура соблюдён: инструмент НЕ режет сам и не решает, что важно.
Он читает транскрипт, считает по каждому куску измеримые признаки и
выдаёт ранжированный список предложений с таймкодами. Что резать -
решает автор, как и на всей остальной механике.

Что считается по каждому кандидату (всё измеримо, без вкусовщины):

  вход       начинается ли клип с самостоятельной фразы. Кусок,
             стартующий с «и вот», «это», «поэтому», без контекста
             непонятен - зритель улетает на первой секунде.
  финал      заканчивается ли завершённой мыслью, а не на середине.
  контекст   отсылки наружу: «как я говорил», «в прошлом видео»,
             «вот здесь на экране». Такой кусок вне эфира разваливается,
             а визуальные отсылки ещё и требуют картинки, которой в
             клипе не будет.
  темп       слов в секунду: вялый кусок не удержит.
  длина      попадание в формат площадки.
  хук        вопрос, цифра, конфликт или обещание в первых секундах.

    python3 clips.py транскрипт.srt --count 5 --min 25 --max 75
    python3 clips.py words.json --json клипы.json --source эфир.mp4
"""
import argparse
import json
import os
import re
import sys

# Слова-связки: фраза, начатая с них, без предыдущей не читается
OPENING_PENALTY = [
    "и ", "а ", "но ", "это ", "эт ", "вот ", "поэтому", "потому",
    "значит", "то есть", "так вот", "он ", "она ", "они ", "их ",
    "его ", "её ", "ее ", "там ", "тут ", "здесь", "затем", "потом",
    "далее", "дальше", "также", "тоже ", "ещё ", "еще ", "кстати",
]

# Отсылки наружу: клип с ними вне эфира разваливается
CONTEXT_REFS = [
    "как я говорил", "как говорил", "как сказал", "выше", "ранее",
    "в прошлом видео", "в прошлый раз", "мы уже", "напомню",
    "как мы разобрали", "в начале видео", "чуть позже", "дальше покажу",
    "в следующем видео", "как я показывал",
]

# Отсылки к картинке: без визуала клип бессмысленен
VISUAL_REFS = [
    "вот здесь", "вот тут", "смотрите сюда", "как видите", "на экране",
    "вот это окно", "вот эта кнопка", "нажимаем сюда", "видите",
    "покажу на экране", "вот такой", "вот так вот",
]

# Признаки хука в первых секундах
HOOK_MARKERS = [
    "проблема", "ошибка", "секрет", "никто не", "мало кто", "главное",
    "запомните", "важно", "внимание", "представьте", "смотрите что",
    "в чём", "в чем", "почему", "зачем", "как сделать", "что будет",
]

SENT_END = re.compile(r"[.!?]+[\"»)]?\s*$")


def parse_srt(path):
    """SRT -> список реплик со временем."""
    text = open(path, encoding="utf-8-sig").read()
    blocks = re.split(r"\n\s*\n", text.strip())
    out = []
    for b in blocks:
        lines = [l for l in b.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        tm = next((l for l in lines if "-->" in l), None)
        if not tm:
            continue
        a, bb = [x.strip() for x in tm.split("-->")]
        body = " ".join(lines[lines.index(tm) + 1:]).strip()
        if body:
            out.append({"t0": srt_time(a), "t1": srt_time(bb), "text": body})
    return out


def srt_time(s):
    s = s.replace(",", ".").strip()
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_whisper_json(path):
    """Выдача whisper-cli -oj -> список реплик со временем."""
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for s in data.get("transcription", []):
        t = s.get("text", "").strip()
        if not t:
            continue
        off = s["offsets"]
        out.append({"t0": off["from"] / 1000.0, "t1": off["to"] / 1000.0,
                    "text": t})
    return out


def to_sentences(segments):
    """Склеивает реплики в предложения: клип обязан резаться по мысли."""
    sents, cur = [], None
    for s in segments:
        if cur is None:
            cur = {"t0": s["t0"], "t1": s["t1"], "text": s["text"].strip()}
        else:
            cur["t1"] = s["t1"]
            cur["text"] = (cur["text"] + " " + s["text"].strip()).strip()
        if SENT_END.search(cur["text"]):
            sents.append(cur)
            cur = None
    if cur:
        sents.append(cur)
    return sents


def words(t):
    return re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", t)


def has_any(low, needles):
    return [n for n in needles if n in low]


def score_clip(sent_slice):
    """Оценка куска: 0-100 и разбор по осям."""
    text = " ".join(s["text"] for s in sent_slice).strip()
    low = text.lower()
    dur = sent_slice[-1]["t1"] - sent_slice[0]["t0"]
    wc = len(words(text))
    head = sent_slice[0]["text"].strip().lower()
    parts, notes = {}, []

    # вход: самостоятельная ли первая фраза
    bad_open = next((p for p in OPENING_PENALTY if head.startswith(p)), None)
    parts["вход"] = 0 if bad_open else 20
    if bad_open:
        notes.append(f"начинается со связки «{bad_open.strip()}» - без "
                     f"предыдущей фразы непонятно")

    # финал: завершена ли мысль
    parts["финал"] = 15 if SENT_END.search(text) else 0
    if not parts["финал"]:
        notes.append("обрывается на середине мысли")

    # контекст: отсылки наружу
    refs = has_any(low, CONTEXT_REFS)
    parts["контекст"] = max(0, 20 - 7 * len(refs))
    if refs:
        notes.append(f"отсылки к эфиру: {', '.join(refs[:3])}")

    # визуал: без картинки клип не живёт
    vis = has_any(low, VISUAL_REFS)
    parts["визуал"] = max(0, 15 - 5 * len(vis))
    if vis:
        notes.append(f"привязан к картинке: {', '.join(vis[:3])} - "
                     f"нужен исходный кадр или врезка")

    # темп речи
    wps = wc / max(dur, 0.01)
    parts["темп"] = 15 if 2.0 <= wps <= 3.6 else (8 if 1.5 <= wps <= 4.2 else 0)
    if parts["темп"] < 15:
        notes.append(f"темп {wps:.1f} слов/с - {'вяло' if wps < 2 else 'частит'}")

    # хук в первых секундах
    hook_zone = " ".join(s["text"] for s in sent_slice[:2]).lower()[:220]
    hooks = has_any(hook_zone, HOOK_MARKERS)
    digit = bool(re.search(r"\d", hook_zone))
    question = "?" in hook_zone
    parts["хук"] = min(15, 5 * (len(hooks) > 0) + 5 * digit + 5 * question)
    if parts["хук"] == 0:
        notes.append("в первых секундах нет ни вопроса, ни цифры, ни обещания")

    return sum(parts.values()), parts, notes, text, dur, wc


def build_candidates(sents, lo, hi):
    """Все куски по границам предложений, влезающие в формат."""
    out = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j]["t1"] - sents[i]["t0"]
            if dur < lo:
                continue
            if dur > hi:
                break
            score, parts, notes, text, d, wc = score_clip(sents[i:j + 1])
            out.append({"i": i, "j": j, "t0": sents[i]["t0"],
                        "t1": sents[j]["t1"], "duration": round(d, 1),
                        "words": wc, "score": score, "parts": parts,
                        "notes": notes, "text": text})
    return out


def pick(cands, count):
    """Лучшие по баллу, без пересечений: клипы должны быть разными."""
    chosen = []
    for c in sorted(cands, key=lambda x: (-x["score"], x["duration"])):
        if len(chosen) >= count:
            break
        if any(not (c["j"] < o["i"] or c["i"] > o["j"]) for o in chosen):
            continue
        chosen.append(c)
    return sorted(chosen, key=lambda x: x["t0"])


def hhmmss(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="SRT или JSON от whisper-cli -oj")
    ap.add_argument("--count", type=int, default=5, help="сколько предложить")
    ap.add_argument("--min", type=float, default=25.0, help="мин. длина, с")
    ap.add_argument("--max", type=float, default=75.0, help="макс. длина, с")
    ap.add_argument("--source", help="исходное видео - для готовых команд резки")
    ap.add_argument("--json", help="куда сложить план машинным видом")
    a = ap.parse_args()

    path = os.path.abspath(os.path.expanduser(a.transcript))
    if not os.path.exists(path):
        sys.exit(f"транскрипта нет: {path}")
    segs = (parse_whisper_json(path) if path.endswith(".json")
            else parse_srt(path))
    if not segs:
        sys.exit("транскрипт пустой или формат не опознан")

    sents = to_sentences(segs)
    total = segs[-1]["t1"]
    print(f"транскрипт: {os.path.basename(path)}")
    print(f"длительность: {hhmmss(total)} | реплик: {len(segs)} | "
          f"предложений: {len(sents)}")

    cands = build_candidates(sents, a.min, a.max)
    if not cands:
        sys.exit(f"в диапазоне {a.min:.0f}-{a.max:.0f} с кусков не нашлось - "
                 f"поменяй --min/--max")
    print(f"кандидатов в формате {a.min:.0f}-{a.max:.0f} с: {len(cands)}")

    best = pick(cands, a.count)
    print()
    for n, c in enumerate(best, 1):
        print(f"--- клип {n}   {hhmmss(c['t0'])} - {hhmmss(c['t1'])}   "
              f"{c['duration']:.0f} с   балл {c['score']}/100")
        detail = "  ".join(f"{k} {v}" for k, v in c["parts"].items())
        print(f"    {detail}")
        head = c["text"][:150] + ("…" if len(c["text"]) > 150 else "")
        print(f"    «{head}»")
        for note in c["notes"]:
            print(f"    ! {note}")
        if a.source:
            out = f"клип{n}.mp4"
            print(f"    ffmpeg -ss {c['t0']:.2f} -to {c['t1']:.2f} "
                  f"-i '{a.source}' -c:v libx264 -crf 18 -c:a aac {out}")
        print()

    print("Балл считает механику: вход, финал, отсылки, темп, хук.")
    print("Что из этого реально сильное - решает автор. "
          "Инструмент предлагает, не режет.")

    if a.json:
        json.dump({"source": a.source, "transcript": path,
                   "total_duration": round(total, 1),
                   "clips": [{k: c[k] for k in
                              ("t0", "t1", "duration", "words", "score",
                               "parts", "notes", "text")} for c in best]},
                  open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"\nплан: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
