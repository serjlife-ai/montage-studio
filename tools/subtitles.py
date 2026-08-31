#!/usr/bin/env python3
"""Субтитры word-by-word поверх готового видео.

ffmpeg на Маке собран без libass и drawtext, поэтому кадры титров рисуем в PIL
и отдаём в ffmpeg сырым RGBA-потоком одной полосой. Побочный плюс: полный
контроль над версткой - подсветка активного слова, автоподбор кегля, цензура.
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

W = 1080
BAND_H = 460
BAND_Y = 1130          # лицо выше полосы, UI инсты ниже
FPS = 30

# Шрифт ищем, а не зашиваем: контур работает и на macOS, и на Linux-сервере.
# Порядок: переменная окружения -> шрифты macOS -> шрифты Linux -> поиск
# по системе через fc-match. Кириллица обязательна, латинские шрифты
# без неё не годятся.
def _fc_match():
    """Спросить у системы жирный шрифт с кириллицей (Linux, fontconfig)."""
    for query in ("Noto Sans:bold:lang=ru", "DejaVu Sans:bold:lang=ru",
                  ":bold:lang=ru", ":lang=ru"):
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", query],
                                 capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            if out and os.path.exists(out):
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


FONT_CANDIDATES = [
    os.environ.get("SUBS_FONT", ""),
    # macOS
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux: сначала жирные начертания, титрам нужен вес
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if p and os.path.exists(p)), "")
if not FONT_PATH:
    FONT_PATH = _fc_match()
FONT_VARIATION = "Black"
SIZE = 84
LINE_H = 104
STROKE = 9
MAXW = 900             # предельная ширина текстового блока
MIN_SIZE = 52          # ниже кегль не опускаем
ACCENT = (255, 214, 10, 255)
BASE = (255, 255, 255, 255)
SHADOW = (0, 0, 0, 140)

MAX_WORDS = 4
BREAK_GAP = 0.55       # пауза, после которой начинается новая фраза
HOLD_AFTER = 0.5       # сколько фраза висит после последнего слова

# корни, а не слова: ловится любая форма, включая ту, что whisper слышит иначе
SWEAR_ROOTS = ["хуй", "хуе", "хуё", "хую", "пизд", "бля", "еба", "ебл", "ебн",
               "ёб", "сук", "мудак", "мудил", "пидор", "пидар"]
KEEP_HEAD = 1


def load_font(size):
    if not FONT_PATH:
        sys.exit("шрифт с кириллицей не найден.\n"
                 "  Linux:  sudo apt install fonts-noto-core  (или fonts-dejavu)\n"
                 "  либо задай вручную: SUBS_FONT=/путь/к/шрифту.ttf")
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(FONT_VARIATION)
    except Exception:
        pass
    return f


def words_from(path):
    """whisper-cli -ml 1 отдаёт токены; склеиваем их обратно в слова."""
    tr = json.load(open(path))["transcription"]
    toks = [(s["offsets"]["from"] / 1000.0, s["offsets"]["to"] / 1000.0, s["text"])
            for s in tr if s["text"].strip()]
    out = []
    for a, b, t in toks:
        if t.startswith(" ") or not out:
            out.append([a, b, t.strip()])
        else:
            out[-1][1] = b
            out[-1][2] += t
    res = []
    for a, b, t in out:                      # висящую пунктуацию - к слову
        if res and t and all(c in ".,!?…:;-" for c in t):
            res[-1][1] = b
            res[-1][2] += t
        else:
            res.append([a, b, t])
    return res


def censor(w):
    """Прячем корень за звёздочками, окончание оставляем: слово читается,
    но модерация за него не цепляется."""
    low = w.lower()
    for r in SWEAR_ROOTS:
        i = low.find(r)
        if i >= 0:
            return w[:i + KEEP_HEAD] + "*" * (len(r) - KEEP_HEAD) + w[i + len(r):]
    return w


def clean(w, do_censor):
    return (censor(w) if do_censor else w).upper()


def group(words):
    """Поток слов -> фразы: по знаку конца, по паузе, по лимиту слов."""
    groups, cur = [], []
    for i, (a, b, t) in enumerate(words):
        cur.append((a, b, t))
        gap = words[i + 1][0] - b if i + 1 < len(words) else 99
        ends = t.rstrip().endswith((".", "!", "?", "…"))
        comma = t.rstrip().endswith(",")
        if (len(cur) >= MAX_WORDS or ends or gap > BREAK_GAP
                or (comma and len(cur) >= 2)):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def wordw(font, txt):
    b = font.getbbox(txt, stroke_width=STROKE)
    return b[2] - b[0]


def layout(tokens, do_censor):
    """Перенос строк по РЕАЛЬНОЙ ширине глифов. Считать по символам нельзя:
    так первая версия ролика уехала за края кадра."""
    size = SIZE
    while True:
        font = load_font(size)
        space = wordw(font, " ") + 10
        lines, cur, curw, toowide = [], [], 0, False
        for tk in tokens:
            w = wordw(font, clean(tk[2], do_censor))
            if w > MAXW:
                toowide = True
                break
            add = w if not cur else curw + space + w
            if add > MAXW and cur:
                lines.append((cur, curw))
                cur, curw = [tk], w
            else:
                cur, curw = cur + [tk], add
        if toowide and size > MIN_SIZE:
            size -= 6
            continue
        if cur:
            lines.append((cur, curw))
        break

    placed, lh = [], int(LINE_H * size / SIZE)
    y0 = (BAND_H - len(lines) * lh) // 2
    for li, (line, total_w) in enumerate(lines):
        x = (W - total_w) // 2
        for tk in line:
            p = clean(tk[2], do_censor)
            placed.append((p, x, y0 + li * lh, font))
            x += wordw(font, p) + space
    return placed


def check_fits(groups, do_censor):
    """Инвариант: ни одна фраза не вылезает за кадр и за полосу."""
    bad = []
    for g in groups:
        pl = layout(g, do_censor)
        right = max(x + wordw(f, t) for t, x, _y, f in pl)
        left = min(x for _t, x, _y, _f in pl)
        bottom = max(y for _t, _x, y, _f in pl) + LINE_H
        if right > W - 20 or left < 20 or bottom > BAND_H:
            bad.append(" ".join(t for t, *_ in pl))
    return bad


def render(placed, active_idx):
    img = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (txt, x, y, font) in enumerate(placed):
        col = ACCENT if i == active_idx else BASE
        d.text((x + 4, y + 6), txt, font=font, fill=SHADOW,
               stroke_width=STROKE, stroke_fill=SHADOW)
        d.text((x, y), txt, font=font, fill=col,
               stroke_width=STROKE, stroke_fill=(0, 0, 0, 255))
    return img.tobytes()


def burn(video_in, words_json, video_out, do_censor=False, band_y=BAND_Y,
         mute_ranges=()):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_in], capture_output=True, text=True).stdout.strip())
    words = words_from(words_json)
    groups = group(words)
    bad = check_fits(groups, do_censor)
    assert not bad, f"фразы не влезают в кадр: {bad[:3]}"

    total = int(dur * FPS)
    timeline = [None] * (total + 1)
    for gi, g in enumerate(groups):
        gend = (min(groups[gi + 1][0][0], g[-1][1] + HOLD_AFTER)
                if gi + 1 < len(groups) else g[-1][1] + HOLD_AFTER)
        f0, f1 = int(g[0][0] * FPS), int(gend * FPS)
        for wi, (a, _b, _t) in enumerate(g):
            wf1 = int(g[wi + 1][0] * FPS) if wi + 1 < len(g) else f1
            for f in range(max(f0, int(a * FPS)), min(wf1, total)):
                timeline[f] = (gi, wi)

    # Гашение диапазонов: фраза может НАЧАТЬСЯ до окна и висеть по инерции,
    # накладываясь на презентацию. Фильтра по словам мало - гасим сам таймлайн.
    for a, b in mute_ranges:
        for f in range(max(int(a * FPS), 0), min(int(b * FPS), total)):
            timeline[f] = None

    layouts = [layout(g, do_censor) for g in groups]
    blank = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0)).tobytes()
    cache = {}

    p = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_in,
         "-f", "rawvideo", "-pixel_format", "rgba",
         "-video_size", f"{W}x{BAND_H}", "-framerate", str(FPS), "-i", "-",
         "-filter_complex", f"[0:v][1:v]overlay=0:{band_y}:format=auto:shortest=1[v]",
         "-map", "[v]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "slow", "-crf", "19",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
         "-x264-params", "keyint=60:min-keyint=30",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", video_out], stdin=subprocess.PIPE)
    for f in range(total):
        key = timeline[f]
        if key is None:
            p.stdin.write(blank)
            continue
        if key not in cache:
            cache[key] = render(layouts[key[0]], key[1])
        p.stdin.write(cache[key])
    p.stdin.close()
    assert p.wait() == 0, "ffmpeg не собрал титры"
    return len(words), len(groups)


if __name__ == "__main__":
    a = sys.argv[1:]
    mr = []
    if "--mute" in a:                       # --mute 6.58-10.38,16.9-22.3
        spec = a[a.index("--mute") + 1]
        mr = [tuple(float(x) for x in part.split("-")) for part in spec.split(",")]
    n, g = burn([x for x in a if not x.startswith("--")][0],
                [x for x in a if not x.startswith("--")][1],
                [x for x in a if not x.startswith("--")][2],
                do_censor="--censor" in a, mute_ranges=mr)
    print(f"титры: {n} слов, {g} фраз -> {a[2]}")
