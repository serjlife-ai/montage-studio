#!/usr/bin/env python3
"""Окклюзия: текст уходит ЗА персонажа в каждом кадре, а не лежит поверх кадра.

Даёт объём вместо плоской плашки. Маска переднего плана считается покадрово,
поэтому персонаж может двигаться - поворот головы не рассинхронит эффект.

  python3 tools/occlude.py видео.mp4 текст.png -o слайд.mp4

Текстовый слой берётся от `slide.py --overlay` (RGBA той же геометрии).
Работает на тёмном фоне: персонаж отделяется от чёрного порогом яркости,
дыры внутри силуэта (тёмные очки, одежда) заливаются.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        die(f"не читается {path.name}")
    w, h, rate, frames = out.stdout.split()
    num, den = rate.split("/")
    return {"w": int(w), "h": int(h), "fps": float(num) / float(den),
            "frames": int(frames) if frames.isdigit() else 0}


def foreground(frame: np.ndarray, thr: int, grow: int, feather: float) -> Image.Image:
    """Маска персонажа: порог по яркости, чистка тонких линий фона, заливка дыр."""
    lum = Image.fromarray(frame.max(axis=2))
    m = lum.point(lambda v: 255 if v > thr else 0)
    # opening: неоновые линии фона тоньше персонажа и стираются
    m = m.filter(ImageFilter.MinFilter(grow)).filter(ImageFilter.MaxFilter(grow))

    # заливка дыр: тёмные очки и одежда внутри силуэта должны стать передним планом
    inv = m.point(lambda v: 0 if v else 255)
    w, h = inv.size
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    for s in seeds:
        if inv.getpixel(s) == 255:
            ImageDraw.floodfill(inv, s, 128)
    holes = inv.point(lambda v: 255 if v == 255 else 0)
    m = Image.fromarray(np.maximum(np.asarray(m), np.asarray(holes)))

    return m.filter(ImageFilter.GaussianBlur(feather)) if feather > 0 else m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="видео с персонажем на тёмном фоне")
    ap.add_argument("overlay", help="PNG RGBA: слой, который уходит ЗА персонажа")
    ap.add_argument("--front", help="PNG RGBA: слой, который всегда поверх (плашки, призыв)")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--threshold", type=int, default=10, help="порог яркости фона")
    ap.add_argument("--grow", type=int, default=9, help="ядро чистки тонких линий, нечётное")
    ap.add_argument("--feather", type=float, default=1.2, help="мягкость края маски")
    ap.add_argument("--contact", type=float, default=0.0,
                    help="контактная тень: буквы темнеют у силуэта, 0..1")
    args = ap.parse_args()

    src, ov = Path(args.video), Path(args.overlay)
    for p in (src, ov):
        if not p.exists():
            die(f"нет файла {p}")
    if args.grow % 2 == 0:
        die("--grow должен быть нечётным")

    info = probe(src)
    w, h = info["w"], info["h"]
    text = Image.open(ov).convert("RGBA")
    if text.size != (w, h):
        die(f"оверлей {text.size} не совпадает с кадром {w}x{h}")

    t = np.asarray(text).astype(np.float32)
    text_rgb, text_a = t[..., :3], (t[..., 3:4] / 255.0)

    front_rgb = front_a = None
    if args.front:
        fr = Image.open(args.front).convert("RGBA")
        if fr.size != (w, h):
            die(f"передний слой {fr.size} не совпадает с кадром {w}x{h}")
        fa = np.asarray(fr).astype(np.float32)
        front_rgb, front_a = fa[..., :3], (fa[..., 3:4] / 255.0)

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", str(info["fps"]), "-i", "-",
         "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
         str(args.out)], stdin=subprocess.PIPE)

    size = w * h * 3
    done, covered = 0, []
    text_px = float((text_a > 0.5).sum())
    while True:
        raw = dec.stdout.read(size)
        if len(raw) < size:
            break
        frame = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        mask = np.asarray(foreground(frame, args.threshold, args.grow, args.feather),
                          dtype=np.float32)[..., None] / 255.0

        a = text_a
        if args.contact > 0:
            # у силуэта буквы притемняются - глаз читает это как контактную тень
            near = np.asarray(
                Image.fromarray((mask[..., 0] * 255).astype(np.uint8))
                .filter(ImageFilter.GaussianBlur(14)), dtype=np.float32)[..., None] / 255.0
            shade = 1.0 - args.contact * np.clip(near - mask, 0, 1)
        else:
            shade = 1.0

        out = frame.astype(np.float32)
        out = out * (1 - a) + text_rgb * shade * a      # текст поверх кадра
        out = out * (1 - mask) + frame.astype(np.float32) * mask   # персонаж возвращается вперёд
        if front_a is not None:
            out = out * (1 - front_a) + front_rgb * front_a         # плашки поверх всего
        enc.stdin.write(np.clip(out, 0, 255).astype(np.uint8).tobytes())

        if text_px:
            covered.append(float(((text_a[..., 0] > 0.5) & (mask[..., 0] > 0.5)).sum()) / text_px)
        done += 1

    dec.stdout.close()
    enc.stdin.close()
    dec.wait()
    if enc.wait() != 0:
        die("ffmpeg не собрал результат")

    if not done:
        die("не прочитано ни одного кадра")
    lo, hi = min(covered) * 100, max(covered) * 100
    print(f"{Path(args.out).name}: кадров {done}, за персонажа уходит "
          f"{lo:.1f}-{hi:.1f}% текста (меняется, пока он двигается)")
    if hi > 35:
        print("! перекрытие больше 35% - слово может не читаться, подними текст")


if __name__ == "__main__":
    main()
