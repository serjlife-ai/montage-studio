# Сторонние компоненты пакета

> Что в пакете чужое, на каких условиях едет и где лежит оригинальный текст
> лицензии. Наша часть контура и её условия - [LICENSE.md](LICENSE.md).

---

## 1. Едет внутри архива

| Компонент | Где в пакете | Источник | Лицензия | Текст лицензии |
|---|---|---|---|---|
| Скиллы графики HyperFrames (26 шт.) | `.agents/skills/` | [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) | Apache-2.0 | [vendor-licenses/hyperframes-LICENSE-Apache-2.0.txt](vendor-licenses/hyperframes-LICENSE-Apache-2.0.txt) |
| Скилл `talking-head-recut` (внутри набора выше) | `.agents/skills/talking-head-recut/` | адаптация [notedit/vtake-skills](https://github.com/notedit/vtake-skills) | MIT, © 2026 leeoxiang | [vendor-licenses/talking-head-recut-NOTICE.md](vendor-licenses/talking-head-recut-NOTICE.md) |
| Банк звуков (9 файлов wav) | `sfx/` | библиотека скилла `media-use`, первоисточник - [Pixabay](https://pixabay.com/sound-effects/) | [Pixabay Content License](https://pixabay.com/service/license-summary/) | [vendor-licenses/sfx-CREDITS.md](vendor-licenses/sfx-CREDITS.md) |

**Что это значит на практике.**

- **Apache-2.0** разрешает пользоваться, менять и передавать дальше, включая
  коммерческое использование. Условия: сохранять текст лицензии и указание
  авторства, а в изменённых файлах отмечать факт изменения. В этом пакете
  скиллы едут **без изменений**, ровно как получены из репозитория.
- **MIT** у `talking-head-recut` разрешает то же самое при сохранении
  оригинального уведомления - оно лежит в `vendor-licenses/`.
- **Pixabay Content License** разрешает коммерческое использование звуков без
  указания авторства. Чего нельзя: перепродавать сами звуковые файлы или
  раздавать их как самостоятельный продукт. В нашем пакете звуки - расходный
  материал монтажа, а не товар, и это укладывается в лицензию. Отдельно
  выкладывать папку `sfx/` как «банк звуков» нельзя.

**Наши правки в чужом коде: нет.** Всё в `.agents/skills/` - оригинальные файлы.
Наша работа лежит рядом (`tools/`, `knowledge/`, `agents/`, `CLAUDE.md`) и
чужие файлы не переписывает. Инструмент `tools/sfx_bank.py` - наш код, который
берёт звуки из чужой библиотеки, режет и нормирует их.

---

## 2. Ставится на машину при установке (в архив не входит)

Эти программы `setup.sh` ставит из официальных источников - они не наши и
пакетом не распространяются.

| Программа | Как ставится | Лицензия |
|---|---|---|
| ffmpeg | `brew install ffmpeg` | LGPL-2.1+ / GPL-2+ в зависимости от сборки Homebrew |
| whisper.cpp (`whisper-cli`) | `brew install whisper-cpp` | MIT |
| Node.js | `brew install node` | MIT |
| numpy | pip | BSD-3-Clause |
| pillow | pip | MIT-CMU (HPND) |
| Модель распознавания речи `ggml-large-v3-turbo.bin` | скачивается с [huggingface.co/ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) | MIT (Whisper, OpenAI) |
| Пакет `hyperframes` (npx) | `npx hyperframes` при первом запуске графики | Apache-2.0 |

**Важно про ffmpeg.** Сборка Homebrew собирается с компонентами под GPL. Это
нормально для работы на своей машине и для роликов, которые вы делаете. Если
однажды соберёте на базе контура собственный продукт на продажу с встроенным
ffmpeg внутри - условия GPL надо будет читать отдельно, а не полагаться на
этот файл.

---

## 3. Чего в пакете нет намеренно

- **Скилл `autocut`** - лицензионный, куплен отдельно и передаче не подлежит.
- **Модели генерации голоса и музыки** (Kokoro TTS, MusicGen) - тянут гигабайты
  и не нужны контуру.
- **Remotion** - его задачи закрывает HyperFrames.

---

_Реестр обновляется вместе с пакетом. Пакет пересобирается одной командой
`tools/make_kit.sh`, и сборка падает, если лицензионные файлы пропали._
