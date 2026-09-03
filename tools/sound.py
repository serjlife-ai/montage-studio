#!/usr/bin/env python3
"""Звуковая дорожка презентации: голос + эффекты по плану.

Зачем отдельно от dynamic.py. Тот ставит свуши сам, по своим катам. Здесь
каты заданы сценарием (появилась плашка, поехал счётчик, влетел скриншот),
и эффект обязан попасть в кадр, а не в ритм речи.

Закон 11 контура: уровень эффекта меряется рендером с ЗАГЛУШЕННЫМ голосом.
Общий пик и сравнение полос врут. Поэтому сборка делает два прохода: микс и
контрольный микс без речи, по нему печатает пик каждого эффекта.

План - JSON:

    {"voice": "voice.wav",
     "sfx": [{"at": 12.9, "name": "impact", "gain": -6},
             {"at": 23.5, "name": "whoosh"},
             {"at": 46.8, "name": "transition-a", "gain": -9}]}

`gain` в dB относительно нормализованного эффекта, по умолчанию из таблицы
DEFAULT_GAIN: суб-удары тише (они и так пробиваются, речи на 50 Гц нет),
клики громче (короткий транзиент теряется).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SFX_DIR = os.path.join(os.path.dirname(HERE), "sfx")

# Уровни подобраны замером, не на слух: см. knowledge/sound-design.md.
# Голос идёт на 0.70 (закон 3) - иначе лимитер съедает каждый эффект.
VOICE_GAIN = 0.70
DEFAULT_GAIN = {
    "impact": -9.0, "impact-hard": -9.0,   # суб-удар и так слышен
    "whoosh": -7.0, "whoosh-deep": -7.0,
    "transition-a": -8.0, "transition-b": -8.0, "transition-c": -8.0,
    "pop": -8.0, "click": -11.0, "typing": -13.0,
    "chime": -10.0, "riser": -11.0,
}


def sfx_path(name):
    p = os.path.join(SFX_DIR, name + ".wav")
    if not os.path.exists(p):
        sys.exit(f"нет звука: {p}")
    return p


def build(plan, out, mute_voice=False, duration=None):
    voice = plan["voice"]
    items = plan.get("sfx", [])

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", voice]
    for s in items:
        cmd += ["-i", sfx_path(s["name"])]

    parts = []
    vg = 0.0 if mute_voice else VOICE_GAIN
    parts.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:"
                 f"channel_layouts=stereo,volume={vg}[v]")

    mixed = ["[v]"]
    for i, s in enumerate(items, start=1):
        g = s.get("gain", DEFAULT_GAIN.get(s["name"], -8.0))
        delay = int(round(s["at"] * 1000))
        parts.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo,volume={g}dB,"
            f"adelay={delay}|{delay}[s{i}]"
        )
        mixed.append(f"[s{i}]")

    parts.append(
        "".join(mixed)
        + f"amix=inputs={len(mixed)}:normalize=0:dropout_transition=0[mx]"
    )
    # Голос стоит на 0.70 (закон 3 sound-design) - значит после микса весь
    # трек тише цели на ~3 dB. Возвращаем громкость СТАТИЧЕСКИМ усилением
    # до лимитера, а не loudnorm'ом: тот работает динамически и душит
    # громкие места (закон 4).
    parts.append("[mx]volume=3.1dB[gn]")
    parts.append("[gn]alimiter=limit=0.79:attack=4:release=60:level=disabled[out]")

    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]"]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg: " + r.stderr[-2000:])
    return out


def peak_window(path, start, end):
    """Максимум в окне, dBFS. Единственная честная метрика по закону 11.

    `-v error` здесь ставить нельзя: astats печатает статистику на том же
    уровне info, и с погашенным логом функция возвращает NaN по каждому
    эффекту - выглядит как «34 молчащих звука» на полностью рабочем миксе.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats",
         "-ss", str(max(0, start)), "-to", str(end),
         "-i", path, "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in r.stderr.splitlines():
        if "Peak level dB" in line:
            try:
                return float(line.split(":")[-1])
            except ValueError:
                pass
    return float("nan")


def main():
    ap = argparse.ArgumentParser(description="Сборка звука презентации")
    ap.add_argument("plan")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--check", action="store_true",
                    help="контрольный микс без голоса + пики эффектов")
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(a.plan))
    if not os.path.isabs(plan["voice"]):
        plan["voice"] = os.path.join(base, plan["voice"])

    build(plan, a.out, duration=a.duration)
    print(f"собрано: {a.out}  эффектов: {len(plan.get('sfx', []))}")

    if a.check:
        silent = a.out.replace(".wav", "-novoice.wav")
        build(plan, silent, mute_voice=True, duration=a.duration)
        print("\nпроверка уровней (рендер без голоса, закон 11):")
        bad = 0
        for s in plan.get("sfx", []):
            p = peak_window(silent, s["at"] - 0.05, s["at"] + 1.2)
            mark = ""
            if p != p or p < -45:
                mark = "  <- НЕ СЛЫШНО"
                bad += 1
            print(f"  {s['at']:7.2f}s  {s['name']:14} пик {p:6.1f} dBFS{mark}")
        print(f"\nмолчащих эффектов: {bad}")


if __name__ == "__main__":
    main()
