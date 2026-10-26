#!/usr/bin/env python3
"""Предполётная проверка исходника: что это за файл и что с ним делать.

Идея взята у OpenMontage (их `source_media_review`): агент не имеет права
строить план по имени файла и по рассказу человека. Сначала измеряем файл,
и только потом решаем. Код здесь свой, их лицензия AGPL-3.0 нам не подходит.

Что меряем и почему именно это:
  - реальный кадр с учётом матрицы поворота (iPhone пишет вертикаль как
    1920x1080 + rotation -90, строчка width/height врёт);
  - шумовую полку, громкую речь и авто-порог - те же числа, по которым
    потом будет резать `reels.py`, чтобы человек увидел их ДО рендера;
  - разрыв между тихим и громким говорящим: если он больше 12 dB,
    в файле два источника звука, и об этом надо предупредить;
  - сколько в файле настоящей тишины - это потолок честной резки пауз;
  - битые дорожки, странный fps, моно/стерео, длительность.

Отчёт заканчивается решением: можно ли собирать и что именно
человеку решать руками.

    python3 preflight.py вход.mov [--json отчёт.json]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

STEP = 0.02          # шаг кадра анализа, с
W, H = 1080, 1920    # канон вертикали контура
MIN_PHRASE = 0.4     # кусок речи короче не фраза, а призвук
TWO_SOURCE_GAP = 12  # разброс между фразами в dB: выше - источников два


def probe(src):
    """Полные данные потоков от ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", src],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def real_frame(vs):
    """Реальный кадр с учётом поворота."""
    w, h = int(vs["width"]), int(vs["height"])
    rot = 0
    for sd in vs.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    for k in ("rotate", "rotation"):
        if k in vs.get("tags", {}):
            rot = int(float(vs["tags"][k]))
    return ((h, w) if abs(rot) % 180 == 90 else (w, h)), rot


# Кривые HDR по полю color_transfer ffprobe. Айфон пишет Dolby Vision
# поверх HLG (arib-std-b67), камеры и экранные записи - HDR10/PQ (smpte2084).
HDR_TRANSFERS = {"arib-std-b67": "HLG / Dolby Vision", "smpte2084": "HDR10 / PQ"}

# Тонмаппинг HDR -> SDR. hable держит и тени, и света; desat=0 не гасит цвет
# лица. Каждый HDR-исходник перегоняется ОТДЕЛЬНО и ДО склейки: фильтр на
# готовой сборке испортил бы куски, которые и так были SDR.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
           "format=yuv420p")


def hdr_kind(vs):
    """Название HDR-кривой или None для обычного SDR."""
    return HDR_TRANSFERS.get(vs.get("color_transfer", ""))


