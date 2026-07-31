"""程序化音訊合成：背景音樂、音效與引擎聲（不依賴外部音檔）。"""
from __future__ import annotations

import array
import math
import random

import pygame

PREFERRED_RATE = 22050
SAMPLE_RATE = PREFERRED_RATE     # 由 Audio 依實際 mixer 設定覆寫
CHANNELS = 2
_TAU = math.tau

# ------------------------------------------------------------------ 波形
def _sine(p: float) -> float:
    return math.sin(p * _TAU)


def _square(p: float) -> float:
    return 1.0 if (p % 1.0) < 0.5 else -1.0


def _saw(p: float) -> float:
    return 2.0 * (p % 1.0) - 1.0


def _tri(p: float) -> float:
    x = (p % 1.0) * 4.0
    if x < 1.0:
        return x
    if x < 3.0:
        return 2.0 - x
    return x - 4.0


WAVES = {"sine": _sine, "square": _square, "saw": _saw, "tri": _tri}


def midi_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


# ------------------------------------------------------------------ 合成工具
class Buf:
    """浮點混音緩衝區。"""

    def __init__(self, seconds: float) -> None:
        self.n = int(seconds * SAMPLE_RATE)
        self.data = [0.0] * self.n

    def tone(self, start: float, dur: float, freq: float, amp: float,
             wave: str = "sine", attack: float = 0.01, decay: float = 0.0,
             detune: float = 0.0, sweep_to: float | None = None) -> None:
        fn = WAVES[wave]
        i0 = int(start * SAMPLE_RATE)
        ln = int(dur * SAMPLE_RATE)
        if ln <= 0:
            return
        atk = max(1, int(attack * SAMPLE_RATE))
        rel = max(1, int((decay if decay > 0 else dur * 0.6) * SAMPLE_RATE))
        phase = 0.0
        phase2 = 0.0
        inv = 1.0 / SAMPLE_RATE
        for i in range(ln):
            idx = i0 + i
            if idx >= self.n:
                break
            t = i / ln
            f = freq if sweep_to is None else freq + (sweep_to - freq) * t
            phase += f * inv
            env = min(1.0, i / atk)
            tail = ln - i
            if tail < rel:
                env *= tail / rel
            v = fn(phase) * env
            if detune:
                phase2 += f * (1.0 + detune) * inv
                v = (v + fn(phase2) * env) * 0.5
            self.data[idx] += v * amp

    def noise(self, start: float, dur: float, amp: float, decay: float = 0.0,
              low_pass: float = 1.0, rng: random.Random | None = None) -> None:
        rng = rng or random.Random(1)
        i0 = int(start * SAMPLE_RATE)
        ln = int(dur * SAMPLE_RATE)
        rel = max(1, int((decay if decay > 0 else dur) * SAMPLE_RATE))
        prev = 0.0
        for i in range(ln):
            idx = i0 + i
            if idx >= self.n:
                break
            raw = rng.uniform(-1.0, 1.0)
            prev += (raw - prev) * low_pass
            env = 1.0
            tail = ln - i
            if tail < rel:
                env = tail / rel
            self.data[idx] += prev * env * amp

    def kick(self, start: float, amp: float = 0.9) -> None:
        self.tone(start, 0.18, 120.0, amp, "sine", attack=0.002, decay=0.16,
                  sweep_to=42.0)

    def snare(self, start: float, amp: float = 0.5,
              rng: random.Random | None = None) -> None:
        self.noise(start, 0.13, amp, decay=0.12, low_pass=0.55, rng=rng)
        self.tone(start, 0.08, 190.0, amp * 0.35, "tri", attack=0.002, decay=0.07)

    def hat(self, start: float, amp: float = 0.16,
            rng: random.Random | None = None) -> None:
        self.noise(start, 0.045, amp, decay=0.04, low_pass=0.95, rng=rng)

    def to_bytes(self, volume: float = 0.6) -> bytes:
        peak = max((abs(v) for v in self.data), default=1.0) or 1.0
        gain = volume / max(1.0, peak)
        ch = CHANNELS
        out = array.array("h", bytes(2 * ch * self.n))
        for i, v in enumerate(self.data):
            s = int(max(-1.0, min(1.0, v * gain)) * 32767)
            base = i * ch
            for c in range(ch):
                out[base + c] = s
        return out.tobytes()


# ------------------------------------------------------------------ 樂曲
def build_race_music() -> Buf:
    """快節奏比賽 BGM（Am - F - C - G，150 BPM，4 小節循環）。"""
    bpm = 150.0
    beat = 60.0 / bpm
    bars = 4
    total = bars * 4 * beat
    buf = Buf(total + 0.2)
    rng = random.Random(7)
    roots = [45, 41, 48, 43]          # A2, F2, C3, G2
    triads = [[0, 3, 7], [0, 4, 7], [0, 4, 7], [0, 4, 7]]
    for bar in range(bars):
        base = bar * 4 * beat
        root = roots[bar]
        chord = triads[bar]
        # 低音線：八分音符
        for k in range(8):
            t = base + k * beat / 2
            note = root if k % 4 != 3 else root + 7
            buf.tone(t, beat * 0.42, midi_hz(note), 0.55, "square",
                     attack=0.005, decay=beat * 0.3)
        # 琶音：十六分音符
        seq = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[0] + 24]
        for k in range(16):
            t = base + k * beat / 4
            note = root + seq[k % 4]
            buf.tone(t, beat * 0.22, midi_hz(note), 0.22, "tri",
                     attack=0.004, decay=beat * 0.18, detune=0.004)
        # 節奏組
        for k in range(4):
            t = base + k * beat
            buf.kick(t, 0.85)
            if k % 2 == 1:
                buf.snare(t, 0.45, rng)
            buf.hat(t + beat / 2, 0.14, rng)
            buf.hat(t + beat / 4, 0.08, rng)
    return buf


