"""無頭煙霧測試：模擬完整回合，驗證物理、互動與狀態機不會崩潰。"""
from __future__ import annotations

import os
import random
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motoduel import config as cfg          # noqa: E402
from motoduel.achievements import ACHIEVEMENTS, SaveData  # noqa: E402
from motoduel.ai import AIRider, DIFFICULTIES, DIFFICULTY_ORDER, make_ai_name  # noqa: E402
from motoduel.entities import Bike          # noqa: E402
from motoduel.game import Game              # noqa: E402
from motoduel.terrain import Terrain        # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"  [FAIL] {msg}")
    else:
        print(f"  [ok]   {msg}")


def test_terrain() -> None:
    print("\n== 地形生成 ==")
    t = Terrain(1234)
    check(len(t.heights) == t.count, "高度陣列長度正確")
    check(all(80 < h < 900 for h in t.heights), "高度值在合理範圍")
    slopes = [abs(t.slope_at(x)) for x in range(0, cfg.TRACK_LENGTH, 37)]
    check(max(slopes) <= cfg.MAX_TERRAIN_SLOPE + 0.05,
          f"最大斜率可通行 (max={max(slopes):.2f})")
    check(len(t.obstacles) > 10, f"障礙物數量 {len(t.obstacles)}")
    check(len(t.pickups) > 20, f"道具數量 {len(t.pickups)}")
    check(t.last_checkpoint(2500) <= 2500, "檢查點查詢正確")
    kinds = {o.kind for o in t.obstacles}
    check(kinds <= {"rock", "mud", "spike", "boost"}, f"障礙種類 {kinds}")


def test_physics_full_throttle() -> None:
    """全油門下 AI 應能在合理時間內跑完全程。"""
    print("\n== 物理 / 可完賽性 ==")
    for seed in (7, 99, 20260731):
        game = Game.__new__(Game)     # 不開視窗，僅測物理
        game.terrain = Terrain(seed)
        bikes = [Bike(0, game.terrain), Bike(1, game.terrain)]
        dt = 1 / 60
        t = 0.0
        rng = random.Random(seed)
        while t < 200 and not all(b.finished for b in bikes):
            t += dt
            for b in bikes:
                inp = {
                    "gas": True,
                    "brake": False,
                    "lean_back": (not b.on_ground) and rng.random() < 0.25,
                    "lean_fwd": False,
                    "nitro": b.nitro > 60,
                }
                b.update(dt, inp, t)
                if b.x >= cfg.TRACK_LENGTH and not b.finished:
                    b.finished = True
                    b.stats.finish_time = t
        for b in bikes:
            check(b.finished,
                  f"seed={seed} P{b.index + 1} 完賽於 {b.stats.finish_time or t:.1f}s")
            check(b.stats.top_speed > 60,
                  f"seed={seed} P{b.index + 1} 有速度 {b.stats.top_speed:.0f} km/h")


def test_interactions() -> None:
    print("\n== 互動：道具 / 障礙 / 墜毀 ==")
    game = Game.__new__(Game)
    game.terrain = Terrain(4242)
    game.ob_cooldown = {}
    game.bikes = [Bike(0, game.terrain), Bike(1, game.terrain)]
    b = game.bikes[0]

    coin = next(p for p in game.terrain.pickups if p.kind == "coin")
    b.x, b.y = coin.x, coin.y
    game.handle_collisions(0, b, 1 / 60)
    check(b.stats.coins == 1 and 0 in coin.taken_by, "金幣可拾取且不重複")
    game.handle_collisions(0, b, 1 / 60)
    check(b.stats.coins == 1, "同一金幣不會重複計分")

    nitro_pk = next((p for p in game.terrain.pickups if p.kind == "nitro"), None)
    if nitro_pk:
        b.nitro = 10.0
        b.x, b.y = nitro_pk.x, nitro_pk.y
        game.handle_collisions(0, b, 1 / 60)
        check(b.nitro > 10.0, "氮氣罐可補給")

    shield_pk = next((p for p in game.terrain.pickups if p.kind == "shield"), None)
    if shield_pk:
        b.x, b.y = shield_pk.x, shield_pk.y
        game.handle_collisions(0, b, 1 / 60)
        check(b.shield_timer > 0, "護盾可啟動")

    spike = next((o for o in game.terrain.obstacles if o.kind == "spike"), None)
    if spike:
        b2 = game.bikes[1]
        b2.x = spike.x
        b2.y = game.terrain.height_at(spike.x)
        b2.shield_timer = 0.0
        b2.vx = cfg.SPIKE_SAFE_SPEED + 120
        game.handle_collisions(1, b2, 1 / 60)
        check(b2.crashed, "高速撞尖刺造成墜毀")

        b2.crash_timer = 0.0
        b2.shield_timer = 0.0
        b2.vx = cfg.SPIKE_SAFE_SPEED - 200
        b2.vy = 0.0
        before = b2.stats.score
        game.ob_cooldown.clear()
        game.handle_collisions(1, b2, 1 / 60)
        check(not b2.crashed and b2.stats.score == before + cfg.SCORE_SPIKE_GRAZE,
              "低速擦過尖刺僅扣分")

        b2.crash_timer = 0.0
        b2.shield_timer = 3.0
        b2.vx = cfg.SPIKE_SAFE_SPEED + 120
        game.ob_cooldown.clear()
        game.handle_collisions(1, b2, 1 / 60)
        check(not b2.crashed and b2.shield_timer == 0, "護盾可抵擋尖刺一次")

    boost = next((o for o in game.terrain.obstacles if o.kind == "boost"), None)
    if boost:
        b3 = Bike(0, game.terrain)
        b3.x = boost.x
        b3.y = game.terrain.height_at(boost.x)
        b3.vx = 200
        game.ob_cooldown.clear()
        game.handle_collisions(0, b3, 1 / 60)
        check(b3.speed > 200 and b3.stats.boosts == 1, "加速板提供推進")

    b4 = Bike(1, game.terrain)
    b4.x = 3000
    b4.crash("測試")
    check(b4.crashed and b4.stats.crashes == 1, "墜毀計數正確")
    for _ in range(int(cfg.CRASH_TIME * 60) + 5):
        b4.update(1 / 60, {"gas": False, "brake": False, "lean_back": False,
                           "lean_fwd": False, "nitro": False}, 0.0)
    check(not b4.crashed, "墜毀後可重生")
    check(b4.x <= 3000, "重生於先前檢查點")


