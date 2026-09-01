#!/usr/bin/env python3
"""Сборка ролика-реакции: чужое видео + свои врезки + ускорение скучного.

Жанр и его законы - `knowledge/comedy-reaction.md`. Здесь только механика.

Три типа врезок:

  говорящая ("take")   донор останавливается, Серж говорит, потом донор
                       продолжает с того же места. Перед врезкой пауза
                       замершим кадром - вдох перед ударом.

  немая реакция        видеоряд переключается на лицо Сержа, а РЕЧЬ ДОНОРА
  ("react")            продолжает идти непрерывно. Зритель слышит блогера
                       и видит, как Серж от этого офигевает. Кусок донора
                       под реакцией не дублируется - он «съедается».
                       Паузы перед такой врезкой нет: речь не должна рваться.

  ускорение ("speed")  кусок донора прогоняется быстрее с сохранением
                       высоты голоса. Для мест вида «зайди сюда, нажми туда».

План - JSON:

{
  "donor": "donor.MP4",
  "cuts": [
    {"at": 3.8,  "file": "take_9908.MOV", "take": [0.6, 2.6],
     "type": "реакция"},
    {"at": 34.0, "file": "take_9911.MOV", "take": [0.5, 3.5],
     "react": true, "type": "немая реакция"},
    {"speed": [42.4, 55.8], "factor": 1.9}
  ]
}

  at     - секунда донора, где встаёт врезка
  file   - файл врезки (можно общий "takes" на весь план)
  take   - [начало, конец] куска из файла врезки
  react  - true: немая реакция поверх звука донора
  speed  - [начало, конец] куска донора для ускорения
  factor - во сколько раз (1.4-2.2; выше 2.5 речь не читается)

    python3 interject.py план.json -o ролик.mp4
    python3 interject.py план.json --check     только проверить план
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

W, H, FPS = 1080, 1920, 30

DONOR_LUFS = -16      # донор подсаживается
TAKE_LUFS = -13       # врезка выходит вперёд
PAUSE_BEFORE = 0.30   # пауза перед говорящей врезкой, с
MAX_FACTOR = 2.5      # выше речь превращается в писк


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def atempo_chain(factor):
    """atempo умеет 0.5-2.0 за раз, большее набираем цепочкой."""
    parts, f = [], factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.4f}")
    return ",".join(parts)


VF_FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}")


def cut_piece(src, t0, t1, dest, speed=None, lufs=None, quiet_tail=0.0):
    """Кусок: вырезать, при нужде ускорить, выровнять громкость.

    Режем по одному куску за проход - многократный декод вертикали
    из одного входа съедает память и процесс убивает OOM.
    """
    vf = [VF_FIT]
    af = []
    if speed and abs(speed - 1.0) > 0.01:
        vf.insert(0, f"setpts=PTS/{speed:.4f}")
        af.append(atempo_chain(speed))
    if lufs is not None:
        af.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    if quiet_tail > 0:
        dur = (t1 - t0) / (speed or 1.0)
        af.append(f"afade=t=out:st={max(dur - quiet_tail, 0):.3f}:"
                  f"d={quiet_tail:.3f}")

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}",
           "-i", src, "-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", dest]
    subprocess.run(cmd, check=True)
    return dest


def react_piece(take_src, t0, t1, donor_src, d0, d1, dest):
    """Немая реакция: видео из врезки, ЗВУК ИЗ ДОНОРА.

    Речь блогера не прерывается, а зритель видит лицо. Это и есть
    приём «моя реакция»: без него вставка рвёт чужую фразу пополам.
    """
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", take_src,
           "-ss", f"{d0:.3f}", "-to", f"{d1:.3f}", "-i", donor_src,
           "-map", "0:v:0", "-map", "1:a:0",
           "-vf", VF_FIT,
           "-af", f"loudnorm=I={DONOR_LUFS}:TP=-1.5:LRA=11",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-shortest", dest]
    subprocess.run(cmd, check=True)
    return dest


def freeze_pause(src, dest, seconds):
    """Пауза перед говорящей врезкой: последний кадр донора замирает.

    Не чёрный кадр и не склейка встык: замерший кадр читается как
    «человек договорил, повисла тишина» - вдох перед ударом.
    """
    frame = dest + ".png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1", "-i", src,
                    "-vframes", "1", frame], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{seconds:.3f}",
         "-i", frame, "-f", "lavfi", "-t", f"{seconds:.3f}",
         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-vf", f"scale={W}:{H},setsar=1,fps={FPS}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", dest],
        check=True)
    os.remove(frame)
    return dest


def validate(plan, donor_dur, take_durs):
    """Проверка плана до рендера: битый план должен падать сразу."""
    errors, warnings = [], []
    cuts = plan.get("cuts", [])
    if not cuts:
        errors.append("в плане нет ни одной врезки")

    inserts = [c for c in cuts if "take" in c]
    speeds = [c for c in cuts if "speed" in c]

    for i, c in enumerate(cuts, 1):
        if "take" in c:
            if "at" not in c:
                errors.append(f"врезка {i}: нет поля at")
                continue
            if not 0 <= c["at"] <= donor_dur:
                errors.append(f"врезка {i}: at={c['at']} вне донора "
                              f"(0-{donor_dur:.1f})")
            t0, t1 = c["take"]
            if t1 <= t0:
                errors.append(f"врезка {i}: take конец раньше начала")
            src_dur = take_durs.get(c.get("_file"), 0)
            if t1 > src_dur + 0.5:
                errors.append(f"врезка {i}: take={t1} вне файла "
                              f"{os.path.basename(c.get('_file', '?'))} "
                              f"(0-{src_dur:.1f})")
            if not c.get("react") and t1 - t0 > 25:
                warnings.append(f"врезка {i}: длина {t1 - t0:.0f} с - "
                                f"это уже лекция, а не реакция")
            if c.get("react") and t1 - t0 > 6:
                warnings.append(f"врезка {i}: немая реакция {t1 - t0:.0f} с - "
                                f"длинновато, лицо без речи держит 1-4 с")
        if "speed" in c:
            s0, s1 = c["speed"]
            f = c.get("factor", 1.8)
            if s1 <= s0:
                errors.append(f"ускорение {i}: конец раньше начала")
            if f > MAX_FACTOR:
                errors.append(f"ускорение {i}: {f}х - выше {MAX_FACTOR}х "
                              f"речь не читается")

    # пересечения: ускорения между собой и с немыми реакциями
    spans = [(c["speed"][0], c["speed"][1], f"ускорение") for c in speeds]
    for c in inserts:
        if c.get("react"):
            d = c["take"][1] - c["take"][0]
            spans.append((c["at"], c["at"] + d, "немая реакция"))
    spans.sort()
    for a, b in zip(spans, spans[1:]):
        if b[0] < a[1] - 0.01:
            errors.append(f"{a[2]} {a[0]:.1f}-{a[1]:.1f} пересекается с "
                          f"{b[2]} {b[0]:.1f}-{b[1]:.1f}")

    # закон жанра: плотность должна расти к финалу
    if len(inserts) >= 3 and donor_dur > 30:
        ats = sorted(c["at"] for c in inserts)
        half = donor_dur / 2
        first = sum(1 for t in ats if t < half)
        second = len(ats) - first
        if first > second:
            warnings.append(
                f"врезок в первой половине больше ({first} против {second}) - "
                f"по закону жанра плотность должна РАСТИ к финалу, "
                f"иначе зритель выдыхается на первой минуте")
    return errors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("-o", "--output")
    ap.add_argument("--check", action="store_true", help="только проверить план")
    ap.add_argument("--pause", type=float, default=PAUSE_BEFORE,
                    help="пауза перед говорящей врезкой, с")
    a = ap.parse_args()

    plan = json.load(open(os.path.expanduser(a.plan), encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(os.path.expanduser(a.plan)))

    def resolve(p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(base, p)

    donor = resolve(plan["donor"])
    if not os.path.exists(donor):
        sys.exit(f"донора нет: {donor}")
    donor_dur = probe_duration(donor)
    print(f"донор:  {os.path.basename(donor)}  {donor_dur:.1f} с")

    # каждая врезка может лежать в своём файле
    take_durs = {}
    for c in plan.get("cuts", []):
        if "take" not in c:
            continue
        f = resolve(c.get("file") or plan.get("takes", ""))
        if not f or not os.path.exists(f):
            sys.exit(f"файла врезки нет: {f or '(не указан)'}")
        c["_file"] = f
        if f not in take_durs:
            take_durs[f] = probe_duration(f)
    for f, d in take_durs.items():
        print(f"врезка: {os.path.basename(f):<20} {d:.1f} с")

    errors, warnings = validate(plan, donor_dur, take_durs)
    for w in warnings:
        print(f"  [внимание] {w}")
    for e in errors:
        print(f"  [ОШИБКА]   {e}")
    if errors:
        sys.exit("план не годится - сборка остановлена")
    print("  план проверен, ошибок нет")
    if a.check:
        return 0

    out = os.path.abspath(os.path.expanduser(
        a.output or os.path.splitext(donor)[0] + "_реакция.mp4"))
    wd = tempfile.mkdtemp(prefix="interject_")

    cuts = plan["cuts"]
    speaking = sorted((c for c in cuts if "take" in c and not c.get("react")),
                      key=lambda c: c["at"])
    reacts = sorted((c for c in cuts if c.get("react")), key=lambda c: c["at"])
    speeds = sorted((c for c in cuts if "speed" in c),
                    key=lambda c: c["speed"][0])

    # Границы кусков донора: точки говорящих врезок, границы ускорений
    # и границы немых реакций (они съедают свой отрезок донора).
    marks = {0.0, donor_dur}
    marks |= {c["at"] for c in speaking}
    marks |= {t for c in speeds for t in c["speed"]}
    for c in reacts:
        d = c["take"][1] - c["take"][0]
        marks |= {c["at"], min(c["at"] + d, donor_dur)}
    marks = sorted(marks)

    pieces, n = [], 0
    print("\nсобираю…")
    for x, y in zip(marks, marks[1:]):
        if y - x < 0.05:
            continue

        # этот отрезок донора занят немой реакцией?
        rc = next((c for c in reacts if abs(c["at"] - x) < 0.01), None)
        if rc:
            t0, t1 = rc["take"]
            dst = f"{wd}/r{n:03d}.mp4"
            react_piece(rc["_file"], t0, t1, donor, x, y, dst)
            pieces.append(dst)
            n += 1
            print(f"  РЕАКЦИЯ  на {x:6.1f}  немая, {y - x:.1f} с  "
                  f"({os.path.basename(rc['_file'])}) - речь донора идёт")
            continue

        sp = next((c for c in speeds
                   if c["speed"][0] <= x + 0.01 and y <= c["speed"][1] + 0.01),
                  None)
        factor = sp.get("factor", 1.8) if sp else None
        tail = 0.15 if any(abs(c["at"] - y) < 0.01 for c in speaking) else 0.0
        dst = f"{wd}/d{n:03d}.mp4"
        cut_piece(donor, x, y, dst, speed=factor, lufs=DONOR_LUFS,
                  quiet_tail=tail)
        pieces.append(dst)
        n += 1
        if factor:
            print(f"  донор    {x:6.1f}-{y:6.1f}  ускорен {factor}х")

        for c in speaking:
            if abs(c["at"] - y) < 0.01:
                if a.pause > 0:
                    pz = f"{wd}/p{n:03d}.mp4"
                    freeze_pause(dst, pz, a.pause)
                    pieces.append(pz)
                    n += 1
                t0, t1 = c["take"]
                iv = f"{wd}/i{n:03d}.mp4"
                cut_piece(c["_file"], t0, t1, iv, lufs=TAKE_LUFS)
                pieces.append(iv)
                n += 1
                print(f"  ВРЕЗКА   на {c['at']:6.1f}  "
                      f"{c.get('type', 'врезка'):<14} {t1 - t0:.1f} с  "
                      f"({os.path.basename(c['_file'])})")

    lst = f"{wd}/list.txt"
    with open(lst, "w") as fh:
        for p in pieces:
            fh.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True)
    shutil.rmtree(wd, ignore_errors=True)

    final = probe_duration(out)
    own = sum(c["take"][1] - c["take"][0] for c in speaking)
    own_react = sum(c["take"][1] - c["take"][0] for c in reacts)
    share = 100 * (own + own_react) / max(final, 0.01)
    print(f"\nбыло {donor_dur:.1f} с донора -> стало {final:.1f} с")
    print(f"говорящих врезок: {len(speaking)}, немых реакций: {len(reacts)}, "
          f"ускорено кусков: {len(speeds)}")
    print(f"своего в кадре: {own + own_react:.1f} с ({share:.0f}% ролика)")
    if share < 25:
        print("  [внимание] своего мало - площадки считают такое "
              "перезаливом чужого, а не пародией")
    print(f"готово: {out}")
    print("\nДальше: ./staticscore на результат, для этого жанра норма <= 1.5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
