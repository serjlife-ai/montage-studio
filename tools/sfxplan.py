#!/usr/bin/env python3
"""Звуковой план из самих сцен: где что появилось - туда и эффект.

Зачем. Расставлять свуши руками по 14 сценам - это несколько сотен
строк JSON, которые расходятся с вёрсткой при первой же правке тайминга.
Здесь план СЧИТЫВАЕТСЯ из готовых сцен: браузер открывает страницу,
опрашивает Web Animations API и отдаёт время старта каждой анимации.
Правишь сцену - звук едет за ней сам.

Правила подбора (из knowledge/sound-design.md):

  смена сцены        transition-a/b/c   тяжёлый переход
  крупная цифра      impact             суб-удар, речи на 50 Гц нет
  плашка/карточка    pop                выскакивающий элемент
  строка списка      click              короткий транзиент
  полоса/счётчик     chime              на завершение роста
  зачёркивание       whoosh             движение

Плотность ограничена: не больше одного эффекта на 0.28 с, иначе
получается трещотка, а лимитер в миксе давит голос.

    python3 sfxplan.py scenes/ -o scenes/full.sfx.json --voice ../work/voice.wav
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from webrender import Browser  # noqa: E402

# Абсолютное время начала каждой сцены в готовом ролике.
# Совпадает с порядком и длительностями в build-all.sh.
SCENE_START = {}


def collect(html, browser):
    """Вернуть список (время_в_сцене, тип_элемента) для одной сцены."""
    browser.open(html)
    raw = browser.js(r"""(() => {
      const out = [];
      for (const a of document.getAnimations()) {
        const el = a.effect && a.effect.target;
        if (!el || !el.getBoundingClientRect) continue;
        const t = a.effect.getComputedTiming();
        // Бесконечные анимации - это фон и дыхание, они не события.
        if (!isFinite(t.endTime) || t.iterations === Infinity) continue;
        const start = (t.delay || 0) / 1000;
        if (start < 0) continue;
        const box = el.getBoundingClientRect();
        if (box.width < 8 || box.height < 8) continue;
        const cls = (el.className && el.className.baseVal !== undefined)
                    ? el.className.baseVal : String(el.className || '');
        out.push({t: start, cls: cls, tag: el.tagName,
                  w: Math.round(box.width), h: Math.round(box.height),
                  name: (a.animationName || (a.effect.getKeyframes ? '' : ''))});
      }
      // Начала сцен - отдельно: это точки переходов.
      const sc = [];
      for (const e of document.querySelectorAll('.sc')) {
        const v = getComputedStyle(e).getPropertyValue('--a').trim();
        if (v) sc.push(parseFloat(v));
      }
      return JSON.stringify({items: out, scenes: sc});
    })()""")
    return json.loads(raw)


def classify(it):
    """Какой звук просит этот элемент."""
    c = it["cls"]
    big = it["h"] >= 110 or it["w"] >= 900
    if re.search(r"\b(fig|big|figure|money|v)\b", c) and big:
        return "impact", -11
    if re.search(r"stamp|verdict|fin|always|out\b|yes\b", c):
        return "impact", -10
    if re.search(r"card|tile|mkc|\bd\b|\bg\b|\bs\b|\bc\b|side|box|warn|new3", c):
        return "pop", -13
    if re.search(r"role|chip|who|need|regs|nd\b|pt\b|st\b|\bl\b|it\b|step|msg", c):
        return "click", -15
    if re.search(r"fill|col|ring|rail|line2|path|track", c):
        return "chime", -14
    if re.search(r"not\b|old|exc|panic|line", c):
        return "whoosh", -13
    if re.search(r"sh\b|shot|proof", c):
        return "whoosh-deep", -10
    return None, None


def build_plan(scenes_dir, order, voice):
    b = Browser()
    plan = []
    try:
        clock = 0.0
        for name, dur in order:
            path = os.path.join(scenes_dir, name + ".html")
            if not os.path.exists(path):
                clock += dur
                continue
            data = collect(path, b)

            # переходы между сценами внутри файла
            trans = ["transition-a", "transition-b", "transition-c"]
            for i, s in enumerate(data["scenes"]):
                if s > 0.05:
                    plan.append({"at": round(clock + s, 2),
                                 "name": trans[i % 3], "gain": -10})

            picked = []
            for it in sorted(data["items"], key=lambda x: x["t"]):
                snd, gain = classify(it)
                if not snd:
                    continue
                at = clock + it["t"]
                # не чаще одного эффекта на 0.28 с
                if picked and at - picked[-1] < 0.28:
                    continue
                picked.append(at)
                plan.append({"at": round(at, 2), "name": snd, "gain": gain})
            clock += dur
    finally:
        b.close()

    plan.sort(key=lambda x: x["at"])
    return {"voice": voice, "sfx": plan}


def main():
    ap = argparse.ArgumentParser(description="Звуковой план из сцен")
    ap.add_argument("scenes_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--voice", default="../work/voice.wav")
    ap.add_argument("--order", default="",
                    help="s01:101,s02:114,... по умолчанию читается build-all.sh")
    a = ap.parse_args()

    if a.order:
        order = [(n, float(d)) for n, d in
                 (x.split(":") for x in a.order.split(","))]
    else:
        sh = os.path.join(os.path.dirname(os.path.abspath(a.scenes_dir)),
                          "build-all.sh")
        order = [(m.group(1), float(m.group(2))) for m in
                 re.finditer(r'"(s\d\d) (\d+)"', open(sh, encoding="utf-8").read())]

    plan = build_plan(a.scenes_dir, order, a.voice)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)

    from collections import Counter
    c = Counter(x["name"] for x in plan["sfx"])
    print(f"сцен: {len(order)}  эффектов: {len(plan['sfx'])}")
    for k, v in c.most_common():
        print(f"  {k:14} {v}")
    print(f"план: {a.out}")


if __name__ == "__main__":
    main()
