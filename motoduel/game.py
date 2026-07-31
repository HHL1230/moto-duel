"""主遊戲流程：狀態機、輸入、賽事邏輯與畫面組裝。"""
from __future__ import annotations

import math
import random

import pygame

from . import config as cfg
from . import render as R
from .achievements import ACHIEVEMENTS, SaveData
from .entities import Bike
from .terrain import Terrain

MENU, COUNTDOWN, RACING, ROUND_END, MATCH_END, ACHIEVE_VIEW, PAUSED = range(7)

CONTROLS = [
    # (label, gas, brake, lean_back, lean_fwd, nitro)
    ("玩家 1", pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d, pygame.K_LSHIFT),
    ("玩家 2", pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RCTRL),
]
NITRO_ALT = {0: (pygame.K_LCTRL,), 1: (pygame.K_RSHIFT, pygame.K_KP0, pygame.K_RETURN)}

FINISH_GRACE = 15.0     # 首位完賽後，另一位玩家的寬限秒數


class Game:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption(cfg.TITLE)
        self.screen = pygame.display.set_mode((cfg.SCREEN_W, cfg.SCREEN_H))
        self.clock = pygame.time.Clock()
        self.save = SaveData()
        self.views = [
            pygame.Surface((cfg.VIEW_W, cfg.VIEW_H)),
            pygame.Surface((cfg.VIEW_W, cfg.VIEW_H)),
        ]
        self.skies = [
            R.make_sky(cfg.VIEW_W, cfg.VIEW_H, False),
            R.make_sky(cfg.VIEW_W, cfg.VIEW_H, True),
        ]
        self.state = MENU
        self.prev_state = MENU
        self.running = True
        self.t = 0.0

        self.terrain: Terrain | None = None
        self.bikes: list[Bike] = []
        self.cams = [0.0, 0.0]
        self.cam_y = [0.0, 0.0]
        self.round_index = 0
        self.round_wins = [0, 0]
        self.match_scores = [0, 0]
        self.race_time = 0.0
        self.countdown = 0.0
        self.finish_deadline: float | None = None
        self.round_result: dict | None = None
        self.new_achievements: list = []
        self.ob_cooldown: dict[tuple[int, int], float] = {}
        self.achieve_scroll = 0

    # ============================================================== 賽事建立
    def start_match(self) -> None:
        self.round_index = 0
        self.round_wins = [0, 0]
        self.match_scores = [0, 0]
        self.start_round()

    def start_round(self) -> None:
        seed = random.randrange(1, 10_000_000)
        self.terrain = Terrain(seed)
        self.bikes = [Bike(0, self.terrain), Bike(1, self.terrain)]
        # 落後補償：上一回合輸的一方獲得起始氮氣加成
        if self.round_index > 0 and self.round_wins[0] != self.round_wins[1]:
            loser = 0 if self.round_wins[0] < self.round_wins[1] else 1
            self.bikes[loser].nitro = min(
                cfg.NITRO_MAX, self.bikes[loser].nitro + cfg.COMEBACK_NITRO
            )
            self.bikes[loser].add_float("追趕補給！", cfg.C_CYAN)
        for i, b in enumerate(self.bikes):
            self.cams[i] = b.x - cfg.VIEW_W * 0.34
            self.cam_y[i] = b.y - cfg.VIEW_H * 0.55
        self.race_time = 0.0
        self.countdown = 3.2
        self.finish_deadline = None
        self.round_result = None
        self.new_achievements = []
        self.ob_cooldown.clear()
        self.state = COUNTDOWN

    # ============================================================== 主迴圈
    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(cfg.FPS) / 1000.0, 1 / 30)
            self.t += dt
            self.handle_events()
            self.update(dt)
            self.draw()
        pygame.quit()

    # ============================================================== 輸入
    def handle_events(self) -> None:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                self.on_key(e.key)

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            if self.state in (MENU,):
                self.running = False
            elif self.state in (ACHIEVE_VIEW, PAUSED):
                self.state = self.prev_state
            else:
                self.state = MENU
            return

        if self.state == MENU:
            if key == pygame.K_SPACE:
                self.start_match()
            elif key == pygame.K_a:
                self.prev_state = MENU
                self.achieve_scroll = 0
                self.state = ACHIEVE_VIEW
        elif self.state == ACHIEVE_VIEW:
            if key in (pygame.K_a, pygame.K_SPACE):
                self.state = self.prev_state
        elif self.state in (COUNTDOWN, RACING):
            if key == pygame.K_p:
                self.prev_state = self.state
                self.state = PAUSED
            elif key == pygame.K_r:
                self.start_round()
        elif self.state == PAUSED:
            if key == pygame.K_p:
                self.state = self.prev_state
        elif self.state == ROUND_END:
            if key == pygame.K_SPACE:
                self.round_index += 1
                if self.round_index >= cfg.ROUNDS_PER_MATCH:
                    self.finish_match()
                else:
                    self.start_round()
        elif self.state == MATCH_END:
            if key == pygame.K_SPACE:
                self.state = MENU

    def read_inputs(self) -> list[dict]:
        keys = pygame.key.get_pressed()
        out = []
        for i, (_, gas, brake, lb, lf, nitro) in enumerate(CONTROLS):
            nitro_down = keys[nitro] or any(keys[k] for k in NITRO_ALT[i])
            out.append({
                "gas": keys[gas],
                "brake": keys[brake],
                "lean_back": keys[lb],
                "lean_fwd": keys[lf],
                "nitro": nitro_down,
            })
        return out

    # ============================================================== 更新
    def update(self, dt: float) -> None:
        if self.state == COUNTDOWN:
            self.countdown -= dt
            for i, b in enumerate(self.bikes):
                self._update_camera(i, b, dt * 4)
            if self.countdown <= 0:
                self.countdown = 0.0
                self.state = RACING
        elif self.state == RACING:
            self.update_race(dt)

    def update_race(self, dt: float) -> None:
        self.race_time += dt
        inputs = self.read_inputs()

        for i, bike in enumerate(self.bikes):
            bike.update(dt, inputs[i], self.race_time)
            self.handle_collisions(i, bike, dt)
            self.check_finish(i, bike)
            self._update_camera(i, bike, dt)

        # 賽程後段落後紀錄（供逆轉勝成就）
        for i, bike in enumerate(self.bikes):
            rival = self.bikes[1 - i]
            if bike.progress > 0.75 and bike.x < rival.x - 150:
                bike.stats.was_behind_late = True

        # 動態平衡：落後超過 900px 者氮氣回復加成
        for i, bike in enumerate(self.bikes):
            rival = self.bikes[1 - i]
            if not bike.finished and rival.x - bike.x > 900:
                bike.nitro = min(cfg.NITRO_MAX, bike.nitro + 6.0 * dt)

        for k in list(self.ob_cooldown):
            self.ob_cooldown[k] -= dt
            if self.ob_cooldown[k] <= 0:
                del self.ob_cooldown[k]

        if all(b.finished for b in self.bikes):
            self.end_round()
        elif self.finish_deadline is not None and self.race_time >= self.finish_deadline:
            self.end_round()

    def _update_camera(self, i: int, bike: Bike, dt: float) -> None:
        target_x = bike.x - cfg.VIEW_W * 0.34
        target_y = bike.y - cfg.VIEW_H * 0.55
        lerp = min(1.0, 7.0 * dt)
        self.cams[i] += (target_x - self.cams[i]) * lerp
        self.cam_y[i] += (target_y - self.cam_y[i]) * lerp
        self.cams[i] = max(0.0, self.cams[i])

    # -------------------------------------------------------------- 互動
    def handle_collisions(self, idx: int, bike: Bike, dt: float) -> None:
        if bike.crashed or bike.finished or self.terrain is None:
            return
        ground_y = self.terrain.height_at(bike.x)

        for oi, ob in enumerate(self.terrain.obstacles):
            if abs(bike.x - ob.x) > ob.w / 2 + 16:
                continue
            key = (idx, oi)
            if key in self.ob_cooldown:
                continue
            top = ground_y - ob.h - 8
            if ob.kind == "mud":
                if bike.y > ground_y - 34:
                    bike.in_mud = True
            elif ob.kind == "rock":
                if bike.y > top:
                    self.ob_cooldown[key] = 1.2
                    if bike.speed > 430 and bike.shield_timer <= 0:
                        bike.crash("撞上巨石！")
                    elif bike.shield_timer > 0:
                        bike.add_float("護盾撞碎巨石", cfg.C_PURPLE)
                        bike.emit(10, (140, 140, 150))
                    else:
                        bike.vx *= 0.35
                        bike.vy = min(bike.vy, -160)
                        bike.add_float("顛簸！", cfg.C_DIM)
            elif ob.kind == "spike":
                if bike.y > top:
                    self.ob_cooldown[key] = 1.2
                    if bike.shield_timer > 0:
                        bike.shield_timer = 0.0
                        bike.add_float("護盾破裂！", cfg.C_PURPLE)
                    else:
                        bike.crash("尖刺陷阱！")
            elif ob.kind == "boost":
                if bike.y > ground_y - 40 and bike.boost_timer <= 0:
                    self.ob_cooldown[key] = 0.8
                    bike.apply_boost()

        for pk in self.terrain.pickups:
            if idx in pk.taken_by:
                continue
            if abs(pk.x - bike.x) > 46:
                continue
            if math.hypot(pk.x - bike.x, pk.y - bike.y) > pk.radius + 22:
                continue
            pk.taken_by.add(idx)
            if pk.kind == "coin":
                bike.stats.coins += 1
                bike.stats.score += cfg.SCORE_COIN
                bike.add_float(f"+{cfg.SCORE_COIN}", cfg.C_GOLD)
                bike.emit(5, cfg.C_GOLD, spread=90, up=90)
            elif pk.kind == "nitro":
                bike.nitro = min(cfg.NITRO_MAX, bike.nitro + cfg.NITRO_PICKUP)
                bike.add_float("氮氣 +45", cfg.C_CYAN)
                bike.emit(6, cfg.C_CYAN, spread=90, up=90)
            else:
                bike.shield_timer = cfg.SHIELD_TIME
                bike.stats.shields += 1
                bike.add_float("護盾啟動！", cfg.C_PURPLE)
                bike.emit(8, cfg.C_PURPLE, spread=110, up=110)

    def check_finish(self, idx: int, bike: Bike) -> None:
        if bike.finished or bike.x < cfg.TRACK_LENGTH:
            return
        bike.finished = True
        bike.stats.finish_time = self.race_time
        bonus = int(max(0.0, cfg.PAR_TIME - self.race_time) * cfg.SCORE_FINISH_TIME_BONUS)
        bike.stats.score += bonus
        bike.add_float(f"完賽！ +{bonus}", cfg.C_GOLD)
        if self.finish_deadline is None:
            self.finish_deadline = self.race_time + FINISH_GRACE

    # -------------------------------------------------------------- 結算
    def end_round(self) -> None:
        b0, b1 = self.bikes
        t0, t1 = b0.stats.finish_time, b1.stats.finish_time
        if t0 is not None and t1 is not None:
            winner = 0 if t0 <= t1 else 1
        elif t0 is not None:
            winner = 0
        elif t1 is not None:
            winner = 1
        else:
            winner = 0 if b0.x > b1.x else (1 if b1.x > b0.x else None)

        if winner is not None:
            self.round_wins[winner] += 1
            self.bikes[winner].stats.score += cfg.SCORE_WIN

        for i, b in enumerate(self.bikes):
            self.match_scores[i] += b.stats.score

        self.save.record_race([b.stats for b in self.bikes], winner)
        self.new_achievements = []
        for i, b in enumerate(self.bikes):
            ctx = {
                "won": winner == i,
                "coins": b.stats.coins,
                "flips": b.stats.flips,
                "crashes": b.stats.crashes,
                "top_speed": b.stats.top_speed,
                "nitro_time": b.stats.nitro_time,
                "boosts": b.stats.boosts,
                "shields": b.stats.shields,
                "finish_time": b.stats.finish_time,
                "comeback": b.stats.was_behind_late,
                "score": b.stats.score,
                "air_time": b.stats.air_time,
                "best_flip_combo": b.stats.best_flip_combo,
                "total_races": self.save.data["total_races"],
                "total_wins": self.save.data["total_wins"][i],
                "total_coins": self.save.data["total_coins"],
                "total_flips": self.save.data["total_flips"],
                "perfect_match": False,
            }
            for ach in self.save.evaluate(ctx):
                self.new_achievements.append((i, ach))
        self.save.save()
        self.round_result = {"winner": winner}
        self.state = ROUND_END

    def finish_match(self) -> None:
        w0, w1 = self.round_wins
        if w0 == w1:
            champ = 0 if self.match_scores[0] >= self.match_scores[1] else 1
        else:
            champ = 0 if w0 > w1 else 1
        if max(w0, w1) == cfg.ROUNDS_PER_MATCH:
            ctx = {k: 0 for k in (
                "coins", "flips", "crashes", "top_speed", "nitro_time", "boosts",
                "shields", "score", "air_time", "best_flip_combo", "total_races",
                "total_wins", "total_coins", "total_flips")}
            ctx.update({"won": True, "comeback": False, "finish_time": None,
                        "perfect_match": True})
            for ach in self.save.evaluate(ctx):
                self.new_achievements.append((champ, ach))
            self.save.save()
        self.match_champion = champ
        self.state = MATCH_END

    # ============================================================== 繪製
    def draw(self) -> None:
        if self.state == MENU:
            self.draw_menu()
        elif self.state == ACHIEVE_VIEW:
            self.draw_achievements()
        elif self.state in (COUNTDOWN, RACING, PAUSED):
            self.draw_race()
            if self.state == PAUSED:
                self.overlay_text("暫停", "按 P 繼續  |  ESC 回主選單")
        elif self.state == ROUND_END:
            self.draw_race()
            self.draw_round_end()
        elif self.state == MATCH_END:
            self.draw_match_end()
        pygame.display.flip()

    # -------------------------------------------------------------- 賽中
    def draw_race(self) -> None:
        assert self.terrain is not None
        for i, bike in enumerate(self.bikes):
            view = self.views[i]
            view.blit(self.skies[i], (0, 0))
            cam_x, cam_y = self.cams[i], self.cam_y[i]
            R.draw_parallax(view, cam_x, cfg.VIEW_H)
            R.draw_terrain(view, self.terrain, cam_x, cam_y)
            R.draw_obstacles(view, self.terrain, cam_x, cam_y)
            R.draw_pickups(view, self.terrain, cam_x, cam_y, i, self.t)
            R.draw_finish(view, self.terrain, cam_x, cam_y)
            rival = self.bikes[1 - i]
            if abs(rival.x - bike.x) < cfg.VIEW_W:
                R.draw_bike(view, rival, cam_x, cam_y)
            R.draw_particles(view, bike, cam_x, cam_y)
            R.draw_bike(view, bike, cam_x, cam_y)
            R.draw_floats(view, bike, cam_x, cam_y)
            self.draw_offscreen_arrow(view, bike, rival, cam_x)
            rank = 1 if self.rank_of(i) == 1 else 2
            R.draw_hud(view, bike, rival, self.race_time, rank, self.countdown)
            self.screen.blit(view, (0, i * (cfg.VIEW_H + cfg.DIVIDER_H)))

        pygame.draw.rect(self.screen, (12, 14, 22),
                         (0, cfg.VIEW_H, cfg.SCREEN_W, cfg.DIVIDER_H))
        R.draw_text(self.screen, f"回合 {self.round_index + 1}/{cfg.ROUNDS_PER_MATCH}   "
                                 f"{self.round_wins[0]} - {self.round_wins[1]}",
                    16, cfg.SCREEN_W - 90, cfg.VIEW_H - 16, cfg.C_DIM, center=True)

    def rank_of(self, i: int) -> int:
        b, r = self.bikes[i], self.bikes[1 - i]
        bt, rt = b.stats.finish_time, r.stats.finish_time
        if bt is not None and rt is not None:
            return 1 if bt <= rt else 2
        if bt is not None:
            return 1
        if rt is not None:
            return 2
        if b.x == r.x:
            return 1 if i == 0 else 2
        return 1 if b.x > r.x else 2

    def draw_offscreen_arrow(self, view, bike: Bike, rival: Bike, cam_x: float) -> None:
        sx = rival.x - cam_x
        if 0 <= sx <= cfg.VIEW_W:
            return
        y = cfg.VIEW_H // 2
        if sx < 0:
            pts = [(28, y), (58, y - 16), (58, y + 16)]
            tx = 74
        else:
            pts = [(cfg.VIEW_W - 28, y), (cfg.VIEW_W - 58, y - 16), (cfg.VIEW_W - 58, y + 16)]
            tx = cfg.VIEW_W - 92
        pygame.draw.polygon(view, rival.color, pts)
        R.draw_text(view, f"{abs(rival.x - bike.x) / 10:.0f}m", 16, tx, y - 10, rival.color)

    def overlay_text(self, title: str, sub: str) -> None:
        veil = pygame.Surface((cfg.SCREEN_W, cfg.SCREEN_H), pygame.SRCALPHA)
        veil.fill((6, 8, 14, 190))
        self.screen.blit(veil, (0, 0))
        R.draw_text(self.screen, title, 64, cfg.SCREEN_W // 2, cfg.SCREEN_H // 2 - 40,
                    cfg.C_GOLD, center=True)
        R.draw_text(self.screen, sub, 24, cfg.SCREEN_W // 2, cfg.SCREEN_H // 2 + 30,
                    cfg.C_DIM, center=True)

    # -------------------------------------------------------------- 選單
    def draw_menu(self) -> None:
        self.screen.blit(self.skies[0], (0, 0))
        self.screen.blit(self.skies[1], (0, cfg.VIEW_H))
        veil = pygame.Surface((cfg.SCREEN_W, cfg.SCREEN_H), pygame.SRCALPHA)
        veil.fill((8, 10, 18, 150))
        self.screen.blit(veil, (0, 0))

        R.draw_text(self.screen, "MOTO DUEL", 82, cfg.SCREEN_W // 2, 90, cfg.C_GOLD, center=True)
        R.draw_text(self.screen, "雙人越野機車對決", 32, cfg.SCREEN_W // 2, 156,
                    cfg.C_WHITE, center=True)

        cols = [
            (cfg.SCREEN_W // 2 - 330, cfg.PLAYER_COLORS[0], "玩家 1（左）",
             ["W  油門", "S  煞車", "A / D  後傾 / 前傾", "左 Shift  氮氣"]),
            (cfg.SCREEN_W // 2 + 60, cfg.PLAYER_COLORS[1], "玩家 2（右）",
             ["↑  油門", "↓  煞車", "← / →  後傾 / 前傾", "右 Ctrl  氮氣"]),
        ]
        for x, color, title, lines in cols:
            pygame.draw.rect(self.screen, (14, 16, 26), (x - 20, 200, 290, 190),
                             border_radius=12)
            pygame.draw.rect(self.screen, color, (x - 20, 200, 290, 190), 3, border_radius=12)
            R.draw_text(self.screen, title, 26, x, 214, color)
            for k, line in enumerate(lines):
                R.draw_text(self.screen, line, 22, x, 254 + k * 32, cfg.C_WHITE)

        tips = [
            "空中用左右鍵翻滾可獲得分數與氮氣；落地角度不對會摔車。",
            "綠色加速板衝刺、金幣加分、氮氣罐補給、紫色護盾抵擋一次傷害。",
            f"三回合制對決，取得最多回合勝利者獲勝。",
        ]
        for k, line in enumerate(tips):
            R.draw_text(self.screen, line, 20, cfg.SCREEN_W // 2, 412 + k * 30,
                        cfg.C_DIM, center=True)

        d = self.save.data
        best = f"{d['best_time']:.2f}s" if d.get("best_time") else "—"
        R.draw_text(self.screen,
                    f"總場次 {d['total_races']}　勝場 P1 {d['total_wins'][0]} / P2 {d['total_wins'][1]}"
                    f"　最佳單場分數 {d['best_score']:,}　最速完賽 {best}"
                    f"　成就 {len(self.save.unlocked)}/{len(ACHIEVEMENTS)}",
                    20, cfg.SCREEN_W // 2, 520, cfg.C_GREEN, center=True)

        blink = 0.5 + 0.5 * math.sin(self.t * 4)
        col = tuple(int(c * (0.55 + 0.45 * blink)) for c in cfg.C_GOLD)
        R.draw_text(self.screen, "[空白鍵] 開始對決　　[A] 成就　　[ESC] 離開",
                    30, cfg.SCREEN_W // 2, 600, col, center=True)
        R.draw_text(self.screen, "賽中：[P] 暫停　[R] 重跑本回合", 18,
                    cfg.SCREEN_W // 2, 656, cfg.C_DIM, center=True)

    def draw_achievements(self) -> None:
        self.screen.fill(cfg.C_PANEL)
        R.draw_text(self.screen, "成就", 48, cfg.SCREEN_W // 2, 26, cfg.C_GOLD, center=True)
        unlocked = self.save.unlocked
        R.draw_text(self.screen, f"已解鎖 {len(unlocked)} / {len(ACHIEVEMENTS)}",
                    22, cfg.SCREEN_W // 2, 84, cfg.C_DIM, center=True)
        for i, ach in enumerate(ACHIEVEMENTS):
            col_i, row_i = divmod(i, 8)
            x = 60 + col_i * 610
            y = 124 + row_i * 66
            got = ach.key in unlocked
            box = pygame.Rect(x, y, 570, 58)
            pygame.draw.rect(self.screen, (24, 28, 44) if got else (18, 20, 30), box,
                             border_radius=10)
            pygame.draw.rect(self.screen, cfg.C_GOLD if got else (46, 50, 66), box, 2,
                             border_radius=10)
            icon = "★" if got else "☆"
            R.draw_text(self.screen, icon, 30, x + 16, y + 12,
                        cfg.C_GOLD if got else (70, 74, 92))
            R.draw_text(self.screen, ach.name, 22, x + 58, y + 6,
                        cfg.C_WHITE if got else cfg.C_DIM)
            R.draw_text(self.screen, ach.desc, 18, x + 58, y + 32,
                        cfg.C_DIM if got else (86, 90, 110))
        R.draw_text(self.screen, "[空白鍵 / A / ESC] 返回", 22, cfg.SCREEN_W // 2,
                    cfg.SCREEN_H - 36, cfg.C_GOLD, center=True)

    # -------------------------------------------------------------- 結算畫面
    def draw_round_end(self) -> None:
        veil = pygame.Surface((cfg.SCREEN_W, cfg.SCREEN_H), pygame.SRCALPHA)
        veil.fill((6, 8, 14, 225))
        self.screen.blit(veil, (0, 0))
        board = pygame.Rect(cfg.SCREEN_W // 2 - 430, 8, 860, cfg.SCREEN_H - 72)
        pygame.draw.rect(self.screen, (14, 17, 28), board, border_radius=16)
        pygame.draw.rect(self.screen, (52, 58, 80), board, 3, border_radius=16)
        winner = self.round_result["winner"] if self.round_result else None
        title = "平手！" if winner is None else f"{cfg.PLAYER_NAMES[winner]} 拿下本回合！"
        col = cfg.C_GOLD if winner is None else cfg.PLAYER_COLORS[winner]
        R.draw_text(self.screen, title, 52, cfg.SCREEN_W // 2, 40, col, center=True)
        R.draw_text(self.screen,
                    f"回合 {self.round_index + 1}/{cfg.ROUNDS_PER_MATCH}　"
                    f"總比分 {self.round_wins[0]} - {self.round_wins[1]}",
                    24, cfg.SCREEN_W // 2, 104, cfg.C_DIM, center=True)

        rows = [
            ("完賽時間", lambda s: f"{s.finish_time:.2f}s" if s.finish_time else "未完賽"),
            ("金幣", lambda s: str(s.coins)),
            ("翻滾", lambda s: str(s.flips)),
            ("墜毀", lambda s: str(s.crashes)),
            ("最高時速", lambda s: f"{s.top_speed:.0f} km/h"),
            ("滯空時間", lambda s: f"{s.air_time:.1f}s"),
            ("加速板", lambda s: str(s.boosts)),
            ("本回合分數", lambda s: f"{s.score:,}"),
        ]
        x0, x1, x2 = cfg.SCREEN_W // 2, cfg.SCREEN_W // 2 - 300, cfg.SCREEN_W // 2 + 300
        R.draw_text(self.screen, cfg.PLAYER_NAMES[0], 26, x1, 150, cfg.PLAYER_COLORS[0],
                    center=True)
        R.draw_text(self.screen, cfg.PLAYER_NAMES[1], 26, x2, 150, cfg.PLAYER_COLORS[1],
                    center=True)
        for i, (label, fn) in enumerate(rows):
            y = 192 + i * 38
            R.draw_text(self.screen, label, 22, x0, y, cfg.C_DIM, center=True)
            R.draw_text(self.screen, fn(self.bikes[0].stats), 22, x1, y, cfg.C_WHITE,
                        center=True)
            R.draw_text(self.screen, fn(self.bikes[1].stats), 22, x2, y, cfg.C_WHITE,
                        center=True)

        if self.new_achievements:
            R.draw_text(self.screen, "解鎖成就", 24, cfg.SCREEN_W // 2, 508, cfg.C_GOLD,
                        center=True)
            for k, (pi, ach) in enumerate(self.new_achievements[:3]):
                R.draw_text(self.screen,
                            f"★ {cfg.PLAYER_NAMES[pi]}：{ach.name} — {ach.desc}",
                            20, cfg.SCREEN_W // 2, 542 + k * 28,
                            cfg.PLAYER_COLORS[pi], center=True)

        nxt = "查看最終結果" if self.round_index + 1 >= cfg.ROUNDS_PER_MATCH else "下一回合"
        R.draw_text(self.screen, f"[空白鍵] {nxt}　　[ESC] 回主選單", 26,
                    cfg.SCREEN_W // 2, cfg.SCREEN_H - 44, cfg.C_GOLD, center=True)

    def draw_match_end(self) -> None:
        self.screen.fill(cfg.C_PANEL)
        champ = getattr(self, "match_champion", 0)
        R.draw_text(self.screen, "對決結束", 56, cfg.SCREEN_W // 2, 80, cfg.C_WHITE,
                    center=True)
        R.draw_text(self.screen, f"{cfg.PLAYER_NAMES[champ]} 獲勝！", 72,
                    cfg.SCREEN_W // 2, 180, cfg.PLAYER_COLORS[champ], center=True)
        R.draw_text(self.screen,
                    f"回合比分　{self.round_wins[0]} - {self.round_wins[1]}",
                    34, cfg.SCREEN_W // 2, 290, cfg.C_GOLD, center=True)
        R.draw_text(self.screen,
                    f"總積分　P1 {self.match_scores[0]:,}　　P2 {self.match_scores[1]:,}",
                    28, cfg.SCREEN_W // 2, 350, cfg.C_GREEN, center=True)
        if self.new_achievements:
            for k, (pi, ach) in enumerate(self.new_achievements[-3:]):
                R.draw_text(self.screen, f"★ {cfg.PLAYER_NAMES[pi]}：{ach.name}",
                            22, cfg.SCREEN_W // 2, 420 + k * 30,
                            cfg.PLAYER_COLORS[pi], center=True)
        R.draw_text(self.screen, "[空白鍵] 回主選單", 30, cfg.SCREEN_W // 2,
                    cfg.SCREEN_H - 80, cfg.C_GOLD, center=True)


def main() -> None:
    Game().run()
