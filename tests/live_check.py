"""真實視窗 + 音效裝置的實機檢查：FPS、音訊通道狀態。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from motoduel import config as cfg   # noqa: E402
from motoduel.game import Game       # noqa: E402


def main() -> None:
    t0 = time.perf_counter()
    g = Game()
    print(f"初始化耗時 {time.perf_counter() - t0:.2f}s")
    print("mixer:", pygame.mixer.get_init())
    print("audio.ok:", g.audio.ok)
    import tempfile
    from pathlib import Path
    from motoduel.achievements import SaveData
    g.save = SaveData(Path(tempfile.mkdtemp()) / "save.json")

    g.draw()
    pygame.display.flip()
    print("menu BGM busy:", pygame.mixer.Channel(0).get_busy())

    g.menu_index = 0
    g.on_key(pygame.K_SPACE)
    frames = 0
    start = time.perf_counter()
    engine_seen = False
    while time.perf_counter() - start < 8.0:
        pygame.event.pump()
        dt = min(g.clock.tick(cfg.FPS) / 1000.0, 1 / 30)
        g.t += dt
        g.update(dt)
        g.draw()
        pygame.display.flip()
        frames += 1
        if pygame.mixer.get_init() and pygame.mixer.Channel(1).get_busy():
            engine_seen = True
    elapsed = time.perf_counter() - start
    print(f"FPS: {frames / elapsed:.1f}  ({frames} 幀 / {elapsed:.1f}s)")
    print("race BGM busy:", pygame.mixer.Channel(0).get_busy())
    print("engine channel busy:", engine_seen)
    print("玩家 x:", int(g.bikes[0].x), " 電腦 x:", int(g.bikes[1].x))
    g.audio.toggle_mute()
    print("靜音後 BGM busy:", pygame.mixer.Channel(0).get_busy())
    pygame.quit()


if __name__ == "__main__":
    main()
