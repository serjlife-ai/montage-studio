#!/usr/bin/env python3
"""Верстка слайда карусели: фото от нейросети снизу, текст нашим шрифтом сверху.

Второй слой закона двух слоёв из knowledge/dynamic-carousel.md. Нейросеть даёт
визуал, буквы ставит этот инструмент - тогда кириллица не плывёт, а правка
формулировки не требует перегенерации картинки.

  python3 tools/slide.py слайд1.json -o слайд1.png

Формат плана - см. FORMAT ниже и knowledge/carousel-cyberpunk-serge.md.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FORMAT = """
{
  "canvas": [1080, 1440],
  "photo": {
    "src": "панорама.png",
    "crop": [120, 10, 1120, 1024],   // необязательно: область исходника
    "height": 900,                    // высота на слайде
    "anchor": "bottom-right",         // bottom-right | bottom-left | bottom-center
    "bleed": 130                      // на сколько уйти за край кадра
  },
  "layers": [
    {"type": "text",  "lines": ["СТРОКА", "ЕЩЁ"], "pos": [70, 100],
     "size": 104, "leading": 116},
    {"type": "arrow", "from": [74, 630], "to": [360, 630], "width": 6},
    {"type": "badge", "pos": [118, 1210], "radius": 46,
     "lines": ["Цепляет", "с первого слайда"], "size": 36}
  ]
}
"""

FONT_CANDIDATES = [
    os.environ.get("SLIDE_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]

# Safe zone: интерфейс Instagram ест края, текст в них теряется.
SAFE = 0.94


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def font_path() -> str:
    p = next((p for p in FONT_CANDIDATES if p and Path(p).exists()), "")
    if not p:
        die("не найден шрифт с кириллицей, задай SLIDE_FONT=/путь/к/шрифту.ttf")
    return p


def check_safe(box: tuple, w: int, h: int, what: str, problems: list) -> None:
    """Инвариант: ни один элемент не вылезает за safe zone."""
    mx, my = w * (1 - SAFE) / 2, h * (1 - SAFE) / 2
    x0, y0, x1, y1 = box
    if x0 < mx or y0 < my or x1 > w - mx or y1 > h - my:
        problems.append(f"{what}: выходит за safe zone ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})")


def place_photo(canvas: Image.Image, spec: dict) -> None:
    src_path = Path(spec["src"])
    if not src_path.exists():
        die(f"нет картинки {src_path}")
    img = Image.open(src_path).convert("RGB")
    if "crop" in spec:
        img = img.crop(tuple(spec["crop"]))
    height = int(spec.get("height", canvas.height))
    if img.height == 0:
        die("пустая область crop")
    k = height / img.height
    img = img.resize((max(1, int(img.width * k)), height), Image.LANCZOS)

    dim = float(spec.get("dim", 0))
    if dim > 0:
        # персонаж уходит в тень, чтобы центр отдать тексту
        img = Image.eval(img, lambda v: int(v * (1 - dim)))

    bleed = int(spec.get("bleed", 0))
    anchor = spec.get("anchor", "bottom-right")
    y = canvas.height - img.height
    if anchor == "bottom-right":
        x = canvas.width - img.width + bleed
    elif anchor == "bottom-left":
        x = -bleed
    elif anchor == "bottom-center":
        x = (canvas.width - img.width) // 2
    else:
        die(f"неизвестный anchor {anchor!r}")
    canvas.paste(img, (x, y))


WEIGHTS = {
    "black": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "regular": "/System/Library/Fonts/Supplemental/Arial.ttf",
}


def face(ly: dict, default: str) -> str:
    """Начертание слоя: заголовкам black, мелкому тексту regular."""
    w = ly.get("font")
    if not w:
        return default
    path = WEIGHTS.get(w, w)
    if not Path(path).exists():
        die(f"нет шрифта {path}")
    return path


def draw_layers(canvas: Image.Image, layers: list, problems: list) -> dict:
    d = ImageDraw.Draw(canvas)
    fp = font_path()
    counts = {"text": 0, "arrow": 0, "badge": 0}

    for i, ly in enumerate(layers):
        kind = ly.get("type")
        if kind == "text":
            f = ImageFont.truetype(face(ly, fp), ly["size"])
            x, y = ly["pos"]
            leading = ly.get("leading", int(ly["size"] * 1.12))
            fill = tuple(ly.get("color", [255, 255, 255]))
            widest = 0
            for line in ly["lines"]:
                d.text((x, y), line, font=f, fill=fill)
                widest = max(widest, d.textlength(line, font=f))
                y += leading
            check_safe((x, ly["pos"][1], x + widest, y), canvas.width, canvas.height,
                       f"слой {i} (текст)", problems)
            counts["text"] += len(ly["lines"])

        elif kind == "arrow":
            (x0, y0), (x1, y1) = ly["from"], ly["to"]
            w = ly.get("width", 6)
            head = ly.get("head", w * 6)
            d.line([(x0, y0), (x1 - head // 2, y1)], fill=(255, 255, 255), width=w)
            d.polygon([(x1 - head, y1 - head // 2), (x1, y1), (x1 - head, y1 + head // 2)],
                      fill=(255, 255, 255))
            check_safe((min(x0, x1), min(y0, y1) - head, max(x0, x1), max(y0, y1) + head),
                       canvas.width, canvas.height, f"слой {i} (стрелка)", problems)
            counts["arrow"] += 1

        elif kind == "badge":
            cx, cy = ly["pos"]
            r = ly.get("radius", 46)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=5)
            d.line([(cx - r * 0.43, cy), (cx + r * 0.35, cy)], fill=(255, 255, 255), width=5)
            d.polygon([(cx + r * 0.22, cy - r * 0.28), (cx + r * 0.6, cy),
                       (cx + r * 0.22, cy + r * 0.28)], fill=(255, 255, 255))
            f = ImageFont.truetype(face(ly, fp), ly.get("size", 36))
            tx = cx + r + ly.get("gap", 26)
            ty = cy - ly.get("size", 36) - 8
            widest = 0
            for line in ly["lines"]:
                d.text((tx, ty), line, font=f, fill=(255, 255, 255))
                widest = max(widest, d.textlength(line, font=f))
                ty += ly.get("size", 36) + 10
            check_safe((cx - r, cy - r, tx + widest, max(cy + r, ty)),
                       canvas.width, canvas.height, f"слой {i} (плашка)", problems)
            counts["badge"] += 1

        elif kind == "pill":
            f = ImageFont.truetype(face(ly, fp), ly.get("size", 40))
            x, y = ly["pos"]
            pad = ly.get("pad", 26)
            tw = d.textlength(ly["text"], font=f)
            th = ly.get("size", 40)
            box = [x, y, x + tw + pad * 2, y + th + pad]
            d.rounded_rectangle(box, radius=(th + pad) // 2,
                                fill=tuple(ly.get("bg", [22, 22, 26])),
                                outline=tuple(ly.get("outline", [90, 90, 100])), width=2)
            d.text((x + pad, y + pad // 2 - 4), ly["text"], font=f, fill=(255, 255, 255))
            check_safe(tuple(box), canvas.width, canvas.height, f"слой {i} (плашка)", problems)
            counts["pill"] = counts.get("pill", 0) + 1

        elif kind == "panel":
            x0, y0, x1, y1 = ly["box"]
            over = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(over).rounded_rectangle(
                [x0, y0, x1, y1], radius=ly.get("radius", 28),
                fill=tuple(ly.get("fill", [10, 12, 16])) + (int(255 * ly.get("opacity", 0.82)),),
                outline=tuple(ly.get("outline", [0, 200, 255])) + (200,),
                width=ly.get("width", 2))
            merged = Image.alpha_composite(canvas.convert("RGBA"), over)
            canvas.paste(merged.convert(canvas.mode), (0, 0))
            d = ImageDraw.Draw(canvas)
            check_safe((x0, y0, x1, y1), canvas.width, canvas.height, f"слой {i} (панель)", problems)
            counts["panel"] = counts.get("panel", 0) + 1

        elif kind == "body":
            f = ImageFont.truetype(face(ly, fp), ly.get("size", 34))
            x, y = ly["pos"]
            maxw = ly["width"]
            leading = ly.get("leading", int(ly.get("size", 34) * 1.42))
            fill = tuple(ly.get("color", [222, 226, 232]))
            drawn = 0
            for para in ly["paragraphs"]:
                line = ""
                for word in para.split():
                    probe = (line + " " + word).strip()
                    if d.textlength(probe, font=f) <= maxw:
                        line = probe
                    else:
                        d.text((x, y), line, font=f, fill=fill)
                        y += leading
                        drawn += 1
                        line = word
                if line:
                    d.text((x, y), line, font=f, fill=fill)
                    y += leading
                    drawn += 1
                y += ly.get("gap", 14)
            check_safe((x, ly["pos"][1], x + maxw, y), canvas.width, canvas.height,
                       f"слой {i} (текст блока)", problems)
            counts["body"] = counts.get("body", 0) + drawn

        elif kind == "bookmark":
            x, y = ly["pos"]
            s = ly.get("size", 30)
            d.line([(x, y), (x + s * 0.72, y)], fill=(255, 255, 255), width=3)
            d.line([(x, y), (x, y + s)], fill=(255, 255, 255), width=3)
            d.line([(x + s * 0.72, y), (x + s * 0.72, y + s)], fill=(255, 255, 255), width=3)
            d.line([(x, y + s), (x + s * 0.36, y + s * 0.72)], fill=(255, 255, 255), width=3)
            d.line([(x + s * 0.72, y + s), (x + s * 0.36, y + s * 0.72)], fill=(255, 255, 255), width=3)
            f = ImageFont.truetype(face(ly, fp), ly.get("text_size", 26))
            tx, ty = x + s + 18, y
            widest = 0
            for line in ly["lines"]:
                d.text((tx, ty), line, font=f, fill=(255, 255, 255))
                widest = max(widest, d.textlength(line, font=f))
                ty += ly.get("text_size", 26) + 8
            check_safe((x, y, tx + widest, max(y + s, ty)), canvas.width, canvas.height,
                       f"слой {i} (закладка)", problems)
            counts["bookmark"] = counts.get("bookmark", 0) + 1

        else:
            die(f"неизвестный тип слоя {kind!r}, доступны: text, body, arrow, badge, pill, panel, bookmark")

    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?", help="JSON-план слайда")
    ap.add_argument("-o", "--out", help="куда сохранить PNG")
    ap.add_argument("--format", action="store_true", help="показать формат плана")
    ap.add_argument("--overlay", action="store_true",
                    help="только текстовые слои на прозрачном фоне, для наложения на видео")
    args = ap.parse_args()

    if args.format:
        print(FORMAT)
        return
    if not args.plan or not args.out:
        die("нужен план и -o результат (формат: --format)")

    plan_path = Path(args.plan)
    if not plan_path.exists():
        die(f"нет плана {plan_path}")
    try:
        plan = json.loads(plan_path.read_text())
    except json.JSONDecodeError as e:
        die(f"план не читается как JSON: {e}")

    w, h = plan.get("canvas", [1080, 1440])
    if args.overlay:
        canvas = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGB", (int(w), int(h)), tuple(plan.get("background", [0, 0, 0])))

    if "photo" in plan and not args.overlay:
        # Пути в плане - относительно самого плана, чтобы план переезжал целиком.
        spec = dict(plan["photo"])
        spec["src"] = str((plan_path.parent / spec["src"]).resolve())
        place_photo(canvas, spec)

    problems: list = []
    counts = draw_layers(canvas, plan.get("layers", []), problems)

    if problems:
        print("СБОРКА ОСТАНОВЛЕНА, верстка выходит за safe zone:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.out)
    canvas.save(out)
    print(f"{out.name}: {canvas.width}x{canvas.height}, "
          f"строк текста {counts['text']}, стрелок {counts['arrow']}, плашек {counts['badge']}")


if __name__ == "__main__":
    main()
