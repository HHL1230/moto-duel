"""單人模式的電腦車手（AI）。"""
from __future__ import annotations

import random

from . import config as cfg
from .entities import Bike, wrap_angle
from .terrain import Terrain

DIFFICULTIES = {
    "easy": {
        "name": "簡單",
        "hint": "新手練習：對手保守駕駛，約 47 秒完賽",
        "throttle": 0.72,
        "nitro_min": 85.0,
        "kp": 5.0,
        "kd": 2.4,
        "react": 0.16,
        "sloppy": 0.35,      # 空中放棄修正的機率
        "trick": 0.0,        # 主動翻滾傾向
        "speed_cap": 0.62,   # 巡航速度上限比例
        "alert": 0.55,       # 察覺前方危險並減速的機率
    },
    "normal": {
        "name": "普通",
        "hint": "勢均力敵：對手全速前進，約 40 秒完賽",
        "throttle": 0.95,
        "nitro_min": 45.0,
        "kp": 8.0,
        "kd": 3.2,
        "react": 0.08,
        "sloppy": 0.14,
        "trick": 0.45,
        "speed_cap": 0.88,
        "alert": 0.85,
    },
    "hard": {
        "name": "困難",
        "hint": "高手挑戰：對手幾乎不失誤，約 35 秒完賽",
        "throttle": 1.0,
        "nitro_min": 20.0,
        "kp": 12.0,
        "kd": 4.0,
        "react": 0.03,
        "sloppy": 0.03,
        "trick": 0.85,
        "speed_cap": 1.0,
        "alert": 1.0,
    },
}
DIFFICULTY_ORDER = ["easy", "normal", "hard"]

IDLE_INPUT = {"gas": False, "brake": False, "lean_back": False,
              "lean_fwd": False, "nitro": False}


