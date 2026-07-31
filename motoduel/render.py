"""繪圖層：字型、世界渲染、HUD。"""
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
        surf.blit(sh, (rect.x + 2, rect.y + 2))
    surf.blit(img, rect)
    return rect


# ------------------------------------------------------------------ 背景
def make_sky(w: int, h: int, warm: bool) -> pygame.Surface:
    surf = pygame.Surface((w, h))
    top = cfg.C_SKY_TOP2 if warm else cfg.C_SKY_TOP
    bot = cfg.C_SKY_BOT2 if warm else cfg.C_SKY_BOT
    for i in range(h):
        t = i / max(1, h - 1)
        surf.fill(
            (
                int(top[0] * (1 - t) + bot[0] * t),
                int(top[1] * (1 - t) + bot[1] * t),
                int(top[2] * (1 - t) + bot[2] * t),
            ),
            (0, i, w, 1),
        )
    return surf


def draw_parallax(surf, cam_x: float, view_h: int) -> None:
    w = surf.get_width()
    for depth, color, base, amp, wl in (
        (0.15, cfg.C_HILL_FAR, view_h * 0.72, 46, 620),
        (0.30, cfg.C_HILL_MID, view_h * 0.84, 62, 380),
    ):
        pts = [(0, view_h)]
        off = cam_x * depth
        for sx in range(0, w + 24, 24):
            wx = sx + off
            y = base - math.sin(wx / wl * cfg.TAU) * amp - math.sin(wx / (wl * 2.7)) * amp * 0.5
            pts.append((sx, y))
        pts.append((w, view_h))
        pygame.draw.polygon(surf, color, pts)


# ------------------------------------------------------------------ 世界
def draw_terrain(surf, terrain: Terrain, cam_x: float, cam_y: float) -> None:
    w, h = surf.get_size()
    start = max(0, int((cam_x - terrain.step) / terrain.step))
    end = min(terrain.count - 1, int((cam_x + w + terrain.step) / terrain.step) + 1)
    pts = []
    for i in range(start, end + 1):
        wx = i * terrain.step
        pts.append((wx - cam_x, terrain.heights[i] - cam_y))
    if len(pts) < 2:
        return
    body = pts + [(pts[-1][0], h + 40), (pts[0][0], h + 40)]
    pygame.draw.polygon(surf, cfg.C_DIRT, body)
    pygame.draw.lines(surf, cfg.C_GRASS, False, pts, 5)
    # 土層紋理
    deep = [(px, py + 26) for px, py in pts]
    pygame.draw.lines(surf, cfg.C_DIRT_DARK, False, deep, 3)


