"""擷取畫面供人工檢視。"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motoduel import config as cfg   # noqa: E402
from motoduel.game import Game       # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pygame.init()
    pygame.display.set_mode((cfg.SCREEN_W, cfg.SCREEN_H))
    g = Game()
    # 使用暫存存檔，避免污染真實紀錄
    import tempfile
    from pathlib import Path
    from motoduel.achievements import SaveData
    tmp = tempfile.mkdtemp()
    g.save = SaveData(Path(tmp) / "save.json")

    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "01_menu.png"))

    g.menu_index = 0
    g.on_key(pygame.K_SPACE)
    for _ in range(int(12 * 60)):
        g.update(1 / 60)
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "02_solo_race.png"))

    for b in g.bikes:
        b.x = cfg.TRACK_LENGTH - 60
        b.y = g.terrain.height_at(b.x) - cfg.RIDE_HEIGHT
        b.vx = 620
    for _ in range(900):
        g.update(1 / 60)
        if g.state == 3:
            break
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "03_solo_round_end.png"))

    g.on_key(pygame.K_ESCAPE)
    g.menu_index = 1
    g.on_key(pygame.K_SPACE)
    for _ in range(int(12 * 60)):
        g.update(1 / 60)
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "04_split_race.png"))
    print("saved to", OUT)


if __name__ == "__main__":
    main()
