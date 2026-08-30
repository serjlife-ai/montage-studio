#!/usr/bin/env python3
"""Динамичный ролик из говорящей головы: каты с зумом вперёд/назад + свуши.

Для материала, где резать НЕЧЕГО - человек говорит без пауз. Динамика тут не из
резки, а из ритма кадрирования: на каждой смысловой границе кадр прыгает крупнее
или мельче, и прыжок подпирается звуком.

Почему база НЕ 1.00: чтобы был откат назад, нужен запас пикселей. Базовый кадр
слегка приближен (BASE_ZOOM), тогда можно и наехать, и отъехать.

Использование:
    python3 dynamic.py вход.mov -o выход.mp4 [--words words.json] [--no-sfx]
                       [--plan] [--sfx-dir ../sfx]
"""
import argparse, json, os, subprocess, sys, tempfile, wave
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reels                                    # real_frame + ACHAIN переиспользуем

W, H, FPS = 1080, 1920, 30

# Диапазон подобран замером на крупном плане: на уже близком кадре разница
# в 15-20% глазом НЕ читается - каты выглядят как брак склейки, а не как приём.
# Потолок 1.38: выше начинается мыло от апскейла (1080/1.38 = 783px исходника).
ZOOM_IN = [1.26, 1.38, 1.32]
ZOOM_OUT = [1.00, 1.06]
CX, CY = 0.47, 0.34     # центр кадрирования: лицо, не середина кадра
CY_ALT = 0.30           # второй вариант высоты - чтобы соседние планы не совпадали

MIN_SHOT = 0.75         # короче - монтаж рябит
MAX_SHOT = 2.60         # длиннее - ролик провисает
NUDGE = 0.03            # горизонтальный сдвиг кадра для разнообразия

SFX_BEFORE = 0.42       # переход ведёт В кат: воздух до склейки, удар на ней
TRANSITIONS = ["transition-a", "transition-b", "transition-c"]  # ротация, чтобы ухо
                        # не цеплялось за одну текстуру: 22 одинаковых вуша слышны как шум
WHIP_PX = 300           # сдвиг на whip-склейке
WHIP_T = 0.13           # за сколько он гасится
DRIFT_PX = 44           # медленный дрейф внутри плана - чтобы не было статики
# Уровни эффектов держим НИЖЕ, чем хочется: их пики съедают запас до тру-пика,
# и итоговый ролик приходится тянуть вниз (было -16.4 LUFS при норме -14).
# +6...+8 dB над речью в своей полосе уже отлично слышно - проверено замером.
VOL_WHOOSH = 0.30
VOL_DEEP = 0.22
VOL_POP = 0.28
VOL_CLICK = 0.24
POP_RUN = 3             # столько запятых подряд = перечисление, туда просятся попы
POP_SPAN = 4.5          # в каком окне ищем этот забег
TP_TARGET = -1.5        # тру-пик итога


def run(cmd):
    subprocess.run(cmd, check=True)


def load_words(path):
    return json.load(open(path))


def cut_points(words, total):
    """Границы катов: сначала знаки препинания, потом добиваем длинные планы.

    Знак препинания - это место, где говорящий сам сделал смысловой шов.
    Кат туда читается как решение, а не как сбой."""
    cand = [0.0]
    for i in range(1, len(words)):
        if words[i - 1]["text"].rstrip().endswith((",", ".", "!", "?", "…")):
            cand.append(words[i]["start"])
    cand.append(total)
    cand = sorted(set(round(c, 2) for c in cand))

    # выкидываем слишком близкие
    keep = [cand[0]]
    for c in cand[1:]:
        if c - keep[-1] >= MIN_SHOT:
            keep.append(c)
    if keep[-1] < total:
        keep[-1] = total if total - keep[-2] >= MIN_SHOT else keep[-1]

    # разбиваем планы длиннее MAX_SHOT по ближайшей границе слова
    starts = [w["start"] for w in words]
    out = []
    for i, c in enumerate(keep[:-1]):
        out.append(c)
        nxt = keep[i + 1]
        while nxt - out[-1] > MAX_SHOT:
            target = out[-1] + MAX_SHOT * 0.8
            best = min((s for s in starts if out[-1] + MIN_SHOT <= s <= nxt - MIN_SHOT),
                       key=lambda s: abs(s - target), default=None)
            if best is None:
                break
            out.append(round(best, 2))
    out.append(total)
    return out


