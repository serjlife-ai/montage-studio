#!/usr/bin/env python3
"""Гибрид: говорящая голова уезжает в окошко, рядом освобождается место под текст.

Зачем. На крупном плане титры ложатся поверх лица и спорят с мимикой. Но убрать
лицо совсем нельзя - на хуке и панчах эмоция и есть содержание. Отсюда гибрид:
полный кадр там, где работает лицо, окошко там, где работает текст.

Инструмент меняет ТОЛЬКО геометрию. Текст в освободившуюся зону кладёт
creative.py, титры в оконных кусках выключаются (иначе спорят снова).

    python3 window.py вход.mp4 окна.json выход.mp4
    окна.json: [{"start": 6.58, "end": 10.38}, ...]
"""
import json, os, subprocess, sys

W, H, FPS = 1080, 1920, 30
WIN_W = 520                     # ширина окошка
WIN_X = (W - WIN_W) // 2
WIN_Y = 150
BG = "0x0A0A0C"
MIN_SEG = 0.30


def win_h():
    h = int(WIN_W * H / W)
    return h // 2 * 2


def build(src, wins, out):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip())

    wins = sorted([(float(w["start"]), float(w["end"])) for w in wins])
    for i, (a, b) in enumerate(wins):
        assert b > a, f"окно {i}: конец раньше начала"
        assert b - a >= MIN_SEG, f"окно {i}: короче {MIN_SEG}s - мигнёт и пропадёт"
        if i:
            assert a >= wins[i - 1][1], f"окно {i} налезает на предыдущее"
    assert wins[-1][1] <= dur + 0.05, "окно выходит за длину ролика"

    # режем таймлайн на чередование полный кадр / окошко
    segs, cur = [], 0.0
    for a, b in wins:
        if a - cur > 0.01:
            segs.append((cur, a, False))
        segs.append((a, b, True))
        cur = b
    if dur - cur > 0.01:
        segs.append((cur, dur, False))

    wh = win_h()
    parts, cat = [], []
    for i, (a, b, is_win) in enumerate(segs):
        if is_win:
            parts.append(
                f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                f"scale={WIN_W}:{wh}:flags=lanczos,"
                f"pad={W}:{H}:{WIN_X}:{WIN_Y}:color={BG}[v{i}];")
        else:
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];")
        cat.append(f"[v{i}]")
    fc = "".join(parts) + "".join(cat) + f"concat=n={len(segs)}:v=1:a=0[vo]"
    fp = out + ".filter.txt"
    open(fp, "w").write(fc)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
         "-filter_complex_script", fp, "-map", "[vo]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "copy", out], check=True)
    os.remove(fp)
    win_time = sum(b - a for a, b, w in segs if w)
    print(f"окон: {len(wins)}  в окне {win_time:.1f}s из {dur:.1f}s "
          f"({100*win_time/dur:.0f}%)  окно {WIN_W}x{wh} на {WIN_X},{WIN_Y}")
    return wins


if __name__ == "__main__":
    a = sys.argv[1:]
    build(a[0], json.load(open(a[1])), a[2])
