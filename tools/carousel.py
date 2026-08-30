#!/usr/bin/env python3
"""Динамичная карусель Instagram: нарезка панорамы и проверка комплекта перед загрузкой.

Две команды.

  slice  - режет широкий макет на N бесшовных слайдов заданного соотношения.
           Работает и с картинкой, и с видео (одним и тем же кропом).

  check  - проверяет готовый комплект слайдов перед загрузкой: одинаковое ли
           соотношение сторон, влезает ли в лимиты Instagram, не разъедется ли
           карусель. Инстаграм обрезает по первому слайду - расхождение ловим тут.

Примеры:
  python3 tools/carousel.py slice панорама.png --slides 2
  python3 tools/carousel.py slice панорама.mp4 --slides 2 --ratio 4:5 -o out/
  python3 tools/carousel.py check out/*.png
"""

import argparse
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}

# Лимиты ленты Instagram. Вертикаль ограничена сверху, квадрат и горизонт - снизу.
IG_MIN_RATIO = Fraction(3, 4)   # самый вытянутый вертикальный кадр
IG_MAX_RATIO = Fraction(191, 100)  # 1.91:1, самый широкий горизонтальный


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def probe(path: Path) -> dict:
    """Размеры и длительность файла. Одинаково для картинки и видео."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        die(f"не читается {path.name}: {out.stderr.strip().splitlines()[-1] if out.stderr.strip() else 'ffprobe молчит'}")
    data = json.loads(out.stdout)
    if not data.get("streams"):
        die(f"в {path.name} нет видеопотока")
    st = data["streams"][0]
    dur = st.get("duration") or data.get("format", {}).get("duration")
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = None
    return {"w": int(st["width"]), "h": int(st["height"]), "dur": dur}


def parse_ratio(text: str) -> Fraction:
    if ":" not in text:
        die(f"соотношение пишется как 3:4, а не {text!r}")
    a, b = text.split(":", 1)
    try:
        f = Fraction(int(a), int(b))
    except (ValueError, ZeroDivisionError):
        die(f"соотношение пишется как 3:4, а не {text!r}")
    if f <= 0:
        die("соотношение должно быть положительным")
    return f


def even(n: int) -> int:
    """Кодеки любят чётные размеры. Картинкам не мешает."""
    return n - (n % 2)


def cmd_slice(args: argparse.Namespace) -> None:
    src = Path(args.source)
    if not src.exists():
        die(f"нет файла {src}")
    if args.slides < 1:
        die("слайдов не может быть меньше одного")

    ratio = parse_ratio(args.ratio)
    info = probe(src)

    # Целевой слайд: ширина задана, высота из соотношения.
    sw = even(args.width)
    sh = even(round(sw / ratio))
    total_w, total_h = sw * args.slides, sh

    # Панорама масштабируется так, чтобы ПОКРЫТЬ полотно, лишнее срезается по центру.
    scale = max(total_w / info["w"], total_h / info["h"])
    fit_w, fit_h = even(round(info["w"] * scale)), even(round(info["h"] * scale))
    cut_x, cut_y = (fit_w - total_w) // 2, (fit_h - total_h) // 2

    src_ratio = Fraction(info["w"], info["h"])
    need_ratio = Fraction(total_w, total_h)
    loss = 1 - min(src_ratio / need_ratio, need_ratio / src_ratio)
    if loss > 0.02:
        print(f"! панорама {info['w']}x{info['h']} ({float(src_ratio):.2f}:1) "
              f"не совпадает с нужной {float(need_ratio):.2f}:1 - "
              f"срежется около {loss * 100:.0f}% кадра по краю")

    is_video = src.suffix.lower() in VIDEO_EXT
    out_dir = Path(args.out) if args.out else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() if is_video else ".png"

    made = []
    for i in range(args.slides):
        suffix = "_кадр" if args.slides == 1 else f"_слайд{i + 1}"
        dst = out_dir / f"{src.stem}{suffix}{ext}"
        vf = (f"scale={fit_w}:{fit_h}:flags=lanczos,"
              f"crop={sw}:{sh}:{cut_x + i * sw}:{cut_y}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf]
        if is_video:
            cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "slow",
                    "-pix_fmt", "yuv420p", "-c:a", "copy"]
        else:
            cmd += ["-frames:v", "1"]
        cmd.append(str(dst))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            die(f"ffmpeg не собрал {dst.name}: {r.stderr.strip()}")
        made.append(dst)

    if args.slides == 1:
        print(f"Кадр приведён к {sw}x{sh} ({args.ratio}): {made[0].name}")
        return
    print(f"Нарезано {len(made)} слайдов {sw}x{sh} ({args.ratio}) в {out_dir}")
    for p in made:
        print(f"  {p.name}")
    print("\nПорядок загрузки в Instagram - слева направо, как напечатано выше.")


def cmd_check(args: argparse.Namespace) -> None:
    paths = [Path(p) for p in args.slides]
    missing = [p for p in paths if not p.exists()]
    if missing:
        die("нет файлов: " + ", ".join(p.name for p in missing))
    if len(paths) < 2:
        die("карусель - это минимум 2 слайда")
    if len(paths) > 20:
        print(f"! слайдов {len(paths)}, Instagram примет только первые 20")

    rows, problems = [], []
    base = None
    for p in paths:
        info = probe(p)
        r = Fraction(info["w"], info["h"])
        if base is None:
            base = r
        rows.append((p, info, r))

    print(f"{'файл':<34} {'размер':>12} {'соотношение':>12}  тип")
    for p, info, r in rows:
        kind = "видео" if p.suffix.lower() in VIDEO_EXT else "фото"
        if info["dur"] and kind == "видео":
            kind += f" {info['dur']:.1f}с"
        print(f"{p.name:<34} {info['w']}x{info['h']:>6} {float(r):>11.3f}  {kind}")

        if r != base:
            problems.append(f"{p.name}: соотношение {float(r):.3f} против {float(base):.3f} "
                            f"у первого слайда - Instagram обрежет его по первому")
        if r < IG_MIN_RATIO:
            problems.append(f"{p.name}: вертикаль {float(r):.3f} уже лимита 3:4 (0.750) - обрежет сверху и снизу")
        if r > IG_MAX_RATIO:
            problems.append(f"{p.name}: горизонт {float(r):.3f} шире лимита 1.91:1 - обрежет по бокам")
        if info["w"] < 1080:
            problems.append(f"{p.name}: ширина {info['w']} меньше 1080 - Instagram будет тянуть и мылить")
        if p.suffix.lower() in VIDEO_EXT and info["dur"] and info["dur"] > 60:
            problems.append(f"{p.name}: {info['dur']:.0f}с, лимит слайда 60с")

    print()
    if problems:
        print("НЕ ГОТОВО К ЗАГРУЗКЕ:")
        for pr in problems:
            print(f"  - {pr}")
        sys.exit(1)
    print(f"Готово к загрузке: {len(rows)} слайдов, соотношение {float(base):.3f} у всех.")


def frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(out.stdout.strip())
    except ValueError:
        die(f"не считаются кадры в {path.name}")


def seam_gap(path: Path) -> float:
    """Насколько последний кадр отличается от первого. 0 - цикл бесшовный."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        first, last = Path(tmp) / "a.png", Path(tmp) / "b.png"
        n = frame_count(path)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                        "-vf", "select=eq(n\\,0)", "-frames:v", "1", str(first)],
                       capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n - 1})", "-frames:v", "1", str(last)],
                       capture_output=True)
        if not first.exists() or not last.exists():
            return -1.0
        try:
            from PIL import Image, ImageChops, ImageStat
        except ImportError:
            return -1.0
        a = Image.open(first).convert("RGB")
        b = Image.open(last).convert("RGB")
        return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def cmd_loop(args: argparse.Namespace) -> None:
    src = Path(args.source)
    if not src.exists():
        die(f"нет файла {src}")
    if src.suffix.lower() not in VIDEO_EXT:
        die("зацикливать имеет смысл видео, а не картинку")

    info = probe(src)
    if not info["dur"]:
        die("не читается длительность")
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_цикл{src.suffix}")

    if args.mode == "pingpong":
        # Прямой ход + обратный без крайних кадров: стык гладкий с обеих сторон.
        n = frame_count(src)
        if n < 4:
            die(f"в ролике {n} кадров, зацикливать нечего")
        vf = (f"[0:v]split[a][b];"
              f"[b]reverse,select='between(n\\,1\\,{n - 2})',setpts=N/FRAME_RATE/TB[r];"
              f"[a][r]concat=n=2:v=1[out]")
        note = f"пинг-понг, итог около {info['dur'] * 2:.1f}с"
    else:
        f = args.fade
        d = info["dur"]
        if f <= 0 or f >= d / 2:
            die(f"растворение {f}с не влезает в ролик {d:.1f}с")
        vf = (f"[0:v]trim=0:{f},setpts=PTS-STARTPTS[head];"
              f"[0:v]trim={d - f}:{d},setpts=PTS-STARTPTS[tail];"
              f"[head][tail]blend=all_expr='A*(T/{f})+B*(1-T/{f})'[mix];"
              f"[0:v]trim={f}:{d - f},setpts=PTS-STARTPTS[body];"
              f"[mix][body]concat=n=2:v=1[out]")
        note = f"растворение {f}с, итог около {d - f:.1f}с"

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-filter_complex", vf, "-map", "[out]",
           "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-pix_fmt", "yuv420p",
           "-an", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg не собрал цикл: {r.stderr.strip()}")

    before, after = seam_gap(src), seam_gap(out)
    res = probe(out)
    print(f"{out.name}: {res['w']}x{res['h']}, {res['dur']:.1f}с, {note}")
    if before >= 0 and after >= 0:
        print(f"разрыв на стыке: было {before:.1f}, стало {after:.1f} (из 255)")
        if after > before:
            print("! стык стал хуже, посмотри глазами перед публикацией")


