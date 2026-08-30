# Киберпанк-карусель Сержа: раскадр и промпты

> Точное повторение структуры карусели @beautymaster (7 слайдов, 1314 лайков, 1901 пересылка) в нашей стилистике. Текст и порядок слайдов - его, визуал - киберпанк-аватар Сержа. Механика формата - [[dynamic-carousel]].

## Стиль, единый на все слайды

От аватарки Сержа (идёт в GPT референсом без изменений):

- чёрный фон, глубокая тень,
- левая половина лица живая, монохром, наушники, тёмные очки,
- правая половина - механика: платы, кабели, неоновые линии,
- два цвета неона: холодный голубой и красный. Третий не вводить,
- в правой линзе очков светится `AI`.

У блогера светлый фон и античность, у нас чёрный фон и неон. Копируем **структуру и текст**, не палитру.

## Раскадр 7 слайдов

| № | Роль | Текст |
|---|---|---|
| 1 | крючок, левая половина панорамы | НОВЫЙ ФОРМАТ ВИДЕО КАРУСЕЛЕЙ ДЛЯ БЛОГА + стрелка + «Цепляет с первого слайда» |
| 2 | продолжение панорамы | За счёт чего он работает? + абзац про первые два слайда + «СОХРАНИ, ЧТОБЫ НЕ ПОТЕРЯТЬ» |
| 3 | инструкция | Шаг №1 и промпт: СОЗДАЁМ ПАНОРАМНУЮ КАРТИНКУ ДЛЯ ПЕРВЫХ 2х СЛАЙДОВ |
| 4 | инструкция | Шаг №2 и промпт: ОЖИВЛЯЕМ ОБЪЕКТ НА КАРТИНКЕ |
| 5 | доказательство | Я УЖЕ ПРОВЕРИЛ ЭТО НА СВОЁМ БЛОГЕ + скриншоты охватов |
| 6 | доказательство | РЕАЛЬНЫЙ РЕЗУЛЬТАТ + цифры за месяц |
| 7 | призыв | ХОЧЕШЬ РАСТИТЬ БЛОГ БЕЗ REELS? + список + кодовое слово в комментарии |

**Слайды 5 и 6 - чужие цифры.** Их нельзя переносить как есть: это результат его блога, а не Сержа. Варианты замены - в разделе ниже.

## Слайды 1-2: панорама

Генерится одним широким кадром 3:2, режется пополам. Промпт для GPT Image, фото прикладывается к сообщению:

```
Use the attached portrait as the exact visual reference for the man:
keep his face, hairstyle, headphones, dark glasses with the glowing "AI"
lettering, and the half-human half-cybernetic split of his head unchanged.

Create a single seamless horizontal composition, landscape 3:2, that will be
cut in half into two vertical Instagram slides. Critically important: it is one
continuous image with no seam, frame or divider in the middle.

Style: dark cyberpunk editorial poster, premium and minimal, deep black
background, high contrast, cinematic rim light. Only two accent colors:
cold cyan glow and deep red glow. No third color.
Typography: heavy grotesque sans-serif, pure white, tight leading, high
readability.

LEFT half:
- Large bold uppercase Russian headline:
  НОВЫЙ ФОРМАТ ВИДЕО КАРУСЕЛЕЙ ДЛЯ БЛОГА
- Below it a long thin horizontal arrow pointing right.
- Bottom left: a thin outlined circle with a small arrow inside, next to it
  two lines of small text: Цепляет с первого слайда

RIGHT half:
- Large headline: За счёт чего он работает?
- Below it smaller text: Всё решают первые 2 слайда. Сделай их визуально
  цепляющими - человек залипает и листает дальше, пока не дойдёт до конца.
- Top right corner: a small bookmark icon with tiny uppercase text
  СОХРАНИ, ЧТОБЫ НЕ ПОТЕРЯТЬ

MAIN VISUAL: the man from the reference photo, large, positioned in the CENTER
so that his head sits on the LEFT half and his shoulder, cybernetic arm and
neon circuit traces flow onto the RIGHT half, creating depth and one continuous
image. He looks into the frame. He never overlaps the text.

All Russian text must be spelled exactly as written above, perfectly legible,
no distortion, no extra words, no watermark, no logo.
Keep every text element inside the central 80% of the frame height.
```

Нарезка:

```bash
python3 tools/carousel.py slice панорама.png --slides 2
```

GPT Image отдаёт 1536x1024 - это ровно 3:2, обрезки не будет, но каждый слайд тянется с 768 до 1080 по ширине. Для чистоты панораму до нарезки прогнать апскейлером до 2160x1440.

## Слайды 3-4: инструкция

Те же правила стиля, но персонаж уходит на край, а центр отдан под текст промпта. Заменяемая часть - блок Layout:

```
LEFT: a small dark pill-shaped badge with white text [Шаг №1 и промпт],
under it a large uppercase headline [СОЗДАЁМ ПАНОРАМНУЮ КАРТИНКУ],
under it a smaller line [ДЛЯ ПЕРВЫХ 2х СЛАЙДОВ].
CENTER: a large rounded rectangle panel with a thin cyan outline, semi
transparent over the black background, filled with small readable Russian
body text in several short paragraphs. Leave the panel EMPTY of text -
it is a placeholder, fill it with plain lorem-style Russian lines.
RIGHT EDGE: the man from the reference, cropped by the edge, only his
cybernetic half visible, dimmed so the panel stays the focus.
```

