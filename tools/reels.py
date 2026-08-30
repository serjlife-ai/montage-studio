#!/usr/bin/env python3
"""Reels из сырого исходника: резка пауз -> выравнивание уровней -> титры.

Главный урок контура (17.08.2026): порог тишины НЕЛЬЗЯ брать константой.
Серж говорит в петличку (-31 dB), нейросеть отвечает из динамика ноутбука
(-44 dB), тишина -65 dB. Порог -30 dB вырезал 46 секунд речи нейросети,
потому что она была ТИШЕ порога. Поэтому порог считается из гистограммы
самого файла, детектор смотрит на пик в окне, а инвариант падает с ошибкой,
если под нож попал хоть один кадр громче порога.

Использование:
    python3 reels.py вход.mov [-o выход.mp4] [--censor] [--no-level]
                              [--threshold -46] [--analyze]
"""
import argparse, json, os, subprocess, sys, tempfile, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subtitles

W, H, FPS = 1080, 1920, 30
# Модель whisper ищем, а не зашиваем: на другой машине она лежит иначе.
# Порядок: переменная окружения -> рядом с контуром -> прежнее место на Маке Сержа.
MODEL_CANDIDATES = [
    os.environ.get("WHISPER_MODEL", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models",
                 "ggml-large-v3-turbo.bin"),
    os.path.expanduser("~/.config/tg-remote/models/ggml-large-v3-turbo.bin"),
    os.path.expanduser("~/.cache/whisper/ggml-large-v3-turbo.bin"),
]
WHISPER_MODEL = next((os.path.abspath(p) for p in MODEL_CANDIDATES
                      if p and os.path.exists(os.path.expanduser(p))), "")

WIN = 0.16        # окно поиска пика
PAD = 0.08        # подушка вокруг речи, чтобы не срубать атаку слова
MINGAP = 0.22     # дырки короче не трогаем
MIN_SEG = 0.20

# лифт тихой речи + экспандер ниже -58 dB, чтобы не поднимать комнатный шум
ACHAIN = ("highpass=f=70,compand=attacks=0.02:decays=0.35:"
          "points=-90/-100|-58/-70|-50/-32|-40/-25|-30/-20|-18/-15|0/-6:soft-knee=8")

ZOOMS = [1.00, 1.06, 1.00, 1.11, 1.03, 1.08]
ZOOM_CX, ZOOM_CY = 0.40, 0.42     # центр кадрирования смещён к лицу


def run(cmd):
    subprocess.run(cmd, check=True)


