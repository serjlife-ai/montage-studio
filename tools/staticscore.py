#!/usr/bin/env python3
"""Оценка статичности готового ролика числом, а не на глаз.

Идея взята у OpenMontage (их `lib/slideshow_risk.py`): перед сдачей
посчитать риск, что вышел «анимированный PowerPoint», и не пускать такое
в публикацию. У них шесть осей под генеративное видео. Нам нужны другие:
мы монтируем реальную съёмку говорящего человека, и наш враг - не
«декоративные картинки», а неподвижная говорящая голова без единого
события в кадре. Код и оси свои, их лицензия AGPL-3.0 нам не подходит.

Меряем по готовому файлу четыре оси, каждая 0-5, больше - хуже:

  cuts_rate       склеек в минуту. Ноль склеек на минуту речи - зритель
                  улетает: в вертикальном коротком видео глазу нужно
                  событие каждые несколько секунд.
  frame_change    насколько меняется картинка между кадрами. Считаем
                  среднюю разницу соседних кадров: статичный штатив с
                  говорящей головой даёт почти ноль.
  longest_static  самый долгий кусок без заметного изменения кадра.
                  Одна «мёртвая» зона в 12 секунд убивает удержание
                  вернее, чем ровная посредственность по всей длине.
  audio_flat      ровность громкости. Речь без динамики усыпляет;
                  меряем разброс уровня по фразам.

Вердикт по среднему: <1.5 живой, <2.5 нормально, <3.5 переделать,
дальше - не публиковать без правки.

Инструмент НЕ решает, смешно ли и попадает ли ритм - это зона автора.
Он ловит ровно одно: механическую неподвижность, которую видно числом.

    python3 staticscore.py готовый.mp4 [--json отчёт.json]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

STEP = 0.02
SAMPLE_FPS = 4        # столько кадров в секунду тянем на анализ картинки
STATIC_EPS = 2.0      # разница кадров ниже этой считается «ничего не меняется»


def scene_cuts(src):
    """Склейки по детектору смены сцены ffmpeg."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", src, "-vf",
         "select='gt(scene,0.25)',metadata=print:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    return [float(m.split("pts_time:")[1].split()[0])
            for m in p.stdout.splitlines() if "pts_time:" in m]


def frame_motion(src, wd):
    """Средняя разница соседних кадров и самый долгий статичный кусок.

    Тянем маленькие серые кадры: для оценки движения разрешение не нужно,
    а полноразмерный декод вертикали 1080x1920 съедает память.
    """
    out = f"{wd}/f%05d.pgm"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                    "-vf", f"fps={SAMPLE_FPS},scale=64:114,format=gray",
                    out], check=True)
    files = sorted(f for f in os.listdir(wd) if f.endswith(".pgm"))
    if len(files) < 3:
        return 0.0, 0.0, []
    frames = []
    for f in files:
        with open(os.path.join(wd, f), "rb") as fh:
            data = fh.read()
        # PGM: три заголовочные строки, дальше байты пикселей
        parts = data.split(b"\n", 3)
        frames.append(np.frombuffer(parts[3], dtype=np.uint8).astype(np.float32))
    diffs = [float(np.abs(frames[i] - frames[i - 1]).mean())
             for i in range(1, len(frames))]
    # самый долгий кусок, где картинка почти не меняется
    longest, run = 0, 0
    for d in diffs:
        run = run + 1 if d < STATIC_EPS else 0
        longest = max(longest, run)
    return float(np.mean(diffs)), longest / SAMPLE_FPS, diffs


def audio_spread(src, wd):
    """Разброс громкости по фразам - та же метрика, что в preflight."""
    wav = f"{wd}/a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
    w = wave.open(wav)
    sr, n = w.getframerate(), w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(sr * STEP)
    F = len(a) // hop
    if F < 10:
        return 0.0
    rms = np.sqrt(np.maximum(np.mean(a[:F * hop].reshape(F, hop) ** 2, axis=1), 1e-12))
    db = 20 * np.log10(rms)
    floor, loud = np.percentile(db, 5), np.percentile(db, 90)
    th = floor + 0.5 * (loud - floor)
    k = 9
    padded = np.pad(db, k // 2, mode="edge")
    peak = np.array([padded[i:i + k].max() for i in range(len(db))])
    speech = peak > th
    levels, i = [], 0
    while i < len(db):
        if speech[i]:
            j = i
            while j < len(db) and speech[j]:
                j += 1
            if (j - i) * STEP >= 0.4:
                levels.append(float(np.median(db[i:j])))
            i = j
        else:
            i += 1
    if len(levels) < 4:
        return 0.0
    return float(np.percentile(levels, 90) - np.percentile(levels, 10))


def scale(value, good, bad):
    """Линейная шкала 0-5: good -> 0, bad -> 5."""
    if bad == good:
        return 0.0
    v = (value - good) / (bad - good) * 5
    return float(max(0.0, min(5.0, v)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--json")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", src], capture_output=True, text=True,
        check=True).stdout.strip())

    wd = tempfile.mkdtemp(prefix="static_")
    cuts = scene_cuts(src)
    motion, static_run, _ = frame_motion(src, wd)
    spread = audio_spread(src, wd)
    cpm = len(cuts) / max(dur / 60, 0.01)

    dims = {
        "cuts_rate": (scale(cpm, 20, 2),
                      f"склеек в минуту: {cpm:.0f}"),
        "frame_change": (scale(motion, 6.0, 0.5),
                         f"изменение кадра: {motion:.1f}"),
        "longest_static": (scale(static_run, 2.0, 12.0),
                           f"самый долгий статичный кусок: {static_run:.1f} с"),
        "audio_flat": (scale(spread, 12.0, 3.0),
                       f"разброс громкости фраз: {spread:.0f} dB"),
    }
    avg = sum(v[0] for v in dims.values()) / len(dims)
    verdict = ("живой" if avg < 1.5 else "нормально" if avg < 2.5
               else "переделать" if avg < 3.5 else "не публиковать")

    print(f"файл: {os.path.basename(src)}  ({dur:.1f} с)")
    print()
    for name, (score, why) in dims.items():
        bar = "#" * int(round(score)) + "." * (5 - int(round(score)))
        print(f"  {name:<16} {score:>4.1f}  {bar}  {why}")
    print()
    print(f"средний балл: {avg:.1f} из 5 (меньше - живее)")
    print(f"вердикт: {verdict}")
    if avg >= 2.5:
        print()
        print("что делать: добавить катов и микро-зумов (tools/dynamic.py), "
              "врезки и графику поверх (скилл hyperframes)")

    if a.json:
        json.dump({"file": src, "duration": round(dur, 1),
                   "dimensions": {k: {"score": round(v[0], 2), "note": v[1]}
                                  for k, v in dims.items()},
                   "average": round(avg, 2), "verdict": verdict},
                  open(a.json, "w"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
