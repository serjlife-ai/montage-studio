#!/usr/bin/env python3
"""Выбор последнего дубля из записи с запинками и повторами.

Задача Сержа 01.09.2026: он пишет врезки подряд, одну фразу может
проговорить три раза - сбился, начал заново, ещё раз. Нужен последний
вариант каждой фразы, остальные выбросить.

Как ищем повторы: не по звуку, а по СЛОВАМ. Два куска речи считаются
дублями одной фразы, если их тексты похожи выше порога. Сравниваем
нормализованные слова (без пунктуации, регистра и слов-паразитов),
похожесть считаем по общим словам с учётом порядка - difflib по
последовательности слов, а не букв: «я собрал второй мозг» и «я собрал
второй мозг ну то есть папку» это один дубль, а «второй мозг» и
«второй ролик» - разные фразы.

Инструмент НЕ режет сам. Он показывает найденные группы дублей и
предлагает план: какие куски взять, какие выбросить. Решает автор -
последний дубль по порядку не всегда лучший по подаче.

    python3 takes.py врезки.mp4 --json дубли.json
    python3 takes.py врезки.mp4 --apply -o чистовик.mp4
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile

# Слова-паразиты: на них фразы не сравниваем, иначе всё похоже на всё
FILLER = {
    "ну", "вот", "это", "как", "бы", "там", "типа", "короче", "значит",
    "то", "есть", "в", "общем", "да", "а", "и", "так", "уже", "просто",
    "эт", "чё", "что", "ща", "сейчас", "давай", "блин", "э", "ээ", "мм",
}

WORD = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+")


def norm_words(text):
    """Слова фразы без паразитов и регистра."""
    ws = [w.lower() for w in WORD.findall(text)]
    return [w for w in ws if w not in FILLER]


def transcribe(src, wd, lang="ru"):
    """Транскрипт с таймкодами фраз."""
    whisper = os.environ.get("WHISPER_CLI", "whisper-cli")
    model = os.environ.get("WHISPER_MODEL", "")
    if not model or not os.path.exists(model):
        for p in (os.path.expanduser("~/tools/whisper.cpp/models/ggml-large-v3-turbo.bin"),
                  os.path.expanduser("~/.config/tg-remote/models/ggml-large-v3-turbo.bin")):
            if os.path.exists(p):
                model = p
                break
    if not model:
        sys.exit("модель whisper не найдена: задай WHISPER_MODEL")

    wav = f"{wd}/a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
    pref = f"{wd}/words"
    subprocess.run([whisper, "-m", model, "-f", wav, "-l", lang,
                    "-oj", "-of", pref, "-np"],
                   check=True, capture_output=True)
    data = json.load(open(pref + ".json"))
    out = []
    for s in data.get("transcription", []):
        t = s.get("text", "").strip()
        if not t:
            continue
        off = s["offsets"]
        out.append({"t0": off["from"] / 1000.0, "t1": off["to"] / 1000.0,
                    "text": t})
    return out


def similarity(a, b):
    """Похожесть двух фраз по словам, 0..1."""
    wa, wb = norm_words(a), norm_words(b)
    if not wa or not wb:
        return 0.0
    # difflib по СПИСКАМ слов: перестановка букв внутри слова роли не играет,
    # а вот порядок слов важен - «мозг второй» и «второй мозг» это одно.
    return difflib.SequenceMatcher(None, wa, wb).ratio()


def group_takes(phrases, threshold, window):
    """Группы дублей: похожие фразы, идущие подряд в пределах окна."""
    groups, used = [], set()
    for i, p in enumerate(phrases):
        if i in used:
            continue
        group = [i]
        for j in range(i + 1, min(i + window + 1, len(phrases))):
            if j in used:
                continue
            # сравниваем с ПЕРВОЙ фразой группы: так три подхода к одной
            # мысли собираются вместе, даже если второй был скомкан
            if similarity(phrases[i]["text"], phrases[j]["text"]) >= threshold:
                group.append(j)
                used.add(j)
        used.add(i)
        if len(group) > 1:
            groups.append(group)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--threshold", type=float, default=0.62,
                    help="порог похожести фраз, 0..1")
    ap.add_argument("--window", type=int, default=4,
                    help="в скольких соседних фразах искать повтор")
    ap.add_argument("--json", help="куда сложить план")
    ap.add_argument("--apply", action="store_true",
                    help="сразу собрать чистовик из последних дублей")
    ap.add_argument("-o", "--output")
    ap.add_argument("--lang", default="ru")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    if not os.path.exists(src):
        sys.exit(f"файла нет: {src}")
    wd = tempfile.mkdtemp(prefix="takes_")

    print(f"файл: {os.path.basename(src)}")
    print("распознаю речь…")
    phrases = transcribe(src, wd, a.lang)
    if not phrases:
        sys.exit("речь не распознана")
    print(f"фраз: {len(phrases)}")

    groups = group_takes(phrases, a.threshold, a.window)
    dropped = {i for g in groups for i in g[:-1]}

    print()
    if not groups:
        print("повторов не нашлось - в записи каждая фраза одна")
    else:
        print(f"групп повторов: {len(groups)}, лишних дублей: {len(dropped)}")
        print()
        for n, g in enumerate(groups, 1):
            print(f"--- повтор {n}: {len(g)} дубля")
            for k, idx in enumerate(g):
                p = phrases[idx]
                mark = "БЕРЁМ  " if k == len(g) - 1 else "выброс "
                txt = p["text"][:88] + ("…" if len(p["text"]) > 88 else "")
                print(f"    {mark} {p['t0']:6.1f}-{p['t1']:6.1f}  «{txt}»")
            print()

    keep = [p for i, p in enumerate(phrases) if i not in dropped]
    total_in = phrases[-1]["t1"] - phrases[0]["t0"]
    total_out = sum(p["t1"] - p["t0"] for p in keep)
    print(f"речи было {total_in:.1f} с, после чистки останется ~{total_out:.1f} с")

    plan = {"source": src,
            "phrases": phrases,
            "groups": [[{"index": i, **phrases[i]} for i in g] for g in groups],
            "keep": keep,
            "dropped_indexes": sorted(dropped)}
    out_json = a.json or f"{os.path.splitext(src)[0]}_дубли.json"
    json.dump(plan, open(out_json, "w"), ensure_ascii=False, indent=2)
    print(f"план: {out_json}")

    print()
    print("Инструмент предлагает ПОСЛЕДНИЙ дубль каждой фразы.")
    print("Лучший по подаче выбирает автор - проверь спорные места глазами.")

    if a.apply:
        out = a.output or f"{os.path.splitext(src)[0]}_чистовик.mp4"
        print(f"\nсобираю чистовик: {out}")
        pieces = []
        for n, p in enumerate(keep):
            piece = f"{wd}/p{n}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-ss", f"{max(p['t0'] - 0.12, 0):.3f}",
                 "-to", f"{p['t1'] + 0.12:.3f}", "-i", src,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", piece],
                check=True)
            pieces.append(piece)
        lst = f"{wd}/list.txt"
        with open(lst, "w") as fh:
            for p in pieces:
                fh.write(f"file '{p}'\n")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
        print(f"готово: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
