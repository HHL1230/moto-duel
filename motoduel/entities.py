"""機車實體、物理模擬與互動判定。"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import config as cfg
from .terrain import Terrain


def wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= cfg.TAU
    while a < -math.pi:
        a += cfg.TAU
    return a


@dataclass
class FloatText:
    text: str
    color: tuple
    x: float
    y: float
    life: float = 1.4


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    color: tuple
    size: float = 3.0
    kind: str = "dirt"      # "dirt" | "smoke" | "spark"
    max_life: float = 0.8
    gravity: float = 620.0
    grow: float = 0.0       # 每秒尺寸增長（煙霧擴散用）
    spin: float = 0.0


@dataclass
class RaceStats:
    coins: int = 0
    flips: int = 0
    crashes: int = 0
    air_time: float = 0.0
    nitro_time: float = 0.0
    top_speed: float = 0.0
    boosts: int = 0
    shields: int = 0
    score: int = 0
    finish_time: float | None = None
    best_flip_combo: int = 0
    was_behind_late: bool = False   # 賽程 80% 之後曾經落後 → 供「逆轉勝」成就判定


class Bike:
    """單一玩家的越野機車。"""

    def __init__(self, index: int, terrain: Terrain) -> None:
        self.index = index
        self.terrain = terrain
        self.color = cfg.PLAYER_COLORS[index]
        self.name = cfg.PLAYER_NAMES[index]
        self.stats = RaceStats()
        self.floats: list[FloatText] = []
        self.particles: list[Particle] = []
        self.events: list[str] = []      # 供音效層取用的事件佇列
        self.reset(start_x=60.0)

    # -------------------------------------------------------------- 狀態
    def reset(self, start_x: float) -> None:
        self.x = start_x
        self.y = self.terrain.height_at(start_x) - cfg.RIDE_HEIGHT
        self.vx = 0.0
        self.vy = 0.0
        self.angle = self.terrain.angle_at(start_x)
        self.ang_vel = 0.0
        self.on_ground = True
        self.crash_timer = 0.0
        self.air_start_angle = self.angle
        self.air_rotation = 0.0
        self.air_timer = 0.0
        self.nitro = cfg.NITRO_MAX * 0.5
        self.shield_timer = 0.0
        self.boost_timer = 0.0
        self.in_mud = False
        self.finished = False
        self.wheel_spin = 0.0
        self.nitro_active = False
        self.throttle_on = False
        self.lean_input = 0.0       # 騎士重心（-1 後傾 / +1 前傾），供繪製使用
        self.susp_f = 0.0           # 前避震壓縮量 0..1
        self.susp_r = 0.0           # 後避震壓縮量 0..1
        self.smoke_acc = 0.0        # 排氣煙生成累積器
        self.dust_acc = 0.0         # 揚塵生成累積器

    def full_reset(self, start_x: float = 60.0) -> None:
        self.stats = RaceStats()
        self.floats.clear()
        self.particles.clear()
        self.events.clear()
        self.reset(start_x)

    # -------------------------------------------------------------- 輔助
    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def kmh(self) -> float:
        return self.speed * 0.24

    @property
    def crashed(self) -> bool:
        return self.crash_timer > 0.0

    @property
    def progress(self) -> float:
        return max(0.0, min(1.0, self.x / cfg.TRACK_LENGTH))

    def add_float(self, text: str, color: tuple) -> None:
        self.floats.append(FloatText(text, color, self.x, self.y - 44))

    def emit(self, n: int, color: tuple, spread: float = 160.0, up: float = 120.0,
             kind: str = "spark", size: tuple[float, float] = (2.0, 4.5),
             life: tuple[float, float] = (0.3, 0.8), gravity: float = 620.0,
             grow: float = 0.0, ox: float = 0.0, oy: float = 12.0) -> None:
        for _ in range(n):
            lf = random.uniform(*life)
            self.particles.append(
                Particle(
                    self.x + ox, self.y + oy,
                    random.uniform(-spread, spread) - self.vx * 0.15,
                    random.uniform(-up, 20) - self.vy * 0.15,
                    lf, color, random.uniform(*size),
                    kind, lf, gravity, grow,
                    random.uniform(-6.0, 6.0),
                )
            )

    def emit_dirt(self, n: int, strength: float = 1.0) -> None:
        """輪胎揚起的泥土碎屑（受車速影響往後拋灑）。"""
        for _ in range(n):
            lf = random.uniform(0.35, 0.75)
            self.particles.append(
                Particle(
                    self.x - 18 + random.uniform(-6, 6),
                    self.y + 14 + random.uniform(-3, 3),
                    -self.vx * random.uniform(0.12, 0.34) + random.uniform(-40, 40),
                    -random.uniform(60, 240) * strength,
                    lf,
                    random.choice((cfg.C_DIRT, cfg.C_DIRT_DARK, cfg.C_SUBSOIL)),
                    random.uniform(1.6, 3.6), "dirt", lf, 900.0, 0.0,
                    random.uniform(-8, 8),
                )
            )

    def emit_dust(self, n: int, strength: float = 1.0) -> None:
        """輪胎接地揚起的粉塵（緩慢擴散上升）。"""
        for _ in range(n):
            lf = random.uniform(0.5, 1.1)
            g = random.randint(0, 26)
            self.particles.append(
                Particle(
                    self.x - 14 + random.uniform(-10, 10),
                    self.y + 14,
                    -self.vx * 0.06 + random.uniform(-30, 30),
                    -random.uniform(10, 50) * strength,
                    lf, (150 + g, 128 + g, 104 + g),
                    random.uniform(5.0, 9.0), "smoke", lf, -40.0,
                    random.uniform(16, 30),
                )
            )

    def emit_exhaust(self, n: int = 1) -> None:
        """排氣管廢氣。"""
        a = self.angle
        ox = -26 * math.cos(a) - 2 * math.sin(a)
        oy = -26 * math.sin(a) + 2 * math.cos(a)
        for _ in range(n):
            lf = random.uniform(0.35, 0.7)
            g = random.randint(0, 30)
            self.particles.append(
                Particle(
                    self.x + ox, self.y + oy,
                    -self.vx * 0.1 + random.uniform(-24, 24),
                    random.uniform(-46, -14),
                    lf, (96 + g, 96 + g, 104 + g),
                    random.uniform(3.0, 5.5), "smoke", lf, -30.0,
                    random.uniform(12, 22),
                )
            )

    # -------------------------------------------------------------- 主更新
    def update(self, dt: float, inp: dict, race_time: float) -> None:
        self._update_effects(dt)

        if self.finished:
            self._coast(dt)
            self._update_fx(dt)
            return

        if self.crashed:
            self.crash_timer -= dt
            if self.crash_timer <= 0:
                cp = self.terrain.last_checkpoint(self.x)
                self.reset(max(cp, self.x - 90))
                self.nitro = max(self.nitro, 30.0)
            self._update_fx(dt)
            return

        throttle = 1.0 if inp["gas"] else 0.0
        self.throttle_on = bool(inp["gas"])
        brake = 1.0 if inp["brake"] else 0.0
        lean = (-1.0 if inp["lean_back"] else 0.0) + (1.0 if inp["lean_fwd"] else 0.0)
        self.lean_input += (lean - self.lean_input) * min(1.0, 9.0 * dt)

        # ---- 氮氣
        self.nitro_active = bool(inp["nitro"]) and self.nitro > 0 and not self.crashed
        if self.nitro_active:
            self.nitro = max(0.0, self.nitro - cfg.NITRO_DRAIN * dt)
            self.stats.nitro_time += dt
            self.emit(3, cfg.C_CYAN, spread=50, up=30, kind="spark",
                      size=(1.6, 3.4), life=(0.14, 0.3), gravity=120.0, oy=6.0)
        else:
            self.nitro = min(cfg.NITRO_MAX, self.nitro + cfg.NITRO_REGEN * dt)

        # ---- 重力與空中控制
        self.vy += cfg.GRAVITY * dt
        if not self.on_ground:
            self.ang_vel += lean * cfg.AIR_TORQUE * dt
            self.ang_vel = max(-cfg.MAX_ANG_VEL, min(cfg.MAX_ANG_VEL, self.ang_vel))
            # 放開方向鍵時快速穩定姿態，讓玩家能調整落地角度
            damp = cfg.AIR_DAMP_ACTIVE if lean else cfg.AIR_DAMP_IDLE
            self.ang_vel *= max(0.0, 1.0 - damp * dt)
            self.angle += self.ang_vel * dt
            self.air_rotation += self.ang_vel * dt
            self.air_timer += dt
            self.stats.air_time += dt

        # ---- 位置積分
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.wheel_spin += self.vx * dt * 0.06

        # ---- 地面接觸
        ground_y = self.terrain.height_at(self.x) - cfg.RIDE_HEIGHT
        if self.y >= ground_y:
            self._resolve_ground(dt, ground_y, throttle, brake, lean)
        else:
            self.on_ground = False

        self._clamp_and_limits(dt)
        self._update_fx(dt)
        self.stats.top_speed = max(self.stats.top_speed, self.kmh)

    # -------------------------------------------------------------- 內部
    def _resolve_ground(self, dt, ground_y, throttle, brake, lean) -> None:
        a = self.terrain.angle_at(self.x)
        ca, sa = math.cos(a), math.sin(a)
        v_t = self.vx * ca + self.vy * sa
        v_n = -self.vx * sa + self.vy * ca

        if not self.on_ground:
            # 落地判定
            delta = abs(wrap_angle(self.angle - a))
            if delta > cfg.CRASH_ANGLE and v_n > cfg.CRASH_IMPACT and self.shield_timer <= 0:
                self.crash("落地失敗！")
                return
            if delta > cfg.CRASH_ANGLE and self.shield_timer > 0:
                self.add_float("護盾抵擋！", cfg.C_CYAN)
            # 結算空中翻滾
            flips = int(abs(self.air_rotation) / cfg.TAU)
            if flips > 0:
                self.stats.flips += flips
                self.stats.best_flip_combo = max(self.stats.best_flip_combo, flips)
                gain = cfg.SCORE_FLIP * flips
                self.stats.score += gain
                self.nitro = min(cfg.NITRO_MAX, self.nitro + cfg.NITRO_PER_FLIP * flips)
                self.add_float(f"翻滾 x{flips}  +{gain}", cfg.C_PURPLE)
                self.events.append("flip")
            if self.air_timer > 0.9:
                bonus = int(self.air_timer * cfg.SCORE_AIRTIME)
                self.stats.score += bonus
                self.add_float(f"滯空 +{bonus}", cfg.C_GOLD)
            if self.air_timer > 0.45:
                self.events.append("land")
            impact = min(1.0, max(0.0, v_n / 620.0))
            self.susp_f = max(self.susp_f, 0.35 + impact * 0.65)
            self.susp_r = max(self.susp_r, 0.45 + impact * 0.55)
            self.emit_dirt(int(6 + impact * 14), 0.6 + impact)
            self.emit_dust(int(3 + impact * 6), 0.6 + impact)
            self.on_ground = True

        self.y = ground_y
        self.air_rotation = 0.0
        self.air_timer = 0.0
        self.ang_vel = 0.0

        # 重力已包含在 vy 積分中，此處不再重複施加沿斜面分量

        # 動力
        if throttle:
            accel = cfg.ENGINE_ACCEL
            if self.nitro_active:
                accel += cfg.NITRO_ACCEL
            v_t += accel * dt
        if brake:
            if v_t > 0:
                v_t = max(0.0, v_t - cfg.BRAKE_ACCEL * dt)
            else:
                v_t -= cfg.REVERSE_ACCEL * dt

        # 阻力
        v_t -= v_t * cfg.ROLL_RESIST * dt
        v_t -= math.copysign(cfg.AIR_DRAG * v_t * v_t, v_t) * dt
        if self.in_mud:
            v_t *= cfg.MUD_FACTOR

        cap = cfg.MAX_SPEED_NITRO if self.nitro_active else cfg.MAX_SPEED
        v_t = max(-260.0, min(cap, v_t))

        self.vx = v_t * ca
        self.vy = v_t * sa

        # 車身角度貼合地面，前後傾可微調（跳台起飛姿態）
        target = a + lean * 0.42
        self.angle += wrap_angle(target - self.angle) * min(1.0, cfg.GROUND_ALIGN * dt)

        if throttle and abs(v_t) > 60:
            self.dust_acc += abs(v_t) * dt * (2.2 if self.nitro_active else 1.4)
            while self.dust_acc > 90:
                self.dust_acc -= 90
                self.emit_dirt(1, 0.5 + abs(v_t) / cfg.MAX_SPEED)
                if random.random() < 0.7:
                    self.emit_dust(1, 0.4 + abs(v_t) / cfg.MAX_SPEED * 0.6)

    def _coast(self, dt: float) -> None:
        """完賽後自動減速滑行。"""
        self.vy += cfg.GRAVITY * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        ground_y = self.terrain.height_at(self.x) - cfg.RIDE_HEIGHT
        if self.y >= ground_y:
            self.y = ground_y
            a = self.terrain.angle_at(self.x)
            v_t = (self.vx * math.cos(a) + self.vy * math.sin(a)) * (1 - 1.4 * dt)
            self.vx, self.vy = v_t * math.cos(a), v_t * math.sin(a)
            self.angle += wrap_angle(a - self.angle) * min(1.0, 10 * dt)
            self.on_ground = True
        self.wheel_spin += self.vx * dt * 0.06

    def _clamp_and_limits(self, dt: float) -> None:
        if self.x < 20:
            self.x = 20
            self.vx = max(0.0, self.vx)
        if self.boost_timer > 0:
            self.boost_timer -= dt

    def _update_effects(self, dt: float) -> None:
        if self.shield_timer > 0:
            self.shield_timer = max(0.0, self.shield_timer - dt)
        self.in_mud = False

    def _update_fx(self, dt: float) -> None:
        # 避震回彈（臨界阻尼近似）
        self.susp_f += (0.0 - self.susp_f) * min(1.0, 7.0 * dt)
        self.susp_r += (0.0 - self.susp_r) * min(1.0, 6.0 * dt)
        if self.on_ground and not self.crashed and not self.finished:
            # 地形起伏造成的避震微幅作動，讓車體有生命感
            bump = abs(self.terrain.slope_at(self.x + 26) - self.terrain.slope_at(self.x - 26))
            self.susp_f = min(1.0, self.susp_f + bump * abs(self.vx) * 0.0016 * dt * 60)
            self.susp_r = min(1.0, self.susp_r + bump * abs(self.vx) * 0.0013 * dt * 60)
        # 排氣煙
        if not self.crashed and not self.finished:
            self.smoke_acc += dt * (26.0 if self.throttle_on else 9.0)
            while self.smoke_acc > 10:
                self.smoke_acc -= 10
                self.emit_exhaust(1)

        for f in self.floats:
            f.life -= dt
            f.y -= 34 * dt
        self.floats = [f for f in self.floats if f.life > 0]
        for p in self.particles:
            p.life -= dt
            p.vy += p.gravity * dt
            p.vx *= (1.0 - 1.4 * dt) if p.kind == "smoke" else 1.0
            p.x += p.vx * dt
            p.y += p.vy * dt
            if p.grow:
                p.size += p.grow * dt
        self.particles = [p for p in self.particles if p.life > 0]

    # -------------------------------------------------------------- 事件
    def crash(self, reason: str) -> None:
        if self.crashed or self.finished:
            return
        self.crash_timer = cfg.CRASH_TIME
        self.stats.crashes += 1
        self.stats.score += cfg.SCORE_CRASH
        self.vx *= 0.1
        self.vy = 0.0
        self.ang_vel = 0.0
        self.air_rotation = 0.0
        self.air_timer = 0.0
        self.add_float(reason, cfg.C_RED)
        self.emit(18, (255, 176, 80), spread=260, up=220, kind="spark",
                  size=(1.6, 3.2), life=(0.25, 0.6), gravity=760.0)
        self.emit_dirt(16, 1.4)
        self.emit(10, (120, 118, 124), spread=140, up=120, kind="smoke",
                  size=(6.0, 11.0), life=(0.6, 1.2), gravity=-60.0, grow=26.0)
        self.susp_f = 1.0
        self.susp_r = 1.0
        self.events.append("crash")

    def apply_boost(self) -> None:
        a = self.terrain.angle_at(self.x) if self.on_ground else self.angle
        self.vx += math.cos(a) * cfg.BOOST_PAD_SPEED
        self.vy += math.sin(a) * cfg.BOOST_PAD_SPEED - 120
        self.boost_timer = 0.6
        self.stats.boosts += 1
        self.add_float("加速板！", cfg.C_GREEN)
        self.emit(16, cfg.C_GREEN, spread=200, up=140, kind="spark",
                  size=(1.8, 3.6), life=(0.25, 0.6), gravity=420.0)
        self.emit_dust(8, 1.2)
        self.susp_r = 1.0
        self.events.append("boost")