def main() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        die("нужен ffmpeg (brew install ffmpeg)")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("slice", help="разрезать панораму на бесшовные слайды")
    s.add_argument("source", help="широкий макет: картинка или видео")
    s.add_argument("--slides", type=int, default=2, help="на сколько слайдов резать (по умолчанию 2)")
    s.add_argument("--ratio", default="3:4", help="соотношение одного слайда (по умолчанию 3:4)")
    s.add_argument("--width", type=int, default=1080, help="ширина одного слайда в пикселях")
    s.add_argument("-o", "--out", help="куда класть слайды")
    s.set_defaults(func=cmd_slice)

    l = sub.add_parser("loop", help="сделать видео-слайд бесшовно зацикленным")
    l.add_argument("source", help="видео от image-to-video модели")
    l.add_argument("--mode", choices=["pingpong", "crossfade"], default="pingpong",
                   help="pingpong - прямой ход и обратный, надёжно всегда; "
                        "crossfade - растворение хвоста в начало, для направленного движения")
    l.add_argument("--fade", type=float, default=0.5, help="секунд растворения для crossfade")
    l.add_argument("-o", "--out", help="куда сохранить")
    l.set_defaults(func=cmd_loop)

    c = sub.add_parser("check", help="проверить комплект слайдов перед загрузкой")
    c.add_argument("slides", nargs="+", help="файлы слайдов в порядке публикации")
    c.set_defaults(func=cmd_check)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