def build_menu_music() -> Buf:
    """慢速氛圍選單 BGM。"""
    bpm = 92.0
    beat = 60.0 / bpm
    bars = 4
    buf = Buf(bars * 4 * beat + 0.4)
    roots = [45, 50, 43, 48]
    for bar in range(bars):
        base = bar * 4 * beat
        root = roots[bar]
        for iv in (0, 7, 12, 16):
            buf.tone(base, beat * 3.6, midi_hz(root + iv), 0.24, "tri",
                     attack=0.35, decay=1.1, detune=0.006)
        for k in range(4):
            buf.tone(base + k * beat, beat * 0.5, midi_hz(root + 24), 0.10,
                     "sine", attack=0.02, decay=0.3)
    return buf


# ------------------------------------------------------------------ 音效
def build_sfx() -> dict[str, Buf]:
    rng = random.Random(3)
    sfx: dict[str, Buf] = {}

    b = Buf(0.22)
    b.tone(0.0, 0.07, midi_hz(88), 0.7, "square", attack=0.002, decay=0.05)
    b.tone(0.06, 0.14, midi_hz(95), 0.7, "square", attack=0.002, decay=0.11)
    sfx["coin"] = b

    b = Buf(0.32)
    b.tone(0.0, 0.3, 380.0, 0.6, "saw", attack=0.01, decay=0.16, sweep_to=1250.0)
    sfx["nitro_pickup"] = b

    b = Buf(0.45)
    for k, n in enumerate((72, 76, 79, 84)):
        b.tone(k * 0.05, 0.3, midi_hz(n), 0.4, "tri", attack=0.01, decay=0.24)
    sfx["shield"] = b

    b = Buf(0.42)
    b.tone(0.0, 0.36, 220.0, 0.55, "saw", attack=0.005, decay=0.2, sweep_to=980.0)
    b.noise(0.0, 0.3, 0.22, decay=0.28, low_pass=0.5, rng=rng)
    sfx["boost"] = b

    b = Buf(0.62)
    b.noise(0.0, 0.5, 0.85, decay=0.48, low_pass=0.35, rng=rng)
    b.tone(0.0, 0.45, 180.0, 0.5, "square", attack=0.002, decay=0.4, sweep_to=48.0)
    sfx["crash"] = b

    b = Buf(0.45)
    for k, n in enumerate((69, 74, 78, 81)):
        b.tone(k * 0.06, 0.24, midi_hz(n), 0.45, "square", attack=0.004, decay=0.2)
    sfx["flip"] = b

    b = Buf(1.1)
    for k, n in enumerate((72, 76, 79)):
        b.tone(k * 0.11, 0.9 - k * 0.1, midi_hz(n), 0.4, "tri",
               attack=0.01, decay=0.5, detune=0.005)
    b.tone(0.33, 0.72, midi_hz(84), 0.45, "square", attack=0.01, decay=0.5)
    sfx["finish"] = b

    b = Buf(0.2)
    b.tone(0.0, 0.16, 880.0, 0.5, "square", attack=0.004, decay=0.1)
    sfx["beep"] = b

    b = Buf(0.45)
    b.tone(0.0, 0.4, 1318.0, 0.55, "square", attack=0.004, decay=0.3)
    b.tone(0.0, 0.4, 660.0, 0.3, "tri", attack=0.004, decay=0.3)
    sfx["go"] = b

    b = Buf(0.1)
    b.tone(0.0, 0.07, 620.0, 0.4, "square", attack=0.002, decay=0.05)
    sfx["ui"] = b

    b = Buf(0.8)
    for k, n in enumerate((76, 80, 83, 88, 91)):
        b.tone(k * 0.07, 0.5, midi_hz(n), 0.4, "tri", attack=0.006, decay=0.4)
    sfx["unlock"] = b

    b = Buf(0.4)
    b.noise(0.0, 0.35, 0.5, decay=0.3, low_pass=0.25, rng=rng)
    b.tone(0.0, 0.3, 260.0, 0.35, "square", attack=0.004, decay=0.25, sweep_to=120.0)
    sfx["thud"] = b

    b = Buf(0.32)
    b.noise(0.0, 0.26, 0.34, decay=0.22, low_pass=0.18, rng=rng)
    b.tone(0.0, 0.22, 150.0, 0.3, "sine", attack=0.003, decay=0.18, sweep_to=72.0)
    sfx["land"] = b
    return sfx


