"""ONYX — карточки тарифов. Концепция «Обсидиан»: каждый тариф — кристалл
растущей сложности. Реальный 3D-рендер + типографика + свечение + зерно."""
import sys, os, numpy as np
sys.path.insert(0, "/tmp/onyx3d")
from crystal import scene_for, render
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FL = "/usr/share/fonts/truetype/lato/"
F = lambda w, s: ImageFont.truetype(FL + f"Lato-{w}.ttf", s)
DJ = lambda s: ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", s)


def price_ra(d, x, y, num, size, fill):
    """Цифры — Lato, знак рубля — DejaVu (в Lato его нет). Выравнивание по правому краю."""
    fn, fr = F("Black", size), DJ(int(size * 0.86))
    wr = d.textlength("₽", font=fr)
    wn = d.textlength(num, font=fn)
    gap = int(size * 0.16)
    d.text((x - wr, y + int(size * 0.07)), "₽", font=fr, fill=fill)
    d.text((x - wr - gap - wn, y), num, font=fn, fill=fill)

TARIFFS = [
    dict(key="start", num="01", level=1, accent=(0, 168, 252),
         name=["СТАРТ", "ОНЛАЙН"], price="5 990", pref="",
         tag="быстрый выход в интернет",
         inc=["Одностраничный сайт", "Адаптация под все экраны",
              "Форма заявки и контакты", "Домен · хостинг · SSL"],
         who="экспертам, мастерам, салонам", badge=None),
    dict(key="leads", num="02", level=2, accent=(96, 116, 250),
         name=["САЙТ", "+ ЗАЯВКИ"], price="8 900", pref="",
         tag="сайт, который приносит обращения",
         inc=["Всё из «Старт онлайн»", "Структура под заявки",
              "Блок доверия и FAQ", "Telegram-уведомления · Метрика"],
         who="клиникам, ремонту, юристам, B2B", badge="ОПТИМАЛЬНЫЙ ВЫБОР"),
    dict(key="system", num="03", level=3, accent=(197, 84, 252),
         name=["СИСТЕМА", "ПРОДАЖ"], price="19 900", pref="от ",
         tag="сайт, заявки, аналитика, контроль",
         inc=["Всё из «Сайт + заявки»", "CRM или таблица заявок",
              "Онлайн-запись / калькулятор", "Расширенная аналитика"],
         who="бизнесу с высоким чеком", badge=None),
]


LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo_mark.png")
_LOGO = Image.open(LOGO_PATH).convert("RGBA") if os.path.exists(LOGO_PATH) else None


def paste_logo(im, x, y, h, alpha=1.0, anchor="lt"):
    """Фирменный знак ONYX. Родной градиент сохраняем — он и есть айдентика."""
    if _LOGO is None:
        return
    w = int(_LOGO.width * h / _LOGO.height)
    lg = _LOGO.resize((w, int(h)), Image.LANCZOS)
    if alpha < 1.0:
        a = lg.split()[3].point(lambda v: int(v * alpha))
        lg.putalpha(a)
    if anchor == "mm":
        x, y = int(x - w / 2), int(y - h / 2)
    im.alpha_composite(lg, (int(x), int(y)))


def grain(W, H, amt, seed):
    """Очень слабое зерно + дизеринг. Telegram пережимает фото в JPEG:
    крупный шум на тёмном фоне разваливается, а совсем гладкий градиент
    даёт полосы. Нужен минимум шума ровно на уровне кванта яркости."""
    rng = np.random.default_rng(seed)
    n = rng.normal(0, amt, (H, W, 1)) * np.ones((1, 1, 3))
    dither = (rng.random((H, W, 3)) - 0.5) * (1.6 / 255.0)
    return (n + dither).astype(np.float32)


def vignette(W, H, power=1.45, strength=0.72):
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx - W / 2) / (W / 2); ny = (yy - H / 2) / (H / 2)
    d = np.sqrt(nx ** 2 * 0.85 + ny ** 2)
    v = np.clip(1 - strength * np.clip(d, 0, 1.6) ** power, 0, 1)
    return v[..., None].astype(np.float32)


