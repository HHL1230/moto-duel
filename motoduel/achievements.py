"""成就系統與存檔（分數紀錄、解鎖狀態）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SAVE_PATH = Path(__file__).resolve().parent.parent / "save_data.json"


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    desc: str
    check: Callable[[dict], bool]


# ctx 欄位說明（單場結算時提供）：
#   won, coins, flips, crashes, top_speed, nitro_time, boosts, shields,
#   finish_time, comeback, total_races, total_wins, total_coins, total_flips,
#   score, air_time, best_flip_combo, perfect_match
ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_blood", "初嚐勝果", "贏得第一場比賽",
                lambda c: c["won"]),
    Achievement("air_show", "空中特技", "單場完成 1 次以上翻滾",
                lambda c: c["flips"] >= 1),
    Achievement("flip_master", "翻滾大師", "單場累計完成 5 次翻滾",
                lambda c: c["flips"] >= 5),
    Achievement("combo_king", "連環翻滾", "一次滯空完成 2 圈以上翻滾",
                lambda c: c["best_flip_combo"] >= 2),
    Achievement("collector", "拾荒者", "單場收集 40 枚金幣",
                lambda c: c["coins"] >= 40),
    Achievement("nitro_junkie", "氮氣狂人", "單場使用氮氣累計 15 秒",
                lambda c: c["nitro_time"] >= 15),
    Achievement("flawless", "完美無瑕", "零墜毀完成一場比賽",
                lambda c: c["crashes"] == 0 and c["finish_time"] is not None),
    Achievement("speed_demon", "極速惡魔", "時速突破 300 km/h",
                lambda c: c["top_speed"] >= 300),
    Achievement("comeback_kid", "逆轉勝", "在賽程後段落後仍奪冠",
                lambda c: c["won"] and c["comeback"]),
    Achievement("ramp_rat", "跳台老鼠", "單場踩中 12 個加速板",
                lambda c: c["boosts"] >= 12),
    Achievement("survivor", "越野老手", "累計完成 10 場比賽",
                lambda c: c["total_races"] >= 10),
    Achievement("champion", "常勝軍", "累計贏得 5 場比賽",
                lambda c: c["total_wins"] >= 5),
    Achievement("under_90", "神速完賽", "在 55 秒內完賽",
                lambda c: c["finish_time"] is not None and c["finish_time"] <= 55),
    Achievement("high_score", "高分玩家", "單場得分達到 12000",
                lambda c: c["score"] >= 12000),
    Achievement("sweep", "橫掃千軍", "以 3:0 贏得整場對決",
                lambda c: c["perfect_match"]),
]

ACHIEVEMENT_BY_KEY = {a.key: a for a in ACHIEVEMENTS}

DEFAULT_SAVE = {
    "unlocked": [],          # 成就 key（雙人共用同一台電腦，故合併記錄）
    "total_races": 0,
    "total_wins": [0, 0],
    "total_coins": 0,
    "total_flips": 0,
    "best_score": 0,
    "best_time": None,
}


class SaveData:
    def __init__(self, path: Path = SAVE_PATH) -> None:
        self.path = path
        self.data = dict(DEFAULT_SAVE)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_SAVE)
            merged.update({k: v for k, v in raw.items() if k in DEFAULT_SAVE})
            if not isinstance(merged.get("total_wins"), list) or len(merged["total_wins"]) != 2:
                merged["total_wins"] = [0, 0]
            self.data = merged
        except (OSError, ValueError):
            self.data = dict(DEFAULT_SAVE)

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ------------------------------------------------------------ 查詢
    @property
    def unlocked(self) -> set[str]:
        return set(self.data.get("unlocked", []))

    def is_unlocked(self, key: str) -> bool:
        return key in self.unlocked

    # ------------------------------------------------------------ 更新
    def evaluate(self, ctx: dict) -> list[Achievement]:
        """依單場情境檢查成就，回傳本次新解鎖的清單。"""
        unlocked = self.unlocked
        gained: list[Achievement] = []
        for ach in ACHIEVEMENTS:
            if ach.key in unlocked:
                continue
            try:
                if ach.check(ctx):
                    gained.append(ach)
                    unlocked.add(ach.key)
            except (KeyError, TypeError):
                continue
        if gained:
            self.data["unlocked"] = sorted(unlocked)
        return gained

    def record_race(self, stats_list, winner: int | None) -> None:
        self.data["total_races"] = self.data.get("total_races", 0) + 1
        if winner is not None:
            wins = self.data["total_wins"]
            wins[winner] += 1
        for s in stats_list:
            self.data["total_coins"] = self.data.get("total_coins", 0) + s.coins
            self.data["total_flips"] = self.data.get("total_flips", 0) + s.flips
            self.data["best_score"] = max(self.data.get("best_score", 0), s.score)
            if s.finish_time is not None:
                best = self.data.get("best_time")
                if best is None or s.finish_time < best:
                    self.data["best_time"] = round(s.finish_time, 2)
