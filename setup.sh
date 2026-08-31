#!/usr/bin/env bash
# Разворачивает монтажный контур на macOS и на Linux: зависимости, модель
# распознавания, скиллы графики, банк звуков. Ничего не ставит молча - каждый
# шаг говорит, что делает, и в конце печатает честный чеклист.
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

# Размер файла: на macOS stat -f%z, на Linux stat -c%s. Одна буква разницы
# роняла установку целиком, поэтому спрашиваем через обёртку.
# Флаг -L обязателен: модель часто лежит ссылкой на общий файл, и без него
# stat меряет саму ссылку (несколько байт) и решает, что модели нет.
fsize() { stat -Lf%z "$1" 2>/dev/null || stat -Lc%s "$1" 2>/dev/null || echo 0; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *)      PLATFORM="unknown" ;;
esac
say "0/7 платформа"
ok "система: $OS ($PLATFORM)"

# ---------- 1. менеджер пакетов ----------
say "1/7 менеджер пакетов"
PKG=""
if [ "$PLATFORM" = "macos" ]; then
  if command -v brew >/dev/null 2>&1; then
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
    PKG="brew"
  else
    bad "Homebrew не установлен"
    echo '  поставить: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo "  это единственный шаг, который надо сделать руками - он просит пароль."
  fi
