#!/usr/bin/env python3
"""HTML-сцена -> видеоряд. Кадры снимает headless Chrome по CDP.

Зачем отдельный движок. Графику вида «плашки въезжают, счётчик крутится,
скриншот вылетает из-за края» в ffmpeg собирать больно: каждый элемент - свой
overlay-фильтр, а правка тайминга означает пересборку всей цепочки. В HTML это
CSS-анимация: правится текстом, видно в браузере, верстается кириллицей без
подбора кегля в пикселях.

Главное требование к рендеру - ДЕТЕРМИНИЗМ. Скриншоты «в реальном времени»
дают дрожащий тайминг: кадр 30 снялся через 47 мс после кадра 29, потому что
машина была занята. Поэтому время здесь ненастоящее:

  * страница грузится с остановленными анимациями (класс `frozen` на html);
  * перед каждым кадром всем анимациям Web Animations API руками ставится
    `currentTime = t*1000`, видео перематываются на `currentTime = t`;
  * только потом снимается скриншот.

Результат не зависит ни от загрузки машины, ни от скорости самого рендера:
одна и та же сцена всегда даёт кадр в кадр одинаковое видео.

Использование:

    python3 webrender.py сцена.html -o out.mp4 --duration 12 --fps 30

Требования к HTML: анимации через CSS `animation` или WAAPI. Никаких
`setTimeout`/`requestAnimationFrame` для движения - виртуального времени они
не увидят и застынут.
"""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

