"""繪圖層：字型、世界渲染、HUD。

設計原則：
* 所有「靜態且昂貴」的圖層（天空、雲、暗角、面板、柔光粒子）都預先算好並快取，
  每幀只做 blit；動態圖層（地形、車輛、粒子）才即時繪製，以維持 60 FPS。
* 光源假設在畫面右上方，因此高光在物體右上、陰影在左下。
"""
from __future__ import annotations

import math
from pathlib import Path

import pygame

from . import config as cfg
from .entities import Bike
from .terrain import Terrain

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msjh.ttc",
    r"C:\Windows\Fonts\msjhbd.ttc",
    r"C:\Windows\Fonts\mingliu.ttc",
]
_font_cache: dict[int, pygame.font.Font] = {}


def get_font(size: int) -> pygame.font.Font:
    if size not in _font_cache:
        font = None
        for path in _FONT_CANDIDATES:
            if Path(path).exists():
                try:
                    font = pygame.font.Font(path, size)
                    break
                except OSError:
                    continue
        if font is None:
            font = pygame.font.SysFont("microsoftjhenghei,arial", size)
        _font_cache[size] = font
    return _font_cache[size]


def draw_text(surf, text, size, x, y, color=cfg.C_WHITE, center=False, shadow=True):
    font = get_font(size)
    img = font.render(text, True, color)
    rect = img.get_rect()
    if center:
        rect.center = (x, y)
    else:
        rect.topleft = (x, y)
    if shadow:
        sh = font.render(text, True, (0, 0, 0))
        sh.set_alpha(150)
        surf.blit(sh, (rect.x + 2, rect.y + 2))
    surf.blit(img, rect)
    return rect