elif [ "$PLATFORM" = "linux" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    PKG="apt"; ok "apt-get найден"
  elif command -v dnf >/dev/null 2>&1; then
    PKG="dnf"; ok "dnf найден"
  elif command -v pacman >/dev/null 2>&1; then
    PKG="pacman"; ok "pacman найден"
  else
    bad "менеджер пакетов не опознан - ставь зависимости руками"
  fi
else
  bad "система не опознана, ставь зависимости руками"
fi

# ---------- 2. ffmpeg, whisper, node ----------
say "2/7 инструменты: ffmpeg, whisper-cli, node"
declare -a need
for pair in "ffmpeg:ffmpeg" "whisper-cli:whisper-cpp" "node:node"; do
  bin="${pair%%:*}"; pkg="${pair##*:}"
  if command -v "$bin" >/dev/null 2>&1; then
    ok "$bin - $(command -v "$bin")"
  else
    bad "$bin не найден (пакет $pkg)"
    need+=("$pkg")
  fi
done
if [ ${#need[@]} -gt 0 ] && [ "$CHECK_ONLY" = 0 ]; then
  case "$PKG" in
    brew)
      echo "  ставлю: brew install ${need[*]}"
      brew install "${need[@]}" && for p in "${need[@]}"; do ok "поставлен $p"; done ;;
    apt)
      # whisper-cpp в репозиториях Debian/Ubuntu нет - собирается из исходников
      apt_list=(); for p in "${need[@]}"; do
        [ "$p" = "whisper-cpp" ] || apt_list+=("$p"); done
      if [ ${#apt_list[@]} -gt 0 ]; then
        echo "  ставлю: sudo apt-get install -y ${apt_list[*]}"
        sudo apt-get install -y "${apt_list[@]}" \
          && for p in "${apt_list[@]}"; do ok "поставлен $p"; done
      fi
      if printf '%s\n' "${need[@]}" | grep -qx whisper-cpp; then
        echo "  whisper-cli в apt отсутствует, собери из исходников:"
        echo "    git clone https://github.com/ggml-org/whisper.cpp ~/whisper.cpp"
        echo "    cmake -B build -S ~/whisper.cpp && cmake --build build -j"
        echo "    затем добавь build/bin в PATH"
      fi ;;
    dnf)    echo "  ставлю: sudo dnf install -y ${need[*]}"; sudo dnf install -y "${need[@]}" ;;
    pacman) echo "  ставлю: sudo pacman -S --noconfirm ${need[*]}"; sudo pacman -S --noconfirm "${need[@]}" ;;
  esac
fi

# ---------- 3. шрифт с кириллицей ----------
# Титры рисуются в PIL по файлу шрифта. На macOS системные шрифты есть всегда,
# на голом Linux-сервере их может не быть вовсе - и монтаж падал на титрах,
# пройдя перед этим резку и распознавание.
say "3/7 шрифт с кириллицей для титров"
FOUND_FONT=""
for f in /System/Library/Fonts/SFNSRounded.ttf \
         /System/Library/Fonts/SFNS.ttf \
         /usr/share/fonts/truetype/noto/NotoSans-Bold.ttf \
         /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
         /usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf; do
  [ -f "$f" ] && { FOUND_FONT="$f"; break; }
done
if [ -z "$FOUND_FONT" ] && command -v fc-match >/dev/null 2>&1; then
  cand="$(fc-match -f '%{file}' ':bold:lang=ru' 2>/dev/null || true)"
  [ -n "$cand" ] && [ -f "$cand" ] && FOUND_FONT="$cand"
fi
if [ -n "$FOUND_FONT" ]; then
  ok "шрифт: $FOUND_FONT"
elif [ "$CHECK_ONLY" = 0 ] && [ "$PKG" = "apt" ]; then
  echo "  ставлю шрифты: sudo apt-get install -y fonts-dejavu-core fontconfig"
  sudo apt-get install -y fonts-dejavu-core fontconfig >/dev/null 2>&1 \
    && ok "шрифты поставлены" || bad "шрифты не поставились"
else
  bad "шрифта с кириллицей нет - титры не нарисуются"
  echo "  Linux: sudo apt install fonts-dejavu-core   (или fonts-noto-core)"
  echo "  либо задай свой: export SUBS_FONT=/путь/к/шрифту.ttf"
fi

# ---------- 4. python + numpy + pillow ----------
say "4/7 python: numpy и pillow"
PY="python3"
if python3 -c "import numpy, PIL" >/dev/null 2>&1; then
  ok "numpy и pillow есть в системном python3"
elif [ -x "$ROOT/.venv/bin/python3" ] \
     && "$ROOT/.venv/bin/python3" -c "import numpy, PIL" >/dev/null 2>&1; then
  PY="$ROOT/.venv/bin/python3"
  ok "numpy и pillow есть в .venv контура"
else
  if [ "$CHECK_ONLY" = 1 ]; then
    bad "numpy/pillow отсутствуют"
  else
    echo "  системный python3 без библиотек - собираю отдельное окружение .venv"
    # На свежих Debian/Ubuntu venv лежит отдельным пакетом, без него шаг падает
    if ! python3 -m venv --help >/dev/null 2>&1; then
      [ "$PKG" = "apt" ] && sudo apt-get install -y python3-venv >/dev/null 2>&1
    fi
    python3 -m venv "$ROOT/.venv" \
      && "$ROOT/.venv/bin/pip" -q install --upgrade pip \
      && "$ROOT/.venv/bin/pip" -q install numpy pillow \
      && { PY="$ROOT/.venv/bin/python3"; ok "окружение .venv собрано, numpy и pillow внутри"; } \
      || bad "не удалось собрать .venv"
  fi
fi
[ -x "$ROOT/.venv/bin/python3" ] && PY="$ROOT/.venv/bin/python3"

# ---------- 5. модель распознавания ----------
say "5/7 модель распознавания речи (whisper large-v3-turbo, 1.6 ГБ)"
mkdir -p "$ROOT/models"
# Модель могла быть скачана раньше и лежать вне контура (её ставит и
# telegram-контур, и whisper.cpp). Второй раз 1.6 ГБ не качаем: ищем и
# кладём рядом ссылку.
if [ ! -f "$MODEL" ]; then
  for cand in "${WHISPER_MODEL:-}" \
              "$HOME/tools/whisper.cpp/models/ggml-large-v3-turbo.bin" \
              "$HOME/.config/tg-remote/models/ggml-large-v3-turbo.bin" \
              "$HOME/.cache/whisper/ggml-large-v3-turbo.bin"; do
    if [ -n "$cand" ] && [ -f "$cand" ] && [ "$(fsize "$cand")" -gt 500000000 ]; then
      ln -sfn "$cand" "$MODEL"
      ok "модель найдена на машине, поставил ссылку: $cand"
      break
    fi
  done
fi
if [ -f "$MODEL" ] && [ "$(fsize "$MODEL")" -gt 500000000 ]; then
  ok "модель на месте ($(du -hL "$MODEL" | cut -f1))"
elif [ "$CHECK_ONLY" = 1 ] || [ "$SKIP_MODEL" = 1 ]; then
  bad "модели нет - без неё не будет титров и подсчёта слов"
  echo "  скачать: curl -L -o '$MODEL' '$MODEL_URL'"
else
  echo "  качаю модель, это несколько минут..."
  curl -fL --progress-bar -o "$MODEL.part" "$MODEL_URL" \
    && mv "$MODEL.part" "$MODEL" && ok "модель скачана" \
    || { rm -f "$MODEL.part"; bad "модель не скачалась - проверь интернет и повтори ./setup.sh"; }
fi

# ---------- 6. скиллы графики ----------
say "6/7 скиллы HyperFrames"
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

# ---------- 7. банк звуков ----------
say "7/7 банк звуков"
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
  # Общий пролог обёрток: найти python окружения контура и whisper-cli.
  # На сервере whisper часто собран из исходников и в PATH его нет.
  read -r -d '' PRELUDE <<'PRE' || true
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python3 ] && PY=.venv/bin/python3
export WHISPER_MODEL="${WHISPER_MODEL:-$PWD/models/ggml-large-v3-turbo.bin}"
if ! command -v whisper-cli >/dev/null 2>&1; then
  for d in "$HOME/tools/whisper.cpp/build/bin" "$HOME/whisper.cpp/build/bin" \
           /usr/local/bin /opt/homebrew/bin; do
    [ -x "$d/whisper-cli" ] && { export PATH="$d:$PATH"; break; }
  done
