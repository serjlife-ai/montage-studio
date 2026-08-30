#!/usr/bin/env python3
"""Банк звуков для роликов: берём проверенную библиотеку, свой синтез не делаем.

История вопроса. Первая версия синтезировала свуши из белого шума через
однополюсный фильтр - 25% энергии оставалось выше 6 кГц, и это звучало как
шипение поверх речи. Синтез выброшен. Теперь источник один: библиотека,
что едет с HyperFrames media-use - Pixabay Content License, свободно для
коммерции, атрибуция не требуется.

Каждый звук проходит те же две проверки, что завалили синтез:
  1) доля энергии выше 6 кГц (шипение),
  2) приведение к общему уровню, чтобы громкость на треке что-то значила.
"""
import os, sys, glob, subprocess, wave
import numpy as np

# Библиотека звуков едет со скиллом media-use. Ищем её рядом, а не по абсолютному
# пути одной машины: контур должен разворачиваться и у других.
_HERE = os.path.dirname(os.path.abspath(__file__))
LIB_CANDIDATES = [
    os.environ.get("SFX_LIB", ""),
    os.path.join(_HERE, "..", ".agents/skills/media-use/audio/assets/sfx"),
    os.path.join(_HERE, "../../..", ".agents/skills/media-use/audio/assets/sfx"),
    os.path.join(_HERE, "../../..", ".claude/skills/media-use/audio/assets/sfx"),
    os.path.expanduser("~/.claude/skills/media-use/audio/assets/sfx"),
]
LIB = next((os.path.abspath(p) + "/" for p in LIB_CANDIDATES
            if p and os.path.isdir(os.path.expanduser(p))), "")
OUT = sys.argv[1] if len(sys.argv) > 1 else "./sfx"   # куда положить банк
TARGET_PEAK_DB = -6.0         # общий потолок; при -12 громкости трека не хватало,
                              # чтобы эффект пробился сквозь речь (та пикует на -1.5 dB)
HF_MAX = 30.0                 # выше - звук звонкий, в подложку не годится

# что берём и как готовим: (имя в банке, имя на выходе, начало, длительность)
PICKS = [
    ("whoosh-short",    "whoosh",        0.00, None),   # 0.0% верха, центр 383 Гц
    ("whoosh-cinematic","whoosh-deep",   0.00, 1.60),   # 0.4% верха, центр 136 Гц
    ("impact-bass-1",   "impact",        0.00, None),   # чистый суб
    ("impact-bass-2",   "impact-hard",   0.00, None),
    ("pop",             "pop",           0.00, None),   # 0.0% верха
    ("click-soft",      "click",         0.00, None),   # короткий транзиент, «цик»
    ("typing",          "typing",        0.00, None),   # клацание под счётчик
    # у десятисекундного райзера нарастание идёт 0->3.5с, дальше тишина.
    # Берём кусок, который КОНЧАЕТСЯ на пике, чтобы он вёл в удар.
    ("riser",           "riser",         2.20, 1.40),
    # у chime медленная атака: пик на 0.4с. Резать с нуля значит
    # взять 0.4с тишины и потерять весь звук на нормировке.
    ("chime",           "chime",         0.30, 1.20),
]


def measure(path):
    w = wave.open(path)
    sr = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    X = np.abs(np.fft.rfft(a)) ** 2
    fr = np.fft.rfftfreq(len(a), 1 / sr)
    tot = X.sum() + 1e-12
    return a, sr, 100 * X[fr >= 6000].sum() / tot, float((fr * X).sum() / tot)


def main():
    if not LIB:
        sys.exit("библиотека звуков не найдена: поставь скилл media-use "
                 "или задай SFX_LIB=/путь/к/audio/assets/sfx")
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(OUT + "/*.wav"):
        os.remove(f)                                  # старый синтез не оставляем

    print(f"{'звук':14s} {'длит':>6s} {'>6кГц':>7s} {'центр':>8s}")
    for src, dst, start, dur in PICKS:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-i", LIB + src + ".mp3"]
        if dur:
            cmd += ["-t", str(dur)]
        tmp = f"{OUT}/_{dst}.wav"
        cmd += ["-ac", "1", "-ar", "48000", tmp]
        subprocess.run(cmd, check=True)

        a, sr, hf, cent = measure(tmp)
        assert hf < HF_MAX, f"{dst}: {hf:.1f}% энергии выше 6 кГц - звонкий, в подложку не берём"

        # общий потолок уровня + мягкие края, чтобы не щёлкало на стыке
        a = a / (np.abs(a).max() + 1e-9) * (10 ** (TARGET_PEAK_DB / 20))
        fade = min(int(sr * 0.006), len(a) // 4)
        a[:fade] *= np.linspace(0, 1, fade)
        a[-fade:] *= np.linspace(1, 0, fade)

        w = wave.open(f"{OUT}/{dst}.wav", "w")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes()); w.close()
        os.remove(tmp)
        print(f"{dst:14s} {len(a)/sr:5.2f}s {hf:6.1f}% {cent:7.0f}Гц")

    print(f"\nбанк собран -> {OUT}")
    print("источник: HyperFrames media-use, Pixabay Content License (коммерция разрешена)")


if __name__ == "__main__":
    main()