def test_flips_and_score() -> None:
    print("\n== 翻滾與計分 ==")
    terrain = Terrain(55)
    b = Bike(0, terrain)
    b.on_ground = False
    b.y = terrain.height_at(b.x) - 700
    b.vx, b.vy = 400, -100
    b.shield_timer = 99   # 避免落地墜毀干擾翻滾結算
    inp = {"gas": False, "brake": False, "lean_back": False, "lean_fwd": True,
           "nitro": False}
    for _ in range(600):
        b.update(1 / 60, inp, 0.0)
        if b.on_ground:
            break
    check(b.stats.flips >= 1, f"完成翻滾 {b.stats.flips} 次")
    check(b.stats.score >= cfg.SCORE_FLIP, f"翻滾得分 {b.stats.score}")


def test_achievements() -> None:
    print("\n== 成就系統 ==")
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        save = SaveData(Path(d) / "save.json")
        ctx = {
            "won": True, "coins": 45, "flips": 6, "crashes": 0, "top_speed": 320,
            "nitro_time": 18, "boosts": 14, "shields": 1, "finish_time": 50.0,
            "comeback": True, "score": 25000, "air_time": 10, "best_flip_combo": 2,
            "total_races": 12, "total_wins": 6, "total_coins": 200,
            "total_flips": 40, "perfect_match": True,
        }
        gained = save.evaluate(ctx)
        check(len(gained) == len(ACHIEVEMENTS),
              f"全成就可解鎖 ({len(gained)}/{len(ACHIEVEMENTS)})")
        check(save.evaluate(ctx) == [], "已解鎖成就不重複觸發")
        save.save()
        reloaded = SaveData(Path(d) / "save.json")
        check(len(reloaded.unlocked) == len(ACHIEVEMENTS), "存檔可正確讀回")

        empty = SaveData(Path(d) / "missing.json")
        check(empty.data["total_races"] == 0, "缺少存檔時使用預設值")


def _rush_to_finish(game: Game) -> None:
    for _ in range(int(4 * 60)):
        game.update(1 / 60)
    for b in game.bikes:
        b.x = cfg.TRACK_LENGTH - 40
        b.y = game.terrain.height_at(b.x) - cfg.RIDE_HEIGHT
        b.vx = 600
    for _ in range(600):
        game.update(1 / 60)
        if game.state == 3:
            break


def test_round_flow() -> None:
    print("\n== 賽事流程（狀態機） ==")
    import tempfile
    from pathlib import Path
    game = Game()
    with tempfile.TemporaryDirectory() as d:
        game.save = SaveData(Path(d) / "save.json")
        game.start_match()
        check(game.state == 1, "開賽進入倒數狀態")
        for _ in range(int(4 * 60)):
            game.update(1 / 60)
            if game.state == 2:
                break
        check(game.state == 2, "倒數結束進入比賽")

        for b in game.bikes:
            b.x = cfg.TRACK_LENGTH - 40
            b.y = game.terrain.height_at(b.x) - cfg.RIDE_HEIGHT
            b.vx = 600
        for _ in range(600):
            game.update(1 / 60)
            if game.state == 3:
                break
        check(game.state == 3, "雙方完賽後進入回合結算")
        check(sum(game.round_wins) == 1, "回合勝場已計入")
        game.draw()
        check(True, "回合結算畫面繪製正常")

        guard = 0
        while game.state != 4 and guard < 10:
            guard += 1
            game.on_key(pygame.K_SPACE)
            if game.state == 1:
                _rush_to_finish(game)
        check(game.state == 4, "三回合後進入對決結算")
        check(sum(game.round_wins) == cfg.ROUNDS_PER_MATCH, "共進行三回合")
        game.draw()
        check(True, "對決結算畫面繪製正常")
        game.on_key(pygame.K_SPACE)
        check(game.state == 0, "結算後可回主選單")