fi
PRE

  {
    echo '#!/usr/bin/env bash'
    echo '# Запуск монтажа одной командой: ./montage вход.mov --censor'
    echo "$PRELUDE"
    echo 'exec "$PY" tools/reels.py "$@"'
  } > "$ROOT/montage"
  chmod +x "$ROOT/montage"
  ok "команда запуска готова: ./montage"

  # вторая обёртка - эталонная сборка целиком по плану (reel.py тянет
  # reels/subtitles/creative/window и запускает dynamic.py, поэтому запускается
  # из корня пакета, а не из tools/)
  {
    echo '#!/usr/bin/env bash'
    echo '# Эталонная сборка по плану: ./reel вход.mov --plan план.json -o готово.mp4'
    echo "$PRELUDE"
    echo 'exec "$PY" tools/reel.py "$@"'
  } > "$ROOT/reel"
  chmod +x "$ROOT/reel"
  ok "эталонная сборка готова: ./reel"

  # третья обёртка - предполётная проверка исходника
  {
    echo '#!/usr/bin/env bash'
    echo '# Предполётная проверка: ./preflight вход.mov --frames кадры/'
    echo "$PRELUDE"
    echo 'exec "$PY" tools/preflight.py "$@"'
  } > "$ROOT/preflight"
  chmod +x "$ROOT/preflight"
  ok "предполётная проверка готова: ./preflight"

  # четвёртая обёртка - оценка статичности готового ролика
  {
    echo '#!/usr/bin/env bash'
    echo '# Оценка статики готового ролика: ./staticscore готово.mp4'
    echo "$PRELUDE"
    echo 'exec "$PY" tools/staticscore.py "$@"'
  } > "$ROOT/staticscore"
  chmod +x "$ROOT/staticscore"
  ok "оценка статики готова: ./staticscore"

  # пятая обёртка - нарезка эфира на клипы по транскрипту
  {
    echo '#!/usr/bin/env bash'
    echo '# Нарезка эфира на клипы: ./clips транскрипт.srt --count 5'
    echo "$PRELUDE"
    echo 'exec "$PY" tools/clips.py "$@"'
  } > "$ROOT/clips"
  chmod +x "$ROOT/clips"
  ok "нарезка на клипы готова: ./clips"
fi

printf "\n\033[1m== Итог\033[0m\n"
printf "%s\n" "${REPORT[@]}"
if printf "%s\n" "${REPORT[@]}" | grep -q "НЕТ"; then
  printf "\nЕсть незакрытые пункты - смотри строки НЕТ выше.\n"
  printf "Покажи этот вывод Claude, он подскажет что делать дальше.\n"
  exit 1
fi
printf "\nВсё на месте. Проверка на своём файле: ./montage видео.mov --analyze\n"