def accent_points(words):
    """Где ставить поп и клик: перечисления и числа.

    Перечисление - самое «щёлкающее» место в речи: короткие однородные куски
    через запятую. Числа - смысловые опоры образовательного ролика."""
    pops, clicks = [], []
    commas = [i for i, w in enumerate(words)
              if w["text"].rstrip().endswith(",")]
    for k, i in enumerate(commas):
        run = [j for j in commas[k:k + POP_RUN]
               if words[j]["end"] - words[i]["start"] <= POP_SPAN]
        if len(run) >= POP_RUN:
            pops.append(round(words[i]["end"], 2))
    for w in words:
        if any(c.isdigit() for c in w["text"]):
            clicks.append(round(w["start"], 2))
    dedup = lambda xs: sorted({round(x, 2) for x in xs})
    pops = [t for t in dedup(pops)]
    return pops, [t for t in dedup(clicks) if all(abs(t - p) > 0.2 for p in pops)]


def zoom_plan(n):
    """Чередуем наезд и откат так, чтобы два одинаковых не шли подряд."""
    plan, i_in, i_out = [], 0, 0
    for k in range(n):
        if k == 0:
            plan.append(ZOOM_IN[0])                  # хук всегда крупно
        elif k % 2 == 1:
            plan.append(ZOOM_OUT[i_out % len(ZOOM_OUT)]); i_out += 1
        else:
            plan.append(ZOOM_IN[i_in % len(ZOOM_IN)]); i_in += 1
    return plan


def build_video(src, shots, zooms, out, whips=()):
    """Каждый план: кадрирование + медленный дрейф + иногда whip-въезд.

    Дрейф решает главную претензию к первой версии - «только зум туда-сюда»:
    внутри плана картинка живёт, а не стоит. Whip добавляет движение НА склейке,
    и тогда звук перехода читается как движение, а не как шум."""
    parts, cat = [], []
    for i, ((a, b), z) in enumerate(zip(shots, zooms)):
        cw, ch = int(W / z) // 2 * 2, int(H / z) // 2 * 2
        nud = NUDGE if i % 4 == 2 else (-NUDGE if i % 4 == 0 else 0.0)
        cx = min(max(CX + nud, 0.0), 1.0)
        cy = CY_ALT if i % 3 == 1 else CY
        ox = int((W - cw) * cx) // 2 * 2
        oy = int((H - ch) * cy) // 2 * 2
        dur = max(b - a, 0.1)
        dx = DRIFT_PX * (1 if i % 2 else -1)
        dy = (DRIFT_PX // 2) * (1 if i % 4 < 2 else -1)
        wx = (WHIP_PX if i % 2 else -WHIP_PX) if i in whips else 0
        # выражения считаются покадрово; запятые внутри экранируем
        xe = (f"{ox}+({dx})*t/{dur:.3f}" +
              (f"+({wx})*max(0\\,1-t/{WHIP_T})" if wx else ""))
        ye = f"{oy}+({dy})*t/{dur:.3f}"
        xe = f"max(0\\,min({W - cw}\\,{xe}))"
        ye = f"max(0\\,min({H - ch}\\,{ye}))"
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                     f"crop={cw}:{ch}:x='{xe}':y='{ye}',"
                     f"scale={W}:{H}:flags=lanczos[v{i}];")
        cat.append(f"[v{i}]")
    fc = "".join(parts) + "".join(cat) + f"concat=n={len(shots)}:v=1:a=0[vo]"
    fp = out + ".filter.txt"
    open(fp, "w").write(fc)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
         "-filter_complex_script", fp, "-map", "[vo]", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-pix_fmt", "yuv420p", "-r", str(FPS), out])
    os.remove(fp)


def mix_audio(src, cuts, sfx_dir, out, use_sfx=True, pops=(), clicks=()):
    """Голос + свуши на катах. Микшируем сами, а не в движке: так уровень
    каждой дорожки известен точно и его можно померить."""
    inputs = ["-i", src]
    chains = [f"[0:a]{reels.ACHAIN},loudnorm=I=-14:TP=-1.5:LRA=11[voice]"]
    mix = ["[voice]"]
    if use_sfx:
        for i, t in enumerate(cuts):
            name, vol = TRANSITIONS[i % len(TRANSITIONS)], VOL_WHOOSH
            path = os.path.join(sfx_dir, name + ".wav")
            if not os.path.exists(path):
                continue
            idx = len(inputs) // 2
            inputs += ["-i", path]
            at = max(t - SFX_BEFORE, 0.0)
            chains.append(f"[{idx}:a]adelay={int(at*1000)}|{int(at*1000)},"
                          f"volume={vol}[s{i}]")
            mix.append(f"[s{i}]")
        for kind, times, vol in (("pop", pops, VOL_POP), ("click", clicks, VOL_CLICK)):
            path = os.path.join(sfx_dir, kind + ".wav")
            if not os.path.exists(path):
                continue
            for j, t in enumerate(times):
                idx = len(inputs) // 2
                inputs += ["-i", path]
                lbl = f"{kind}{j}"
                chains.append(f"[{idx}:a]adelay={int(t*1000)}|{int(t*1000)},"
                              f"volume={vol}[{lbl}]")
                mix.append(f"[{lbl}]")
    chains.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:"
                  f"dropout_transition=0[aout]")
    fc = ";".join(chains)
    fp = out + ".afilter.txt"
    open(fp, "w").write(fc)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
        ["-filter_complex_script", fp, "-map", "[aout]",
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out])
    os.remove(fp)


