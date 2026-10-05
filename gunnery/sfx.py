"""攻击模块音效 —— 纯标准库程序化合成 WAV 字节流，后台线程阻塞播放。

无任何音频素材依赖；非 Windows / 无音频设备环境自动静默降级不抛异常。
注意：Win32 PlaySound 的 SND_MEMORY 不支持 SND_ASYNC（组合会报错），
因此用守护线程逐条阻塞播放，既不卡主循环又避免新声掉旧声。
对外暴露 fire() / torp() / hit() / splash() / explode() / sink() / incoming()。
"""

import io
import math
import random
import struct
import threading
import wave

try:
    import winsound
except ImportError:          # 非 Windows 平台静默
    winsound = None

SR = 22050                   # 采样率
_cache = {}                  # 名称 -> wav bytes（懒合成缓存）


def _wav_bytes(samples):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000))
        for s in samples))
    w.close()
    return buf.getvalue()


def _noise_burst(dur, decay, lp=1, amp=1.0):
    """指数衰减噪声；lp 为低通平滑次数（越大越沉闷）。"""
    n = int(SR * dur)
    rnd = random.Random(1234)
    out = []
    prev = 0.0
    for i in range(n):
        s = rnd.uniform(-1.0, 1.0) * amp * math.exp(-i / SR / decay)
        for _ in range(lp):
            prev = prev * 0.6 + s * 0.4
        out.append(prev)
    return out


def _thump(dur, freq, decay, amp=0.9):
    """低频轰鸣（炮声主体）。"""
    n = int(SR * dur)
    return [math.sin(2.0 * math.pi * freq * i / SR)
            * math.exp(-i / SR / decay) * amp for i in range(n)]


def _mix(*layers):
    m = max(len(x) for x in layers)
    return [sum(x[i] if i < len(x) else 0.0 for x in layers)
            for i in range(m)]


def _snd_fire():
    return _mix(_thump(0.85, 46, 0.24, 1.0),
                _noise_burst(0.6, 0.11, 2, 1.0))


def _snd_torp():
    return _noise_burst(0.7, 0.4, 7, 0.6)          # 沉闷气泡发射声


def _snd_hit():
    return _mix(_thump(0.4, 90, 0.09, 0.8),
                _noise_burst(0.28, 0.05, 2, 0.85))


def _snd_explode():
    """大爆炸：雷声般下坠低频 + 大噪声混响（鱼雷/重创命中）。"""
    return _mix(_thump(1.4, 55, 0.5, 1.0),
                _thump(1.0, 30, 0.6, 0.8),
                _noise_burst(1.1, 0.35, 4, 0.95))


def _snd_sink():
    """舰体沉没：延后低频哀鸣 + 水声。"""
    return _mix(_thump(2.0, 40, 0.9, 0.9),
                _noise_burst(1.8, 0.6, 6, 0.5))


def _snd_splash():
    return _noise_burst(0.9, 0.3, 5, 0.7)          # 水花


def _snd_incoming():
    """来袭弹呼啸：噪声渐强后骤停。"""
    n = int(SR * 0.6)
    rnd = random.Random(99)
    out = []
    prev = 0.0
    for i in range(n):
        k = i / n
        s = rnd.uniform(-1.0, 1.0) * (0.15 + 0.65 * k)
        prev = prev * 0.55 + s * 0.45
        out.append(prev * 0.5)
    return out


_SOUNDS = {"fire": _snd_fire, "torp": _snd_torp, "hit": _snd_hit,
           "explode": _snd_explode, "splash": _snd_splash, "sink": _snd_sink,
           "incoming": _snd_incoming}


def _play(name):
    if winsound is None:
        return
    try:
        data = _cache.get(name)
        if data is None:
            data = _cache[name] = _wav_bytes(_SOUNDS[name]())
    except Exception:
        return                       # 合成失败静默，不影响战斗逻辑

    def _run(buf=data):
        try:   # SND_MEMORY 阻塞播放（不能与 SND_ASYNC 组合），在守护线程里逐条播完
            winsound.PlaySound(buf, winsound.SND_MEMORY)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def fire():
    _play("fire")


def torp():
    _play("torp")


def hit():
    _play("hit")


def splash():
    _play("splash")


def explode():
    _play("explode")


def sink():
    _play("sink")


def incoming():
    _play("incoming")
