"""遊戲全域設定與常數。"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- 視窗 / 畫面
SCREEN_W = 1280
SCREEN_H = 720
FPS = 60
DIVIDER_H = 6
VIEW_W = SCREEN_W
VIEW_H = (SCREEN_H - DIVIDER_H) // 2

TITLE = "MOTO DUEL - 雙人越野機車對決"

# ---------------------------------------------------------------- 賽道
TERRAIN_STEP = 12           # 地形取樣間距 (px)
TRACK_LENGTH = 22000        # 終點 X 座標
TERRAIN_PADDING = 1200      # 終點後仍生成的地形長度
GROUND_BASE = 420           # 地面基準高度 (世界座標 y, 越大越低)
MAX_TERRAIN_SLOPE = 0.62    # 地形最大斜率 (約 32 度)，確保引擎爬得上去
CHECKPOINT_SPACING = 900    # 墜毀後重生的檢查點間距

# ---------------------------------------------------------------- 物理
GRAVITY = 1750.0            # px/s^2
ENGINE_ACCEL = 1250.0
BRAKE_ACCEL = 1250.0
REVERSE_ACCEL = 320.0
NITRO_ACCEL = 900.0         # 氮氣額外推力
MAX_SPEED = 1150.0
MAX_SPEED_NITRO = 1500.0
ROLL_RESIST = 0.55          # 滾動阻力係數 (1/s)
AIR_DRAG = 0.00035          # 空氣阻力 (v^2 項)
AIR_TORQUE = 16.0           # 空中翻滾角加速度 (rad/s^2)
MAX_ANG_VEL = 11.0
AIR_DAMP_ACTIVE = 0.2       # 持續按方向鍵時的角速度阻尼
AIR_DAMP_IDLE = 3.2         # 放開方向鍵時快速穩定姿態
GROUND_ALIGN = 14.0         # 貼地角度校正速度
RIDE_HEIGHT = 16.0          # 車體中心離地高度
CRASH_ANGLE = 1.15          # 落地角度誤差超過此值 (rad) 即墜毀
CRASH_IMPACT = 430.0        # 且法線速度超過此值
CRASH_TIME = 1.5            # 墜毀停頓秒數

# ---------------------------------------------------------------- 資源 / 加成
NITRO_MAX = 100.0
NITRO_DRAIN = 34.0          # 每秒消耗
NITRO_REGEN = 3.2           # 每秒自然回復
NITRO_PICKUP = 45.0
NITRO_PER_FLIP = 22.0
SHIELD_TIME = 6.0
BOOST_PAD_SPEED = 640.0     # 加速板瞬間增速
MUD_FACTOR = 0.955          # 泥沼每幀速度衰減
COMEBACK_NITRO = 25.0       # 落後者每回合追趕補償

# ---------------------------------------------------------------- 分數
SCORE_COIN = 100
SCORE_FLIP = 500
SCORE_AIRTIME = 40          # 每秒滯空
SCORE_WIN = 3000
SCORE_CRASH = -250
SCORE_SPIKE_GRAZE = -100
ROCK_SAFE_SPEED = 430.0     # 高於此速度撞巨石會墜毀
SPIKE_SAFE_SPEED = 500.0    # 高於此速度輾過尖刺會墜毀
SCORE_FINISH_TIME_BONUS = 60    # (基準秒數 - 完賽秒數) * 此值
PAR_TIME = 75.0

ROUNDS_PER_MATCH = 3

# ---------------------------------------------------------------- 顏色
C_SKY_TOP = (24, 30, 58)
C_SKY_BOT = (92, 76, 120)
C_SKY_TOP2 = (58, 28, 40)
C_SKY_BOT2 = (150, 96, 78)
C_HILL_FAR = (48, 52, 92)
C_HILL_MID = (38, 44, 76)
C_DIRT = (74, 52, 34)
C_DIRT_DARK = (52, 36, 24)
C_GRASS = (108, 168, 68)
C_WHITE = (240, 240, 245)
C_DIM = (170, 175, 190)
C_GOLD = (255, 206, 70)
C_RED = (232, 78, 72)
C_BLUE = (86, 168, 255)
C_GREEN = (110, 224, 140)
C_CYAN = (110, 240, 230)
C_PURPLE = (196, 128, 255)
C_PANEL = (18, 20, 32)

PLAYER_COLORS = [(232, 78, 72), (86, 168, 255)]
PLAYER_NAMES = ["玩家 1", "玩家 2"]

TAU = math.tau
