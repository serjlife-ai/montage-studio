#!/usr/bin/env python3
"""Чекпоинты монтажа: работа не теряется при обрыве сессии.

Идея взята у OpenMontage (их `lib/checkpoint.py` и стадии пайплайна):
production - это не одна команда, а цепочка стадий, и каждая стадия
записывает результат на диск. Оборвалась связь - следующая сессия
поднимает состояние с диска, а не начинает заново. Код здесь свой.

Зачем нам: ролик с врезками собирается часами и через несколько заходов,
а сессия Клода часто обрывается. Без чекпоинта после обрыва теряется
всё: план резки, посчитанный порог, транскрипт (а он считается минуту
на минуту записи).

Устройство простое, без БД: папка проекта, в ней JSON на стадию.

    проекты/<имя>/
      project.json          паспорт: исходник, канон, когда начали
      stage_preflight.json  результат предполётной проверки
      stage_cut.json        план резки: пороги, куски, инвариант
      stage_subs.json       транскрипт и титры
      stage_render.json     что отрендерено, с числами сдачи
      renders/              сами файлы

Использование из кода:
    p = Project("~/монтаж/проекты/reels-31-08", src="/path/src.mov")
    p.save("cut", {"threshold_db": -44, "keeps": [...]})
    data = p.load("cut")        # None, если стадии ещё не было
    p.done("cut")               # отметить стадию закрытой

Из командной строки:
    python3 checkpoint.py status ~/монтаж/проекты/reels-31-08
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

STAGES = ["preflight", "cut", "subs", "render"]


class Project:
    """Папка проекта с состоянием по стадиям."""

    def __init__(self, path, src=None, canon="1080x1920"):
        self.path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.join(self.path, "renders"), exist_ok=True)
        self.meta_path = os.path.join(self.path, "project.json")
        if os.path.exists(self.meta_path):
            self.meta = json.load(open(self.meta_path))
            # исходник могли передать заново - проверяем, что тот же
            if src and self.meta.get("source") != os.path.abspath(src):
                self.meta["source_changed_from"] = self.meta.get("source")
                self.meta["source"] = os.path.abspath(src)
                self._flush()
        else:
            if not src:
                raise SystemExit("новый проект без исходника не создать: "
                                 "укажи src=/путь/к/файлу")
            self.meta = {
                "source": os.path.abspath(os.path.expanduser(src)),
                "canon": canon,
                "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stages": {},
            }
            self._flush()

    def _flush(self):
        # пишем через временный файл: обрыв посреди записи не должен
        # оставить битый JSON, из-за которого проект перестанет читаться
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.meta, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.meta_path)

    def _stage_file(self, stage):
        return os.path.join(self.path, f"stage_{stage}.json")

    def save(self, stage, data):
        """Записать результат стадии."""
        if stage not in STAGES:
            raise ValueError(f"неизвестная стадия {stage}, есть {STAGES}")
        tmp = self._stage_file(stage) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._stage_file(stage))
        self.meta["stages"][stage] = {
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "done": False}
        self._flush()
        return self._stage_file(stage)

    def load(self, stage):
        """Прочитать результат стадии или None, если её не было."""
        f = self._stage_file(stage)
        return json.load(open(f)) if os.path.exists(f) else None

    def done(self, stage):
        """Отметить стадию закрытой."""
        self.meta["stages"].setdefault(stage, {})["done"] = True
        self.meta["stages"][stage]["closed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._flush()

    def next_stage(self):
        """Первая незакрытая стадия - отсюда продолжать."""
        for s in STAGES:
            if not self.meta["stages"].get(s, {}).get("done"):
                return s
        return None

    def keep_source_safe(self):
        """Проверка закона контура: исходник не трогаем, работаем в копиях."""
        src = self.meta["source"]
        if not os.path.exists(src):
            return False, f"исходник пропал: {src}"
        cur = os.path.getmtime(src)
        known = self.meta.get("source_mtime")
        if known is None:
            self.meta["source_mtime"] = cur
            self._flush()
            return True, "исходник взят под наблюдение"
        if abs(cur - known) > 1:
            return False, ("исходник ИЗМЕНИЛСЯ с момента начала работы - "
                           "закон контура нарушен, проверь, что режется копия")
        return True, "исходник нетронут"

    def status(self):
        lines = [f"проект:    {self.path}",
                 f"исходник:  {self.meta['source']}",
                 f"начат:     {self.meta['started']}"]
        ok, msg = self.keep_source_safe()
        lines.append(f"{'проверка:  ' if ok else 'ТРЕВОГА:   '}{msg}")
        lines.append("")
        for s in STAGES:
            st = self.meta["stages"].get(s)
            if not st:
                lines.append(f"  [ ] {s:<10} не начата")
            elif st.get("done"):
                lines.append(f"  [x] {s:<10} закрыта {st.get('closed', '')}")
            else:
                lines.append(f"  [~] {s:<10} есть данные от {st['saved']}, "
                             f"не закрыта")
        nxt = self.next_stage()
        lines.append("")
        lines.append(f"продолжать со стадии: {nxt}" if nxt
                     else "все стадии закрыты")
        return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["status", "init", "reset"])
    ap.add_argument("path")
    ap.add_argument("--src", help="исходник, нужен при init")
    ap.add_argument("--stage", help="какую стадию сбросить при reset")
    a = ap.parse_args()

    if a.command == "init":
        p = Project(a.path, src=a.src)
        print(p.status())
    elif a.command == "status":
        if not os.path.exists(os.path.join(
                os.path.expanduser(a.path), "project.json")):
            sys.exit(f"проекта нет: {a.path}")
        print(Project(a.path).status())
    elif a.command == "reset":
        p = Project(a.path)
        if not a.stage:
            sys.exit("укажи --stage, что сбрасывать")
        f = p._stage_file(a.stage)
        if os.path.exists(f):
            os.remove(f)
        p.meta["stages"].pop(a.stage, None)
        p._flush()
        print(f"стадия {a.stage} сброшена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
