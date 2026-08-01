"""主遊戲流程：狀態機、輸入、賽事邏輯與畫面組裝。"""
from __future__ import annotations

import math
import random

import pygame

from . import config as cfg
from . import render as R
from .achievements import ACHIEVEMENTS, SaveData
from .ai import AIRider, DIFFICULTIES, DIFFICULTY_ORDER, make_ai_name
from .audio import Audio
from .entities import Bike
from .terrain import Terrain

MENU, COUNTDOWN, RACING, ROUND_END, MATCH_END, ACHIEVE_VIEW, PAUSED = range(7)

MODE_1P = "1p"
MODE_2P = "2p"

MENU_ITEMS = ["單人模式（對戰電腦）", "雙人模式（同機對戰）", "成就一覽", "離開遊戲"]

CONTROLS = [
    # (label, gas, brake, lean_back, lean_fwd, nitro)
    ("玩家 1", pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d, pygame.K_LCTRL),
    ("玩家 2", pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_SLASH),
]
NITRO_ALT = {0: (), 1: (pygame.K_KP_DIVIDE,)}

FINISH_GRACE = 15.0     # 首位完賽後，另一位玩家的寬限秒數


class Game:
    def __init__(self, audio_enabled: bool = True) -> None:
        pygame.init()
        pygame.display.set_caption(cfg.TITLE)
        self.fullscreen = False
        self.screen = self._apply_display_mode(False)
        self.clock = pygame.time.Clock()
        self._splash("音效合成中…")
        self.save = SaveData()
        self.audio = Audio(audio_enabled)
        if self.save.data.get("muted"):
            self.audio.toggle_mute()
        if self.save.data.get("fullscreen"):
            self.set_fullscreen(True)
        self.views = [
            pygame.Surface((cfg.VIEW_W, cfg.VIEW_H)),
            pygame.Surface((cfg.VIEW_W, cfg.VIEW_H)),
        ]
        self.skies = [
            R.make_sky(cfg.VIEW_W, cfg.VIEW_H, False),
            R.make_sky(cfg.VIEW_W, cfg.VIEW_H, True),
        ]
        self.full_view = pygame.Surface((cfg.SCREEN_W, cfg.SCREEN_H))
        self.full_sky = R.make_sky(cfg.SCREEN_W, cfg.SCREEN_H, False)
        self.state = MENU
        self.prev_state = MENU
        self.running = True
        self.t = 0.0

        self.mode = MODE_2P
        self.difficulty = "normal"
        self.menu_index = 0
        self.ai: AIRider | None = None

        self.terrain: Terrain | None = None
        self.bikes: list[Bike] = []
        self.cams = [0.0, 0.0]
        self.cam_y = [0.0, 0.0]
        self.round_index = 0
        self.round_wins = [0, 0]
        self.match_scores = [0, 0]
        self.race_time = 0.0
        self.countdown = 0.0
        self.countdown_mark = 4
        self.finish_deadline: float | None = None
        self.round_result: dict | None = None
        self.new_achievements: list = []
        self.ob_cooldown: dict[tuple[int, int], float] = {}
        self.achieve_scroll = 0
        self.audio.play_music("menu")

    def _apply_display_mode(self, fullscreen: bool) -> pygame.Surface:
        """建立顯示視窗。全螢幕採獨佔模式並將解析度切至 1280x720。

        若顯示器不支援該解析度，改用 SCALED 全螢幕（維持 1280x720 邏輯畫布，
        由 SDL 等比縮放），確保不會因模式切換失敗而崩潰。
        """
        size = (cfg.SCREEN_W, cfg.SCREEN_H)
        if fullscreen:
            attempts = [pygame.FULLSCREEN, pygame.FULLSCREEN | pygame.SCALED]
        else:
            attempts = [0]
        surf = None
        for flags in attempts:
            try:
                surf = pygame.display.set_mode(size, flags)
                break
            except pygame.error:
                continue
        if surf is None:
            surf = pygame.display.set_mode(size)
            fullscreen = False
        self.fullscreen = fullscreen
        # 關閉 SDL 文字輸入，避免中文輸入法攔截空白鍵導致選單無反應
        try:
            pygame.key.stop_text_input()
        except AttributeError:
            pass
        pygame.mouse.set_visible(not fullscreen)
        return surf

    def set_fullscreen(self, fullscreen: bool) -> None:
        if fullscreen == self.fullscreen:
            return
        self.screen = self._apply_display_mode(fullscreen)
        self.save.data["fullscreen"] = self.fullscreen
        self.save.save()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.fullscreen)

    def _splash(self, text: str) -> None:
        self.screen.fill(cfg.C_PANEL)
        R.draw_text(self.screen, "MOTO DUEL", 64, cfg.SCREEN_W // 2,
                    cfg.SCREEN_H // 2 - 50, cfg.C_GOLD, center=True)
        R.draw_text(self.screen, text, 24, cfg.SCREEN_W // 2,
                    cfg.SCREEN_H // 2 + 30, cfg.C_DIM, center=True)
        pygame.display.flip()

    @property
    def player_count(self) -> int:
        return 1 if self.mode == MODE_1P else 2

    # ============================================================== 賽事建立
    def start_match(self, mode: str = MODE_2P) -> None:
        self.mode = mode
        self.round_index = 0
        self.round_wins = [0, 0]
        self.match_scores = [0, 0]
        self.start_round()

    def start_round(self) -> None:
        seed = random.randrange(1, 10_000_000)
        self.terrain = Terrain(seed)
        self.bikes = [Bike(0, self.terrain), Bike(1, self.terrain)]
        if self.mode == MODE_1P:
            self.bikes[1].name = make_ai_name(self.difficulty)
            self.ai = AIRider(self.bikes[1], self.terrain, self.difficulty, seed)
        else:
            self.ai = None
        # 落後補償：上一回合輸的一方獲得起始氮氣加成
        if self.round_index > 0 and self.round_wins[0] != self.round_wins[1]:
            loser = 0 if self.round_wins[0] < self.round_wins[1] else 1
            self.bikes[loser].nitro = min(
                cfg.NITRO_MAX, self.bikes[loser].nitro + cfg.COMEBACK_NITRO
            )
            self.bikes[loser].add_float("追趕補給！", cfg.C_CYAN)
        for i, b in enumerate(self.bikes):
            view_w = cfg.SCREEN_W if self.mode == MODE_1P else cfg.VIEW_W
            view_h = cfg.SCREEN_H if self.mode == MODE_1P else cfg.VIEW_H
            self.cams[i] = b.x - view_w * 0.34
            self.cam_y[i] = b.y - view_h * 0.55
        self.race_time = 0.0
        self.countdown = 3.2
        self.countdown_mark = 4
        self.finish_deadline = None
        self.round_result = None
        self.new_achievements = []
        self.ob_cooldown.clear()
        self.state = COUNTDOWN
        self.audio.play_music("race")

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
        """處理事件。

        中文輸入法（IME）啟用時，Windows 可能吃掉空白鍵而只送出 TEXTINPUT，
        因此保留一條備援路徑，把「輸入了一個空白字元」視為按下空白鍵。
        """
        space_from_key = False
        pending_text_space = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_SPACE:
                    space_from_key = True
                self.on_key(e.key, e.mod)
            elif e.type == pygame.TEXTINPUT and e.text and e.text.strip(" \u3000") == "":
                pending_text_space = True
        if pending_text_space and not space_from_key:
            self.on_key(pygame.K_SPACE)

    def on_key(self, key: int, mod: int = 0) -> None:
        # F11 或 Alt+Enter 切換全螢幕
        if key == pygame.K_F11 or (key in (pygame.K_RETURN, pygame.K_KP_ENTER)
                                   and mod & pygame.KMOD_ALT):
            self.toggle_fullscreen()
            self.audio.play("ui")
            return

        if key == pygame.K_m:
            muted = self.audio.toggle_mute()
            if not muted:
                self.audio.play("ui")
            self.save.data["muted"] = muted
            self.save.save()
            return

        if key == pygame.K_ESCAPE:
            if self.state in (MENU,):
                self.running = False
            elif self.state in (ACHIEVE_VIEW, PAUSED):
                self.audio.play("ui")
                self.state = self.prev_state
            else:
                self.audio.play("ui")
                self.audio.stop_engine()
                self.audio.play_music("menu")
                self.state = MENU
            return

        if self.state == MENU:
            self.menu_key(key)
        elif self.state == ACHIEVE_VIEW:
            if key in (pygame.K_a, pygame.K_SPACE):
                self.audio.play("ui")
                self.state = self.prev_state
        elif self.state in (COUNTDOWN, RACING):
            if key == pygame.K_p:
                self.prev_state = self.state
                self.state = PAUSED
                self.audio.stop_engine()
                self.audio.play("ui")
            elif key == pygame.K_r:
                self.start_round()
        elif self.state == PAUSED:
            if key == pygame.K_p:
                self.audio.play("ui")
                self.state = self.prev_state
        elif self.state == ROUND_END:
            if key == pygame.K_SPACE:
                self.audio.play("ui")
                self.round_index += 1
                if self.round_index >= cfg.ROUNDS_PER_MATCH:
                    self.finish_match()
                else:
                    self.start_round()
        elif self.state == MATCH_END:
            if key == pygame.K_SPACE:
                self.audio.play("ui")
                self.audio.play_music("menu")
                self.state = MENU

    def menu_key(self, key: int) -> None:
        if key in (pygame.K_UP, pygame.K_w):
            self.menu_index = (self.menu_index - 1) % len(MENU_ITEMS)
            self.audio.play("ui")
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.menu_index = (self.menu_index + 1) % len(MENU_ITEMS)
            self.audio.play("ui")
        elif key in (pygame.K_LEFT, pygame.K_a) and self.menu_index == 0:
            i = DIFFICULTY_ORDER.index(self.difficulty)
            self.difficulty = DIFFICULTY_ORDER[(i - 1) % len(DIFFICULTY_ORDER)]
            self.audio.play("ui")
        elif key in (pygame.K_RIGHT, pygame.K_d) and self.menu_index == 0:
            i = DIFFICULTY_ORDER.index(self.difficulty)
            self.difficulty = DIFFICULTY_ORDER[(i + 1) % len(DIFFICULTY_ORDER)]
            self.audio.play("ui")
        elif key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.audio.play("ui")
            if self.menu_index == 0:
                self.start_match(MODE_1P)
            elif self.menu_index == 1:
                self.start_match(MODE_2P)
            elif self.menu_index == 2:
                self.prev_state = MENU
                self.achieve_scroll = 0
                self.state = ACHIEVE_VIEW
            else:
                self.running = False

    def read_inputs(self, dt: float = 1 / 60) -> list[dict]:
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
        if self.mode == MODE_1P:
            # 單人模式：玩家 1 沿用方向鍵之外的配置，並額外接受方向鍵操作
            p1 = out[0]
            p1["gas"] = p1["gas"] or keys[pygame.K_UP]
            p1["brake"] = p1["brake"] or keys[pygame.K_DOWN]
            p1["lean_back"] = p1["lean_back"] or keys[pygame.K_LEFT]
            p1["lean_fwd"] = p1["lean_fwd"] or keys[pygame.K_RIGHT]
            p1["nitro"] = p1["nitro"] or keys[pygame.K_RCTRL]
            out[1] = self.ai.think(dt) if self.ai else dict(out[1])
        return out

    # ============================================================== 更新
    def update(self, dt: float) -> None:
        if self.state == COUNTDOWN:
            self.countdown -= dt
            mark = int(math.ceil(self.countdown))
            if mark < self.countdown_mark:
                self.countdown_mark = mark
                self.audio.play("beep" if mark >= 1 else "go")
            for i, b in enumerate(self.bikes):
                self._update_camera(i, b, dt * 4)
            if self.countdown <= 0:
                self.countdown = 0.0
                self.state = RACING
            self._update_engine_audio()
        elif self.state == RACING:
            self.update_race(dt)
            self._update_engine_audio()

    def _update_engine_audio(self) -> None:
        if not self.bikes:
            self.audio.stop_engine()
            return
        b = self.bikes[0]
        ratio = max(0.0, min(1.0, b.vx / cfg.MAX_SPEED))
        self.audio.update_engine(ratio, b.throttle_on, not b.finished and not b.crashed)

    def _consume_events(self) -> None:
        for i, bike in enumerate(self.bikes):
            vol = 1.0 if i == 0 or self.mode == MODE_2P else 0.55
            for name in bike.events:
                self.audio.play(name, vol)
            bike.events.clear()

    def update_race(self, dt: float) -> None:
        self.race_time += dt
        inputs = self.read_inputs(dt)

        for i, bike in enumerate(self.bikes):
            bike.update(dt, inputs[i], self.race_time)
            self.handle_collisions(i, bike, dt)
            self.check_finish(i, bike)
            self._update_camera(i, bike, dt)
        self._consume_events()

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
        view_w = cfg.SCREEN_W if self.mode == MODE_1P else cfg.VIEW_W
        view_h = cfg.SCREEN_H if self.mode == MODE_1P else cfg.VIEW_H
        target_x = bike.x - view_w * 0.34
        target_y = bike.y - view_h * 0.55
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
                    if bike.speed > cfg.ROCK_SAFE_SPEED and bike.shield_timer <= 0:
                        bike.crash("撞上巨石！")
                    elif bike.shield_timer > 0:
                        bike.add_float("護盾撞碎巨石", cfg.C_PURPLE)
                        bike.emit(10, (140, 140, 150))
                    else:
                        bike.vx *= 0.35
                        bike.vy = min(bike.vy, -160)
                        bike.add_float("顛簸！", cfg.C_DIM)
                        bike.events.append("thud")
            elif ob.kind == "spike":
                if bike.y > top:
                    self.ob_cooldown[key] = 1.2
                    if bike.shield_timer > 0:
                        bike.shield_timer = 0.0
                        bike.add_float("護盾破裂！", cfg.C_PURPLE)
                        bike.events.append("thud")
                    elif bike.speed > cfg.SPIKE_SAFE_SPEED:
                        bike.crash("尖刺陷阱！")
                    else:
                        bike.vx *= 0.3
                        bike.vy = min(bike.vy, -120)
                        bike.stats.score += cfg.SCORE_SPIKE_GRAZE
                        bike.add_float(f"擦過尖刺 {cfg.SCORE_SPIKE_GRAZE}", cfg.C_RED)
                        bike.emit(8, cfg.C_RED, spread=120, up=90)
                        bike.events.append("thud")
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
                bike.events.append("coin")
            elif pk.kind == "nitro":
                bike.nitro = min(cfg.NITRO_MAX, bike.nitro + cfg.NITRO_PICKUP)
                bike.add_float("氮氣 +45", cfg.C_CYAN)
                bike.emit(6, cfg.C_CYAN, spread=90, up=90)
                bike.events.append("nitro_pickup")
            else:
                bike.shield_timer = cfg.SHIELD_TIME
                bike.stats.shields += 1
                bike.add_float("護盾啟動！", cfg.C_PURPLE)
                bike.emit(8, cfg.C_PURPLE, spread=110, up=110)
                bike.events.append("shield")

    def check_finish(self, idx: int, bike: Bike) -> None:
        if bike.finished or bike.x < cfg.TRACK_LENGTH:
            return
        bike.finished = True
        bike.stats.finish_time = self.race_time
        bonus = int(max(0.0, cfg.PAR_TIME - self.race_time) * cfg.SCORE_FINISH_TIME_BONUS)
        bike.stats.score += bonus
        bike.add_float(f"完賽！ +{bonus}", cfg.C_GOLD)
        bike.events.append("finish")
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
            if self.mode == MODE_1P and i == 1:
                continue    # 電腦對手不解成就
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
        self.audio.stop_engine()
        self.audio.play_music("menu")
        if self.new_achievements:
            self.audio.play("unlock")

    def finish_match(self) -> None:
        w0, w1 = self.round_wins
        if w0 == w1:
            champ = 0 if self.match_scores[0] >= self.match_scores[1] else 1
        else:
            champ = 0 if w0 > w1 else 1
        if max(w0, w1) == cfg.ROUNDS_PER_MATCH:
            if not (self.mode == MODE_1P and champ == 1):
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
        self.audio.stop_engine()
        self.audio.play_music("menu")
        self.audio.play("finish")

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
        if self.mode == MODE_1P:
            self.draw_race_solo()
        else:
            self.draw_race_split()

    def draw_race_solo(self) -> None:
        assert self.terrain is not None
        view = self.full_view
        bike, rival = self.bikes[0], self.bikes[1]
        view.blit(self.full_sky, (0, 0))
        cam_x, cam_y = self.cams[0], self.cam_y[0]
        R.draw_parallax(view, cam_x, cfg.SCREEN_H)
        R.draw_terrain(view, self.terrain, cam_x, cam_y)
        R.draw_obstacles(view, self.terrain, cam_x, cam_y)
        R.draw_pickups(view, self.terrain, cam_x, cam_y, 0, self.t)
        R.draw_finish(view, self.terrain, cam_x, cam_y)
        if abs(rival.x - bike.x) < cfg.SCREEN_W:
            R.draw_bike(view, rival, cam_x, cam_y)
            R.draw_particles(view, rival, cam_x, cam_y)
        R.draw_particles(view, bike, cam_x, cam_y)
        R.draw_bike(view, bike, cam_x, cam_y)
        R.draw_floats(view, bike, cam_x, cam_y)
        self.draw_offscreen_arrow(view, bike, rival, cam_x)
        R.draw_hud(view, bike, rival, self.race_time, self.rank_of(0), self.countdown)
        self.screen.blit(view, (0, 0))
        R.draw_text(self.screen,
                    f"回合 {self.round_index + 1}/{cfg.ROUNDS_PER_MATCH}   "
                    f"{self.round_wins[0]} - {self.round_wins[1]}   "
                    f"對手：{rival.name}",
                    20, cfg.SCREEN_W // 2, cfg.SCREEN_H - 34, cfg.C_DIM, center=True)
        self.draw_mute_badge()

    def draw_mute_badge(self) -> None:
        if self.audio.muted:
            R.draw_text(self.screen, "[ 靜音中 - 按 M 開啟 ]", 18, 16,
                        cfg.SCREEN_H - 30, cfg.C_DIM)

    def draw_race_split(self) -> None:
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
        self.draw_mute_badge()

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
        vw, vh = view.get_width(), view.get_height()
        if 0 <= sx <= vw:
            return
        y = vh // 2
        if sx < 0:
            pts = [(28, y), (58, y - 16), (58, y + 16)]
            tx = 74
        else:
            pts = [(vw - 28, y), (vw - 58, y - 16), (vw - 58, y + 16)]
            tx = vw - 92
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
        veil.fill((8, 10, 18, 165))
        self.screen.blit(veil, (0, 0))

        R.draw_text(self.screen, "MOTO DUEL", 76, cfg.SCREEN_W // 2, 32, cfg.C_GOLD, center=True)
        R.draw_text(self.screen, "越野機車對決　單人 / 雙人", 26, cfg.SCREEN_W // 2, 106,
                    cfg.C_WHITE, center=True)

        # ---- 選單項目
        mx, my = 90, 168
        for i, label in enumerate(MENU_ITEMS):
            sel = i == self.menu_index
            box = pygame.Rect(mx, my + i * 62, 480, 52)
            pygame.draw.rect(self.screen, (26, 30, 48) if sel else (16, 18, 28), box,
                             border_radius=10)
            pygame.draw.rect(self.screen, cfg.C_GOLD if sel else (46, 50, 66), box,
                             3 if sel else 2, border_radius=10)
            col = cfg.C_GOLD if sel else cfg.C_WHITE
            R.draw_text(self.screen, ("> " if sel else "   ") + label, 26,
                        mx + 18, my + i * 62 + 12, col)
        # 難度列（僅單人）
        d = DIFFICULTIES[self.difficulty]
        dsel = self.menu_index == 0
        R.draw_text(self.screen, f"<  電腦難度：{d['name']}  >", 24, mx + 18,
                    my + len(MENU_ITEMS) * 62 + 14,
                    cfg.C_CYAN if dsel else (70, 74, 92))
        R.draw_text(self.screen, d["hint"], 19, mx + 18,
                    my + len(MENU_ITEMS) * 62 + 46,
                    cfg.C_DIM if dsel else (60, 64, 80))

        # ---- 操作說明
        cols = [
            (700, cfg.PLAYER_COLORS[0], "玩家 1",
             ["W / ↑　油門", "S / ↓　煞車", "A D / ← →　後傾 前傾", "左Ctrl / 右Ctrl　氮氣"]),
            (990, cfg.PLAYER_COLORS[1], "玩家 2（雙人）",
             ["↑　油門", "↓　煞車", "← / →　後傾 / 前傾", "/　氮氣"]),
        ]
        for x, color, title, lines in cols:
            pygame.draw.rect(self.screen, (14, 16, 26), (x - 18, 168, 272, 190),
                             border_radius=12)
            pygame.draw.rect(self.screen, color, (x - 18, 168, 272, 190), 3, border_radius=12)
            R.draw_text(self.screen, title, 24, x, 180, color)
            for k, line in enumerate(lines):
                R.draw_text(self.screen, line, 19, x, 218 + k * 32, cfg.C_WHITE)

        tips = [
            "空中用左右鍵翻滾可獲得分數與氮氣，落地角度不對會摔車。",
            "加速板衝刺、金幣加分、氮氣罐補給、護盾擋一次傷害。",
            "高速撞巨石或尖刺會摔車，減速通過只顛簸扣分。",
            "三回合制對決，取得最多回合勝利者獲勝。",
        ]
        for k, line in enumerate(tips):
            R.draw_text(self.screen, line, 18, 686, 388 + k * 28, cfg.C_DIM)

        d2 = self.save.data
        best = f"{d2['best_time']:.2f}s" if d2.get("best_time") else "—"
        R.draw_text(self.screen,
                    f"總場次 {d2['total_races']}　勝場 P1 {d2['total_wins'][0]} / P2 {d2['total_wins'][1]}"
                    f"　最佳單場分數 {d2['best_score']:,}　最速完賽 {best}"
                    f"　成就 {len(self.save.unlocked)}/{len(ACHIEVEMENTS)}",
                    20, cfg.SCREEN_W // 2, 560, cfg.C_GREEN, center=True)

        blink = 0.5 + 0.5 * math.sin(self.t * 4)
        col = tuple(int(c * (0.55 + 0.45 * blink)) for c in cfg.C_GOLD)
        R.draw_text(self.screen, "[↑↓] 選擇　[←→] 難度　[空白鍵] 確認　[ESC] 離開",
                    28, cfg.SCREEN_W // 2, 610, col, center=True)
        mute = "靜音中" if self.audio.muted else "開啟"
        scr = "全螢幕" if self.fullscreen else "視窗"
        R.draw_text(self.screen,
                    f"賽中：[P] 暫停　[R] 重跑本回合　[M] 音效（{mute}）"
                    f"　[F11] 全螢幕（目前：{scr}）", 18,
                    cfg.SCREEN_W // 2, 664, cfg.C_DIM, center=True)

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
    def pname(self, i: int) -> str:
        if self.mode == MODE_1P and i < len(self.bikes):
            return self.bikes[i].name
        return cfg.PLAYER_NAMES[i]

    def draw_round_end(self) -> None:
        veil = pygame.Surface((cfg.SCREEN_W, cfg.SCREEN_H), pygame.SRCALPHA)
        veil.fill((6, 8, 14, 225))
        self.screen.blit(veil, (0, 0))
        board = pygame.Rect(cfg.SCREEN_W // 2 - 430, 8, 860, cfg.SCREEN_H - 72)
        pygame.draw.rect(self.screen, (14, 17, 28), board, border_radius=16)
        pygame.draw.rect(self.screen, (52, 58, 80), board, 3, border_radius=16)
        winner = self.round_result["winner"] if self.round_result else None
        title = "平手！" if winner is None else f"{self.pname(winner)} 拿下本回合！"
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
        R.draw_text(self.screen, self.pname(0), 26, x1, 150, cfg.PLAYER_COLORS[0],
                    center=True)
        R.draw_text(self.screen, self.pname(1), 26, x2, 150, cfg.PLAYER_COLORS[1],
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
                            f"★ {self.pname(pi)}：{ach.name} — {ach.desc}",
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
        R.draw_text(self.screen, f"{self.pname(champ)} 獲勝！", 72,
                    cfg.SCREEN_W // 2, 180, cfg.PLAYER_COLORS[champ], center=True)
        R.draw_text(self.screen,
                    f"回合比分　{self.round_wins[0]} - {self.round_wins[1]}",
                    34, cfg.SCREEN_W // 2, 290, cfg.C_GOLD, center=True)
        R.draw_text(self.screen,
                    f"總積分　{self.pname(0)} {self.match_scores[0]:,}　　"
                    f"{self.pname(1)} {self.match_scores[1]:,}",
                    28, cfg.SCREEN_W // 2, 350, cfg.C_GREEN, center=True)
        if self.new_achievements:
            for k, (pi, ach) in enumerate(self.new_achievements[-3:]):
                R.draw_text(self.screen, f"★ {self.pname(pi)}：{ach.name}",
                            22, cfg.SCREEN_W // 2, 420 + k * 30,
                            cfg.PLAYER_COLORS[pi], center=True)
        R.draw_text(self.screen, "[空白鍵] 回主選單", 30, cfg.SCREEN_W // 2,
                    cfg.SCREEN_H - 80, cfg.C_GOLD, center=True)


def main() -> None:
    Game().run()
