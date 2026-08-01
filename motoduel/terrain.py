"""程序化越野地形、障礙物與加成道具生成。"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import config as cfg


# ------------------------------------------------------------------ 資料類別
@dataclass
class Obstacle:
    kind: str          # "rock" | "mud" | "spike" | "boost"
    x: float
    w: float
    h: float
    y: float = 0.0     # 生成時由地形填入 (物件底部貼地)


@dataclass
class Pickup:
    kind: str          # "coin" | "nitro" | "shield"
    x: float
    y: float
    taken_by: set = field(default_factory=set)

    @property
    def radius(self) -> float:
        return 13.0 if self.kind == "coin" else 17.0


# ------------------------------------------------------------------ 地形
class Terrain:
    """以取樣點陣列表示的 1D 高度場，並負責放置障礙與道具。"""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.step = cfg.TERRAIN_STEP
        self.total_len = cfg.TRACK_LENGTH + cfg.TERRAIN_PADDING
        self.count = int(self.total_len / self.step) + 2
        self.heights: list[float] = []
        self.obstacles: list[Obstacle] = []
        self.pickups: list[Pickup] = []
        self.checkpoints: list[float] = []
        self._generate_heights()
        self._place_features()

    # -------------------------------------------------------------- 高度場
    def _generate_heights(self) -> None:
        rng = self.rng
        # 多層正弦波 + 隨機相位，形成連續起伏
        layers = [
            (rng.uniform(900, 1400), rng.uniform(70, 110), rng.uniform(0, cfg.TAU)),
            (rng.uniform(380, 560), rng.uniform(28, 46), rng.uniform(0, cfg.TAU)),
            (rng.uniform(150, 240), rng.uniform(9, 18), rng.uniform(0, cfg.TAU)),
        ]
        # 隨機大跳台 (以高斯隆起疊加)
        jumps = []
        x = 1300.0
        while x < cfg.TRACK_LENGTH - 600:
            jumps.append((x, rng.uniform(70, 150), rng.uniform(90, 160)))
            x += rng.uniform(700, 1150)
        # 隨機坑洞
        pits = []
        x = 1800.0
        while x < cfg.TRACK_LENGTH - 800:
            pits.append((x, rng.uniform(45, 95), rng.uniform(70, 130)))
            x += rng.uniform(1100, 1900)

        for i in range(self.count):
            wx = i * self.step
            h = cfg.GROUND_BASE
            for wl, amp, ph in layers:
                h -= math.sin(wx / wl * cfg.TAU + ph) * amp
            for jx, amp, width in jumps:
                h -= amp * math.exp(-((wx - jx) ** 2) / (2 * width * width))
            for px, amp, width in pits:
                h += amp * math.exp(-((wx - px) ** 2) / (2 * width * width))
            # 起跑區與終點區壓平，確保公平且好收尾
            if wx < 520:
                t = max(0.0, min(1.0, wx / 520.0))
                h = cfg.GROUND_BASE * (1 - t) + h * t
            if wx > cfg.TRACK_LENGTH - 360:
                t = max(0.0, min(1.0, (wx - (cfg.TRACK_LENGTH - 360)) / 360.0))
                h = h * (1 - t) + cfg.GROUND_BASE * t
            self.heights.append(h)

        # 平滑化，避免尖銳鋸齒造成物理爆衝
        for _ in range(2):
            smoothed = self.heights[:]
            for i in range(1, self.count - 1):
                smoothed[i] = (self.heights[i - 1] + self.heights[i] * 2 + self.heights[i + 1]) / 4
            self.heights = smoothed

        self._clamp_slopes()

    def _clamp_slopes(self) -> None:
        """限制相鄰取樣點高度差，確保任何坡度都爬得上去、不會卡死。"""
        max_d = cfg.MAX_TERRAIN_SLOPE * self.step
        for _ in range(3):
            for i in range(1, self.count):
                d = self.heights[i] - self.heights[i - 1]
                if d > max_d:
                    self.heights[i] = self.heights[i - 1] + max_d
                elif d < -max_d:
                    self.heights[i] = self.heights[i - 1] - max_d
            for i in range(self.count - 2, -1, -1):
                d = self.heights[i] - self.heights[i + 1]
                if d > max_d:
                    self.heights[i] = self.heights[i + 1] + max_d
                elif d < -max_d:
                    self.heights[i] = self.heights[i + 1] - max_d

    # -------------------------------------------------------------- 特徵物
    def _place_features(self) -> None:
        rng = self.rng
        x = cfg.CHECKPOINT_SPACING
        while x < cfg.TRACK_LENGTH:
            self.checkpoints.append(x)
            x += cfg.CHECKPOINT_SPACING

        # 障礙物：避開起跑保護區與終點線
        x = 900.0
        while x < cfg.TRACK_LENGTH - 400:
            slope = abs(self.slope_at(x))
            kind = rng.choices(
                ["rock", "mud", "spike", "boost"],
                weights=[34, 26, 16, 24],
            )[0]
            if kind in ("rock", "spike") and slope > 0.55:
                kind = "boost"   # 陡坡上不放致命障礙，避免無解
            if kind == "rock":
                w, h = rng.uniform(34, 58), rng.uniform(30, 52)
            elif kind == "mud":
                w, h = rng.uniform(120, 230), 16
            elif kind == "spike":
                w, h = rng.uniform(40, 70), 26
            else:
                w, h = rng.uniform(80, 120), 12
            self.obstacles.append(Obstacle(kind, x, w, h, self.height_at(x)))
            x += rng.uniform(280, 520)

        # 道具：金幣多排成弧線（鼓勵跳躍拿取），氮氣與護盾零星分布
        x = 700.0
        while x < cfg.TRACK_LENGTH - 200:
            roll = rng.random()
            if roll < 0.6:
                n = rng.randint(3, 6)
                gap = rng.uniform(40, 60)
                arc = rng.uniform(40, 130)
                for i in range(n):
                    cx = x + i * gap
                    t = i / max(1, n - 1)
                    lift = 42 + arc * math.sin(t * math.pi)
                    self.pickups.append(Pickup("coin", cx, self.height_at(cx) - lift))
                x += n * gap
            elif roll < 0.85:
                self.pickups.append(Pickup("nitro", x, self.height_at(x) - 46))
            else:
                self.pickups.append(Pickup("shield", x, self.height_at(x) - 50))
            x += rng.uniform(320, 620)

    # -------------------------------------------------------------- 查詢
    def height_at(self, x: float) -> float:
        """線性內插取得世界座標 x 的地面高度。"""
        if x <= 0:
            return self.heights[0]
        idx = x / self.step
        i = int(idx)
        if i >= self.count - 1:
            return self.heights[-1]
        frac = idx - i
        return self.heights[i] * (1 - frac) + self.heights[i + 1] * frac

    def slope_at(self, x: float) -> float:
        """地面斜率 dy/dx（螢幕座標，正值代表往右下）。"""
        d = self.step
        return (self.height_at(x + d) - self.height_at(x - d)) / (2 * d)

    def angle_at(self, x: float) -> float:
        return math.atan(self.slope_at(x))

    def last_checkpoint(self, x: float) -> float:
        best = 60.0
        for c in self.checkpoints:
            if c <= x:
                best = c
            else:
                break
        return best

    def mini_profile(self, n: int = 90) -> list[float]:
        """賽道高度剖面（0=最低, 1=最高），供 HUD 迷你地圖使用；結果快取。

        每格取多點平均，避免 22000px 的賽道被稀疏取樣成鋸齒雜訊。
        """
        n = max(8, int(n))
        cached = getattr(self, "_mini_cache", None)
        if cached is not None and cached[0] == n:
            return cached[1]
        seg = cfg.TRACK_LENGTH / (n - 1)
        ys = []
        for i in range(n):
            cx = seg * i
            samples = [self.height_at(cx + (k - 10) * seg * 0.15) for k in range(21)]
            ys.append(sum(samples) / len(samples))
        lo, hi = min(ys), max(ys)
        span = max(1.0, hi - lo)
        prof = [(hi - y) / span for y in ys]     # y 越小(越高) → 值越大
        self._mini_cache = (n, prof)
        return prof