def real_frame(src):
    """Реальный размер кадра с учётом матрицы поворота.
    iPhone пишет вертикаль как 1920x1080 + rotation -90: строчка width/height врёт."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", src],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list", []):
        rot = int(float(sd.get("rotation", 0)))
    return (h, w) if abs(rot) == 90 else (w, h)


def frame_db(wav):
    w = wave.open(wav)
    sr, n = w.getframerate(), w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    hop = int(sr * 0.02)
    F = len(a) // hop
    rms = np.sqrt(np.maximum(np.mean(a[:F * hop].reshape(F, hop) ** 2, axis=1), 1e-12))
    return 20 * np.log10(rms), n / sr


def auto_threshold(db):
    """Порог между шумовой полкой и САМЫМ ТИХИМ говорящим.
    Полка = 5-й перцентиль, громкая речь = 90-й. Режем по середине."""
    floor, loud = np.percentile(db, 5), np.percentile(db, 90)
    return float(floor + 0.5 * (loud - floor)), float(floor), float(loud)


def plan_cuts(db, total, th):
    k = int(WIN / 0.02) // 2 * 2 + 1
    padded = np.pad(db, k // 2, mode="edge")
    peak = np.array([padded[i:i + k].max() for i in range(len(db))])
    speech = peak > th
    p = int(PAD / 0.02)
    speech = np.convolve(speech.astype(float), np.ones(2 * p + 1), "same") > 0

    cuts, i, F = [], 0, len(db)
    while i < F:
        if not speech[i]:
            j = i
            while j < F and not speech[j]:
                j += 1
            if (j - i) * 0.02 >= MINGAP:
                cuts.append((i * 0.02, j * 0.02))
            i = j
        else:
            i += 1

    loudest = max((db[int(x / 0.02):int(y / 0.02)].max() for x, y in cuts), default=-99)
    assert loudest <= th, f"под нож попал кадр {loudest:.0f} dB - речь бы пострадала"

    keeps, cur = [], 0.0
    for x, y in cuts:
        if x - cur >= MIN_SEG:
            keeps.append((cur, x))
        cur = y
    if total - cur >= MIN_SEG:
        keeps.append((cur, total))
    merged = []
    for s in keeps:
        if merged and s[0] - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], s[1])
        else:
            merged.append(s)
    return merged, cuts, loudest


def build_filter(keeps, audio_from_second_input):
    parts, cat = [], []
    asrc = "[1:a]" if audio_from_second_input else "[0:a]"
    for i, (x, y) in enumerate(keeps):
        z = ZOOMS[i % len(ZOOMS)]
        if z == 1.00:
            parts.append(f"[0:v]trim=start={x:.3f}:end={y:.3f},setpts=PTS-STARTPTS[v{i}];")
        else:
            cw, ch = int(W / z) // 2 * 2, int(H / z) // 2 * 2
            ox, oy = int((W - cw) * ZOOM_CX) // 2 * 2, int((H - ch) * ZOOM_CY) // 2 * 2
            parts.append(f"[0:v]trim=start={x:.3f}:end={y:.3f},setpts=PTS-STARTPTS,"
                         f"crop={cw}:{ch}:{ox}:{oy},scale={W}:{H}:flags=lanczos[v{i}];")
        parts.append(f"{asrc}atrim=start={x:.3f}:end={y:.3f},asetpts=PTS-STARTPTS[a{i}];")
        cat.append(f"[v{i}][a{i}]")
    return ("".join(parts) + "".join(cat) +
            f"concat=n={len(keeps)}:v=1:a=1[vo][ax];"
            f"[ax]loudnorm=I=-14:TP=-1.5:LRA=11[ao]")


def transcribe(wav, out_prefix, lang="ru"):
    if not WHISPER_MODEL:
        sys.exit("модель whisper не найдена. Положи ggml-large-v3-turbo.bin в ../models/ "
                 "или задай WHISPER_MODEL=/путь/к/модели.bin")
    run(["whisper-cli", "-m", WHISPER_MODEL, "-f", wav, "-l", lang,
         "-ml", "1", "-oj", "-of", out_prefix])
    return out_prefix + ".json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--censor", action="store_true", help="мат за звёздочками")
    ap.add_argument("--no-level", action="store_true", help="не выравнивать громкость")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--threshold", type=float, help="порог тишины dB, по умолчанию авто")
    ap.add_argument("--analyze", action="store_true", help="только отчёт, без рендера")
    ap.add_argument("--lang", default="ru")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    out = os.path.abspath(os.path.expanduser(
        a.output or os.path.splitext(src)[0] + "_reels.mp4"))
    fw, fh = real_frame(src)
    if (fw, fh) != (W, H):
        sys.exit(
            f"кадр {fw}x{fh}, а контур собирает вертикаль {W}x{H}.\n"
            f"Перегони исходник и повтори:\n"
            f"  ffmpeg -i '{src}' -vf \"scale={W}:-2,crop={W}:{H}\" "
            f"-c:a copy вертикаль.mp4\n"
            f"Если кадр горизонтальный, кроп возьмёт середину - проверь, "
            f"что говорящий не уехал за край.")
    wd = tempfile.mkdtemp(prefix="reels_")

    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", f"{wd}/src.wav"])
    db, total = frame_db(f"{wd}/src.wav")
    auto, floor, loud = auto_threshold(db)
    th = a.threshold if a.threshold is not None else auto
    keeps, cuts, loudest = plan_cuts(db, total, th)
    dur = sum(y - x for x, y in keeps)

    print(f"шумовая полка {floor:.0f} dB | громкая речь {loud:.0f} dB | порог {th:.0f} dB"
          f"{' (авто)' if a.threshold is None else ' (задан)'}")
    print(f"катов: {len(cuts)}  вырезано: {total-dur:.1f}s  "
          f"итог: {int(dur//60)}:{dur%60:04.1f} из {int(total//60)}:{total%60:04.1f}")
    print(f"инвариант: громчайший вырезанный кадр {loudest:.0f} dB (порог {th:.0f})")
    if a.analyze:
        return 0

    if a.no_level:
        second = []
        fc = build_filter(keeps, False)
        inputs = ["-i", src]
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", ACHAIN,
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le", f"{wd}/lvl.wav"])
        fc = build_filter(keeps, True)
        inputs = ["-i", src, "-i", f"{wd}/lvl.wav"]

    open(f"{wd}/filter.txt", "w").write(fc)
    json.dump(keeps, open(f"{wd}/keeps.json", "w"))
    stage = f"{wd}/cut.mp4" if not a.no_subs else out
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
        ["-filter_complex_script", f"{wd}/filter.txt", "-map", "[vo]", "-map", "[ao]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", stage])

    if a.no_subs:
        print(f"готово: {out}")
        return 0

    run(["ffmpeg", "-y", "-v", "error", "-i", stage, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", f"{wd}/cut.wav"])
    wj = transcribe(f"{wd}/cut.wav", f"{wd}/cutwords", a.lang)
    n, g = subtitles.burn(stage, wj, out, do_censor=a.censor)

    # контроль: сколько слов уцелело относительно исходника
    src_wj = transcribe(f"{wd}/src.wav", f"{wd}/srcwords", a.lang)
    import re
    def wc(p):
        return len(re.findall(r"[А-Яа-яЁёA-Za-z]+",
                              "".join(s["text"] for s in json.load(open(p))["transcription"])))
    print(f"слов: исходник {wc(src_wj)} -> итог {wc(wj)} | титры: {g} фраз")
    print(f"готово: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
