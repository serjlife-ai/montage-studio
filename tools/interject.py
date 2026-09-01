#!/usr/bin/env python3
"""Сборка ролика-реакции: чужое видео + свои врезки + ускорение скучного.

Жанр и его законы - `knowledge/comedy-reaction.md`. Здесь только механика.

Что делает:
  - режет донора в точках, указанных в плане;
  - вставляет между кусками врезки из своей записи;
  - держит паузу перед врезкой (0.2-0.4 с) - без неё это перебивание,
    а не панч;
  - ускоряет помеченные куски донора с сохранением высоты голоса
    (atempo, не setpts: setpts на звуке превращает речь в бурундука);
  - сводит уровни: донор тише, врезка ближе к зрителю на 3-4 dB.

План - JSON:

{
  "donor": "донор.mp4",
  "takes": "врезки.mp4",
  "cuts": [
    {"at": 12.4, "take": [3.1, 4.6], "type": "реакция"},
    {"at": 38.2, "take": [15.0, 22.4], "type": "комментарий"},
    {"speed": [45.0, 58.0], "factor": 2.0}
  ]
}

  at     - секунда донора, ПОСЛЕ которой встаёт врезка
  take   - [начало, конец] куска из файла врезок
  speed  - [начало, конец] куска донора, который ускоряем
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
PAUSE_BEFORE = 0.30   # пауза перед врезкой, с
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


def cut_piece(src, t0, t1, dest, speed=None, lufs=None, quiet_tail=0.0):
    """Один кусок: вырезать, при нужде ускорить, выровнять громкость.

    Режем по одному куску за проход - многократный декод вертикали
    из одного входа съедает память и процесс убивает OOM.
    """
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=decrease",
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2", f"fps={FPS}"]
    af = []
    if speed and abs(speed - 1.0) > 0.01:
        vf.insert(0, f"setpts=PTS/{speed:.4f}")
        af.append(atempo_chain(speed))
    if lufs is not None:
        af.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    if quiet_tail > 0:
        dur = (t1 - t0) / (speed or 1.0)
        start = max(dur - quiet_tail, 0)
        af.append(f"afade=t=out:st={start:.3f}:d={quiet_tail:.3f}")

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}",
           "-i", src, "-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", dest]
    subprocess.run(cmd, check=True)
    return dest


def freeze_pause(src, dest, seconds):
    """Пауза перед врезкой: последний кадр донора замирает.

    Не чёрный кадр и не склейка встык: замерший кадр читается как
    «человек договорил, повисла тишина» - это и есть вдох перед ударом.
    """
    frame = dest + ".png"
    dur = probe_duration(src)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1", "-i", src,
                    "-vframes", "1", frame], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{seconds:.3f}",
         "-i", frame, "-f", "lavfi", "-t", f"{seconds:.3f}",
         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-vf", f"scale={W}:{H},fps={FPS}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", dest],
        check=True)
    os.remove(frame)
    return dest


def validate(plan, donor_dur, takes_dur):
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
            if t1 > takes_dur + 0.5:
                errors.append(f"врезка {i}: take={t1} вне файла врезок "
                              f"(0-{takes_dur:.1f})")
            if t1 - t0 > 25:
                warnings.append(f"врезка {i}: длина {t1 - t0:.0f} с - "
                                f"это уже лекция, а не реакция")
        if "speed" in c:
            s0, s1 = c["speed"]
            f = c.get("factor", 1.8)
            if s1 <= s0:
                errors.append(f"ускорение {i}: конец раньше начала")
            if f > MAX_FACTOR:
                errors.append(f"ускорение {i}: {f}х - выше {MAX_FACTOR}х "
                              f"речь не читается")
            if f < 1.05:
                warnings.append(f"ускорение {i}: {f}х - это почти ничего")

    # пересечения ускоряемых кусков
    ranges = sorted((c["speed"] for c in speeds), key=lambda r: r[0])
    for a, b in zip(ranges, ranges[1:]):
        if b[0] < a[1]:
            errors.append(f"ускорения пересекаются: {a} и {b}")

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
                    help="пауза перед врезкой, с")
    a = ap.parse_args()

    plan = json.load(open(os.path.expanduser(a.plan), encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(os.path.expanduser(a.plan)))

    def resolve(p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(base, p)

    donor = resolve(plan["donor"])
    takes = resolve(plan["takes"]) if plan.get("takes") else None
    for f in (donor, takes):
        if f and not os.path.exists(f):
            sys.exit(f"файла нет: {f}")

    donor_dur = probe_duration(donor)
    takes_dur = probe_duration(takes) if takes else 0.0
    print(f"донор:  {os.path.basename(donor)}  {donor_dur:.1f} с")
    if takes:
        print(f"врезки: {os.path.basename(takes)}  {takes_dur:.1f} с")

    errors, warnings = validate(plan, donor_dur, takes_dur)
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
    inserts = sorted((c for c in cuts if "take" in c), key=lambda c: c["at"])
    speeds = sorted((c for c in cuts if "speed" in c),
                    key=lambda c: c["speed"][0])

    # Строим ленту донора: обычные куски и ускоренные, в порядке времени.
    marks = sorted({0.0, donor_dur}
                   | {c["at"] for c in inserts}
                   | {t for c in speeds for t in c["speed"]})
    pieces, n = [], 0
    print("\nсобираю…")
    for x, y in zip(marks, marks[1:]):
        if y - x < 0.05:
            continue
        sp = next((c for c in speeds
                   if c["speed"][0] <= x + 0.01 and y <= c["speed"][1] + 0.01),
                  None)
        factor = sp.get("factor", 1.8) if sp else None
        # хвост донора приглушаем только перед врезкой
        tail = 0.15 if any(abs(c["at"] - y) < 0.01 for c in inserts) else 0.0
        dst = f"{wd}/d{n:03d}.mp4"
        cut_piece(donor, x, y, dst, speed=factor, lufs=DONOR_LUFS,
                  quiet_tail=tail)
        pieces.append(dst)
        n += 1
        if factor:
            print(f"  донор {x:6.1f}-{y:6.1f}  ускорен {factor}х")

        for c in inserts:
            if abs(c["at"] - y) < 0.01:
                if a.pause > 0:
                    pz = f"{wd}/p{n:03d}.mp4"
                    freeze_pause(dst, pz, a.pause)
                    pieces.append(pz)
                    n += 1
                t0, t1 = c["take"]
                iv = f"{wd}/i{n:03d}.mp4"
                cut_piece(takes, t0, t1, iv, lufs=TAKE_LUFS)
                pieces.append(iv)
                n += 1
                kind = c.get("type", "врезка")
                print(f"  ВРЕЗКА на {c['at']:6.1f}  {kind:<12} "
                      f"{t1 - t0:.1f} с")

    lst = f"{wd}/list.txt"
    with open(lst, "w") as fh:
        for p in pieces:
            fh.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True)
    shutil.rmtree(wd, ignore_errors=True)

    final = probe_duration(out)
    print(f"\nбыло {donor_dur:.1f} с донора -> стало {final:.1f} с")
    print(f"врезок: {len(inserts)}, ускорено кусков: {len(speeds)}")
    own = sum(c['take'][1] - c['take'][0] for c in inserts)
    share = 100 * own / max(final, 0.01)
    print(f"своего материала: {own:.1f} с ({share:.0f}% ролика)")
    if share < 25:
        print("  [внимание] своего мало - площадки считают такое "
              "перезаливом чужого, а не пародией")
    print(f"готово: {out}")
    print("\nДальше: ./staticscore на результат, для этого жанра норма <= 1.5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