# ------------------------------------------------------------------ 色彩工具
def mix(c1, c2, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def shade(c, f: float) -> tuple[int, int, int]:
    return (max(0, min(255, int(c[0] * f))),
            max(0, min(255, int(c[1] * f))),
            max(0, min(255, int(c[2] * f))))


def _hash01(i: int, salt: int = 0) -> float:
    """確定性偽亂數，讓場景細節不隨機閃爍。"""
    v = (int(i) * 374761393 + salt * 668265263) & 0x7FFFFFFF
    v = (v ^ (v >> 13)) * 1274126177 & 0x7FFFFFFF
    return ((v >> 8) % 65536) / 65536.0


# ------------------------------------------------------------------ 柔光快取
_soft_cache: dict[tuple, pygame.Surface] = {}
_glow_cache: dict[tuple, pygame.Surface] = {}


def soft_sprite(color, radius: int) -> pygame.Surface:
    """以 alpha 遞減的放射狀柔邊光點（一般混合用，例如煙霧、雲）。"""
    radius = max(2, int(radius))
    key = (color, radius)
    s = _soft_cache.get(key)
    if s is None:
        d = radius * 2
        s = pygame.Surface((d, d), pygame.SRCALPHA)
        steps = max(6, min(radius, 26))
        for i in range(steps, 0, -1):
            t = i / steps
            a = int(235 * (1.0 - t) ** 1.6 + 20)
            pygame.draw.circle(s, (*color, a), (radius, radius), max(1, int(radius * t)))
        _soft_cache[key] = s
    return s


def glow_sprite(color, radius: int) -> pygame.Surface:
    """以 RGB 強度遞減的光暈（加法混合用）。

    加法混合會忽略 alpha，因此衰減必須烘進 RGB，否則會變成一塊硬邊色盤。
    """
    radius = max(2, int(radius))
    key = (color, radius)
    s = _glow_cache.get(key)
    if s is None:
        d = radius * 2
        s = pygame.Surface((d, d))
        s.fill((0, 0, 0))
        steps = max(8, min(radius, 42))
        for i in range(steps, 0, -1):
            t = i / steps
            f = (1.0 - t) ** 2.0
            pygame.draw.circle(s, shade(color, f), (radius, radius),
                               max(1, int(radius * t)))
        _glow_cache[key] = s
    return s


def blit_glow(surf, color, x, y, radius, alpha=255) -> None:
    """加法光暈。alpha 只用來量化強度（避免快取爆量）。"""
    q = round(max(0.0, min(1.0, alpha / 255.0)) * 4) / 4
    if q <= 0:
        return
    col = shade(color, q)
    if max(col) < 3:
        return
    s = glow_sprite(col, int(radius))
    surf.blit(s, (x - s.get_width() / 2, y - s.get_height() / 2),
              special_flags=pygame.BLEND_RGB_ADD)


# ------------------------------------------------------------------ 天空 / 雲
def make_sky(w: int, h: int, warm: bool) -> pygame.Surface:
    """三段漸層天空 + 星空 + 太陽光暈（一次性預算）。"""
    surf = pygame.Surface((w, h))
    top = cfg.C_SKY_TOP2 if warm else cfg.C_SKY_TOP
    mid = cfg.C_SKY_MID2 if warm else cfg.C_SKY_MID
    bot = cfg.C_SKY_BOT2 if warm else cfg.C_SKY_BOT
    for i in range(h):
        t = i / max(1, h - 1)
        col = mix(top, mid, t / 0.62) if t < 0.62 else mix(mid, bot, (t - 0.62) / 0.38)
        surf.fill(col, (0, i, w, 1))

    # 星星（冷色調的黃昏天空才有）
    if not warm:
        for i in range(150):
            sx = int(_hash01(i, 11) * w)
            sy = int(_hash01(i, 22) ** 1.6 * h * 0.6)
            b = 120 + int(_hash01(i, 33) * 135)
            r = 1 if _hash01(i, 44) < 0.82 else 2
            pygame.draw.circle(surf, (b, b, min(255, b + 12)), (sx, sy), r)

    # 太陽 / 月亮與大氣輝光
    sun = (int(w * 0.24), int(h * 0.40)) if warm else (int(w * 0.78), int(h * 0.26))
    core = cfg.C_SUN2 if warm else cfg.C_SUN
    for r, f in ((int(h * 0.40), 0.30), (int(h * 0.20), 0.34), (int(h * 0.10), 0.42)):
        blit_glow(surf, shade(core, f), sun[0], sun[1], r)
    pygame.draw.circle(surf, core, sun, max(8, int(h * 0.032)))
    pygame.draw.circle(surf, mix(core, (255, 255, 255), 0.6), sun,
                       max(5, int(h * 0.022)))

    # 地平線霞光
    band = pygame.Surface((w, int(h * 0.3)), pygame.SRCALPHA)
    hz = shade(bot, 1.15)
    for i in range(band.get_height()):
        a = int(120 * (i / band.get_height()) ** 1.4)
        band.fill((*hz, a), (0, i, w, 1))
    surf.blit(band, (0, h - band.get_height()))
    return surf


_cloud_cache: dict[tuple, pygame.Surface] = {}


def make_clouds(w: int, h: int, warm: bool) -> pygame.Surface:
    """可水平無縫捲動的雲層（寬度即為一個循環）。"""
    key = (w, h, warm)
    s = _cloud_cache.get(key)
    if s is not None:
        return s
    band_h = max(60, int(h * 0.34))
    s = pygame.Surface((w, band_h), pygame.SRCALPHA)
    lit = cfg.C_SUN2 if warm else (206, 212, 240)
    dark = (118, 88, 100) if warm else (86, 92, 132)
    for i in range(9):
        cx = _hash01(i, 71) * w
        cy = 24 + _hash01(i, 72) ** 1.4 * (band_h - 54)
        scale = 0.5 + _hash01(i, 73) * 0.7
        depth = cy / band_h
        base = mix(lit, dark, 0.35 + depth * 0.45)
        alpha = int(88 + 78 * (1.0 - depth))
        puffs = 5 + int(_hash01(i, 74) * 4)
        layer = pygame.Surface((w, band_h), pygame.SRCALPHA)
        for dx in (0.0, -float(w), float(w)):
            for k in range(puffs):
                px = cx + dx + (k - puffs / 2) * 24 * scale
                py = cy + math.sin(k * 1.9 + i) * 6 * scale
                rr = int((15 + _hash01(i * 13 + k, 75) * 15) * scale)
                # 下緣陰影
                pygame.draw.circle(layer, (*shade(base, 0.74), 255),
                                   (int(px), int(py + rr * 0.34)), rr)
        for dx in (0.0, -float(w), float(w)):
            for k in range(puffs):
                px = cx + dx + (k - puffs / 2) * 24 * scale
                py = cy + math.sin(k * 1.9 + i) * 6 * scale
                rr = int((15 + _hash01(i * 13 + k, 75) * 15) * scale)
                # 受光的上緣
                pygame.draw.circle(layer, (*base, 255),
                                   (int(px), int(py - rr * 0.16)), int(rr * 0.86))
                pygame.draw.circle(layer, (*mix(base, (255, 255, 255), 0.35), 255),
                                   (int(px - rr * 0.2), int(py - rr * 0.42)),
                                   int(rr * 0.42))
        layer.set_alpha(alpha)
        s.blit(layer, (0, 0))
    _cloud_cache[key] = s
    return s


# ------------------------------------------------------------------ 遠景
def _ridge_points(w, off, base, waves, jag=0.0, salt=0, step=16):
    pts = []
    for sx in range(-step, w + step * 2, step):
        wx = sx + off
        y = base
        for wl, amp in waves:
            y -= math.sin(wx / wl * cfg.TAU) * amp
        if jag:
            y -= (_hash01(int(wx / step), salt) - 0.5) * jag
        pts.append((sx, y))
    return pts


_fog_cache: dict[tuple, pygame.Surface] = {}


def _fog_band(w: int, h: int, color, alpha: int) -> pygame.Surface:
    key = (w, h, color, alpha)
    s = _fog_cache.get(key)
    if s is None:
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        for i in range(h):
            a = int(alpha * (i / max(1, h - 1)) ** 0.7)
            s.fill((*color, a), (0, i, w, 1))
        _fog_cache[key] = s
    return s


def draw_parallax(surf, cam_x: float, view_h: int, t: float = 0.0,
                  warm: bool = False, cam_y: float = 0.0) -> None:
    """雲 → 遠山（積雪）→ 中景丘陵（針葉林）→ 近景樹線，並加上大氣霧。

    各層基準線會隨鏡頭上下微幅移動（垂直視差），並固定落在地平線附近，
    避免整片山景被前景地形蓋住。
    """
    w = surf.get_width()

    # --- 雲層（緩慢飄移）
    clouds = make_clouds(w, view_h, warm)
    off = int((cam_x * 0.05 + t * 6.0) % w)
    cy = int(-cam_y * 0.03)
    surf.blit(clouds, (-off, cy))
    surf.blit(clouds, (w - off, cy))

    # --- 遠山：稜線 + 積雪 + 大氣透視
    far_base = view_h * 0.44 - cam_y * 0.05
    pts = _ridge_points(w, cam_x * 0.08, far_base,
                        ((980, 56), (430, 24), (170, 10)), jag=10, salt=5)
    body = [(pts[0][0], view_h)] + pts + [(pts[-1][0], view_h)]
    pygame.draw.polygon(surf, cfg.C_HILL_FAR, body)
    snow_line = far_base - 48
    snow_col = mix(cfg.C_HILL_FAR, cfg.C_SNOW, 0.72)
    run: list[tuple[float, float]] = []
    for p in pts:
        if p[1] < snow_line:
            run.append(p)
        elif run:
            if len(run) > 1:
                pygame.draw.polygon(
                    surf, snow_col,
                    run + [(run[-1][0], snow_line), (run[0][0], snow_line)])
            run = []
    if len(run) > 1:
        pygame.draw.polygon(surf, snow_col,
                            run + [(run[-1][0], snow_line), (run[0][0], snow_line)])
    surf.blit(_fog_band(w, int(view_h * 0.16), cfg.C_FOG, 80),
              (0, int(far_base - view_h * 0.02)))

    # --- 中景丘陵 + 針葉林剪影
    mid_base = view_h * 0.54 - cam_y * 0.09
    pts = _ridge_points(w, cam_x * 0.20, mid_base, ((520, 40), (210, 15)), step=18)
    body = [(pts[0][0], view_h)] + pts + [(pts[-1][0], view_h)]
    pygame.draw.polygon(surf, cfg.C_HILL_MID, body)
    for i in range(0, len(pts) - 1, 2):
        x, y = pts[i]
        seed = int((x + cam_x * 0.20) / 18)
        if _hash01(seed, 91) > 0.45:
            hgt = 10 + _hash01(seed, 92) * 12
            pygame.draw.polygon(surf, shade(cfg.C_HILL_MID, 0.72),
                                [(x, y - hgt), (x - hgt * 0.33, y + 2),
                                 (x + hgt * 0.33, y + 2)])

    # --- 近景樹線
    near_base = view_h * 0.62 - cam_y * 0.13
    pts = _ridge_points(w, cam_x * 0.38, near_base, ((330, 26), (120, 9)), step=20)
    body = [(pts[0][0], view_h)] + pts + [(pts[-1][0], view_h)]
    pygame.draw.polygon(surf, cfg.C_HILL_NEAR, body)
    for i in range(len(pts) - 1):
        x, y = pts[i]
        seed = int((x + cam_x * 0.38) / 20)
        r = _hash01(seed, 95)
        if r > 0.3:
            hgt = 14 + r * 18
            pygame.draw.polygon(surf, shade(cfg.C_HILL_NEAR, 0.78),
                                [(x, y - hgt), (x - hgt * 0.30, y + 3),
                                 (x + hgt * 0.30, y + 3)])

    # --- 飛鳥
    for i in range(3):
        bx = (cam_x * 0.30 + t * 26 + i * 430) % (w + 200) - 100
        by = view_h * (0.16 + 0.06 * i) - cam_y * 0.04 + math.sin(t * 1.3 + i) * 6
        flap = math.sin(t * 7.0 + i * 2.1) * 4
        pygame.draw.lines(surf, (48, 52, 76), False,
                          [(bx - 7, by + flap), (bx, by - 2), (bx + 7, by + flap)], 2)


# ------------------------------------------------------------------ 地形
def draw_terrain(surf, terrain: Terrain, cam_x: float, cam_y: float) -> None:
    """分層土壤（表土 / 底土 / 岩盤）＋ 草緣、草叢與碎石。"""
    w, h = surf.get_size()
    start = max(0, int((cam_x - terrain.step) / terrain.step))
    end = min(terrain.count - 1, int((cam_x + w + terrain.step) / terrain.step) + 1)
    pts = []
    for i in range(start, end + 1):
        wx = i * terrain.step
        pts.append((wx - cam_x, terrain.heights[i] - cam_y))
    if len(pts) < 2:
        return

    bottom = h + 40
    # 岩盤（最深層）
    pygame.draw.polygon(surf, cfg.C_BEDROCK,
                        pts + [(pts[-1][0], bottom), (pts[0][0], bottom)])
    # 底土
    sub = [(px, py + 78 + math.sin(px * 0.012) * 8) for px, py in pts]
    pygame.draw.polygon(surf, cfg.C_SUBSOIL,
                        pts + [(sub[-1][0], sub[-1][1])] + list(reversed(sub))
                        + [(sub[0][0], sub[0][1])])
    # 表土
    topsoil = [(px, py + 30 + math.sin(px * 0.02 + 1.7) * 4) for px, py in pts]
    pygame.draw.polygon(surf, cfg.C_DIRT,
                        pts + list(reversed(topsoil)))
    pygame.draw.lines(surf, shade(cfg.C_DIRT_DARK, 1.15), False, topsoil, 2)

    # 岩盤層理
    for depth, w_amp, sh_f in ((124, 6, 1.55), (172, 9, 1.32), (232, 7, 1.18)):
        line = [(px, py + depth + math.sin((px + depth) * 0.008) * w_amp)
                for px, py in pts]
        if line[0][1] < h + 20:
            pygame.draw.lines(surf, shade(cfg.C_BEDROCK, sh_f), False, line, 2)

    # 埋在土裡的碎石
    for k in range(start, end, 4):
        r1, r2, r3 = _hash01(k, 3), _hash01(k, 4), _hash01(k, 5)
        if r1 < 0.45:
            continue
        px = k * terrain.step - cam_x + r2 * 40
        py = terrain.height_at(k * terrain.step) - cam_y + 16 + r3 * 92
        rr = int(2 + r1 * 4)
        pygame.draw.circle(surf, shade(cfg.C_BEDROCK, 1.5), (int(px), int(py)), rr)
        pygame.draw.circle(surf, shade(cfg.C_BEDROCK, 2.0),
                           (int(px - rr * 0.3), int(py - rr * 0.3)), max(1, rr // 2))

    # 草皮：底色 + 亮面 + 草叢
    pygame.draw.lines(surf, cfg.C_GRASS_DARK, False, [(x, y + 5) for x, y in pts], 7)
    pygame.draw.lines(surf, cfg.C_GRASS, False, pts, 5)
    pygame.draw.lines(surf, cfg.C_GRASS_LIGHT, False,
                      [(x, y - 2) for x, y in pts], 2)
    for idx, (px, py) in enumerate(pts):
        k = start + idx
        r = _hash01(k, 7)
        if r < 0.55:
            continue
        blades = 2 if r > 0.8 else 1
        for b in range(blades):
            bx = px + (_hash01(k, 8 + b) - 0.5) * 10
            bh = 4 + _hash01(k, 12 + b) * 6
            tilt = (_hash01(k, 16 + b) - 0.5) * 5
            col = cfg.C_GRASS_LIGHT if (k + b) % 3 else cfg.C_GRASS
            pygame.draw.line(surf, col, (bx, py + 2), (bx + tilt, py - bh), 2)


# ------------------------------------------------------------------ 障礙
def draw_obstacles(surf, terrain: Terrain, cam_x: float, cam_y: float,
                   t: float = 0.0) -> None:
    w = surf.get_width()
    for ob in terrain.obstacles:
        sx = ob.x - cam_x
        if sx < -240 or sx > w + 240:
            continue
        gy = terrain.height_at(ob.x) - cam_y
        seed = int(ob.x)
        if ob.kind == "rock":
            _draw_rock(surf, sx, gy, ob.w, ob.h, seed)
        elif ob.kind == "mud":
            _draw_mud(surf, terrain, ob, cam_x, cam_y, t)
        elif ob.kind == "spike":
            _draw_spikes(surf, terrain, ob, cam_x, cam_y)
        elif ob.kind == "boost":
            _draw_boost(surf, terrain, ob, sx, gy, t)


def _draw_rock(surf, sx, gy, ow, oh, seed) -> None:
    # 接地陰影
    sh = pygame.Surface((int(ow * 1.6), 14), pygame.SRCALPHA)
    pygame.draw.ellipse(sh, (0, 0, 0, 90), sh.get_rect())
    surf.blit(sh, (sx - ow * 0.8, gy - 6))

    hw = ow / 2
    pts = [
        (sx - hw, gy + 2),
        (sx - hw * 0.86, gy - oh * (0.42 + _hash01(seed, 1) * 0.2)),
        (sx - hw * 0.24, gy - oh * (0.92 + _hash01(seed, 2) * 0.12)),
        (sx + hw * 0.30, gy - oh * (0.80 + _hash01(seed, 3) * 0.16)),
        (sx + hw * 0.90, gy - oh * (0.34 + _hash01(seed, 4) * 0.2)),
        (sx + hw, gy + 2),
    ]
    base = (118, 114, 126)
    pygame.draw.polygon(surf, base, pts)
    # 受光面（右上）與陰影面（左下）
    pygame.draw.polygon(surf, shade(base, 1.28),
                        [pts[2], pts[3], pts[4], (sx + hw * 0.2, gy - oh * 0.32)])
    pygame.draw.polygon(surf, shade(base, 0.66),
                        [pts[0], pts[1], pts[2], (sx - hw * 0.1, gy - oh * 0.28)])
    pygame.draw.polygon(surf, shade(base, 0.42), pts, 2)
    # 岩紋
    pygame.draw.line(surf, shade(base, 0.55),
                     (sx - hw * 0.2, gy - oh * 0.75), (sx + hw * 0.35, gy - oh * 0.2), 2)
    # 頂端苔蘚
    pygame.draw.line(surf, cfg.C_GRASS_DARK, pts[2],
                     ((pts[2][0] + pts[3][0]) / 2, (pts[2][1] + pts[3][1]) / 2 - 1), 3)


def _draw_mud(surf, terrain, ob, cam_x, cam_y, t) -> None:
    xs = [ob.x - ob.w / 2 + i * ob.w / 10 for i in range(11)]
    top = [(x - cam_x, terrain.height_at(x) - cam_y - 2) for x in xs]
    bot = [(x - cam_x, terrain.height_at(x) - cam_y + 12) for x in reversed(xs)]
    pygame.draw.polygon(surf, (44, 32, 22), top + bot)
    pygame.draw.lines(surf, (86, 64, 42), False, top, 3)
    # 水光反射
    for i in range(1, len(top) - 1, 2):
        x, y = top[i]
        gl = 0.5 + 0.5 * math.sin(t * 2.0 + i)
        pygame.draw.line(surf, mix((70, 58, 44), (150, 140, 118), gl),
                         (x - 6, y + 4), (x + 6, y + 4), 2)
    # 氣泡
    for i in range(0, len(xs), 3):
        bx, by = top[i]
        r = 2 + (math.sin(t * 3.0 + i * 1.3) + 1) * 1.6
        pygame.draw.circle(surf, (96, 76, 52), (int(bx + 8), int(by + 6)), int(r), 1)


def _draw_spikes(surf, terrain, ob, cam_x, cam_y) -> None:
    n = max(2, int(ob.w // 16))
    for i in range(n):
        wx = ob.x - ob.w / 2 + i * (ob.w / n)
        bx = wx - cam_x
        bw = ob.w / n
        gy_i = terrain.height_at(wx) - cam_y
        tip = (bx + bw / 2, gy_i - ob.h)
        pygame.draw.polygon(surf, (150, 40, 40),
                            [(bx, gy_i + 2), tip, (bx + bw, gy_i + 2)])
        pygame.draw.polygon(surf, (216, 96, 96),
                            [(bx + bw * 0.5, gy_i + 2), tip, (bx + bw, gy_i + 2)])
        pygame.draw.line(surf, (255, 224, 224), (bx + bw * 0.62, gy_i),
                         (tip[0] - 1, tip[1] + 3), 2)
    # 警示底座
    pygame.draw.line(surf, (40, 40, 46),
                     (ob.x - ob.w / 2 - cam_x, terrain.height_at(ob.x) - cam_y + 3),
                     (ob.x + ob.w / 2 - cam_x, terrain.height_at(ob.x) - cam_y + 3), 4)


def _draw_boost(surf, terrain, ob, sx, gy, t) -> None:
    a = terrain.angle_at(ob.x)
    ca, sa = math.cos(a), math.sin(a)
    hw = ob.w / 2
    p1 = (sx - hw * ca, gy - hw * sa)
    p2 = (sx + hw * ca, gy + hw * sa)
    nx, ny = -sa, ca
    quad = [(p1[0] + nx * 3, p1[1] + ny * 3), (p2[0] + nx * 3, p2[1] + ny * 3),
            (p2[0] - nx * 8, p2[1] - ny * 8), (p1[0] - nx * 8, p1[1] - ny * 8)]
    blit_glow(surf, shade(cfg.C_GREEN, 0.30), (p1[0] + p2[0]) / 2,
              (p1[1] + p2[1]) / 2 - 6, int(ob.w * 0.45))
    pygame.draw.polygon(surf, (24, 70, 44), quad)
    pygame.draw.polygon(surf, cfg.C_GREEN, quad, 2)
    for k in range(3):
        ph = (t * 1.8 + k * 0.33) % 1.0
        u = -0.7 + ph * 1.4
        mx = sx + hw * u * ca - nx * 3
        my = gy + hw * u * sa - ny * 3
        a2 = int(255 * (1.0 - abs(u) / 0.75))
        if a2 <= 0:
            continue
        col = mix((20, 60, 40), (220, 255, 220), a2 / 255)
        pygame.draw.polygon(surf, col, [
            (mx - 7 * ca + 5 * nx, my - 7 * sa + 5 * ny),
            (mx + 6 * ca, my + 6 * sa),
            (mx - 7 * ca - 5 * nx, my - 7 * sa - 5 * ny),
        ])


# ------------------------------------------------------------------ 道具
def draw_pickups(surf, terrain: Terrain, cam_x, cam_y, player: int, t: float) -> None:
    w = surf.get_width()
    for pk in terrain.pickups:
        if player in pk.taken_by:
            continue
        sx = pk.x - cam_x
        if sx < -60 or sx > w + 60:
            continue
        bob = math.sin(t * 3.0 + pk.x * 0.01) * 5
        sy = pk.y - cam_y + bob
        if pk.kind == "coin":
            _draw_coin(surf, sx, sy, pk.radius, t, pk.x)
        elif pk.kind == "nitro":
            _draw_nitro(surf, sx, sy, t)
        else:
            _draw_shield(surf, sx, sy, t)


def _draw_coin(surf, sx, sy, r, t, seed) -> None:
    spin = t * 3.4 + seed * 0.02
    wob = max(0.22, abs(math.cos(spin)))
    blit_glow(surf, shade(cfg.C_GOLD, 0.30), sx, sy, int(r * 1.8))
    edge = mix(cfg.C_GOLD, (120, 78, 20), 0.6)
    rect = pygame.Rect(sx - r * wob, sy - r, max(3, r * 2 * wob), r * 2)
    pygame.draw.ellipse(surf, edge, rect.move(0, 2))
    pygame.draw.ellipse(surf, cfg.C_GOLD, rect)
    if rect.w > 6:
        pygame.draw.ellipse(surf, mix(cfg.C_GOLD, (255, 255, 255), 0.55),
                            rect.inflate(-rect.w * 0.5, -r))
        pygame.draw.ellipse(surf, edge, rect, 2)
    # 星形閃光
    tw = (math.sin(spin * 1.7) + 1) * 0.5
    if tw > 0.8:
        s = 3 + (tw - 0.8) * 22
        cx, cy = sx + r * 0.3, sy - r * 0.5
        pygame.draw.line(surf, (255, 255, 235), (cx - s, cy), (cx + s, cy), 1)
        pygame.draw.line(surf, (255, 255, 235), (cx, cy - s), (cx, cy + s), 1)


def _draw_nitro(surf, sx, sy, t) -> None:
    blit_glow(surf, shade(cfg.C_CYAN, 0.28), sx, sy, 24)
    rect = pygame.Rect(sx - 9, sy - 15, 18, 30)
    pygame.draw.rect(surf, shade(cfg.C_CYAN, 0.55), rect, border_radius=6)
    pygame.draw.rect(surf, cfg.C_CYAN, rect.inflate(-6, -4), border_radius=5)
    pygame.draw.rect(surf, (230, 255, 255),
                     pygame.Rect(sx - 6, sy - 11, 4, 20), border_radius=2)
    pygame.draw.rect(surf, (18, 78, 88), rect, 2, border_radius=6)
    pygame.draw.rect(surf, cfg.C_STEEL_DARK,
                     pygame.Rect(sx - 4, sy - 20, 8, 6), border_radius=2)
    draw_text(surf, "N", 15, sx, sy + 2, (10, 40, 46), center=True, shadow=False)


def _draw_shield(surf, sx, sy, t) -> None:
    blit_glow(surf, shade(cfg.C_PURPLE, 0.28), sx, sy, 26)
    pts = [(sx, sy - 17), (sx + 14, sy - 8), (sx + 14, sy + 6),
           (sx, sy + 17), (sx - 14, sy + 6), (sx - 14, sy - 8)]
    pygame.draw.polygon(surf, shade(cfg.C_PURPLE, 0.55), pts)
    inner = [(sx + (px - sx) * 0.62, sy + (py - sy) * 0.62) for px, py in pts]
    pygame.draw.polygon(surf, cfg.C_PURPLE, inner)
    pygame.draw.polygon(surf, (236, 216, 255), pts, 2)
    ang = t * 2.0
    for k in range(3):
        a = ang + k * cfg.TAU / 3
        pygame.draw.line(surf, (236, 216, 255),
                         (sx + math.cos(a) * 6, sy + math.sin(a) * 6),
                         (sx + math.cos(a) * 12, sy + math.sin(a) * 12), 2)


# ------------------------------------------------------------------ 終點
def draw_finish(surf, terrain: Terrain, cam_x, cam_y) -> None:
    sx = cfg.TRACK_LENGTH - cam_x
    if sx < -200 or sx > surf.get_width() + 200:
        return
    gy = terrain.height_at(cfg.TRACK_LENGTH) - cam_y
    top = gy - 210
    # 立柱
    for px in (sx - 70, sx + 70):
        pygame.draw.rect(surf, cfg.C_STEEL_DARK, (px - 6, top, 12, 210))
        pygame.draw.rect(surf, cfg.C_STEEL, (px - 6, top, 4, 210))
        pygame.draw.polygon(surf, shade(cfg.C_STEEL_DARK, 0.8),
                            [(px - 16, gy + 2), (px + 16, gy + 2),
                             (px + 8, gy - 10), (px - 8, gy - 10)])
    # 橫樑與旗幟
    pygame.draw.rect(surf, cfg.C_STEEL_DARK, (sx - 76, top, 152, 34))
    pygame.draw.rect(surf, shade(cfg.C_STEEL_DARK, 1.3), (sx - 76, top, 152, 4))
    draw_text(surf, "FINISH", 26, sx, top + 17, cfg.C_GOLD, center=True)
    size = 13
    for row in range(4):
        for col in range(11):
            c = (245, 245, 245) if (row + col) % 2 == 0 else (26, 26, 30)
            wave = math.sin(pygame.time.get_ticks() * 0.004 + col * 0.5) * 3
            pygame.draw.rect(surf, c,
                             (sx - 71 + col * size, top + 36 + row * size + wave,
                              size, size))


# ------------------------------------------------------------------ 機車
def draw_bike(surf, bike: Bike, cam_x: float, cam_y: float,
              terrain: Terrain | None = None, t: float = 0.0) -> None:
    sx = bike.x - cam_x
    sy = bike.y - cam_y
    ang = bike.angle
    ca, sa = math.cos(ang), math.sin(ang)
    col = bike.color if not bike.crashed else shade(bike.color, 0.55)
    dark = shade(col, 0.45)

    def W(lx, ly):     # 車輪座標系（貼齊地面）
        return (sx + lx * ca - ly * sa, sy + lx * sa + ly * ca)

    # 車體因避震而下沉與俯仰
    dip = (bike.susp_f + bike.susp_r) * 0.5 * 4.0
    pitch = (bike.susp_f - bike.susp_r) * 0.10
    cp, sp = math.cos(pitch), math.sin(pitch)

    def B(lx, ly):     # 車架座標系（含避震下沉 / 俯仰）
        px = lx * cp - (ly + dip) * sp
        py = lx * sp + (ly + dip) * cp
        return W(px, py)

    # --- 落地陰影
    if terrain is not None:
        gy = terrain.height_at(bike.x) - cam_y
        air = max(0.0, gy - (sy + cfg.RIDE_HEIGHT))
        f = max(0.0, 1.0 - air / 260.0)
        if f > 0.02:
            sw = int(74 * (0.55 + f * 0.45))
            sh = pygame.Surface((sw, 16), pygame.SRCALPHA)
            pygame.draw.ellipse(sh, (0, 0, 0, int(120 * f)), sh.get_rect())
            surf.blit(sh, (sx - sw / 2, gy - 8))

    # --- 護盾力場
    if bike.shield_timer > 0:
        pulse = 40 + math.sin(t * 8.0) * 3
        fade = 1.0 if bike.shield_timer > 1.2 else (0.4 + 0.6 * abs(math.sin(t * 14)))
        blit_glow(surf, shade(cfg.C_PURPLE, 0.30 * fade), sx, sy - 4, int(pulse * 1.4))
        pygame.draw.circle(surf, mix((60, 30, 90), cfg.C_PURPLE, fade),
                           (int(sx), int(sy - 4)), int(pulse), 2)

    wheel_r = 14
    rear = W(-22, 2)
    front = W(22, 2)

    # --- 氮氣尾焰（畫在車體後方）
    if bike.nitro_active:
        base = B(-27, 0)
        flick = 1.0 + math.sin(t * 37.0) * 0.18 + math.sin(t * 61.0) * 0.08
        nx, ny = -sa, ca          # 車身法線
        for ln, wd, c in ((52, 9, shade(cfg.C_CYAN, 0.55)),
                          (34, 6, (150, 240, 255)),
                          (18, 4, (255, 255, 255))):
            ln *= flick
            tip = (base[0] - ca * ln, base[1] - sa * ln)
            flame = [
                (base[0] + nx * wd, base[1] + ny * wd),
                (base[0] + ca * 3 + nx * wd * 0.4, base[1] + sa * 3 + ny * wd * 0.4),
                (base[0] - nx * wd, base[1] - ny * wd),
                (tip[0], tip[1]),
            ]
            pygame.draw.polygon(surf, c, flame)
        blit_glow(surf, shade(cfg.C_CYAN, 0.5), base[0] - ca * 18, base[1] - sa * 18,
                  int(30 * flick))

    _draw_wheel(surf, rear, wheel_r, bike.wheel_spin, ca, sa)
    _draw_wheel(surf, front, wheel_r, bike.wheel_spin, ca, sa)

    # --- 後搖臂與後避震
    swing_pivot = B(-4, 4)
    pygame.draw.line(surf, cfg.C_STEEL_DARK, swing_pivot, rear, 6)
    pygame.draw.line(surf, cfg.C_STEEL, swing_pivot, rear, 2)
    shock_top = B(-6, -8)
    pygame.draw.line(surf, shade(col, 1.15), shock_top,
                     ((swing_pivot[0] + rear[0]) / 2, (swing_pivot[1] + rear[1]) / 2), 4)

    # --- 前叉（伸縮量隨避震變化）
    head = B(16, -12)
    pygame.draw.line(surf, cfg.C_STEEL_DARK, head, front, 6)
    pygame.draw.line(surf, (226, 230, 240), head, front, 2)
    mid_fork = ((head[0] + front[0]) / 2, (head[1] + front[1]) / 2)
    pygame.draw.line(surf, cfg.C_STEEL_DARK, mid_fork, front, 8)

    # --- 引擎
    eng = [B(-8, 0), B(6, -2), B(9, 6), B(-6, 8)]
    pygame.draw.polygon(surf, (58, 60, 70), eng)
    pygame.draw.polygon(surf, (30, 32, 40), eng, 2)
    for k in range(3):
        pygame.draw.line(surf, (92, 96, 108), B(-6 + k * 4, -1), B(-5 + k * 4, 6), 2)

    # --- 排氣管
    pygame.draw.lines(surf, cfg.C_STEEL_DARK, False,
                      [B(2, -1), B(-10, -4), B(-22, -6)], 5)
    pygame.draw.lines(surf, cfg.C_STEEL, False,
                      [B(2, -2), B(-10, -5), B(-22, -7)], 2)

    # --- 車架 / 油箱 / 座墊（玩家色）
    tank = [B(-16, -10), B(-4, -14), B(9, -11), B(12, -4), B(-6, -2)]
    pygame.draw.polygon(surf, col, tank)
    pygame.draw.polygon(surf, shade(col, 1.35),
                        [B(-15, -11), B(-4, -14), B(8, -11), B(-2, -9)])
    pygame.draw.polygon(surf, dark, tank, 2)
    seat = [B(-26, -8), B(-14, -11), B(-12, -8), B(-25, -5)]
    pygame.draw.polygon(surf, (34, 34, 42), seat)
    pygame.draw.polygon(surf, (16, 16, 22), seat, 2)
    # 後土除
    pygame.draw.lines(surf, col, False, [B(-24, -9), B(-31, -11), B(-34, -14)], 4)
    # 前土除與號碼牌
    pygame.draw.lines(surf, col, False, [B(15, -6), B(24, -6), B(29, -3)], 4)
    plate = [B(14, -16), B(22, -14), B(21, -7), B(13, -9)]
    pygame.draw.polygon(surf, (238, 238, 240), plate)
    pygame.draw.polygon(surf, dark, plate, 2)
    draw_text(surf, str(bike.index + 1), 13, (plate[0][0] + plate[2][0]) / 2,
              (plate[0][1] + plate[2][1]) / 2, (40, 40, 50), center=True, shadow=False)
    # 車手把
    bar_l, bar_r = B(15, -22), B(23, -20)
    pygame.draw.line(surf, cfg.C_STEEL_DARK, B(16, -14), bar_l, 3)
    pygame.draw.line(surf, (30, 30, 36), bar_l, bar_r, 4)
    # 頭燈
    lamp = B(26, -12)
    pygame.draw.circle(surf, (255, 244, 200), (int(lamp[0]), int(lamp[1])), 4)
    pygame.draw.circle(surf, (120, 110, 80), (int(lamp[0]), int(lamp[1])), 4, 1)
    blit_glow(surf, (90, 82, 52), lamp[0] + ca * 8, lamp[1] + sa * 8, 14)

    _draw_rider(surf, bike, B, col, dark, t)

    # --- 高速殘影
    if bike.kmh > 200 and not bike.crashed:
        f = min(1.0, (bike.kmh - 200) / 140)
        for k in range(3):
            oy = -18 + k * 14
            p1 = W(-30 - k * 6, oy)
            p2 = W(-62 - 40 * f - k * 10, oy)
            pygame.draw.line(surf, mix((120, 130, 150), (255, 255, 255), 0.3),
                             p1, p2, 1)


def _draw_wheel(surf, pos, r, spin, ca, sa) -> None:
    x, y = int(pos[0]), int(pos[1])
    pygame.draw.circle(surf, cfg.C_RUBBER, (x, y), r)
    # 胎塊
    for k in range(10):
        a = spin + k * cfg.TAU / 10
        c1 = math.cos(a) * (r - 1)
        s1 = math.sin(a) * (r - 1)
        pygame.draw.line(surf, (58, 58, 68), (x + c1 * 0.86, y + s1 * 0.86),
                         (x + c1, y + s1), 3)
    pygame.draw.circle(surf, (96, 98, 110), (x, y), r, 1)
    # 輪圈與輻條
    pygame.draw.circle(surf, cfg.C_STEEL, (x, y), int(r * 0.62), 2)
    for k in range(6):
        a = spin + k * cfg.TAU / 6
        pygame.draw.line(surf, (150, 154, 168), (x, y),
                         (x + math.cos(a) * r * 0.6, y + math.sin(a) * r * 0.6), 1)
    pygame.draw.circle(surf, cfg.C_STEEL_DARK, (x, y), 3)


def _draw_rider(surf, bike: Bike, B, col, dark, t) -> None:
    lean = bike.lean_input
    if bike.crashed:
        lean = -1.6
    suit = (54, 58, 74)          # 深色車衣，與車身色形成對比
    suit_hi = (78, 84, 104)
    boot = (24, 24, 30)
    glove = shade(col, 0.9)

    hip = B(-10 - lean * 2, -13)
    knee = B(-1 + lean * 2, -6)
    foot = B(-6, 4)
    shoulder = B(-1 + lean * 7, -26 + abs(lean) * 2)
    hand = B(19 + lean * 2, -21)
    elbow = B(9 + lean * 5, -19 + (0 if lean > 0 else 3))
    head = B(3 + lean * 8, -34 + abs(lean) * 1.5)

    # 腿（大腿 / 小腿 / 靴）
    pygame.draw.line(surf, suit, hip, knee, 9)
    pygame.draw.line(surf, suit_hi, hip, knee, 3)
    pygame.draw.line(surf, shade(suit, 0.8), knee, foot, 7)
    pygame.draw.line(surf, boot, foot, B(-9, 6), 7)
    # 軀幹（深色車衣 + 玩家色胸線）
    pygame.draw.line(surf, suit, hip, shoulder, 12)
    mid = ((hip[0] + shoulder[0]) / 2, (hip[1] + shoulder[1]) / 2)
    pygame.draw.line(surf, col, mid, shoulder, 5)
    pygame.draw.line(surf, suit_hi, hip, mid, 4)
    # 手臂
    pygame.draw.line(surf, suit, shoulder, elbow, 7)
    pygame.draw.line(surf, shade(suit, 0.85), elbow, hand, 6)
    pygame.draw.circle(surf, glove, (int(hand[0]), int(hand[1])), 4)
    # 安全帽
    hx, hy = int(head[0]), int(head[1])
    pygame.draw.circle(surf, col, (hx, hy), 10)
    pygame.draw.circle(surf, shade(col, 1.35), (hx - 2, hy - 3), 6)
    pygame.draw.circle(surf, (245, 245, 250), (hx - 5, hy - 5), 2)
    pygame.draw.circle(surf, (18, 18, 24), (hx, hy), 10, 2)
    # 面罩（朝向前方）
    fwd = B(3 + lean * 8 + 9, -34 + abs(lean) * 1.5 - 1)
    pygame.draw.line(surf, (110, 190, 230), (hx + (fwd[0] - hx) * 0.35,
                                             hy + (fwd[1] - hy) * 0.35), fwd, 5)
    # 帽簷
    peak = B(3 + lean * 8 + 14, -34 + abs(lean) * 1.5 - 6)
    pygame.draw.line(surf, (24, 24, 32), (hx, hy - 6), peak, 4)


# ------------------------------------------------------------------ 粒子 / 浮字
def draw_particles(surf, bike: Bike, cam_x, cam_y) -> None:
    for p in bike.particles:
        t = max(0.0, min(1.0, p.life / max(0.01, p.max_life)))
        x = p.x - cam_x
        y = p.y - cam_y
        if x < -40 or x > surf.get_width() + 40:
            continue
        if p.kind == "smoke":
            r = max(2, int(p.size))
            s = soft_sprite(p.color, r)
            s.set_alpha(int(150 * t * t))
            surf.blit(s, (x - r, y - r))
        elif p.kind == "spark":
            # 帶速度殘影的火花
            tx, ty = x - p.vx * 0.016, y - p.vy * 0.016
            c = mix((40, 40, 50), p.color, 0.4 + 0.6 * t)
            pygame.draw.line(surf, c, (tx, ty), (x, y), max(1, int(p.size * 0.7)))
            blit_glow(surf, p.color, x, y, max(3, int(p.size * 2.2)),
                      alpha=int(200 * t))
        else:  # dirt
            col = shade(p.color, 0.55 + 0.45 * t)
            r = max(1, int(p.size))
            pygame.draw.circle(surf, col, (int(x), int(y)), r)
            if r > 2:
                pygame.draw.circle(surf, shade(col, 1.35),
                                   (int(x - r * 0.3), int(y - r * 0.3)), 1)


def draw_floats(surf, bike: Bike, cam_x, cam_y) -> None:
    for f in bike.floats:
        a = max(0.0, min(1.0, f.life / 1.4))
        col = mix((30, 30, 40), f.color, 0.25 + 0.75 * a)
        size = 20 + int((1.0 - a) * 4)
        draw_text(surf, f.text, size, f.x - cam_x, f.y - cam_y, col, center=True)


# ------------------------------------------------------------------ 畫面後製
_veil_cache: dict[tuple, pygame.Surface] = {}


def draw_vignette(surf) -> None:
    """四周壓暗，讓畫面中央更聚焦（預算一次）。"""
    w, h = surf.get_size()
    key = ("vig", w, h)
    v = _veil_cache.get(key)
    if v is None:
        v = pygame.Surface((w, h), pygame.SRCALPHA)
        band = int(h * 0.26)
        for i in range(band):
            a = int(92 * (1 - i / band) ** 2.2)
            v.fill((0, 0, 0, a), (0, i, w, 1))
            v.fill((0, 0, 0, int(a * 0.9)), (0, h - 1 - i, w, 1))
        side = int(w * 0.16)
        for i in range(side):
            a = int(70 * (1 - i / side) ** 2.2)
            v.fill((0, 0, 0, a), (i, 0, 1, h), special_flags=pygame.BLEND_RGBA_MAX)
            v.fill((0, 0, 0, a), (w - 1 - i, 0, 1, h),
                   special_flags=pygame.BLEND_RGBA_MAX)
        _veil_cache[key] = v
    surf.blit(v, (0, 0))


def draw_speed_fx(surf, bike: Bike, t: float) -> None:
    """高速 / 氮氣時的速度線與畫面色調。"""
    w, h = surf.get_size()
    f = 0.0
    if bike.nitro_active:
        f = 1.0
    elif bike.kmh > 230:
        f = min(1.0, (bike.kmh - 230) / 110)
    if f <= 0.01:
        return
    col = cfg.C_CYAN if bike.nitro_active else (210, 220, 255)
    for i in range(9):
        ph = (t * 2.6 + i * 0.37) % 1.0
        y = h * ((i * 0.11 + 0.06) % 1.0)
        ln = 40 + 150 * ph * f
        x = w * (1.0 - ph) - 60
        a = int(90 * f * (1.0 - abs(ph - 0.5) * 2))
        if a <= 0:
            continue
        pygame.draw.line(surf, mix((20, 24, 34), col, a / 90),
                         (x, y), (x + ln, y), 2)


# ------------------------------------------------------------------ HUD
_panel_cache: dict[tuple, pygame.Surface] = {}


def panel(w: int, h: int, top=(26, 30, 46), bot=(12, 14, 24), alpha=200,
          border=(90, 100, 130)) -> pygame.Surface:
    key = (w, h, top, bot, alpha, border)
    s = _panel_cache.get(key)
    if s is None:
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        body = pygame.Surface((w, h), pygame.SRCALPHA)
        for i in range(h):
            body.fill((*mix(top, bot, i / max(1, h - 1)), alpha), (0, i, w, 1))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=10)
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(body, (0, 0))
        pygame.draw.rect(s, (*border, 150), s.get_rect(), 2, border_radius=10)
        pygame.draw.line(s, (255, 255, 255, 40), (8, 2), (w - 8, 2), 1)
        _panel_cache[key] = s
    return s


def draw_bar(surf, x, y, w, h, ratio, color, bg=(40, 44, 60)) -> None:
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=h // 2)
    fill = max(0, min(1, ratio))
    if fill > 0:
        fw = max(h, int(w * fill))
        pygame.draw.rect(surf, shade(color, 0.7), (x, y, fw, h), border_radius=h // 2)
        pygame.draw.rect(surf, color, (x, y, fw, max(1, h - 4)),
                         border_radius=h // 2)
        pygame.draw.rect(surf, mix(color, (255, 255, 255), 0.5),
                         (x + 2, y + 1, max(1, fw - 4), max(1, h // 3)),
                         border_radius=h // 3)
    pygame.draw.rect(surf, (12, 14, 22), (x, y, w, h), 2, border_radius=h // 2)


def _draw_speedo(surf, cx, cy, r, kmh, nitro_active) -> None:
    """類比式速度錶：刻度環 + 指針 + 數字。"""
    top = max(0.0, min(1.0, kmh / 290.0))
    start, sweep = math.radians(200), math.radians(-220)
    rect = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
    pygame.draw.arc(surf, (44, 48, 66), rect, start + sweep, start, 6)
    # 已達速度的彩色弧
    seg = 22
    for i in range(seg):
        f0 = i / seg
        if f0 > top:
            break
        a0 = start + sweep * f0
        a1 = start + sweep * ((i + 0.85) / seg)
        c = mix(cfg.C_GREEN, cfg.C_RED, f0)
        if nitro_active:
            c = mix(c, cfg.C_CYAN, 0.5)
        pygame.draw.arc(surf, c, rect, min(a0, a1), max(a0, a1), 6)
    # 刻度
    for i in range(7):
        a = start + sweep * (i / 6)
        c, s = math.cos(a), -math.sin(a)
        pygame.draw.line(surf, (140, 148, 172),
                         (cx + c * (r - 9), cy + s * (r - 9)),
                         (cx + c * (r - 2), cy + s * (r - 2)), 2)
    # 指針
    a = start + sweep * top
    c, s = math.cos(a), -math.sin(a)
    pygame.draw.line(surf, cfg.C_RED, (cx - c * 5, cy - s * 5),
                     (cx + c * (r - 8), cy + s * (r - 8)), 3)
    pygame.draw.circle(surf, (200, 206, 224), (int(cx), int(cy)), 4)
    draw_text(surf, f"{kmh:.0f}", 22, cx, cy + r * 0.42, cfg.C_WHITE, center=True)
    draw_text(surf, "km/h", 12, cx, cy + r * 0.78, cfg.C_DIM, center=True)


def _draw_minimap(surf, x, y, w, h, terrain, bike, rival) -> None:
    prof = terrain.mini_profile(max(16, w // 9)) if terrain is not None else None
    pygame.draw.rect(surf, (16, 18, 30), (x, y, w, h), border_radius=6)
    if prof:
        n = len(prof)
        pts = [(x + w * i / (n - 1), y + h - 3 - prof[i] * (h - 8))
               for i in range(n)]
        pygame.draw.polygon(surf, (48, 56, 78),
                            pts + [(x + w, y + h), (x, y + h)])
        pygame.draw.lines(surf, (96, 140, 92), False, pts, 2)
    pygame.draw.rect(surf, (70, 78, 104), (x, y, w, h), 2, border_radius=6)
    # 終點旗
    pygame.draw.line(surf, cfg.C_GOLD, (x + w - 3, y + 2), (x + w - 3, y + h - 2), 2)
    for b, ring in ((rival, False), (bike, True)):
        px = x + w * b.progress
        py = y + h / 2
        pygame.draw.circle(surf, b.color, (int(px), int(py)), 5)
        pygame.draw.circle(surf, (10, 12, 20), (int(px), int(py)), 5, 2)
        if ring:
            pygame.draw.circle(surf, (240, 240, 250), (int(px), int(py)), 7, 1)


def draw_hud(surf, bike: Bike, rival: Bike, race_time: float, rank: int,
             countdown: float, terrain: Terrain | None = None) -> None:
    w, h = surf.get_size()
    compact = h < 500

    # ---- 左側主面板
    pw, ph = 316, (98 if compact else 116)
    surf.blit(panel(pw, ph), (12, 10))
    draw_text(surf, bike.name, 21, 26, 16, bike.color)
    score_col = cfg.C_GREEN if bike.stats.score >= 0 else cfg.C_RED
    draw_text(surf, f"{bike.stats.score:,}", 24, 26, 42, score_col)
    draw_text(surf, f"金幣 {bike.stats.coins:02d}", 17, 26, ph - 30, cfg.C_GOLD)
    draw_text(surf, f"翻滾 {bike.stats.flips:02d}", 17, 128, ph - 30, cfg.C_PURPLE)
    draw_text(surf, "NITRO", 12, 128, 18, cfg.C_CYAN)
    nr = bike.nitro / cfg.NITRO_MAX
    ncol = cfg.C_CYAN if not bike.nitro_active else mix(cfg.C_CYAN, (255, 255, 255),
                                                        0.5 + 0.5 * math.sin(race_time * 22))
    draw_bar(surf, 128, 34, 108, 13, nr, ncol)
    _draw_speedo(surf, 272, 10 + ph / 2, min(44, ph / 2 - 6), bike.kmh,
                 bike.nitro_active)

    # ---- 名次與差距
    gap = bike.x - rival.x
    rank_txt = "1st" if rank == 1 else "2nd"
    rank_col = cfg.C_GOLD if rank == 1 else cfg.C_DIM
    surf.blit(panel(92, 62), (w - 104, 10))
    draw_text(surf, rank_txt, 28, w - 58, 20, rank_col, center=True)
    gap_txt = f"{'+' if gap >= 0 else '-'}{abs(gap) / 10:.0f} m"
    draw_text(surf, gap_txt, 17, w - 58, 52, cfg.C_GREEN if gap >= 0 else cfg.C_RED,
              center=True)

    # ---- 賽程迷你地圖
    bar_w = 380
    bar_x, bar_y = w // 2 - bar_w // 2, 12
    _draw_minimap(surf, bar_x, bar_y, bar_w, 26, terrain, bike, rival)
    draw_text(surf, f"{race_time:05.2f}s", 18, w // 2, bar_y + 30, cfg.C_WHITE,
              center=True)

    if bike.shield_timer > 0:
        draw_text(surf, f"護盾 {bike.shield_timer:.1f}s", 17, 26, ph + 14, cfg.C_PURPLE)

    if bike.finished:
        draw_text(surf, "完賽！", 44, w // 2, h // 2 - 30, cfg.C_GOLD, center=True)
    if countdown > 0:
        frac = countdown - math.floor(countdown)
        n = int(math.ceil(countdown))
        txt = "GO!" if n <= 0 else str(n)
        size = int(70 + (1.0 - frac) * 46)
        col = cfg.C_GREEN if txt == "GO!" else cfg.C_GOLD
        blit_glow(surf, shade(col, 0.35), w // 2, h // 2, 90)
        draw_text(surf, txt, size, w // 2, h // 2, col, center=True)


# ------------------------------------------------------------------ 選單背景
def draw_menu_ground(surf, cam_x: float, base_y: float) -> None:
    """選單用的前景地面剪影（不需要 Terrain 物件）。"""
    w = surf.get_width()
    pts = []
    for sx in range(-20, w + 40, 20):
        wx = sx + cam_x
        y = base_y - math.sin(wx / 420 * cfg.TAU) * 26 - math.sin(wx / 130) * 8
        pts.append((sx, y))
    body = [(pts[0][0], surf.get_height())] + pts + [(pts[-1][0], surf.get_height())]
    pygame.draw.polygon(surf, cfg.C_DIRT, body)
    pygame.draw.lines(surf, cfg.C_GRASS, False, pts, 5)
    pygame.draw.lines(surf, cfg.C_GRASS_LIGHT, False, [(x, y - 2) for x, y in pts], 2)