def build_engine(level: int) -> Buf:
    """引擎循環音（level 0~4 為由低到高的轉速檔位）。"""
    base = 52.0 * (1.34 ** level)
    dur = 0.5
    buf = Buf(dur)
    n = buf.n
    rng = random.Random(11 + level)
    inv = 1.0 / SAMPLE_RATE
    p1 = p2 = p3 = 0.0
    prev = 0.0
    for i in range(n):
        p1 += base * inv
        p2 += base * 2.01 * inv
        p3 += base * 3.02 * inv
        raw = rng.uniform(-1.0, 1.0)
        prev += (raw - prev) * 0.22
        v = _saw(p1) * 0.55 + _square(p2) * 0.22 + _saw(p3) * 0.12 + prev * 0.16
        buf.data[i] = v
    # 頭尾交叉淡化，確保無縫循環
    fade = int(0.01 * SAMPLE_RATE)
    for i in range(fade):
        a = i / fade
        buf.data[i] = buf.data[i] * a + buf.data[n - fade + i] * (1 - a)
    return buf


# ------------------------------------------------------------------ 管理器
class Audio:
    """負責初始化 mixer、生成並播放所有聲音；無音效裝置時自動停用。"""

    ENGINE_LEVELS = 5

    def __init__(self, enabled: bool = True) -> None:
        self.ok = False
        self.muted = not enabled
        self.sfx: dict[str, pygame.mixer.Sound] = {}
        self.music: dict[str, pygame.mixer.Sound] = {}
        self.engines: list[pygame.mixer.Sound] = []
        self.music_channel: pygame.mixer.Channel | None = None
        self.engine_channel: pygame.mixer.Channel | None = None
        self.current_music: str | None = None
        self._engine_level = -1
        self.music_volume = 0.42
        self.sfx_volume = 0.75
        if not enabled:
            return
        try:
            # pygame.init() 可能已用預設參數啟動 mixer，先關閉再以指定格式重建
            pygame.mixer.quit()
            pygame.mixer.pre_init(PREFERRED_RATE, -16, 2, 512)
            pygame.mixer.init(PREFERRED_RATE, -16, 2, 512)
            info = pygame.mixer.get_init()
            if info:
                global SAMPLE_RATE, CHANNELS
                SAMPLE_RATE, _, CHANNELS = info[0], info[1], info[2]
            pygame.mixer.set_num_channels(20)
            self._build()
            self.ok = True
        except (pygame.error, ValueError):
            self.ok = False

    def _build(self) -> None:
        for name, buf in build_sfx().items():
            self.sfx[name] = pygame.mixer.Sound(buffer=buf.to_bytes(0.72))
        self.music["race"] = pygame.mixer.Sound(buffer=build_race_music().to_bytes(0.62))
        self.music["menu"] = pygame.mixer.Sound(buffer=build_menu_music().to_bytes(0.55))
        for lv in range(self.ENGINE_LEVELS):
            self.engines.append(
                pygame.mixer.Sound(buffer=build_engine(lv).to_bytes(0.5))
            )
        self.music_channel = pygame.mixer.Channel(0)
        self.engine_channel = pygame.mixer.Channel(1)

    # -------------------------------------------------------------- 播放
    def play(self, name: str, volume: float = 1.0) -> None:
        if not self.ok or self.muted:
            return
        snd = self.sfx.get(name)
        if snd is None:
            return
        snd.set_volume(max(0.0, min(1.0, volume)) * self.sfx_volume)
        snd.play()

    def play_music(self, name: str) -> None:
        if not self.ok or self.current_music == name:
            return
        self.current_music = name
        if self.muted or self.music_channel is None:
            return
        snd = self.music.get(name)
        if snd is None:
            return
        self.music_channel.stop()
        self.music_channel.set_volume(self.music_volume)
        self.music_channel.play(snd, loops=-1)

    def stop_music(self) -> None:
        self.current_music = None
        if self.ok and self.music_channel is not None:
            self.music_channel.stop()

    def update_engine(self, speed_ratio: float, throttle: bool, active: bool) -> None:
        """依速度切換引擎檔位與音量。"""
        if not self.ok or self.engine_channel is None:
            return
        if self.muted or not active:
            if self._engine_level != -1:
                self.engine_channel.stop()
                self._engine_level = -1
            return
        lv = max(0, min(self.ENGINE_LEVELS - 1,
                        int(speed_ratio * self.ENGINE_LEVELS)))
        if lv != self._engine_level:
            self.engine_channel.stop()
            self.engine_channel.play(self.engines[lv], loops=-1)
            self._engine_level = lv
        vol = (0.30 if throttle else 0.16) * self.sfx_volume
        self.engine_channel.set_volume(vol)

    def stop_engine(self) -> None:
        if self.ok and self.engine_channel is not None:
            self.engine_channel.stop()
            self._engine_level = -1

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        if not self.ok:
            return self.muted
        if self.muted:
            self.stop_engine()
            if self.music_channel is not None:
                self.music_channel.stop()
        else:
            name, self.current_music = self.current_music, None
            if name:
                self.play_music(name)
        return self.muted
