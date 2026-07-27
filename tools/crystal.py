"""Мини-движок: реальный 3D — вершины, матрицы поворота, нормали, Фонг-освещение."""
import numpy as np

def rot_y(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c,0,s],[0,1,0],[-s,0,c]])

def rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1,0,0],[0,c,-s],[0,s,c]])

def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c,-s,0],[s,c,0],[0,0,1]])


def crystal(n_sides=6, h_top=1.55, h_bot=-1.75, r=0.72, waist=0.30, seed=0):
    """Огранённый кристалл: два пояса вершин + острые вершины сверху/снизу.
    Лёгкая неровность радиусов — чтобы грани ловили свет по-разному, как настоящий камень."""
    rng = np.random.default_rng(seed)
    V, F = [], []
    top_i, bot_i = 0, 1
    V.append([0, h_top, 0])
    V.append([0, h_bot, 0])
    belts = []
    for k, (y, rad) in enumerate([(waist + 0.42, r), (waist - 0.62, r * 0.93)]):
        idx = []
        for i in range(n_sides):
            a = 2 * np.pi * i / n_sides + k * (np.pi / n_sides) * 0.18
            rr = rad * (1 + rng.uniform(-0.055, 0.055))
            idx.append(len(V))
            V.append([rr * np.cos(a), y + rng.uniform(-0.03, 0.03), rr * np.sin(a)])
        belts.append(idx)
    up, low = belts
    for i in range(n_sides):
        j = (i + 1) % n_sides
        F.append((top_i, up[i], up[j]))          # верхняя пирамида
        F.append((up[i], low[i], low[j]))        # пояс
        F.append((up[i], low[j], up[j]))
        F.append((low[i], bot_i, low[j]))        # нижняя пирамида
    return np.array(V, dtype=float), F


def shard(scale=1.0, seed=1):
    """Осколок поменьше — для кластеров."""
    V, F = crystal(5, 1.15, -1.25, 0.44, 0.18, seed)
    return V * scale, F


def scene_for(level, t):
    """level 1..3 — сложность структуры. t — фаза анимации 0..1.
    1: один кристалл. 2: кристалл + спутник. 3: кластер из 4."""
    a = 2 * np.pi * t
    objs = []
    V, F = crystal(6, 1.6, -1.8, 0.74, 0.30, seed=7)
    M = rot_y(a) @ rot_x(0.20 + 0.05 * np.sin(a)) @ rot_z(0.06 * np.sin(a * 2))
    objs.append((V @ M.T, F, np.array([0, 0, 0.0]), 1.0))

    if level >= 2:
        Vs, Fs = shard(0.62, seed=3)
        Ms = rot_y(-a * 1.35 + 1.1) @ rot_z(0.5)
        off = np.array([1.02 * np.cos(a * 0.8), -0.62 + 0.10 * np.sin(a * 1.6), 0.55 * np.sin(a * 0.8)])
        objs.append((Vs @ Ms.T, Fs, off, 0.86))

    if level >= 3:
        for k in range(3):
            ph = a * (1.0 + 0.22 * k) + k * 2.094
            Vs, Fs = shard(0.42 + 0.05 * k, seed=11 + k)
            Ms = rot_y(ph * 1.6) @ rot_x(0.4 * k)
            R = 1.28
            off = np.array([R * np.cos(ph), 0.75 - 0.55 * k + 0.08 * np.sin(ph * 2), R * np.sin(ph)])
            objs.append((Vs @ Ms.T, Fs, off, 0.78))
    return objs


def render(objs, W, H, accent, cam_z=5.2, fov=2.55, ambient=0.10, cx_f=0.5, cy_f=0.5):
    """Painter's algorithm + Фонг. Возвращает RGB float-буфер и маску свечения."""
    rgb = np.zeros((H, W, 3), dtype=np.float32)
    glow = np.zeros((H, W), dtype=np.float32)
    L1 = np.array([-0.55, 0.78, 0.75]); L1 /= np.linalg.norm(L1)   # ключевой
    L2 = np.array([0.85, -0.25, 0.42]); L2 /= np.linalg.norm(L2)   # контровой (акцент)
    eye = np.array([0, 0, 1.0])
    acc = np.array(accent, dtype=float) / 255.0

    tris = []
    for V, F, off, bright in objs:
        P = V + off
        for f in F:
            p = P[list(f)]
            z = p[:, 2].mean()
            tris.append((z, p, bright))
    tris.sort(key=lambda x: x[0])          # дальние первыми

    yy, xx = np.mgrid[0:H, 0:W]
    for z, p, bright in tris:
        n = np.cross(p[1] - p[0], p[2] - p[0])
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n = n / ln
        if n[2] < 0:                        # отбраковка задних граней
            n = -n
        d1 = max(0.0, float(n @ L1)); d2 = max(0.0, float(n @ L2))
        hv = (L1 + eye); hv /= np.linalg.norm(hv)
        spec = max(0.0, float(n @ hv)) ** 42
        fres = (1.0 - max(0.0, float(n @ eye))) ** 2.4      # френель по краям

        base = np.array([0.045, 0.05, 0.062])
        col = (base * (ambient + 0.85 * d1)
               + acc * (0.62 * d2 + 0.50 * fres)
               + np.array([1, 1, 1]) * spec * 0.95)
        col *= bright

        # проекция
        s = fov / (cam_z - p[:, 2])
        sx = W * cx_f + p[:, 0] * s * W / 3.2
        sy = H * cy_f - p[:, 1] * s * H / 4.0
        x0, x1 = int(max(0, sx.min() - 2)), int(min(W, sx.max() + 2))
        y0, y1 = int(max(0, sy.min() - 2)), int(min(H, sy.max() + 2))
        if x1 <= x0 or y1 <= y0:
            continue
        X = xx[y0:y1, x0:x1]; Y = yy[y0:y1, x0:x1]
        ax, ay = sx[0], sy[0]; bx, by = sx[1], sy[1]; cx, cy = sx[2], sy[2]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        w0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        w1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        w2 = 1 - w0 - w1
        m = (w0 >= -0.004) & (w1 >= -0.004) & (w2 >= -0.004)
        if not m.any():
            continue
        sub = rgb[y0:y1, x0:x1]
        sub[m] = np.clip(col, 0, 1)          # плоская заливка — гранёный вид
        rgb[y0:y1, x0:x1] = sub
        g = glow[y0:y1, x0:x1]
        g[m] = np.maximum(g[m], float(np.clip(0.45 * d2 + 0.85 * spec + 0.35 * fres, 0, 1)))
        glow[y0:y1, x0:x1] = g
    return rgb, glow
