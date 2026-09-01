#!/usr/bin/env python3
"""ЭТАЛОН сборки рилса. Одна команда на весь контур.

Стиль зафиксирован Сержем 18.08.2026 на ролике «нейросеть вместо монтажёра»
(версия 3). Порядок слоёв и правила менять нельзя без его решения - только
добавлять новые приёмы.

Что делает:
  1. меряет тишину и решает, нужна ли резка пауз (>=5% - режем, иначе нет);
  2. каты по знакам препинания, зум вперёд-назад, дрейф, whip, звук переходов;
  3. в объясняющих кусках уводит голову в окно, освобождая площадь под текст;
  4. титры по словам - и АВТОМАТИЧЕСКИ гасит их в оконных кусках;
  5. всплывашки, чек-листы, флеши, число, призыв;
  6. печатает приёмку числами.

    python3 reel.py вход.mov --plan план.json -o выход.mp4
    python3 reel.py вход.mov --plan план.json --dry     # только разбор и план

План (json):
{
  "fixes":    {"нейросет": "нейросеть"},                  правки распознавания
  "windows":  [{"start": 6.58, "end": 10.38}],            где голова уходит в окно
  "creative": [ ... ]                                     стикеры/панели/флеши/чип/cta
}
"""
import argparse, json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reels
from whisper_json import load_whisper_json, subtitles, creative, window as windowmod

CUT_IF_SILENCE_OVER = 0.05     # доля тишины, при которой включаем резку пауз
MUTE_PAD = 0.08                # запас гашения титров вокруг окна


def run(cmd):
    subprocess.run(cmd, check=True)


def words_of(src, wd, lang, fixes):
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", f"{wd}/src.wav"])
    wj = reels.transcribe(f"{wd}/src.wav", f"{wd}/raw", lang)
    tr = load_whisper_json(wj)["transcription"]
    toks = [(s["offsets"]["from"] / 1000.0, s["offsets"]["to"] / 1000.0, s["text"])
            for s in tr if s["text"].strip()]
    out = []
    for a, b, t in toks:
        if t.startswith(" ") or not out:
            out.append({"start": a, "end": b, "text": t.strip()})
        else:
            out[-1]["end"] = b
            out[-1]["text"] += t
    res = []
    for x in out:                                   # висящую пунктуацию - к слову
        t = x["text"]
        if res and t and all(c in ".,!?…:;-" for c in t):
            res[-1]["end"] = x["end"]
            res[-1]["text"] += t
        else:
            res.append(x)
    n = 0
    for x in res:                                   # правки распознавания
        core = x["text"].rstrip(".,!?…:;-")
        if core.lower() in fixes:
            x["text"] = fixes[core.lower()] + x["text"][len(core):]
            n += 1
    return res, n


