#!/usr/bin/env python3
"""Слой всплывашек: стикеры, чек-лист, полоса прогресса, цветовой флеш.

Отвечает на претензию «только зум туда-сюда»: даёт точки внимания, которых
в говорящей голове нет по определению. Всё рисуется в PIL и кладётся одним
RGBA-слоем - тот же приём, что и титры.

Зона слоя - ВЕРХ кадра (0..BAND_H). Титры живут внизу (см. subtitles.py),
пересекаться им нельзя: проверяется инвариантом.

    python3 creative.py вход.mp4 сценарий.json выход.mp4

Элементы сценария:
  {"type":"bar"}                                     полоса прогресса на весь ролик
  {"type":"sticker","text":"...","start":..,"end":..,"side":"left|right","row":0}
  {"type":"stack","items":[{"text":"...","at":..}],"start":..,"end":..}
  {"type":"flash","start":..,"end":..,"color":[255,214,10]}
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
BAND_H = H             # слой на ВЕСЬ кадр: флеш обязан накрывать всё,
                       # иначе на границе полосы видна горизонтальная линия
CAPTION_TOP = 1130
# зона под окном: сюда кладём текст, когда голова уехала в окошко
PANEL_Y = 1150
WIN_RECT = (280, 150, 520, 924)   # должно совпадать с window.py

ACCENT = (255, 214, 10, 255)
INK = (10, 10, 14, 235)
PAPER = (255, 255, 255, 255)

FONT_CANDIDATES = [
    os.environ.get("SUBS_FONT", ""),
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/SFNS.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if p and os.path.exists(p)), "")
_fc = {}


def font(size, weight="Black"):
    if not FONT_PATH:
        sys.exit("шрифт не найден: задай SUBS_FONT=/путь/к/шрифту.ttf")
    k = (size, weight)
    if k not in _fc:
        f = ImageFont.truetype(FONT_PATH, size)
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
        _fc[k] = f
    return _fc[k]


def tw(f, s):
    b = f.getbbox(s)
    return b[2] - b[0], b[3] - b[1]


def ease(x):
    return 1 - (1 - max(0.0, min(1.0, x))) ** 3


def check_glyphs(items):
    """Инвариант: ни один символ не выйдет квадратом .notdef."""
    probe = font(64)
    ref = Image.new("L", (140, 140), 0)
    ImageDraw.Draw(ref).text((8, 8), "￿", font=probe, fill=255)
    ref = ref.tobytes()
    texts = []
    for it in items:
        texts.append(it.get("text", ""))
        texts += [x.get("text", "") for x in it.get("items", [])]
    bad = set()
    for ch in set("".join(texts)):
        if ch.isspace():
            continue
        im = Image.new("L", (140, 140), 0)
        ImageDraw.Draw(im).text((8, 8), ch, font=probe, fill=255)
        if im.tobytes() == ref:
            bad.add(ch)
    assert not bad, f"шрифт не знает символы {sorted(bad)} - выйдут квадратами"


def chip(img, x, y, text, prog, filled=False, maxw=560):
    """Плашка-стикер с точкой или галочкой. Влезает по ширине - иначе кегль вниз."""
    fs = 40
    f = font(fs)
    while tw(f, text)[0] > maxw - 130 and fs > 24:
        fs -= 3
        f = font(fs)
    twd, thd = tw(f, text)
    bw, bh = twd + 130, 74
    e = ease(prog / 0.22) if prog < 0.22 else 1.0
    pop = 1 + 0.10 * max(0.0, 1 - abs(prog - 0.26) / 0.20) if prog < 0.5 else 1.0
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=22, fill=INK)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=22,
                        outline=ACCENT if filled else (70, 70, 80, 220), width=3)
    cx, cy = 42, bh // 2
    if filled:
        d.line([(cx - 13, cy + 1), (cx - 4, cy + 11), (cx + 14, cy - 12)],
               fill=ACCENT, width=8, joint="curve")
    else:
        d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline=(120, 120, 130, 255), width=4)
    d.text((84, (bh - thd) // 2 - f.getbbox(text)[1]), text, font=f,
           fill=PAPER if filled else (185, 185, 195, 255))
    sw, sh = max(int(bw * e * pop), 1), max(int(bh * e * pop), 1)
    layer = layer.resize((sw, sh), Image.LANCZOS)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(prog / 0.14, 1.0))))
    img.alpha_composite(layer, (int(x), int(y)))


def fit(text, sub, fs1, fs2, padx, maxw=980):
    while fs1 > 44:
        f1, f2 = font(fs1), font(fs2)
        if max(tw(f1, text)[0], tw(f2, sub)[0] if sub else 0) + padx * 2 <= maxw:
            return f1, f2
        fs1 -= 6
        fs2 = max(int(fs2 * 0.94), 22)
    return font(44), font(max(fs2, 22))


def big_chip(img, prog, text, sub, y0=230):
    """Крупное число: выезжает снизу и отпружинивает."""
    f1, f2 = fit(text, sub, 132, 38, 46)
    w1, h1 = tw(f1, text)
    w2, h2 = (tw(f2, sub) if sub else (0, 0))
    padx, pady = 46, 26
    bw, bh = max(w1, w2) + padx * 2, h1 + pady * 2 + (h2 + 16 if sub else 0)
    e = ease(prog / 0.24)
    pop = 1 + 0.07 * max(0.0, 1 - abs(prog - 0.30) / 0.22) if prog < 0.55 else 1.0
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=28, fill=ACCENT)
    d.text(((bw - w1) // 2, pady - f1.getbbox(text)[1]), text, font=f1, fill=(12, 12, 16, 255))
    if sub:
        d.text(((bw - w2) // 2, pady + h1 + 16 - f2.getbbox(sub)[1]), sub,
               font=f2, fill=(60, 50, 0, 255))
    sw, sh = max(int(bw * (0.72 + 0.28 * e) * pop), 1), max(int(bh * (0.72 + 0.28 * e) * pop), 1)
    layer = layer.resize((sw, sh), Image.LANCZOS)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(prog / 0.16, 1.0))))
    img.alpha_composite(layer, ((W - sw) // 2, int(y0 + (1 - e) * 70)))


def cta_card(img, prog, text, sub, y0=250):
    """Карточка призыва: тёмная плашка, ключевое слово жёлтым."""
    f1, f2 = fit(text, sub, 96, 40, 48)
    w1, h1 = tw(f1, text)
    w2, h2 = (tw(f2, sub) if sub else (0, 0))
    padx, pady = 48, 34
    bw, bh = max(w1, w2) + padx * 2, h1 + pady * 2 + (h2 + 18 if sub else 0)
    e = ease(prog / 0.30)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=26, fill=(10, 10, 14, 238))
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=26, outline=ACCENT, width=5)
    d.text(((bw - w1) // 2, pady - f1.getbbox(text)[1]), text, font=f1, fill=ACCENT)
    if sub:
        d.text(((bw - w2) // 2, pady + h1 + 18 - f2.getbbox(sub)[1]), sub, font=f2, fill=PAPER)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * e)))
    img.alpha_composite(layer, ((W - bw) // 2, int(y0 + (1 - e) * 60)))


def panel(img, t, it):
    """Презентация под окном: заголовок + строки, которые приходят по словам."""
    d = ImageDraw.Draw(img)
    ft = font(38)
    title = it.get("title", "")
    if title:
        tw_, th_ = tw(ft, title)
        d.text(((W - tw_) // 2, PANEL_Y - ft.getbbox(title)[1]), title,
               font=ft, fill=(150, 150, 162, 255))
        d.line([(W // 2 - 90, PANEL_Y + th_ + 22), (W // 2 + 90, PANEL_Y + th_ + 22)],
               fill=ACCENT, width=5)
    y = PANEL_Y + 86
    rows = it["items"]
    fr = font(46)
    for k, sub in enumerate(rows):
        if t < sub["at"]:
            continue
        prog = min((t - sub["at"]) / 0.42, 1.0)
        e = ease(prog)
        txt = sub["text"]
        while tw(fr, txt)[0] > 760 and fr.size > 30:
            fr = font(fr.size - 3)
        twd, thd = tw(fr, txt)
        bw, bh = twd + 130, 78
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        dl.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=20, fill=(20, 20, 26, 240))
        dl.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=20, outline=ACCENT, width=3)
        cx, cy = 44, bh // 2
        dl.line([(cx - 14, cy + 1), (cx - 4, cy + 12), (cx + 15, cy - 13)],
                fill=ACCENT, width=8, joint="curve")
        dl.text((88, (bh - thd) // 2 - fr.getbbox(txt)[1]), txt, font=fr, fill=PAPER)
        sw = max(int(bw * (0.86 + 0.14 * e)), 1)
        sh = max(int(bh * (0.86 + 0.14 * e)), 1)
        layer = layer.resize((sw, sh), Image.LANCZOS)
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(prog / 0.2, 1.0))))
        img.alpha_composite(layer, ((W - sw) // 2, int(y + k * 96 + (1 - e) * 26)))
    bottom = y + len(rows) * 96
    assert bottom <= H - 40, f"презентация не влезает по высоте ({bottom}px)"


def render(items, src, out):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip())
    total = int(dur * FPS)
    # элементы (кроме флеша, он на весь кадр) обязаны остаться выше титров
    for it in items:
        if it["type"] in ("sticker", "stack"):
            rows = len(it.get("items", [1]))
            bottom = 168 + rows * 88 + 80
            assert bottom <= CAPTION_TOP, f"{it['type']} налезает на титры ({bottom}px)"
    check_glyphs(items)

    bar = any(i["type"] == "bar" for i in items)
    blank = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", src,
         "-f", "rawvideo", "-pixel_format", "rgba",
         "-video_size", f"{W}x{BAND_H}", "-framerate", str(FPS), "-i", "-",
         "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]",
         "-map", "[v]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p",
         "-x264-params", "keyint=60:min-keyint=30",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", out], stdin=subprocess.PIPE)

    for fr in range(total):
        t = fr / FPS
        img = blank.copy()
        d = ImageDraw.Draw(img)
        if bar:
            d.rectangle([0, 0, int(W * t / dur), 9], fill=ACCENT)
        for it in items:
            ty = it["type"]
            if ty == "bar":
                continue
            if not (it.get("start", 0) <= t < it.get("end", 1e9)):
                continue
            span = max(it["end"] - it["start"], 1e-6)
            prog = (t - it["start"]) / span
            fade = min((it["end"] - t) / 0.30, 1.0)
            if ty == "flash":
                col = tuple(it.get("color", [255, 255, 255]))
                a = int(210 * (1 - prog) ** 0.6)
                d.rectangle([0, 0, W, BAND_H], fill=col + (a,))
            elif ty == "panel":
                panel(img, t, it)
            elif ty == "winframe":
                x0, y0, ww, hh = WIN_RECT
                d.rounded_rectangle([x0 - 4, y0 - 4, x0 + ww + 3, y0 + hh + 3],
                                    radius=18, outline=ACCENT, width=4)
            elif ty == "bigchip":
                big_chip(img, prog, it["text"], it.get("sub", ""))
            elif ty == "cta":
                cta_card(img, prog, it["text"], it.get("sub", ""))
            elif ty == "sticker":
                x = 60 if it.get("side", "left") == "left" else W - 60 - 520
                y = 150 + it.get("row", 0) * 96
                chip(img, x, y, it["text"], prog, filled=it.get("filled", True))
            elif ty == "stack":
                for k, sub in enumerate(it["items"]):
                    if t < sub["at"]:
                        continue
                    sp = (t - sub["at"]) / 0.45
                    chip(img, 62, 168 + k * 88, sub["text"], min(sp, 1.0), filled=True)
            if fade < 1:
                img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
        p.stdin.write(img.tobytes())
    p.stdin.close()
    assert p.wait() == 0, "ffmpeg не собрал слой всплывашек"
    return len([i for i in items if i["type"] != "bar"])


if __name__ == "__main__":
    a = sys.argv[1:]
    n = render(json.load(open(a[1])), a[0], a[2])
    print(f"элементов: {n} -> {a[2]}")
