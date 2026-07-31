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

    def full_reset(self, start_x: float = 60.0) -> None:
        self.stats = RaceStats()
        self.floats.clear()
        self.particles.clear()
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

    def emit(self, n: int, color: tuple, spread: float = 160.0, up: float = 120.0) -> None:
        for _ in range(n):
            self.particles.append(
                Particle(
                    self.x, self.y + 12,
                    random.uniform(-spread, spread) - self.vx * 0.15,
                    random.uniform(-up, 20) - self.vy * 0.15,
                    random.uniform(0.3, 0.8), color,
                    random.uniform(2.0, 4.5),
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
        brake = 1.0 if inp["brake"] else 0.0
        lean = (-1.0 if inp["lean_back"] else 0.0) + (1.0 if inp["lean_fwd"] else 0.0)

        # ---- 氮氣
        self.nitro_active = bool(inp["nitro"]) and self.nitro > 0 and not self.crashed
        if self.nitro_active:
            self.nitro = max(0.0, self.nitro - cfg.NITRO_DRAIN * dt)
            self.stats.nitro_time += dt
            self.emit(2, cfg.C_CYAN, spread=60, up=40)
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
            if self.air_timer > 0.9:
                bonus = int(self.air_timer * cfg.SCORE_AIRTIME)
                self.stats.score += bonus
                self.add_float(f"滯空 +{bonus}", cfg.C_GOLD)
            self.emit(10, cfg.C_DIRT, spread=180, up=90)
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
            self.emit(1, cfg.C_DIRT_DARK, spread=90, up=50)

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
        for f in self.floats:
            f.life -= dt
            f.y -= 34 * dt
        self.floats = [f for f in self.floats if f.life > 0]
        for p in self.particles:
            p.life -= dt
            p.vy += 620 * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
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
        self.emit(24, cfg.C_RED, spread=260, up=220)

    def apply_boost(self) -> None:
        a = self.terrain.angle_at(self.x) if self.on_ground else self.angle
        self.vx += math.cos(a) * cfg.BOOST_PAD_SPEED
        self.vy += math.sin(a) * cfg.BOOST_PAD_SPEED - 120
        self.boost_timer = 0.6
        self.stats.boosts += 1
        self.add_float("加速板！", cfg.C_GREEN)
        self.emit(14, cfg.C_GREEN, spread=200, up=140)
