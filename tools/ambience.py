#!/usr/bin/env python3
"""Саунд-дизайн слайда: раскладка звуков из библиотеки по таймлайну.

Не музыка и не синтез. Берём готовые эффекты из библиотеки media-use
(Pixabay Content License) и расставляем их по секундам. Правило контура:
свой синтез запрещён, источник один - проверенная библиотека.

  python3 tools/ambience.py звук.json -o слайд1.wav

Инструмент меряет то же, что завалило синтез в прошлый раз: долю энергии
выше 6 кГц (шипение) и пиковый уровень. Хвост обязан уходить в тишину,
иначе на зацикленном слайде будет щелчок.
"""

import argparse
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
HF_MAX = 30.0        # доля энергии выше 6 кГц, %; выше - звенит и режет ухо
TAIL_MAX = 0.02      # чем тише хвост последних 0.2с, тем чище петля

_HERE = Path(__file__).resolve().parent
LIB_CANDIDATES = [
    _HERE / ".." / ".." / ".." / ".agents/skills/media-use/audio/assets/sfx",
    _HERE / ".." / ".." / ".." / ".claude/skills/media-use/audio/assets/sfx",
    Path.home() / ".claude/skills/media-use/audio/assets/sfx",
]


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def library() -> Path:
    for p in LIB_CANDIDATES:
        if p.is_dir():
            return p.resolve()
    die("не найдена библиотека звуков media-use")


def load(path: Path) -> np.ndarray:
    """Читаем любой формат через ffmpeg, отдаём моно float в -1..1."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        die(f"не читается {path.name}: {r.stderr.decode().strip()}")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def hf_share(x: np.ndarray) -> float:
    """Доля энергии выше 6 кГц в процентах."""
    if not x.any():
        return 0.0
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    total = (spec ** 2).sum()
    return float((spec[freqs > 6000] ** 2).sum() / total * 100) if total else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="JSON: {duration, cues:[{sound, at, gain_db}]}")
    ap.add_argument("-o", "--out", required=True, help="куда сохранить WAV")
    ap.add_argument("--list", action="store_true", help="показать доступные звуки")
    args = ap.parse_args()

    lib = library()
    if args.list:
        for f in sorted(lib.glob("*.mp3")):
            print(f.stem)
        return

    plan = json.loads(Path(args.plan).read_text())
    dur = float(plan.get("duration", 5.0))
    n = int(dur * SR)
    bed = np.zeros(n, dtype=np.float32)

    used = []
    for cue in plan.get("cues", []):
        src = lib / f"{cue['sound']}.mp3"
        if not src.exists():
            die(f"нет звука {cue['sound']} в библиотеке (список: --list)")
        x = load(src) * (10 ** (float(cue.get("gain_db", -6)) / 20))
        at = int(float(cue["at"]) * SR)
        if at >= n:
            die(f"{cue['sound']} стоит на {cue['at']}с, а дорожка {dur}с")
        end = min(n, at + len(x))
        bed[at:end] += x[:end - at]
        used.append(f"{cue['sound']}@{cue['at']}с")

    # хвост уводим в тишину: на зацикленном слайде обрыв слышен как щелчок
    fade = int(0.25 * SR)
    if n > fade:
        bed[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    bed[:int(0.01 * SR)] *= np.linspace(0, 1, int(0.01 * SR), dtype=np.float32)

    peak = float(np.abs(bed).max()) if bed.any() else 0.0
    if peak > 1.0:
        bed /= peak * 1.02
        peak = float(np.abs(bed).max())

    hf = hf_share(bed)
    tail = float(np.abs(bed[-int(0.2 * SR):]).max())

    problems = []
    if hf > HF_MAX:
        problems.append(f"доля энергии выше 6 кГц {hf:.1f}% при потолке {HF_MAX}% - будет шипеть")
    if tail > TAIL_MAX:
        problems.append(f"хвост не в тишине ({tail:.3f}) - на петле будет щелчок")
    if problems:
        print("СБОРКА ОСТАНОВЛЕНА:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.out)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(bed, -1, 1) * 32767).astype(np.int16).tobytes())

    db = 20 * math.log10(peak) if peak > 0 else -99
    print(f"{out.name}: {dur:.1f}с, пик {db:.1f} dB, выше 6 кГц {hf:.1f}%, хвост {tail:.4f}")
    print("слои: " + ", ".join(used))


if __name__ == "__main__":
    main()