def test_render_frames() -> None:
    print("\n== 畫面繪製 ==")
    game = Game()
    game.draw()
    check(True, "主選單繪製正常")

    game.menu_index = 2
    game.on_key(pygame.K_SPACE)
    game.draw()
    check(game.state == 5, "成就畫面繪製正常")
    game.on_key(pygame.K_SPACE)
    check(game.state == 0, "成就畫面可返回")

    # 雙人分割畫面
    game.menu_index = 1
    game.on_key(pygame.K_SPACE)
    check(game.mode == "2p", "選單可進入雙人模式")
    for _ in range(300):
        game.update(1 / 60)
        game.draw()
    check(True, "雙人賽中畫面連續 300 幀繪製正常")
    game.on_key(pygame.K_p)
    game.draw()
    check(game.state == 6, "暫停畫面繪製正常")

    # 單人全螢幕畫面
    game.on_key(pygame.K_p)
    game.on_key(pygame.K_ESCAPE)
    game.menu_index = 0
    game.on_key(pygame.K_SPACE)
    check(game.mode == "1p" and game.ai is not None, "選單可進入單人模式")
    for _ in range(300):
        game.update(1 / 60)
        game.draw()
    check(True, "單人賽中畫面連續 300 幀繪製正常")
    check(game.bikes[1].x > 300, "單人模式電腦對手會前進")


def test_menu_navigation() -> None:
    print("\n== 選單操作 ==")
    game = Game()
    game.menu_index = 0
    game.on_key(pygame.K_DOWN)
    check(game.menu_index == 1, "↓ 可切換選項")
    game.on_key(pygame.K_UP)
    check(game.menu_index == 0, "↑ 可切換選項")
    before = game.difficulty
    game.on_key(pygame.K_RIGHT)
    check(game.difficulty != before, "→ 可切換難度")
    game.on_key(pygame.K_LEFT)
    check(game.difficulty == before, "← 可切回難度")
    for key in DIFFICULTY_ORDER:
        check(key in DIFFICULTIES and "name" in DIFFICULTIES[key]
              and "hint" in DIFFICULTIES[key], f"難度 {key} 設定完整")


def test_ai_riders() -> None:
    print("\n== 電腦對手 ==")
    from motoduel.terrain import Terrain
    for diff in DIFFICULTY_ORDER:
        times = []
        crashes = []
        for seed in (7, 99, 20260731):
            terrain = Terrain(seed)
            bike = Bike(1, terrain)
            ai = AIRider(bike, terrain, diff, seed)
            t = 0.0
            for _ in range(int(120 * 60)):
                t += 1 / 60
                bike.update(1 / 60, ai.think(1 / 60), t)
                bike.events.clear()
                if bike.x >= cfg.TRACK_LENGTH:
                    break
            times.append(t)
            crashes.append(bike.stats.crashes)
        done = all(x < 119 for x in times)
        avg = sum(times) / len(times)
        check(done, f"{diff} 電腦可完賽（平均 {avg:.1f}s）")
        check(max(crashes) <= 6, f"{diff} 電腦墜毀次數合理（最多 {max(crashes)}）")
    check(True, "AI 名稱可產生：" + make_ai_name("hard"))


def test_audio() -> None:
    print("\n== 音訊系統 ==")
    from motoduel import audio as A
    a = A.Audio(True)
    check(isinstance(a.ok, bool), "Audio 初始化不崩潰")
    if a.ok:
        check(len(a.sfx) >= 13, f"音效數量 {len(a.sfx)}")
        check(len(a.engines) >= 3, f"引擎音階數 {len(a.engines)}")
        for name in ("race", "menu"):
            check(name in a.music, f"背景音樂 {name} 已生成")
        check(a.music["race"].get_length() > 3.0, "比賽 BGM 長度合理")
    for name in ("coin", "crash", "flip", "boost", "finish", "ui", "unlock",
                 "beep", "go", "thud", "nitro_pickup", "shield", "land"):
        a.play(name)
        if a.ok:
            check(name in a.sfx, f"音效 {name} 已生成")
    a.play_music("race")
    for r in (0.0, 0.3, 0.6, 1.0):
        a.update_engine(r, True, True)
    a.update_engine(0.5, False, False)
    a.stop_engine()
    check(a.toggle_mute() is True, "可切換靜音")
    a.play("coin")
    a.play_music("menu")
    check(a.toggle_mute() is False, "可取消靜音")
    a.stop_music()
    check(True, "音訊 API 呼叫全程無例外")


def main() -> int:
    print("MOTO DUEL 煙霧測試")
    pygame.init()
    pygame.display.set_mode((cfg.SCREEN_W, cfg.SCREEN_H))
    test_terrain()
    test_physics_full_throttle()
    test_interactions()
    test_flips_and_score()
    test_achievements()
    test_round_flow()
    test_menu_navigation()
    test_ai_riders()
    test_audio()
    test_render_frames()
    print("\n" + "=" * 46)
    if FAILS:
        print(f"失敗 {len(FAILS)} 項：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("全部測試通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