Текст промпта внутрь панели нейросетью не набирать - мелкий текст она порвёт. Панель генерим пустой, текст кладём сверху своим контуром по закону двух слоёв из [[dynamic-carousel]].

## Слайды 5-6: чужие цифры, нужна замена

У блогера тут скриншоты статистики: 116 000 и 44 000 просмотров, +1600 подписчиков за июль. Переносить эти числа к себе нельзя - это его результат, и на скриншотах он виден. Три честных замены:

1. **Свои цифры из другого формата.** У Сержа есть охваты по Reels и роликам - берём их и меняем подпись на «мои цифры по коротким видео, теперь переношу механику на карусели».
2. **Убрать доказательство, усилить механику.** Слайды 5-6 отдать под «типичные ошибки первого слайда» и «чек-лист перед публикацией». Формат не сломается: у него доказательство держит доверие, у нас его держит польза.
3. **Отложить.** Выпустить карусель без 5-6, собрать свою статистику за две-три публикации, потом добавить слайды с настоящими числами.

Решение за Сержем, по умолчанию идём вторым путём.

## Слайд 7: призыв

```
Large uppercase Russian headline:
ХОЧЕШЬ РАСТИТЬ БЛОГ И ПРИВЛЕКАТЬ КЛИЕНТОВ - БЕЗ REELS И ЕЖЕДНЕВНЫХ СЪЁМОК?
Below: a short list, each line starting with a thin arrow marker.
Below the list: a wide dark bar with white and cyan text:
НАПИШИ «КАРУСЕЛЬ» В КОММЕНТАРИЯХ
Bottom: a bookmark icon with small text: Сохрани пост, чтобы не потерять
RIGHT EDGE: the man from the reference, cropped by the edge, looking at the
headline.
```

Кодовое слово в комментарии ведёт в директ. Приём рабочий сам по себе: отвечать надо в тот же день.

## Оживление

Оживляем слайд 1 и по желанию слайд 7. Кадр сначала приводится к слайду:

```bash
python3 tools/carousel.py slice кадр.png --slides 1
```

Промпт:

```
Static camera. Do not move, pan, zoom or rotate the camera.
The background must remain completely static.
Preserve the man's appearance, colors, lighting and position.
Do not add new elements, letters, symbols or interface elements.

LOCKED DETAIL - the eyewear: the right lens displays exactly the two letters
"A" and "I" in glowing cyan pixel type. These two letters must stay identical
in every frame: same shape, same size, same position, same color. Never replace
them with any other letters, logo, monogram or brand mark. Do not regenerate,
morph, animate or reinterpret the lens content. The frames of the glasses stay
unchanged too.

Moving: he turns his head slightly to the right, about 10 degrees, slowly and
naturally, then holds. The neon circuit traces on his cybernetic half pulse
softly. The "AI" lettering stays perfectly still and unchanged while he moves.
Everything else in the frame stays 100% unchanged.
```

Негатив-промпт, если модель его принимает:

```
LV, logo, monogram, brand text, changing letters, morphing text, new text,
distorted face, camera movement, zoom, background motion
```

Длина 5 секунд. Три движения - потолок, четвёртое модель берёт из фона и ломает статику.

## Описание поста

Первые 125 символов видны до «ещё» - там должен стоять крючок, а не приветствие.
Кодовое слово одно на весь пост, капсом, в кавычках. Правило Империи: максимум
5 хэштегов, 1-2 брендовых плюс 2-3 тематических.

```
Первый слайд - видео. Ты уже досмотрел его, пока читал эту строку.

Собрано без дизайнера, без Canva и без Figma. Кадр сгенерён одним промптом,
оживлён вторым, текст положен поверх. Пятнадцать минут от идеи до публикации.

Внутри карусели оба промпта целиком: чем просить кадр и как заставить
нейросеть двигать ровно один объект, не трогая всё остальное.

Почему формат вообще работает. Карусель с инструкцией сохраняют. А сохранения
тянут охват сильнее лайков: алгоритму важно, что пост забрали с собой, а не
пролистали.

Хочешь полную инструкцию - размеры, оба промпта без сокращений, порядок
сборки и как зациклить видео, чтобы не дёргалось?

Напиши «КАРУСЕЛЬ» в комментариях, и я пришлю её в личные сообщения.

Сохрани пост, чтобы не потерять.

#нейросети #ии #контентдляблога #карусели #djairmode
```

Запасные первые строки, если этот крючок не зайдёт:

- «Этот пост собран за 15 минут. Дизайнера, Canva и Figma тут нет.»
- «Первый слайд двигается. Текст на нём стоит намертво. Показываю, как.»
- «Карусели снова тянут охват. Но только те, у которых первый слайд живой.»

## Почему кодовое слово, а не ссылка

Ссылка в описании Instagram не кликается, и переход теряется. Комментарий с
кодовым словом решает три задачи сразу: поднимает пост в ленте, даёт повод
написать в личку первым, и отсеивает случайных - пишет тот, кому правда нужно.

Отвечать надо в тот же день, пока интерес горячий.

## Проверка перед загрузкой

```bash
python3 tools/carousel.py check слайд1.mp4 слайд2.png слайд3.png слайд4.png слайд5.png слайд6.png слайд7.png
```

Выход 1 - не грузить.