def frame(t, cfg, W, H):
    acc = np.array(cfg["accent"], float)
    # --- фон: глубокий обсидиан + мягкое пятно акцента, дышащее в такт вращению
    base = np.zeros((H, W, 3), np.float32)
    base[:] = np.array([0.026, 0.030, 0.038])
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = W * 0.62, H * 0.26
    r = np.sqrt(((xx - cx) / (W * 0.62)) ** 2 + ((yy - cy) / (H * 0.52)) ** 2)
    halo = np.clip(1 - r, 0, 1) ** 2.4 * (0.16 + 0.05 * np.sin(2 * np.pi * t))
    base += halo[..., None] * (acc / 255.0)

    # --- 3D-кристалл
    rgb, glow = render(scene_for(cfg["level"], t), W, H, cfg["accent"],
                       cam_z=5.9, fov=2.4, cx_f=0.60, cy_f=0.285)
    # композит: кристалл поверх фона там, где он есть
    mask = (rgb.sum(axis=2) > 0.001)[..., None]
    img = np.where(mask, rgb, base)

    # --- bloom: размытая карта свечения добавляется поверх
    gi = Image.fromarray((np.clip(glow, 0, 1) * 255).astype(np.uint8))
    b1 = np.asarray(gi.filter(ImageFilter.GaussianBlur(18)), np.float32) / 255.0
    b2 = np.asarray(gi.filter(ImageFilter.GaussianBlur(52)), np.float32) / 255.0
    bloom = (b1 * 0.55 + b2 * 0.75)[..., None] * (acc / 255.0)
    img = img + bloom * 0.9

    # --- световой луч, проходящий по карточке (усиливает ощущение объёма)
    sweep = np.clip(1 - np.abs(((xx / W + yy / H) / 2 - (t * 1.6 - 0.3)) * 7.0), 0, 1) ** 2
    img += sweep[..., None] * (acc / 255.0) * 0.055

    img = img * vignette(W, H) + grain(W, H, 0.0022, int(t * 1000) % 997)
    # лёгкий контраст — тёмные тона держатся лучше после пережатия
    img = np.clip((img - 0.5) * 1.045 + 0.5 + 0.004, 0, 1)
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def compose(t, cfg, W=720, H=900):
    im = frame(t, cfg, W, H).convert("RGBA")
    S0 = W / 720.0
    paste_logo(im, W - int(96 * S0), H - int(232 * S0), int(196 * S0), alpha=0.055)
    d = ImageDraw.Draw(im, "RGBA")
    acc = cfg["accent"]
    S = W / 720.0
    px = lambda v: int(v * S)

    # шапка
    paste_logo(im, px(46), px(34), px(46))
    lx = px(46) + int(_LOGO.width * px(46) / _LOGO.height) + px(16) if _LOGO else px(46)
    d.text((lx, px(41)), "ONYX", font=F("Black", px(25)), fill=(255, 255, 255))
    wl = d.textlength("ONYX", font=F("Black", px(25)))
    d.text((lx + wl + px(10), px(45)), "WEB", font=F("Light", px(23)), fill=(150, 156, 170))
    d.text((W - px(46), px(40)), cfg["num"], font=F("Black", px(26)), fill=acc, anchor="ra")
    d.line([px(46), px(84), W - px(46), px(84)], fill=(255, 255, 255, 26), width=1)

    y = px(298)
    if cfg["badge"]:
        bw = d.textlength(cfg["badge"], font=F("Black", px(15))) + px(30)
        d.rounded_rectangle([px(46), y, px(46) + bw, y + px(34)], px(17), fill=acc)
        d.text((px(46) + bw / 2, y + px(17)), cfg["badge"], font=F("Black", px(15)),
               fill=(10, 10, 12), anchor="mm")
        y += px(52)

    for line in cfg["name"]:                      # крупный акцидентный заголовок
        d.text((px(44), y), line, font=F("Black", px(56)), fill=(255, 255, 255))
        y += px(58)
    y += px(6)
    d.text((px(46), y), cfg["tag"], font=F("Light", px(23)), fill=(158, 165, 178))
    y += px(46)

    # цена — герой блока
    d.line([px(46), y, W - px(46), y], fill=(255, 255, 255, 22), width=1)
    y += px(26)
    d.text((px(46), y + px(8)), "РАЗРАБОТКА", font=F("Semibold", px(16)), fill=(120, 126, 138))
    price_ra(d, W - px(46), y - px(2), "0", px(38), acc)
    y += px(46)
    d.text((px(46), y + px(14)), "ЗАПУСК ПОД КЛЮЧ", font=F("Semibold", px(16)), fill=(120, 126, 138))
    if cfg["pref"]:
        d.text((px(46), y + px(14)) if False else (W - px(46) - d.textlength(cfg["price"], font=F("Black", px(44))) - px(64), y + px(12)),
               cfg["pref"].strip(), font=F("Light", px(24)), fill=(190, 196, 208))
    price_ra(d, W - px(46), y - px(4), cfg["price"], px(44), (255, 255, 255))
    y += px(60)
    d.line([px(46), y, W - px(46), y], fill=(255, 255, 255, 22), width=1)
    y += px(26)

    for it in cfg["inc"]:
        d.rectangle([px(46), y + px(9), px(52), y + px(15)], fill=acc)
        d.text((px(68), y), it, font=F("Regular", px(22)), fill=(214, 219, 228))
        y += px(33)

    d.text((px(46), y + px(8)), cfg["who"], font=F("Light", px(20)), fill=(122, 129, 142))
    d.line([px(46), H - px(66), W - px(46), H - px(66)], fill=(255, 255, 255, 18), width=1)
    d.text((px(46), H - px(48)), "2–3 дня до первой версии", font=F("Semibold", px(18)), fill=acc)
    d.text((W - px(46), H - px(48)), "150+ сайтов запущено · onyx-web.ru",
           font=F("Light", px(18)), fill=(112, 118, 130), anchor="ra")
    return im.convert("RGB")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "still"
    OUT = "/tmp/onyx3d/out"; os.makedirs(OUT, exist_ok=True)
    if mode == "still":
        for c in TARIFFS:
            compose(0.12, c, 1080, 1350).save(f"{OUT}/{c['key']}_still.png")
            print("still", c["key"])
    else:
        N = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        for c in TARIFFS:
            dd = f"{OUT}/{c['key']}"; os.makedirs(dd, exist_ok=True)
            for i in range(N):
                compose(i / N, c).save(f"{dd}/{i:03d}.png")
            print("frames", c["key"], N)
