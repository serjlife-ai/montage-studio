#!/usr/bin/env bash
# Разворачивает монтажный контур на macOS: зависимости, модель распознавания,
# скиллы графики, банк звуков. Ничего не ставит молча - каждый шаг говорит,
# что делает, и в конце печатает честный чеклист.
#
#   ./setup.sh            полная установка
#   ./setup.sh --check    только проверка, ничего не ставит
#   ./setup.sh --no-model установка без скачивания модели (1.6 ГБ)
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

CHECK_ONLY=0; SKIP_MODEL=0
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --no-model) SKIP_MODEL=1 ;;
    *) echo "неизвестный флаг: $a"; exit 2 ;;
  esac
done

MODEL="$ROOT/models/ggml-large-v3-turbo.bin"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
declare -a REPORT

say()  { printf "\n\033[1m== %s\033[0m\n" "$*"; }
ok()   { REPORT+=("  ОК   $*"); printf "  ок: %s\n" "$*"; }
bad()  { REPORT+=("  НЕТ  $*"); printf "  нет: %s\n" "$*"; }

# ---------- 1. Homebrew ----------
say "1/6 Homebrew"
if command -v brew >/dev/null 2>&1; then
  ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
  BREW=1
else
  BREW=0
  bad "Homebrew не установлен"
  echo '  поставить: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo "  это единственный шаг, который надо сделать руками - он просит пароль."
fi

# ---------- 2. ffmpeg, whisper, node ----------
say "2/6 инструменты: ffmpeg, whisper-cpp, node"
need_brew=()
for pair in "ffmpeg:ffmpeg" "whisper-cli:whisper-cpp" "node:node"; do
  bin="${pair%%:*}"; pkg="${pair##*:}"
  if command -v "$bin" >/dev/null 2>&1; then
    ok "$bin - $(command -v "$bin")"
  else
    bad "$bin не найден (пакет $pkg)"
    need_brew+=("$pkg")
  fi
done
if [ ${#need_brew[@]} -gt 0 ] && [ "$CHECK_ONLY" = 0 ] && [ "$BREW" = 1 ]; then
  echo "  ставлю: brew install ${need_brew[*]}"
  brew install "${need_brew[@]}" && for pkg in "${need_brew[@]}"; do ok "поставлен $pkg"; done
fi

# ---------- 3. python + numpy + pillow ----------
say "3/6 python: numpy и pillow"
PY="python3"
if python3 -c "import numpy, PIL" >/dev/null 2>&1; then
  ok "numpy и pillow есть в системном python3"
else
  if [ "$CHECK_ONLY" = 1 ]; then
    bad "numpy/pillow отсутствуют"
  else
    echo "  системный python3 без библиотек - собираю отдельное окружение .venv"
    python3 -m venv "$ROOT/.venv" \
      && "$ROOT/.venv/bin/pip" -q install --upgrade pip \
      && "$ROOT/.venv/bin/pip" -q install numpy pillow \
      && { PY="$ROOT/.venv/bin/python3"; ok "окружение .venv собрано, numpy и pillow внутри"; } \
      || bad "не удалось собрать .venv"
  fi
fi
[ -x "$ROOT/.venv/bin/python3" ] && PY="$ROOT/.venv/bin/python3"

# ---------- 4. модель распознавания ----------
say "4/6 модель распознавания речи (whisper large-v3-turbo, 1.6 ГБ)"
mkdir -p "$ROOT/models"
if [ -f "$MODEL" ] && [ "$(stat -f%z "$MODEL")" -gt 500000000 ]; then
  ok "модель на месте ($(du -h "$MODEL" | cut -f1))"
elif [ "$CHECK_ONLY" = 1 ] || [ "$SKIP_MODEL" = 1 ]; then
  bad "модели нет - без неё не будет титров и подсчёта слов"
  echo "  скачать: curl -L -o '$MODEL' '$MODEL_URL'"
else
  echo "  качаю модель, это несколько минут..."
  curl -fL --progress-bar -o "$MODEL.part" "$MODEL_URL" \
    && mv "$MODEL.part" "$MODEL" && ok "модель скачана" \
    || { rm -f "$MODEL.part"; bad "модель не скачалась - проверь интернет и повтори ./setup.sh"; }
fi

# ---------- 5. скиллы графики ----------
say "5/6 скиллы HyperFrames"
if [ -d "$ROOT/.agents/skills" ]; then
  n=$(find "$ROOT/.agents/skills" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
  ok "скиллов в пакете: $n"
  if [ "$CHECK_ONLY" = 0 ]; then
    mkdir -p "$ROOT/.claude/skills"
    for d in "$ROOT/.agents/skills"/*/; do
      name=$(basename "$d")
      ln -sfn "../../.agents/skills/$name" "$ROOT/.claude/skills/$name"
    done
    ok "скиллы подключены к Claude (.claude/skills)"
  fi
else
  bad "папки .agents/skills нет - распакуй архив целиком"
fi

# ---------- 6. банк звуков ----------
say "6/6 банк звуков"
n=$(ls "$ROOT/sfx"/*.wav 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" -gt 0 ]; then
  ok "звуков в банке: $n"
elif [ "$CHECK_ONLY" = 0 ]; then
  "$PY" "$ROOT/tools/sfx_bank.py" "$ROOT/sfx" >/dev/null 2>&1 \
    && ok "банк собран заново" || bad "банк не собрался - нужен ffmpeg и скилл media-use"
else
  bad "банк звуков пуст"
fi

# ---------- обёртка запуска ----------
if [ "$CHECK_ONLY" = 0 ]; then
  cat > "$ROOT/montage" <<WRAP
#!/usr/bin/env bash
# Запуск монтажа одной командой: ./montage вход.mov --censor
cd "\$(dirname "\$0")"
PY=python3
[ -x .venv/bin/python3 ] && PY=.venv/bin/python3
export WHISPER_MODEL="\$PWD/models/ggml-large-v3-turbo.bin"
exec "\$PY" tools/reels.py "\$@"
WRAP
  chmod +x "$ROOT/montage"
  ok "команда запуска готова: ./montage"

  # вторая обёртка - эталонная сборка целиком по плану (reel.py тянет
  # reels/subtitles/creative/window и запускает dynamic.py, поэтому запускается
  # из корня пакета, а не из tools/)
  cat > "$ROOT/reel" <<WRAP
#!/usr/bin/env bash
# Эталонная сборка ролика по плану: ./reel вход.mov --plan план.json -o готово.mp4
cd "\$(dirname "\$0")"
PY=python3
[ -x .venv/bin/python3 ] && PY=.venv/bin/python3
export WHISPER_MODEL="\$PWD/models/ggml-large-v3-turbo.bin"
exec "\$PY" tools/reel.py "\$@"
WRAP
  chmod +x "$ROOT/reel"
  ok "эталонная сборка готова: ./reel"
fi

printf "\n\033[1m== Итог\033[0m\n"
printf "%s\n" "${REPORT[@]}"
if printf "%s\n" "${REPORT[@]}" | grep -q "НЕТ"; then
  printf "\nЕсть незакрытые пункты - смотри строки НЕТ выше.\n"
  printf "Покажи этот вывод Claude, он подскажет что делать дальше.\n"
  exit 1
fi
printf "\nВсё на месте. Проверка на своём файле: ./montage видео.mov --analyze\n"