class AIRider:
    """依地形前瞻與落地預測操控機車的電腦對手。"""

    def __init__(self, bike: Bike, terrain: Terrain, difficulty: str = "normal",
                 seed: int | None = None) -> None:
        self.bike = bike
        self.terrain = terrain
        self.difficulty = difficulty if difficulty in DIFFICULTIES else "normal"
        self.p = DIFFICULTIES[self.difficulty]
        self.rng = random.Random(seed)
        self.timer = 0.0
        self.cmd = dict(IDLE_INPUT)
        self.doing_trick = False

    # -------------------------------------------------------------- 預測
    def predict_landing(self, max_t: float = 3.0, step: float = 0.05) -> float:
        """回傳預測落點的地形角度；若很快落地則回傳當前地形角度。"""
        b = self.bike
        x, y, vy = b.x, b.y, b.vy
        t = 0.0
        while t < max_t:
            t += step
            x += b.vx * step
            vy += cfg.GRAVITY * step
            y += vy * step
            if y >= self.terrain.height_at(x) - cfg.RIDE_HEIGHT:
                return self.terrain.angle_at(x)
        return self.terrain.angle_at(x)

    def time_to_land(self, max_t: float = 3.0, step: float = 0.05) -> float:
        b = self.bike
        x, y, vy = b.x, b.y, b.vy
        t = 0.0
        while t < max_t:
            t += step
            x += b.vx * step
            vy += cfg.GRAVITY * step
            y += vy * step
            if y >= self.terrain.height_at(x) - cfg.RIDE_HEIGHT:
                return t
        return max_t

    def hazard_ahead(self, look: float = 340.0):
        """回傳前方需要減速的障礙物與其安全速度。"""
        b = self.bike
        best = None
        for ob in self.terrain.obstacles:
            if ob.kind not in ("rock", "spike"):
                continue
            d = ob.x - b.x
            if 0.0 < d < look:
                safe = (cfg.ROCK_SAFE_SPEED if ob.kind == "rock"
                        else cfg.SPIKE_SAFE_SPEED) * 0.82
                if best is None or d < best[0]:
                    best = (d, ob, safe)
        return best

    # -------------------------------------------------------------- 決策
    def think(self, dt: float) -> dict:
        self.timer -= dt
        if self.timer <= 0:
            self.timer = self.p["react"]
            self.cmd = self._decide()
        elif not self.bike.on_ground and not self.doing_trick:
            # 空中姿態修正需要每幀重算：反應延遲期間持續施加傾斜會嚴重過衝
            self.cmd = dict(self.cmd)
            lean = self._air_lean()
            self.cmd["lean_back"] = lean < 0
            self.cmd["lean_fwd"] = lean > 0
        return self.cmd

    def _air_lean(self) -> int:
        """空中姿態控制，回傳 -1 / 0 / +1。

        車體本身在放開傾斜鍵時會自動朝預測落點的地形角度校正（見 entities），
        因此電腦只在誤差大到自動校正來不及時，才主動施加同向傾斜幫忙加速；
        其餘時間刻意「放手」，讓自動校正接管——這也和玩家的操作感一致。
        """
        b = self.bike
        p = self.p
        err = wrap_angle(self.predict_landing() - b.angle)
        # 剩餘滯空時間內，自動校正是否來得及轉完這個角度
        t_land = max(0.05, self.time_to_land())
        need = abs(err) / t_land          # 需要的平均角速度
        if need < cfg.MAX_ANG_VEL * 0.35:
            return 0                      # 自動校正綽綽有餘，放手最穩
        control = err * p["kp"] - b.ang_vel * p["kd"]
        if control > 2.0:
            return 1
        if control < -2.0:
            return -1
        return 0

    def _decide(self) -> dict:
        b = self.bike
        p = self.p
        inp = dict(IDLE_INPUT)
        if b.crashed or b.finished:
            self.doing_trick = False
            return inp

        speed_ratio = b.speed / cfg.MAX_SPEED
        gas = self.rng.random() < p["throttle"] and speed_ratio < p["speed_cap"]
        inp["gas"] = gas or speed_ratio < 0.35

        if b.on_ground:
            self.doing_trick = False
            slope = self.terrain.slope_at(b.x)

            # 前方有巨石或尖刺且速度過快時減速通過（護盾在身則直接衝）
            hazard = self.hazard_ahead()
            if (hazard is not None and b.shield_timer <= 0
                    and b.speed > hazard[2] and self.rng.random() < p["alert"]):
                inp["gas"] = False
                inp["brake"] = True
                inp["nitro"] = False
                return inp

            # 上坡或平地且氮氣充足時噴射；陡下坡不浪費
            if b.nitro >= p["nitro_min"] and slope > -0.25 and speed_ratio < 0.98:
                inp["nitro"] = True
            # 接近跳台時後傾，讓起飛姿態更容易做動作
            ahead = self.terrain.slope_at(b.x + 90)
            if ahead < -0.3 and speed_ratio > 0.5:
                inp["lean_back"] = True
            return inp

        # 空中：判斷是否有餘裕做翻滾特技，否則修正落地姿態
        t_land = self.time_to_land()

        # 一圈翻滾約需 0.35 秒。策略：滯空足夠才起轉，轉滿整圈就放手，
        # 讓自動校正接管落地姿態（若時間不夠也提早放手）。
        if self.doing_trick and (abs(b.air_rotation) >= cfg.TAU or t_land < 0.2):
            self.doing_trick = False
        elif not self.doing_trick and abs(b.air_rotation) < 0.5 \
                and t_land > 0.55 and self.rng.random() < p["trick"]:
            self.doing_trick = True

        if self.doing_trick:
            inp["lean_back"] = True
            inp["nitro"] = b.nitro > 60
            return inp

        if self.rng.random() < p["sloppy"]:
            return inp

        lean = self._air_lean()
        inp["lean_fwd"] = lean > 0
        inp["lean_back"] = lean < 0
        return inp


def make_ai_name(difficulty: str) -> str:
    return f"電腦（{DIFFICULTIES.get(difficulty, DIFFICULTIES['normal'])['name']}）"


def cycle_difficulty(current: str, delta: int = 1) -> str:
    i = DIFFICULTY_ORDER.index(current) if current in DIFFICULTY_ORDER else 1
    return DIFFICULTY_ORDER[(i + delta) % len(DIFFICULTY_ORDER)]


def angle_error(bike: Bike, terrain: Terrain) -> float:
    """輔助：目前車身角度與腳下地形角度的差（供測試使用）。"""
    return abs(wrap_angle(bike.angle - terrain.angle_at(bike.x)))
