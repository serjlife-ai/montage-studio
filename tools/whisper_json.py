#!/usr/bin/env python3
"""Чтение JSON от whisper-cli: безопасно к битым байтам.

Зачем отдельный модуль. whisper-cli с `-ml 1` режет вывод на токены и
может разорвать русскую букву пополам: кириллица в UTF-8 занимает два
байта, и в файл попадает половинка. Получается формально битый UTF-8,
на котором `json.load(open(...))` падает с UnicodeDecodeError.

Поймано 01.09.2026 на финале ролика: два битых байта на файл роняли
`dynamic.py` уже ПОСЛЕ распознавания - минута работы впустую.

Чинить в самом whisper нельзя (чужой бинарь), поэтому читаем байтами и
выкидываем ровно те байты, которые не декодируются. Половинка буквы -
это половина одного символа из тысяч, на смысл транскрипта не влияет.
"""
import json


def load_whisper_json(path):
    """JSON от whisper-cli, устойчиво к обрывкам многобайтовых символов."""
    raw = open(path, "rb").read()
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        pass

    # выкидываем только неполные последовательности, остальное сохраняем
    cleaned, dropped = bytearray(), 0
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b < 0x80:
            cleaned.append(b)
            i += 1
            continue
        length = 2 if b >> 5 == 0b110 else 3 if b >> 4 == 0b1110 else \
            4 if b >> 3 == 0b11110 else 0
        chunk = raw[i:i + length] if length else b""
        if length and len(chunk) == length and \
                all(c >> 6 == 0b10 for c in chunk[1:]):
            cleaned += chunk
            i += length
        else:
            dropped += 1
            i += 1
    if dropped:
        print(f"  [внимание] в выдаче whisper {dropped} битых байт "
              f"(обрывки кириллицы на границе токенов) - выброшены")
    return json.loads(cleaned.decode("utf-8", errors="ignore"))
