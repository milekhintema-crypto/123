"""gRPC-клиент Yandex SpeechKit v3 — bidirectional streaming распознавание.

Использует готовые protobuf-стабы из пакета `yandexcloud`
(yandex.cloud.ai.stt.v3). Схема сессии:

    1-е сообщение  → StreamingRequest(session_options=StreamingOptions(...))
    далее          → StreamingRequest(chunk=AudioChunk(data=pcm))
    конец записи   → закрытие итератора запросов (сентинель None в очереди)

Ответы сервера: partial (промежуточные гипотезы), final (финал фразы),
eou_update (детект конца высказывания). Партиалы летят в on_partial,
финалы копятся; on_final получает полный склеенный текст всей диктовки.

Каждая диктовка = отдельная gRPC-сессия (это дёшево: канал переиспользуется).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Iterator

import grpc
from yandex.cloud.ai.stt.v3 import stt_pb2, stt_service_pb2_grpc

log = logging.getLogger(__name__)


def _build_session_options(
    language: str, sample_rate: int, glossary: list[str]
) -> stt_pb2.StreamingRequest:
    """Первое сообщение стрима: параметры распознавания."""
    recognition_model = stt_pb2.RecognitionModelOptions(
        audio_format=stt_pb2.AudioFormatOptions(
            raw_audio=stt_pb2.RawAudio(
                audio_encoding=stt_pb2.RawAudio.LINEAR16_PCM,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            )
        ),
        text_normalization=stt_pb2.TextNormalizationOptions(
            text_normalization=stt_pb2.TextNormalizationOptions.TEXT_NORMALIZATION_ENABLED,
            profanity_filter=False,
            literature_text=True,  # пунктуация и капитализация от SpeechKit
        ),
        language_restriction=stt_pb2.LanguageRestrictionOptions(
            restriction_type=stt_pb2.LanguageRestrictionOptions.WHITELIST,
            language_code=[language],
        ),
        audio_processing_type=stt_pb2.RecognitionModelOptions.REAL_TIME,
    )

    options = stt_pb2.StreamingOptions(recognition_model=recognition_model)

    # Пользовательский словарь терминов (если поддерживается версией API)
    if glossary:
        try:
            options.speaker_labeling.CopyFrom(stt_pb2.SpeakerLabelingOptions())
            options.recognition_model.recognition_context.CopyFrom(
                stt_pb2.RecognitionContext(
                    phrases=[stt_pb2.Phrase(value=p) for p in glossary]
                )
            )
        except Exception:
            # Старые версии protobuf-стабов могут не знать recognition_context
            log.debug("recognition_context is not supported by installed stubs")

    return stt_pb2.StreamingRequest(session_options=options)


class SpeechKitStreamer:
    """Одна потоковая сессия распознавания на диктовку.

    Колбэки вызываются из фонового потока — маршалинг в GUI-поток
    делает вызывающая сторона (сигналы Qt в app.py).
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        language: str,
        sample_rate: int,
        glossary: list[str],
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._language = language
        self._sample_rate = sample_rate
        self._glossary = glossary
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error

        self._channel: grpc.Channel | None = None
        self._thread: threading.Thread | None = None
        self._cancelled = False

    # ------------------------------------------------------------------
    def start(self, audio_queue: queue.Queue[bytes | None]) -> None:
        """Запустить сессию; аудио читается из очереди до сентинеля None."""
        self._cancelled = False
        self._thread = threading.Thread(
            target=self._run, args=(audio_queue,), daemon=True, name="speechkit-stream"
        )
        self._thread.start()

    def cancel(self) -> None:
        """Отменить сессию (короткий тап хоткея): финал не будет доставлен."""
        self._cancelled = True

    def wait(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def _requests(
        self, audio_queue: queue.Queue[bytes | None]
    ) -> Iterator[stt_pb2.StreamingRequest]:
        """Генератор запросов: опции сессии, затем аудио-чанки до None."""
        yield _build_session_options(self._language, self._sample_rate, self._glossary)
        while True:
            chunk = audio_queue.get()
            if chunk is None:  # запись остановлена — закрываем стрим
                return
            yield stt_pb2.StreamingRequest(chunk=stt_pb2.AudioChunk(data=chunk))

    def _run(self, audio_queue: queue.Queue[bytes | None]) -> None:
        finals: list[str] = []
        try:
            self._channel = grpc.secure_channel(
                self._endpoint, grpc.ssl_channel_credentials()
            )
            stub = stt_service_pb2_grpc.RecognizerStub(self._channel)
            metadata = (("authorization", f"Api-Key {self._api_key}"),)

            responses = stub.RecognizeStreaming(
                self._requests(audio_queue), metadata=metadata
            )
            for response in responses:
                if self._cancelled:
                    responses.cancel()
                    return
                event = response.WhichOneof("Event")
                if event == "partial" and response.partial.alternatives:
                    # Промежуточная гипотеза текущей фразы: уже готовые
                    # финалы + живой партиал
                    partial_text = response.partial.alternatives[0].text
                    live = " ".join(finals + [partial_text]).strip()
                    if live:
                        self._on_partial(live)
                elif event == "final" and response.final.alternatives:
                    finals.append(response.final.alternatives[0].text)
                    self._on_partial(" ".join(finals).strip())
                elif event == "final_refinement":
                    # Нормализованный (с пунктуацией) вариант финала —
                    # заменяем последний «сырой» финал
                    norm = response.final_refinement.normalized_text
                    if norm.alternatives:
                        refined = norm.alternatives[0].text
                        if finals:
                            finals[-1] = refined
                        else:
                            finals.append(refined)
                        self._on_partial(" ".join(finals).strip())
                # eou_update / status_code — служебные, пропускаем
        except grpc.RpcError as exc:
            if not self._cancelled:
                log.error("SpeechKit RPC error: %s — %s", exc.code(), exc.details())
                self._on_error(f"SpeechKit: {exc.details() or exc.code()}")
                return
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                log.exception("SpeechKit session failed")
                self._on_error(f"SpeechKit: {exc}")
                return
        finally:
            if self._channel is not None:
                self._channel.close()
                self._channel = None

        if not self._cancelled:
            self._on_final(" ".join(finals).strip())
