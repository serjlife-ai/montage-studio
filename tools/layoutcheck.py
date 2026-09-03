#!/usr/bin/env python3
"""Проверка вёрстки сцен: наезды текста, вылеты за кадр, перекос по центру.

Зачем. Первая сборка ушла Сержу с пятью косяками, каждый из которых
виден глазом за секунду: цитата легла поверх цифр «9000/5000», кружок VS
уехал на карточку, «30–40%» не влез в кольцо, блок прижат к верху кадра.
Проверка по одному кадру их не ловит: элемент появляется на 12-й секунде
и наезжает только там.

Поэтому здесь проверяется НЕ кадр, а геометрия во времени: сцена
проматывается по ключевым моментам (старт каждой анимации + середина
каждого окна), и в каждый момент считается попарное пересечение
видимых текстовых блоков.

    python3 layoutcheck.py scenes/            все сцены
    python3 layoutcheck.py scenes/s01.html    одна

Три проверки:

  НАЕЗД     два текстовых блока перекрываются больше чем на 12% площади
            меньшего. Порог не ноль: буквы с выносными элементами
            («р», «у») законно заходят в соседний бокс на 2-5%.
  ВЫЛЕТ     блок выходит за 1920x1080.
  ПЕРЕКОС   содержимое сцены прижато к краю: разница верхнего и нижнего
            поля больше 260 px при пустоте больше трети кадра.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from webrender import Browser  # noqa: E402

W, H = 1920, 1080
OVERLAP_MAX = 0.12      # доля площади меньшего блока
SKEW_MAX = 260          # px разницы полей
EMPTY_MIN = H / 3

PROBE_JS = r"""(() => {
  // Моменты, в которые есть смысл смотреть: старт и конец каждой
  // конечной анимации плюс середина окна каждой сцены.
  const t = new Set([0.4]);
  for (const a of document.getAnimations()) {
    const c = a.effect && a.effect.getComputedTiming();
    if (!c || !isFinite(c.endTime)) continue;
    const d = (c.delay || 0) / 1000;
    const e = d + (c.activeDuration || 0) / 1000;
    if (d >= 0) t.add(Math.round((d + 0.15) * 10) / 10);
    if (e >= 0 && isFinite(e)) t.add(Math.round((e + 0.25) * 10) / 10);
  }
  for (const s of document.querySelectorAll('.sc')) {
    const cs = getComputedStyle(s);
    const a = parseFloat(cs.getPropertyValue('--a')) || 0;
    const b = parseFloat(cs.getPropertyValue('--b')) || 0;
    if (b > a) { t.add(Math.round((a + (b - a) / 2) * 10) / 10);
                 t.add(Math.round((b - 0.4) * 10) / 10); }
  }
  return JSON.stringify([...t].filter(x => x >= 0).sort((x, y) => x - y));
})()"""

# Собрать видимые блоки с текстом в текущий момент.
BOXES_JS = r"""(() => {
  const out = [];
  const walk = document.querySelectorAll('body *');
  for (const e of walk) {
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (parseFloat(cs.opacity) < 0.06) continue;
    // прозрачный родитель гасит потомков
    // Эффективная непрозрачность = произведение по всей цепочке.
    // Собственный opacity элемента врёт: сам он 1, а родительская
    // секция гаснет - и элемент невидим, но в замер попадает.
    let eff = parseFloat(cs.opacity), p = e.parentElement;
    while (p && p !== document.body) {
      eff *= parseFloat(getComputedStyle(p).opacity);
      p = p.parentElement;
    }
    if (eff < 0.06) continue;
    // только листья с собственным текстом либо картинки
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isImg = e.tagName === 'IMG';
    const pseudo = getComputedStyle(e, '::after').content;
    const hasPseudo = pseudo && pseudo !== 'none' && pseudo !== 'normal'
                      && !pseudo.startsWith('"" ') && pseudo !== '""';
    if (!own && !isImg && !hasPseudo) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 12 || r.height < 12) continue;
    const cls = (e.className && e.className.baseVal !== undefined)
                ? e.className.baseVal : String(e.className || '');
    // Путь по DOM: по нему отсекаются пары «предок-потомок». Без этого
    // <span> внутри <h1> считается наездом на собственный заголовок.
    let path = [], n = e;
    while (n && n !== document.body) {
      path.push([...(n.parentElement ? n.parentElement.children : [])].indexOf(n));
      n = n.parentElement;
    }
    out.push({x: r.left, y: r.top, w: r.width, h: r.height, op: eff,
              cls: cls.slice(0, 42), tag: e.tagName, path: path.reverse().join('/'),
              txt: (e.textContent || '').trim().slice(0, 34)});
  }
  return JSON.stringify(out);
})()"""


def overlap(a, b):
    x = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    y = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    if not x or not y:
        return 0.0
    inter = x * y
    return inter / max(1.0, min(a["w"] * a["h"], b["w"] * b["h"]))


def kin(a, b):
    """Один элемент - предок другого? Тогда пересечение законно."""
    pa, pb = a.get("path", ""), b.get("path", "")
    return pa.startswith(pb) or pb.startswith(pa)


def contains(a, b):
    """b целиком внутри a - это вложенность, а не наезд."""
    return (a["x"] <= b["x"] + 2 and a["y"] <= b["y"] + 2
            and a["x"] + a["w"] >= b["x"] + b["w"] - 2
            and a["y"] + a["h"] >= b["y"] + b["h"] - 2)


def check_scene(path, b, verbose=False):
    b.open(path)
    times = json.loads(b.js(PROBE_JS))
    problems = []
    seen = set()

    for t in times:
        if t > 400:
            continue
        b.seek(t)
        boxes = json.loads(b.js(BOXES_JS))

        # 1. вылет за кадр
        for e in boxes:
            if (e["x"] < -2 or e["y"] < -2
                    or e["x"] + e["w"] > W + 2 or e["y"] + e["h"] > H + 2):
                key = ("ВЫЛЕТ", e["cls"], e["txt"])
                if key not in seen:
                    seen.add(key)
                    problems.append(
                        f"ВЫЛЕТ  t={t:6.1f}s  .{e['cls'] or e['tag']} "
                        f"[{e['txt']}] → {e['x']:.0f},{e['y']:.0f} "
                        f"{e['w']:.0f}x{e['h']:.0f}")

        # 2. наезды
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, c = boxes[i], boxes[j]
                if kin(a, c) or contains(a, c) or contains(c, a):
                    continue
                ov = overlap(a, c)
                if ov > OVERLAP_MAX:
                    key = ("НАЕЗД", a["txt"], c["txt"])
                    if key in seen:
                        continue
                    seen.add(key)
                    problems.append(
                        f"НАЕЗД  t={t:6.1f}s  {ov*100:.0f}%  "
                        f"[{a['txt']}] × [{c['txt']}]")

        # 3. перекос по вертикали.
        # Считаем только там, где сцена уже РАСКРЫЛАСЬ. В первые
        # доли секунды видна одна строка заголовка - она законно стоит
        # не по центру, остальное ещё влетает. Порог 1.6 с покрывает
        # самые длинные каскады появлений в проекте.
        # Каскады появлений в проекте доходят до 3 с (плитки по 0.35 с
        # с задержками до 2.5 с). Мерить перекос раньше бессмысленно:
        # половина блоков ещё не влетела, и низ кадра законно пуст.
        # Замер s12/s13: на 1.6 с поля 290/567, на 4 с - 290/406 и
        # 280/285, то есть сцены ровные, а ранний момент врал.
        if t < 3.2:
            continue
        # Гаснущие элементы (зачёркнутые строки, отработавшие плашки)
        # остаются в DOM с opacity ~0.3 и тянут границу блока вниз,
        # из-за чего центрированная сцена выглядит перекошенной.
        # Замер по s12/s13: реальные поля 202/201 и 195/149 - ровно.
        vis = [e for e in boxes if e["h"] > 20 and e.get("op", 1) >= 0.5]
        if vis:
            top = min(e["y"] for e in vis)
            bot = max(e["y"] + e["h"] for e in vis)
            up, down = top, H - bot
            if abs(up - down) > SKEW_MAX and max(up, down) > EMPTY_MIN:
                key = ("ПЕРЕКОС", round(up / 50), round(down / 50))
                if key not in seen:
                    seen.add(key)
                    problems.append(
                        f"ПЕРЕКОС t={t:6.1f}s  сверху {up:.0f}px, "
                        f"снизу {down:.0f}px")
    return times, problems


def main():
    ap = argparse.ArgumentParser(description="Проверка вёрстки сцен")
    ap.add_argument("target")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    files = ([a.target] if a.target.endswith(".html")
             else sorted(glob.glob(os.path.join(a.target, "s*.html"))))
    b = Browser()
    total = 0
    try:
        for f in files:
            times, probs = check_scene(f, b, a.verbose)
            name = os.path.basename(f)
            if probs:
                total += len(probs)
                print(f"\n=== {name}  (проверено моментов: {len(times)}) ===")
                for p in probs:
                    print("  " + p)
            else:
                print(f"OK  {name}  (моментов: {len(times)})")
    finally:
        b.close()
    print(f"\nвсего проблем: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