def static_gain(path, target_lufs=-14.0):
    """Итоговый уровень выставляем ОДНИМ постоянным усилением.
    loudnorm одним проходом работает динамически и душит удары - проверено."""
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-af",
         f"loudnorm=I={target_lufs}:TP={TP_TARGET}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    ls = out.splitlines()
    s0 = max(k for k, l in enumerate(ls) if l.strip().startswith("{"))
    e0 = max(k for k, l in enumerate(ls) if l.strip().startswith("}"))
    d = json.loads("\n".join(ls[s0:e0 + 1]))
    ii, tp = float(d["input_i"]), float(d["input_tp"])
    g = target_lufs - ii
    if tp + g > TP_TARGET:          # тру-пик важнее громкости
        g = TP_TARGET - tp
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--words", help="words.json с потайминговкой (иначе транскрибируем)")
    ap.add_argument("--sfx-dir", default=None)
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--plan", action="store_true", help="только план катов")
    ap.add_argument("--lang", default="ru")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    out = os.path.abspath(os.path.expanduser(
        a.output or os.path.splitext(src)[0] + "_dynamic.mp4"))
    fw, fh = reels.real_frame(src)
    if (fw, fh) != (W, H):
        sys.exit(f"кадр {fw}x{fh}, контур собирает вертикаль {W}x{H}")

    wd = tempfile.mkdtemp(prefix="dyn_")
    total = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
        capture_output=True, text=True).stdout.strip())

    if a.words:
        words = load_words(a.words)
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", f"{wd}/src.wav"])
        wj = reels.transcribe(f"{wd}/src.wav", f"{wd}/w", a.lang)
        tr = json.load(open(wj))["transcription"]
        toks = [(s["offsets"]["from"]/1000.0, s["offsets"]["to"]/1000.0, s["text"])
                for s in tr if s["text"].strip()]
        acc = []
        for x, y, t in toks:
            if t.startswith(" ") or not acc:
                acc.append({"start": x, "end": y, "text": t.strip()})
            else:
                acc[-1]["end"] = y; acc[-1]["text"] += t
        words = acc
        json.dump(words, open(f"{wd}/words.json", "w"), ensure_ascii=False)

    pts = cut_points(words, total)
    shots = list(zip(pts[:-1], pts[1:]))
    zooms = zoom_plan(len(shots))
    lens = [b - x for x, b in shots]
    print(f"планов: {len(shots)}  средний: {np.mean(lens):.2f}s  "
          f"мин: {min(lens):.2f}s  макс: {max(lens):.2f}s")
    assert min(lens) >= MIN_SHOT - 0.01, f"план короче {MIN_SHOT}s - монтаж зарябит"
    assert max(lens) <= MAX_SHOT + 0.01, f"план длиннее {MAX_SHOT}s - ролик провиснет"
    if a.plan:
        for (x, b), z in zip(shots, zooms):
            print(f"  {x:6.2f}-{b:6.2f}  зум {z:.2f}")
        return 0

    sfx_dir = a.sfx_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "sfx")
    pops, clicks = accent_points(words) if not a.no_sfx else ([], [])
    print(f"свушей на катах: {len(pts)-2}  попов: {len(pops)}  кликов: {len(clicks)}")
    whips = {i for i in range(1, len(shots)) if i % 3 == 1}
    print(f"whip-склеек: {len(whips)}  дрейф: во всех планах")
    build_video(src, shots, zooms, f"{wd}/v.mp4", whips=whips)
    mix_audio(src, pts[1:-1], os.path.abspath(sfx_dir), f"{wd}/a.wav",
              use_sfx=not a.no_sfx, pops=pops, clicks=clicks)
    gain = static_gain(f"{wd}/a.wav")
    if abs(gain) > 0.05:
        run(["ffmpeg", "-y", "-v", "error", "-i", f"{wd}/a.wav",
             "-af", f"volume={gain:.2f}dB", "-c:a", "pcm_s16le",
             f"{wd}/a2.wav"])
        os.replace(f"{wd}/a2.wav", f"{wd}/a.wav")
        print(f"итоговое усиление: {gain:+.2f} dB (статическое, без динамики)")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", f"{wd}/v.mp4", "-i", f"{wd}/a.wav", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", out])
    print(f"готово: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
