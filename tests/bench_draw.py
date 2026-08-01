"""繪製效能量測：分別測單人與雙人畫面的每幀繪製耗時。"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from motoduel import config as cfg      # noqa: E402
from motoduel.game import Game, MODE_1P, MODE_2P  # noqa: E402


def bench(g: Game, mode: int, label: str, frames: int = 240) -> None:
    g.start_match(mode)
    g.state = 2  # RACING
    g.countdown = 0.0
    times = []
    dt = 1 / 60
    for _ in range(frames):
        g.t += dt
        for i, b in enumerate(g.bikes):
            b.update(dt, {"gas": True, "brake": False, "lean_back": False,
                          "lean_fwd": False, "nitro": True}, g.t)
            g.handle_collisions(i, b, dt)
            g._update_camera(i, b, dt)
        t0 = time.perf_counter()
        g.draw()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    print(f"{label}: 平均 {statistics.mean(times):.2f} ms  "
          f"中位 {statistics.median(times):.2f} ms  "
          f"p95 {times[int(len(times) * 0.95)]:.2f} ms  "
          f"→ 理論 {1000 / statistics.mean(times):.0f} FPS")


def main() -> None:
    import tempfile
    from motoduel.achievements import SaveData
    g = Game(audio_enabled=False)
    g.save = SaveData(Path(tempfile.mkdtemp()) / "save.json")
    bench(g, MODE_1P, "單人全螢幕視角")
    bench(g, MODE_2P, "雙人分割畫面")
    pygame.quit()


if __name__ == "__main__":
    main()