W, H, FPS = 1920, 1080, 30

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    os.path.expanduser("~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    hit = shutil.which("chromium") or shutil.which("google-chrome")
    if hit:
        return hit
    sys.exit("Chrome/Chromium не найден. Задай CHROME_BIN=/путь/к/chrome")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Browser:
    """Тонкая обёртка над CDP: одна вкладка, синхронные вызовы."""

    def __init__(self, width=W, height=H, scale=1):
        import websocket  # noqa: имя пакета websocket-client

        self.port = free_port()
        self.tmp = f"/tmp/webrender-{self.port}"
        self.proc = subprocess.Popen(
            [
                find_chrome(),
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--mute-audio",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
                f"--force-device-scale-factor={scale}",
                f"--window-size={width},{height}",
                f"--user-data-dir={self.tmp}",
                f"--remote-debugging-port={self.port}",
                # Chrome 111+ рубит CDP-подключение с чужим Origin.
                "--remote-allow-origins=*",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ws_url = self._wait_target()
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self._id = 0
        self._has_video = None
        self.call("Page.enable")
        self.call("Runtime.enable")
        # --window-size задаёт окно вместе с рамкой: реальный viewport выходит
        # ниже запрошенного (1920x937 вместо 1920x1080), и кадр приезжает
        # нечётной высоты - ffmpeg на yuv420p такой не берёт. Размер вьюпорта
        # ставим явно.
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width, height=height, deviceScaleFactor=scale, mobile=False,
        )

    def _wait_target(self, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json", timeout=2
                ).read()
                for t in json.loads(raw):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Chrome не поднял отладочный порт")

    def call(self, method, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.call(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=True,
        )
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS: {r['exceptionDetails']}")
        return r["result"].get("value")

    def open(self, path):
        self._has_video = None
        url = path if path.startswith("http") else "file://" + os.path.abspath(path)
        self.call("Page.navigate", url=url)
        # Ждём и загрузку документа, и шрифты: без document.fonts.ready первый
        # кадр уезжает на подменном шрифте, и кириллица прыгает по ширине.
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if self.js("document.readyState") == "complete":
                    break
            except RuntimeError:
                pass
            time.sleep(0.1)
        self.js("document.fonts ? document.fonts.ready.then(()=>1) : 1")
        # Видео должно быть прогружено ДО первого кадра: при readyState<2
        # у него нет декодированного кадра, и seek отдаёт чёрный экран.
        deadline = time.time() + 20
        while time.time() < deadline:
            ok = self.js("""(()=>{const vs=[...document.querySelectorAll('video')];
              if (!vs.length) return 1;
              return vs.every(v => v.readyState >= 2) ? 1 : 0;})()""")
            if ok:
                break
            time.sleep(0.2)

    def seek(self, t):
        """Поставить всю страницу на момент t секунд."""
        self.js(
            "(() => {"
            "  const ms = %f;"
            "  document.documentElement.classList.add('frozen');"
            "  for (const a of document.getAnimations()) {"
            "    try { a.pause(); a.currentTime = ms; } catch (e) {}"
            "  }"
            "  for (const v of document.querySelectorAll('video')) {"
            "    try { v.pause(); v.currentTime = ms / 1000; } catch (e) {}"
            "  }"
            "  return 1;"
            "})()" % (t * 1000.0)
        )
        # Перемотка видео асинхронна: сразу после присвоения currentTime
        # кадр ещё не декодирован, и скриншот забирает пустой прямоугольник
        # (поймано на врезке с биржей - видео было в кадре, но не рисовалось).
        # Ждём, пока каждое видео закончит seek и получит готовый кадр.
        if self._has_video is None:
            self._has_video = bool(self.js(
                "document.querySelectorAll('video').length"))
        if self._has_video:
            deadline = time.time() + 5
            while time.time() < deadline:
                ready = self.js("""(()=>{
                  const vs=[...document.querySelectorAll('video')];
                  return vs.every(v => !v.seeking && v.readyState >= 2) ? 1 : 0;
                })()""")
                if ready:
                    break
                time.sleep(0.02)

    def shot(self, lossless=False):
        """Кадр из браузера.

        JPEG q=92 (по умолчанию) даёт PSNR 51 dB против PNG - для съёмки
        незаметно, но на ПЛОСКОЙ ГРАФИКЕ с крупными заливками потери
        видны: тонкие градиенты рассыпаются, а x264 потом закрепляет
        артефакт. Для мастер-качества берём PNG (lossless=True).
        """
        if lossless:
            r = self.call("Page.captureScreenshot", format="png",
                          fromSurface=True)
        else:
            r = self.call("Page.captureScreenshot", format="jpeg", quality=92,
                          fromSurface=True)
        return base64.b64decode(r["data"])

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def render(html, out, duration, fps=FPS, width=W, height=H, quiet=False,
           crf=17, start=0.0, grain=True, master=False):
    """Снять сцену в mp4. Возвращает число кадров.

    Два режима.

    Обычный: кадры JPEG q=92, зерно фильтром, x264 crf=17. Быстро
    (4.3 к/с), годится для черновиков и проверки тайминга.

    `master=True` - мастер-качество для плоской графики:
      * кадры PNG без потерь (PSNR 58 против 51 у JPEG q=92);
      * зерно ВЫКЛЮЧЕНО (шум на плоских заливках сам по себе выглядит
        как пикселизация и вдобавок съедает битрейт кодека);
      * x264 crf=12 + tune=stillimage, профиль high10 не берём, чтобы
        файл открывался везде.
    Медленнее примерно вдвое, но на градиентах и крупном тексте
    разница видна глазом.
    """
    b = Browser(width, height)
    ff = None
    try:
        b.open(html)
        b.seek(0)
        vf = [] if (master or not grain) else ["noise=alls=7:allf=t+u"]
        enc = (["-c:v", "libx264", "-preset", "slow", "-crf", "12",
                "-tune", "stillimage", "-x264-params", "aq-mode=3:aq-strength=1.2"]
               if master else
               ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)])
        ff = subprocess.Popen(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
            ]
            + (["-vf", ",".join(vf)] if vf else [])
            + enc
            + [
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out,
            ],
            stdin=subprocess.PIPE,
        )
        total = int(round(duration * fps))
        t0 = time.time()
        for i in range(total):
            b.seek(start + i / fps)
            ff.stdin.write(b.shot(lossless=master))
            if not quiet and (i % (fps * 2) == 0 or i == total - 1):
                done = i + 1
                speed = done / max(time.time() - t0, 0.01)
                left = (total - done) / max(speed, 0.01)
                print(
                    f"  кадр {done}/{total}  {speed:.1f} к/с  "
                    f"осталось ~{left/60:.1f} мин",
                    flush=True,
                )
        ff.stdin.close()
        if ff.wait() != 0:
            raise RuntimeError("ffmpeg упал на сборке кадров")
        return total
    finally:
        if ff and ff.stdin and not ff.stdin.closed:
            ff.stdin.close()
        b.close()


def main():
    ap = argparse.ArgumentParser(description="HTML-сцена в видеоряд")
    ap.add_argument("html")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--width", type=int, default=W)
    ap.add_argument("--height", type=int, default=H)
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--no-grain", action="store_true",
                    help="без плёночного зерна")
    ap.add_argument("--master", action="store_true",
                    help="мастер-качество: PNG-кадры, без зерна, crf 12")
    a = ap.parse_args()

    n = render(a.html, a.out, a.duration, a.fps, a.width, a.height,
               a.quiet, a.crf, a.start, grain=not a.no_grain,
               master=a.master)
    size = os.path.getsize(a.out) / 1e6
    print(f"кадров: {n}  длина: {n/a.fps:.2f}s  файл: {a.out}  {size:.1f} МБ")


if __name__ == "__main__":
    main()