def draw_obstacles(surf, terrain: Terrain, cam_x: float, cam_y: float) -> None:
    w = surf.get_width()
    for ob in terrain.obstacles:
        sx = ob.x - cam_x
        if sx < -220 or sx > w + 220:
            continue
        gy = terrain.height_at(ob.x) - cam_y
        if ob.kind == "rock":
            pts = [
                (sx - ob.w / 2, gy),
                (sx - ob.w * 0.34, gy - ob.h),
                (sx + ob.w * 0.18, gy - ob.h * 0.86),
                (sx + ob.w / 2, gy),
            ]
            pygame.draw.polygon(surf, (122, 118, 128), pts)
            pygame.draw.polygon(surf, (66, 64, 74), pts, 3)
        elif ob.kind == "mud":
            xs = [ob.x - ob.w / 2 + i * ob.w / 8 for i in range(9)]
            top = [(x - cam_x, terrain.height_at(x) - cam_y - 3) for x in xs]
            bot = [(x - cam_x, terrain.height_at(x) - cam_y + 9) for x in reversed(xs)]
            pygame.draw.polygon(surf, (58, 42, 30), top + bot)
            pygame.draw.lines(surf, (94, 70, 46), False, top, 3)
        elif ob.kind == "spike":
            n = max(2, int(ob.w // 16))
            for i in range(n):
                bx = sx - ob.w / 2 + i * (ob.w / n)
                bw = ob.w / n
                gy_i = terrain.height_at(ob.x - ob.w / 2 + i * (ob.w / n)) - cam_y
                pygame.draw.polygon(
                    surf, (206, 96, 96),
                    [(bx, gy_i), (bx + bw / 2, gy_i - ob.h), (bx + bw, gy_i)],
                )
        elif ob.kind == "boost":
            a = terrain.angle_at(ob.x)
            ca, sa = math.cos(a), math.sin(a)
            hw = ob.w / 2
            p1 = (sx - hw * ca, gy - hw * sa)
            p2 = (sx + hw * ca, gy + hw * sa)
            pygame.draw.line(surf, cfg.C_GREEN, p1, p2, 8)
            for k in (-0.5, 0.0, 0.5):
                mx = sx + hw * k * ca
                my = gy + hw * k * sa - 10
                pygame.draw.polygon(
                    surf, (220, 255, 220),
                    [(mx - 6, my + 6), (mx + 6, my), (mx - 6, my - 6)],
                )


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
            r = pk.radius
            wobble = abs(math.cos(t * 3.4 + pk.x * 0.02))
            pygame.draw.ellipse(surf, cfg.C_GOLD,
                                pygame.Rect(sx - r * wobble, sy - r, r * 2 * wobble, r * 2))
            pygame.draw.ellipse(surf, (180, 140, 30),
                                pygame.Rect(sx - r * wobble, sy - r, r * 2 * wobble, r * 2), 2)
        elif pk.kind == "nitro":
            rect = pygame.Rect(sx - 9, sy - 15, 18, 30)
            pygame.draw.rect(surf, cfg.C_CYAN, rect, border_radius=6)
            pygame.draw.rect(surf, (20, 90, 100), rect, 2, border_radius=6)
            draw_text(surf, "N", 15, sx, sy, (10, 40, 46), center=True, shadow=False)
        else:
            pts = [(sx, sy - 17), (sx + 14, sy - 8), (sx + 14, sy + 6),
                   (sx, sy + 17), (sx - 14, sy + 6), (sx - 14, sy - 8)]
            pygame.draw.polygon(surf, cfg.C_PURPLE, pts)
            pygame.draw.polygon(surf, (90, 50, 140), pts, 2)


def draw_finish(surf, terrain: Terrain, cam_x, cam_y) -> None:
    sx = cfg.TRACK_LENGTH - cam_x
    if sx < -120 or sx > surf.get_width() + 120:
        return
    gy = terrain.height_at(cfg.TRACK_LENGTH) - cam_y
    pygame.draw.rect(surf, (230, 230, 230), (sx - 4, gy - 190, 8, 190))
    size = 14
    for row in range(6):
        for col in range(4):
            c = (245, 245, 245) if (row + col) % 2 == 0 else (30, 30, 30)
            pygame.draw.rect(surf, c, (sx + 4 + col * size, gy - 190 + row * size, size, size))


def draw_bike(surf, bike: Bike, cam_x: float, cam_y: float) -> None:
    sx = bike.x - cam_x
    sy = bike.y - cam_y
    ang = bike.angle
    ca, sa = math.cos(ang), math.sin(ang)

    def to_screen(lx, ly):
        return (sx + lx * ca - ly * sa, sy + lx * sa + ly * ca)

    if bike.shield_timer > 0:
        rad = 40 + math.sin(pygame.time.get_ticks() * 0.01) * 3
        pygame.draw.circle(surf, cfg.C_PURPLE, (int(sx), int(sy)), int(rad), 3)

    wheel_r = 13
    back = to_screen(-19, 8)
    front = to_screen(19, 8)
    for wx, wy in (back, front):
        pygame.draw.circle(surf, (28, 28, 34), (int(wx), int(wy)), wheel_r)
        pygame.draw.circle(surf, (110, 110, 120), (int(wx), int(wy)), wheel_r, 2)
        spoke = bike.wheel_spin
        for k in range(3):
            a2 = spoke + k * cfg.TAU / 3
            pygame.draw.line(surf, (150, 150, 160), (wx, wy),
                             (wx + math.cos(a2) * wheel_r * 0.8,
                              wy + math.sin(a2) * wheel_r * 0.8), 2)

    body = [to_screen(-20, 6), to_screen(-6, -6), to_screen(10, -6), to_screen(20, 6)]
    color = bike.color if not bike.crashed else (120, 60, 60)
    pygame.draw.polygon(surf, color, body)
    pygame.draw.polygon(surf, (20, 20, 26), body, 2)
    pygame.draw.line(surf, (200, 200, 210), to_screen(6, -8), to_screen(20, -14), 3)

    # 騎士
    lean = -0.35 if bike.crashed else 0.0
    hip = to_screen(-4, -8)
    head = to_screen(2 + lean * 10, -30)
    hand = to_screen(19, -14)
    foot = to_screen(-10, 4)
    pygame.draw.line(surf, (240, 220, 200), hip, head, 6)
    pygame.draw.line(surf, (240, 220, 200), hip, hand, 4)
    pygame.draw.line(surf, (240, 220, 200), hip, foot, 4)
    pygame.draw.circle(surf, color, (int(head[0]), int(head[1])), 9)
    pygame.draw.circle(surf, (20, 20, 26), (int(head[0]), int(head[1])), 9, 2)

    if bike.nitro_active:
        tail = to_screen(-26, 2)
        flame = to_screen(-26 - 18 - math.sin(pygame.time.get_ticks() * 0.05) * 6, 2)
        pygame.draw.line(surf, cfg.C_CYAN, tail, flame, 7)


def draw_particles(surf, bike: Bike, cam_x, cam_y) -> None:
    for p in bike.particles:
        alpha = max(0.0, min(1.0, p.life / 0.8))
        col = tuple(int(c * alpha) for c in p.color)
        pygame.draw.circle(surf, col, (int(p.x - cam_x), int(p.y - cam_y)), max(1, int(p.size)))


def draw_floats(surf, bike: Bike, cam_x, cam_y) -> None:
    for f in bike.floats:
        alpha = max(0.0, min(1.0, f.life / 1.4))
        col = tuple(int(c * (0.35 + 0.65 * alpha)) for c in f.color)
        draw_text(surf, f.text, 20, f.x - cam_x, f.y - cam_y, col, center=True)


# ------------------------------------------------------------------ HUD
def draw_bar(surf, x, y, w, h, ratio, color, bg=(40, 44, 60)) -> None:
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=h // 2)
    fill = max(0, min(1, ratio))
    if fill > 0:
        pygame.draw.rect(surf, color, (x, y, int(w * fill), h), border_radius=h // 2)
    pygame.draw.rect(surf, (12, 14, 22), (x, y, w, h), 2, border_radius=h // 2)


def draw_hud(surf, bike: Bike, rival: Bike, race_time: float, rank: int, countdown: float) -> None:
    w = surf.get_width()
    panel = pygame.Surface((320, 104), pygame.SRCALPHA)
    panel.fill((10, 12, 20, 175))
    surf.blit(panel, (10, 8))

    draw_text(surf, bike.name, 22, 22, 12, bike.color)
    draw_text(surf, f"{bike.kmh:5.0f} km/h", 22, 22, 40, cfg.C_WHITE)
    score_col = cfg.C_GREEN if bike.stats.score >= 0 else cfg.C_RED
    draw_text(surf, f"{bike.stats.score:,}", 20, 22, 70, score_col)
    draw_text(surf, f"金幣 {bike.stats.coins:02d}", 18, 178, 14, cfg.C_GOLD)
    draw_text(surf, f"翻滾 {bike.stats.flips:02d}", 18, 178, 38, cfg.C_PURPLE)
    draw_text(surf, "NITRO", 13, 178, 64, cfg.C_CYAN)
    draw_bar(surf, 178, 82, 138, 14, bike.nitro / cfg.NITRO_MAX, cfg.C_CYAN)

    # 名次 / 領先資訊
    gap = bike.x - rival.x
    rank_txt = "1st" if rank == 1 else "2nd"
    rank_col = cfg.C_GOLD if rank == 1 else cfg.C_DIM
    draw_text(surf, rank_txt, 30, w - 56, 20, rank_col, center=True)
    gap_txt = f"{'+' if gap >= 0 else '-'}{abs(gap) / 10:.0f} m"
    draw_text(surf, gap_txt, 18, w - 56, 46, cfg.C_GREEN if gap >= 0 else cfg.C_RED, center=True)

    # 賽程進度條（雙方位置）
    bar_x, bar_y, bar_w = w // 2 - 220, 14, 440
    draw_bar(surf, bar_x, bar_y, bar_w, 10, 1.0, (30, 34, 48), (30, 34, 48))
    for b in (rival, bike):
        px = bar_x + bar_w * b.progress
        pygame.draw.circle(surf, b.color, (int(px), bar_y + 5), 7)
        pygame.draw.circle(surf, (10, 12, 20), (int(px), bar_y + 5), 7, 2)
    draw_text(surf, f"{race_time:05.2f}s", 18, w // 2, bar_y + 26, cfg.C_WHITE, center=True)

    if bike.shield_timer > 0:
        draw_text(surf, f"護盾 {bike.shield_timer:.1f}s", 18, 22, 116, cfg.C_PURPLE)

    if bike.finished:
        draw_text(surf, "完賽！", 44, w // 2, surf.get_height() // 2 - 30,
                  cfg.C_GOLD, center=True)
    if countdown > 0:
        n = int(math.ceil(countdown))
        txt = "GO!" if n <= 0 else str(n)
        draw_text(surf, txt, 90, w // 2, surf.get_height() // 2, cfg.C_GOLD, center=True)