def to_sdr(src, dst):
    """Перегнать HDR-исходник в SDR bt709 с тонмаппингом, звук копией."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", TONEMAP,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-c:a", "copy", "-movflags", "+faststart", dst],
        check=True)
    return dst


def frame_db(src, wd):
    """Уровень каждого кадра 20 мс в dB."""
    wav = f"{wd}/probe.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
    w = wave.open(wav)
    sr, n = w.getframerate(), w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(sr * STEP)
    F = len(a) // hop
    if F == 0:
        return np.array([]), 0.0
    rms = np.sqrt(np.maximum(np.mean(a[:F * hop].reshape(F, hop) ** 2, axis=1), 1e-12))
    return 20 * np.log10(rms), n / sr


def phrase_levels(db, th):
    """Медианный уровень каждой непрерывной фразы.

    Мерить разброс громкости по кадрам 20 мс бесполезно: речь двугорбая
    сама по себе (гласные громкие, согласные тихие), и один говорящий даёт
    те же 17 dB разрыва, что и два источника. Проверено замером на стенде:
    ни перцентили кадров, ни критерий Оцу случаи не разделяют.

    Внутри фразы громкость говорящего держится, между говорящими меняется
    скачком. Поэтому считаем медиану внутри каждого куска речи и смотрим
    разброс уже по фразам: один голос даёт 6-9 dB, два источника 12-29 dB.
    """
    k = int(0.16 / STEP) // 2 * 2 + 1
    padded = np.pad(db, k // 2, mode="edge")
    peak = np.array([padded[i:i + k].max() for i in range(len(db))])
    speech = peak > th
    levels, i, F = [], 0, len(db)
    while i < F:
        if speech[i]:
            j = i
            while j < F and speech[j]:
                j += 1
            if (j - i) * STEP >= MIN_PHRASE:
                levels.append(float(np.median(db[i:j])))
            i = j
        else:
            i += 1
    return np.array(levels)


def speech_levels(db):
    """Уровни файла и разброс громкости между фразами."""
    floor, loud = float(np.percentile(db, 5)), float(np.percentile(db, 90))
    th = floor + 0.5 * (loud - floor)
    lv = phrase_levels(db, th)
    if len(lv) < 4:
        return th, floor, loud, None, None, 0.0, len(lv)
    q = float(np.percentile(lv, 10))
    l = float(np.percentile(lv, 90))
    return th, floor, loud, q, l, l - q, len(lv)


def subtitle_band_frames(src, dur, out_dir, frame_h=None):
    """Кадры полосы титров - агент обязан посмотреть их ДО рендера.

    Найдено живым тестом 31.08.2026: исходник был снят с УЖЕ выжженными
    караоке-титрами, контур положил свои вторым слоем, в кадре вышли две
    строки об одном и том же.

    Автодетектор пробовали и не выкатили: на пересъёмке экрана с телефона
    дрожит весь кадр, и частота рывков в полосе титров не отличается от
    контрольной полосы (замер: 4-5 рывков/мин там и там, включая файл,
    где наши титры точно есть). Плохая метрика хуже её отсутствия.

    Поэтому решаем не числом, а глазами: вырезаем полосу, куда контур
    кладёт титры, и показываем её человеку и агенту.

    Полоса считается в ДОЛЯХ кадра, а не в пикселях канона: донор часто
    приходит не 1080x1920 (у блогеров 720x1280 обычное дело), и кроп по
    зашитым пикселям молча не давал ни одного кадра - папка создавалась
    пустой, а предупреждение об уже выжженных титрах не выдавалось.
    """
    os.makedirs(out_dir, exist_ok=True)
    h = int(frame_h or H)
    y0, y1 = int(h * 0.58), int(h * 0.82)
    shots = []
    for i, t in enumerate((dur * 0.25, dur * 0.5, dur * 0.75)):
        p = os.path.join(out_dir, f"band_{i + 1}.png")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src,
             "-vframes", "1", "-vf",
             f"crop=iw:{y1 - y0}:0:{y0},scale=540:-1", p],
            capture_output=True)
        if r.returncode == 0 and os.path.exists(p):
            shots.append(p)
        elif i == 0:
            err = r.stderr.decode(errors="replace").strip().splitlines()
            if err:
                print(f"  [внимание] кадры полосы титров не вырезались: "
                      f"{err[-1][:80]}")
    return shots


def silence_budget(db, th, mingap=0.22):
    """Сколько в файле настоящей тишины - потолок честной резки пауз."""
    quiet = db <= th
    total, run = 0.0, 0
    for v in quiet:
        if v:
            run += 1
        else:
            if run * STEP >= mingap:
                total += run * STEP
            run = 0
    if run * STEP >= mingap:
        total += run * STEP
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--json", help="куда сложить отчёт машинным видом")
    ap.add_argument("--frames", help="куда сложить кадры полосы титров")
    ap.add_argument("--sdr", nargs="?", const="", metavar="ВЫХОД",
                    help="если исходник HDR - перегнать в SDR (по умолчанию "
                         "рядом: <имя>_sdr.mp4) и проверять уже его")
    a = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(a.input))
    if not os.path.exists(src):
        sys.exit(f"файла нет: {src}")

    info = probe(src)
    if a.sdr is not None:
        vs0 = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        if vs0 is not None and hdr_kind(vs0):
            dst = os.path.abspath(os.path.expanduser(a.sdr)) if a.sdr else \
                os.path.splitext(src)[0] + "_sdr.mp4"
            print(f"HDR ({hdr_kind(vs0)}) -> SDR: {os.path.basename(dst)}")
            src = to_sdr(src, dst)
            info = probe(src)
        else:
            print("исходник уже SDR - перегонять нечего")
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    aus = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"].get("duration", 0))
    size_mb = int(info["format"].get("size", 0)) / 1024 / 1024

    print(f"файл:         {os.path.basename(src)}  ({size_mb:.0f} МБ)")
    print(f"длительность: {int(dur // 60)}:{dur % 60:04.1f}")

    rep = {"file": src, "duration": round(dur, 2), "size_mb": round(size_mb, 1),
           "blockers": [], "warnings": [], "decisions": []}

    # ---------- видео ----------
    if vs is None:
        print("видеодорожки нет")
        rep["blockers"].append("в файле нет видеодорожки")
    else:
        (fw, fh), rot = real_frame(vs)
        rate = vs.get("r_frame_rate", "0/1")
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        rot_note = ""
        if rot:
            rot_note = (f" (записан {vs['width']}x{vs['height']}, "
                        f"поворот {rot})")
        print(f"кадр:         {fw}x{fh}{rot_note}"
              f"  {vs['codec_name']}  {fps:.0f} fps")
        rep["frame"] = {"w": fw, "h": fh, "rotation": rot,
                        "codec": vs["codec_name"], "fps": round(fps, 2)}
        hdr = hdr_kind(vs)
        if hdr:
            print(f"цвет:         {hdr} ({vs.get('pix_fmt')}, "
                  f"{vs.get('color_transfer')})")
            rep["frame"]["hdr"] = hdr
            rep["warnings"].append(
                f"исходник в HDR ({hdr}). Айфон снимает так по умолчанию. "
                f"Склейка без тонмаппинга даёт бледную, вымытую картинку и "
                f"серое лицо - ошибки в логе при этом не будет. Перегнать в SDR "
                f"ДО монтажа: ./preflight --sdr вход.mov")
        if (fw, fh) != (W, H):
            if fw > fh:
                rep["decisions"].append(
                    f"кадр горизонтальный {fw}x{fh}. Контур собирает вертикаль "
                    f"{W}x{H}. Кроп возьмёт середину - проверь, что говорящий "
                    f"не уедет за край, либо режь по лицу вручную")
            else:
                rep["warnings"].append(
                    f"кадр {fw}x{fh}, не канон {W}x{H} - потребуется перегон")

    # ---------- звук ----------
    if aus is None:
        print("аудиодорожки нет")
        rep["blockers"].append("в файле нет аудиодорожки - резать нечего")
    else:
        print(f"звук:         {aus['codec_name']}  {aus.get('sample_rate')} Гц  "
              f"{aus.get('channels')} кан.")
        wd = tempfile.mkdtemp(prefix="preflight_")
        db, adur = frame_db(src, wd)
        if len(db) == 0:
            rep["blockers"].append("аудиодорожка пустая")
        else:
            th, floor, loud, q, l, gap, nph = speech_levels(db)
            sil = silence_budget(db, th)
            print()
            print(f"шумовая полка:      {floor:.0f} dB")
            print(f"громкая речь:       {loud:.0f} dB")
            print(f"порог резки (авто): {th:.0f} dB")
            rep["audio"] = {"floor_db": round(floor), "loud_db": round(loud),
                            "auto_threshold_db": round(th),
                            "silence_seconds": round(sil, 1)}
            if q is not None:
                print(f"фраз распознано:    {nph}")
                print(f"громкость фраз:     {q:.0f}…{l:.0f} dB "
                      f"(разброс {gap:.0f} dB)")
                rep["audio"].update({"quiet_phrase_db": round(q),
                                     "loud_phrase_db": round(l),
                                     "phrase_spread_db": round(gap),
                                     "phrases": nph})
                if gap >= TWO_SOURCE_GAP:
                    rep["warnings"].append(
                        f"разброс громкости между фразами {gap:.0f} dB - в файле "
                        f"похоже ДВА источника звука (петличка плюс динамик). "
                        f"Константный порог вырежет тихого: на стенде "
                        f"-35 dB убил 11 слов из 140. Порог берём из файла, "
                        f"уровни сводим компандером")
            print(f"настоящей тишины:   {sil:.1f} с "
                  f"({100 * sil / max(dur, 0.01):.0f}% файла)")
            rep["decisions"].append(
                f"потолок честной резки пауз: {dur - sil:.0f} с из {dur:.0f} с. "
                f"Короче - только резкой по смыслу, и это решает автор")
            if sil / max(dur, 0.01) < 0.05:
                rep["warnings"].append(
                    "тишины почти нет - резать паузы бессмысленно, "
                    "динамику придётся делать катами (tools/dynamic.py)")

    # ---------- полоса титров ----------
    if vs is not None and dur > 0:
        band_dir = a.frames or os.path.join(
            tempfile.mkdtemp(prefix="preflight_band_"), "band")
        shots = subtitle_band_frames(src, dur, band_dir,
                                     frame_h=rep.get("frame", {}).get("h"))
        if shots:
            print()
            print("кадры полосы титров (посмотреть ДО рендера):")
            for p in shots:
                print(f"  {p}")
            rep["subtitle_band_frames"] = shots
            rep["decisions"].append(
                "ГЛАЗАМИ проверить кадры полосы титров: если в исходнике титры "
                "УЖЕ выжжены, наши лягут вторым слоем и в кадре будут две "
                "строки об одном. Тогда собирать с --no-subs")

    # ---------- вывод ----------
    print()
    for tag, key in (("СТОП", "blockers"), ("внимание", "warnings"),
                     ("решает автор", "decisions")):
        for m in rep[key]:
            print(f"[{tag}] {m}")

    ok = not rep["blockers"]
    print()
    print("вердикт: можно собирать" if ok else "вердикт: собирать нельзя")

    if a.json:
        json.dump(rep, open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"отчёт: {a.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
