#!/usr/bin/env python3
"""Смысловые акценты поверх ролика: число-шильдик и карточка призыва.

Зачем отдельно от титров: титры ведут речь слово за словом, а акцент держит
ОДНУ мысль несколько секунд - число, оффер, призыв. В образовательном рилсе
это разные слои, и живут они в разных зонах кадра.

Зона акцентов - верх (ACC_Y), титры внизу (см. subtitles.py). Пересекаться
им нельзя: проверяется инвариантом.

Использование:
    python3 accents.py вход.mp4 акценты.json выход.mp4
    формат: [{"type":"chip|cta","text":"...","sub":"...","start":1.0,"end":3.0}]
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
ACC_Y = 210            # верх полосы акцентов
ACC_H = 420
CAPTION_TOP = 1130     # титры начинаются здесь - пересекать нельзя

ACCENT = (255, 214, 10, 255)
INK = (12, 12, 16, 255)
PAPER = (255, 255, 255, 255)

# SF Rounded первым не случайно: в Arial Black НЕТ глифа рубля - вместо ₽
# рисуется квадрат, и это уезжает в публикацию незаметно. Проверено рендером.
FONT_CANDIDATES = [
    os.environ.get("SUBS_FONT", ""),
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
]
MAXW = 980             # предельная ширина плашки
MIN_FS = 44
FONT_PATH = next((p for p in FONT_CANDIDATES if p and os.path.exists(p)), "")


def font(size, weight="Black"):
    if not FONT_PATH:
        sys.exit("шрифт не найден: задай SUBS_FONT=/путь/к/шрифту.ttf")
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def tw(f, s):
    b = f.getbbox(s)
    return b[2] - b[0], b[3] - b[1]


def fit(text, sub, base_main, base_sub, padx):
    """Подбираем кегль, пока плашка не влезет в кадр.
    Считаем по РЕАЛЬНОЙ ширине глифов - счёт по символам уже подводил."""
    fs1, fs2 = base_main, base_sub
    while fs1 > MIN_FS:
        f1, f2 = font(fs1), font(fs2)
        w = max(tw(f1, text)[0], tw(f2, sub)[0] if sub else 0) + padx * 2
        if w <= MAXW:
            return f1, f2
        fs1 -= 6
        fs2 = max(int(fs2 * 0.94), 22)
    return font(MIN_FS), font(max(fs2, 22))


def check_glyphs(items):
    """Инвариант: ни один символ не рисуется квадратом .notdef."""
    probe = font(64)
    ref = Image.new("L", (140, 140), 0)
    ImageDraw.Draw(ref).text((8, 8), "\uffff", font=probe, fill=255)
    ref = ref.tobytes()
    bad = set()
    for it in items:
        for ch in set((it.get("text", "") + it.get("sub", ""))):
            if ch.isspace():
                continue
            im = Image.new("L", (140, 140), 0)
            ImageDraw.Draw(im).text((8, 8), ch, font=probe, fill=255)
            if im.tobytes() == ref:
                bad.add(ch)
    assert not bad, (f"шрифт не знает символы {sorted(bad)} - они выйдут квадратами. "
                     f"Замени символ или шрифт (SUBS_FONT).")


def ease_out(x):
    return 1 - (1 - x) ** 3


def draw_chip(d, a, prog, text, sub):
    """Жёлтый шильдик с числом: выезжает снизу и слегка отпружинивает."""
    f1, f2 = fit(text, sub, 132, 38, 46)
    w1, h1 = tw(f1, text)
    w2, h2 = (tw(f2, sub) if sub else (0, 0))
    padx, pady = 46, 26
    bw = max(w1, w2) + padx * 2
    bh = h1 + pady * 2 + (h2 + 16 if sub else 0)
    e = ease_out(min(prog / 0.28, 1.0))
    over = 1 + 0.06 * max(0.0, 1 - abs(prog - 0.30) / 0.22) if prog < 0.55 else 1.0
    sc = (0.7 + 0.3 * e) * over
    bw2, bh2 = int(bw * sc), int(bh * sc)
    x0 = (W - bw2) // 2
    y0 = int(ACC_Y + (1 - e) * 70)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=28, fill=ACCENT)
    dl.text(((bw - w1) // 2, pady - f1.getbbox(text)[1]), text, font=f1, fill=INK)
    if sub:
        dl.text(((bw - w2) // 2, pady + h1 + 16 - f2.getbbox(sub)[1]),
                sub, font=f2, fill=(60, 50, 0, 255))
    layer = layer.resize((max(bw2, 1), max(bh2, 1)), Image.LANCZOS)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(prog / 0.18, 1.0))))
    a.alpha_composite(layer, (x0, y0))


def draw_cta(d, a, prog, text, sub):
    """Карточка призыва: тёмная плашка, слово-ключ жёлтым."""
    f1, f2 = fit(text, sub, 96, 40, 48)
    e = ease_out(min(prog / 0.30, 1.0))
    w1, h1 = tw(f1, text)
    w2, h2 = (tw(f2, sub) if sub else (0, 0))
    padx, pady = 48, 34
    bw = max(w1, w2) + padx * 2
    bh = h1 + pady * 2 + (h2 + 18 if sub else 0)
    x0 = (W - bw) // 2
    y0 = int(ACC_Y + (1 - e) * 60)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=26, fill=(10, 10, 14, 235))
    dl.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=26, outline=ACCENT, width=5)
    dl.text(((bw - w1) // 2, pady - f1.getbbox(text)[1]), text, font=f1, fill=ACCENT)
    if sub:
        dl.text(((bw - w2) // 2, pady + h1 + 18 - f2.getbbox(sub)[1]),
                sub, font=f2, fill=PAPER)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * e)))
    a.alpha_composite(layer, (x0, y0))


def render(items, out_video, in_video):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", in_video], capture_output=True, text=True).stdout.strip())
    total = int(dur * FPS)

    assert ACC_Y + ACC_H <= CAPTION_TOP, "полоса акцентов налезает на титры"
    check_glyphs(items)
    for it in items:
        assert it["end"] > it["start"], f"акцент {it['text']}: конец раньше начала"

    blank = Image.new("RGBA", (W, ACC_H), (0, 0, 0, 0)).tobytes()
    cache = {}
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", in_video,
         "-f", "rawvideo", "-pixel_format", "rgba",
         "-video_size", f"{W}x{ACC_H}", "-framerate", str(FPS), "-i", "-",
         "-filter_complex", f"[0:v][1:v]overlay=0:{ACC_Y}:format=auto:shortest=1[v]",
         "-map", "[v]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p",
         "-x264-params", "keyint=60:min-keyint=30",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", out_video], stdin=subprocess.PIPE)

    for fr in range(total):
        t = fr / FPS
        act = next((i for i in items if i["start"] <= t < i["end"]), None)
        if act is None:
            p.stdin.write(blank)
            continue
        prog = (t - act["start"]) / max(act["end"] - act["start"], 1e-6)
        key = (act["text"], round(prog, 3))
        if key not in cache:
            img = Image.new("RGBA", (W, ACC_H), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            fade = min((act["end"] - t) / 0.25, 1.0)
            (draw_cta if act["type"] == "cta" else draw_chip)(
                d, img, prog, act["text"], act.get("sub", ""))
            if fade < 1:
                img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
            cache[key] = img.tobytes()
        p.stdin.write(cache[key])
    p.stdin.close()
    assert p.wait() == 0, "ffmpeg не собрал акценты"
    return len(items)


if __name__ == "__main__":
    a = sys.argv[1:]
    n = render(json.load(open(a[1])), a[2], a[0])
    print(f"акцентов: {n} -> {a[2]}")
