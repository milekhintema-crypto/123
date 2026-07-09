"""Захват аудио с микрофона: sounddevice, 16 kHz mono int16 + webrtcvad.

AudioCapture пишет PCM-чанки в очередь (queue.Queue[bytes]); окончание
записи помечается сентинелем None. Эту же очередь читает ASR-клиент
и стримит содержимое в SpeechKit.

VAD здесь не режет аудио (SpeechKit сам детектит конец фразы) — он
используется только для UI-индикации «идёт речь / тишина» в overlay.
"""

from __future__ import annotations

import logging
import queue
from typing import Callable

import numpy as np
import sounddevice as sd

try:
    import webrtcvad

    _HAS_VAD = True
except Exception:  # webrtcvad — опциональная нативная зависимость
    _HAS_VAD = False

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
# 30 мс — валидный размер кадра для webrtcvad и хорошая гранулярность стрима
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 сэмплов


def list_input_devices() -> list[tuple[int, str]]:
    """(index, name) всех устройств с входными каналами — для окна настроек."""
    devices = []
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                devices.append((idx, dev["name"]))
    except Exception:
        log.exception("Failed to query audio devices")
    return devices


class AudioCapture:
    """Потоковый захват микрофона.

    Использование:
        cap = AudioCapture(device=None, vad_aggressiveness=2,
                           on_speech_activity=cb)
        q = cap.start()   # queue.Queue[bytes | None]
        ...
        cap.stop()        # кладёт None-сентинель в очередь
    """

    def __init__(
        self,
        device: int | None = None,
        vad_aggressiveness: int = 2,
        on_speech_activity: Callable[[bool], None] | None = None,
    ) -> None:
        self._device = device
        self._on_speech_activity = on_speech_activity
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._vad = (
            webrtcvad.Vad(vad_aggressiveness) if _HAS_VAD else None
        )
        self._remainder = b""  # хвост, не кратный кадру VAD

    # ------------------------------------------------------------------
    def start(self) -> queue.Queue[bytes | None]:
        """Открыть поток; возвращает очередь PCM-чанков."""
        self._queue = queue.Queue()
        self._remainder = b""
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            device=self._device,
            blocksize=FRAME_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()
        log.info("Audio capture started (device=%s)", self._device)
        return self._queue

    def stop(self) -> None:
        """Закрыть поток и положить сентинель конца записи."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("Failed to stop audio stream")
            self._stream = None
        self._queue.put(None)
        log.info("Audio capture stopped")

    def set_device(self, device: int | None) -> None:
        self._device = device

    # ------------------------------------------------------------------
    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """Колбэк sounddevice (вызывается в аудио-потоке PortAudio)."""
        if status:
            log.warning("Audio status: %s", status)
        pcm = indata.tobytes()
        self._queue.put(pcm)
        if self._vad is not None and self._on_speech_activity is not None:
            self._feed_vad(pcm)

    def _feed_vad(self, pcm: bytes) -> None:
        """Прогнать аудио через VAD кадрами по 30 мс для индикации речи."""
        data = self._remainder + pcm
        frame_bytes = FRAME_SAMPLES * 2  # int16 → 2 байта на сэмпл
        offset = 0
        speech = False
        while offset + frame_bytes <= len(data):
            frame = data[offset : offset + frame_bytes]
            offset += frame_bytes
            try:
                if self._vad.is_speech(frame, SAMPLE_RATE):
                    speech = True
            except Exception:
                return
        self._remainder = data[offset:]
        try:
            self._on_speech_activity(speech)
        except Exception:
            pass
