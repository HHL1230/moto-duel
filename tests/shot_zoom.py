"""機車 / 場景近拍：把畫面局部放大存檔，方便人工檢視細節。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motoduel import config as cfg      # noqa: E402
from motoduel.game import Game, MODE_1P  # noqa: E402

OUT = Path(__file__).resolve().parent / "_shots"


def crop(surf, cx, cy, w, h, scale, name) -> None:
    cx = max(w // 2, min(surf.get_width() - w // 2, int(cx)))
    cy = max(h // 2, min(surf.get_height() - h // 2, int(cy)))
    sub = surf.subsurface(pygame.Rect(cx - w // 2, cy - h // 2, w, h)).copy()
    big = pygame.transform.scale(sub, (w * scale, h * scale))
    pygame.image.save(big, str(OUT / name))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    import tempfile
    from motoduel.achievements import SaveData
    g = Game(audio_enabled=False)
    g.save = SaveData(Path(tempfile.mkdtemp()) / "save.json")
    g.start_match(MODE_1P)
    g.state = 2
    g.countdown = 0.0

    inp = {"gas": True, "brake": False, "lean_back": False,
           "lean_fwd": False, "nitro": False}
    for step in range(700):
        g.t += 1 / 60
        for i, b in enumerate(g.bikes):
            use = dict(inp)
            use["nitro"] = step > 420
            b.update(1 / 60, use, g.t)
            g.handle_collisions(i, b, 1 / 60)
            g._update_camera(i, b, 1 / 60)
        if step == 460:
            g.draw()
            bx = g.bikes[0].x - g.cams[0]
            by = g.bikes[0].y - g.cam_y[0]
            crop(g.full_view, bx, by, 260, 150, 4, "10_bike_nitro.png")
    g.draw()
    bx = g.bikes[0].x - g.cams[0]
    by = g.bikes[0].y - g.cam_y[0]
    crop(g.full_view, bx, by, 260, 150, 4, "11_bike.png")
    pygame.image.save(g.screen, str(OUT / "12_solo.png"))
    print("saved", OUT)


if __name__ == "__main__":
    main()