def silence_share(wd):
    db, total = reels.frame_db(f"{wd}/src.wav")
    th, _, _ = reels.auto_threshold(db)
    _, cuts, _ = reels.plan_cuts(db, total, th)
    return sum(b - a for a, b in cuts) / total, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--plan", required=True)
    ap.add_argument("-o", "--output")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--keep", help="папка для промежуточных файлов")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    plan = json.load(open(a.plan))
    out = os.path.abspath(os.path.expanduser(
        a.output or os.path.splitext(src)[0] + "_reel.mp4"))
    fw, fh = reels.real_frame(src)
    assert (fw, fh) == (1080, 1920), f"кадр {fw}x{fh}, эталон собирает вертикаль 1080x1920"

    wd = a.keep or tempfile.mkdtemp(prefix="reel_")
    os.makedirs(wd, exist_ok=True)

    words, nfix = words_of(src, wd, a.lang, {k.lower(): v
                                             for k, v in plan.get("fixes", {}).items()})
    share, total = silence_share(wd)
    print(f"─ разбор ─")
    print(f"  длина {total:.1f}s, слов {len(words)}, правок распознавания {nfix}")
    print(f"  тишины {share*100:.1f}%  ->  "
          f"{'режем паузы' if share >= CUT_IF_SILENCE_OVER else 'резать нечего, идём динамикой'}")
    if share >= CUT_IF_SILENCE_OVER:
        print(f"  ВНИМАНИЕ: материал с паузами. Эталон рассчитан на речь без пауз;")
        print(f"  сначала прогони reels.py, потом этот сборщик по результату.")

    wins = plan.get("windows", [])
    mutes = [(w["start"] - MUTE_PAD, w["end"] + MUTE_PAD) for w in wins]
    print(f"  окон: {len(wins)}"
          + (f" ({sum(w['end']-w['start'] for w in wins)/total*100:.0f}% хронометража)" if wins else ""))
    if a.dry:
        return 0

    json.dump(words, open(f"{wd}/words.json", "w"), ensure_ascii=False)
    print("─ сборка ─")

    # 1. каты, зум, дрейф, whip, звук
    run([sys.executable, f"{HERE}/dynamic.py", src, "--words", f"{wd}/words.json",
         "-o", f"{wd}/base.mp4", "--lang", a.lang])

    # 2. окно
    stage = f"{wd}/base.mp4"
    if wins:
        json.dump(wins, open(f"{wd}/windows.json", "w"), ensure_ascii=False)
        windowmod.build(stage, wins, f"{wd}/win.mp4")
        stage = f"{wd}/win.mp4"

    # 3. титры - гашение оконных кусков ставится САМО, это правило, а не опция
    tr = {"transcription": [{"offsets": {"from": int(x["start"] * 1000),
                                         "to": int(x["end"] * 1000)},
                             "text": (" " if i else "") + x["text"]}
                            for i, x in enumerate(words)]}
    json.dump(tr, open(f"{wd}/subs.json", "w"), ensure_ascii=False)
    n, g = subtitles.burn(stage, f"{wd}/subs.json", f"{wd}/cap.mp4",
                          do_censor=True, mute_ranges=mutes)
    print(f"  титры: {n} слов, {g} фраз, погашено в {len(mutes)} оконных кусках")

    # 4. всплывашки
    cre = plan.get("creative", [])
    if wins:                                  # рамка окна ставится сама
        have = {(round(c.get("start", -1), 2)) for c in cre if c.get("type") == "winframe"}
        for w in wins:
            if round(w["start"], 2) not in have:
                cre.append({"type": "winframe", "start": w["start"], "end": w["end"]})
    if cre:
        json.dump(cre, open(f"{wd}/creative.json", "w"), ensure_ascii=False)
        k = creative.render(cre, f"{wd}/cap.mp4", out)
        print(f"  всплывашек: {k}")
    else:
        shutil.copy(f"{wd}/cap.mp4", out)

    # 5. приёмка
    print("─ приёмка ─")
    lo = subprocess.run(["ffmpeg", "-v", "info", "-i", out, "-af",
                         "loudnorm=I=-14:TP=-1.5:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    ls = lo.splitlines()
    s0 = max(k for k, l in enumerate(ls) if l.strip().startswith("{"))
    e0 = max(k for k, l in enumerate(ls) if l.strip().startswith("}"))
    m = json.loads("\n".join(ls[s0:e0 + 1]))
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
         "-show_entries", "stream=width,height", "-of", "json", out],
        capture_output=True, text=True).stdout)
    v = d["streams"][0]
    print(f"  {v['width']}x{v['height']}  {float(d['format']['duration']):.1f}s  "
          f"{int(d['format']['size'])/1e6:.1f} МБ")
    print(f"  громкость {float(m['input_i']):.1f} LUFS, тру-пик {float(m['input_tp']):.1f} dB")
    assert float(m["input_tp"]) <= -1.0, "тру-пик выше -1 dB: риск клиппинга при перекодировке"
    print(f"  готово: {out}")
    if not a.keep:
        shutil.rmtree(wd, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
