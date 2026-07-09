"""Пост-обработка транскрипта через YandexGPT (Foundation Models API).

REST-вызов /foundationModels/v1/completion с тем же Api-Key, что и SpeechKit.
Модель убирает слова-паразиты, повторы и само-исправления, расставляет
пунктуацию и заглавные буквы. При любой ошибке (сеть, квота, таймаут)
возвращаем исходный текст — диктовка не должна теряться никогда.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

# Системный промпт «редактора-корректора» (из ТЗ)
SYSTEM_PROMPT = (
    "Ты — редактор устной речи. Тебе дают сырой транскрипт диктовки на "
    "русском. Верни ТОЛЬКО отредактированный текст без пояснений. Убери "
    "слова-паразиты (эээ, ну, как бы) и повторы, обработай само-исправления "
    "говорящего (оставь финальный вариант), расставь пунктуацию и заглавные "
    "буквы, раздели на предложения/абзацы по смыслу. НЕ добавляй ничего от "
    "себя, не меняй смысл, сохраняй термины из словаря пользователя: "
    "{glossary}."
)


class PostProcessor:
    """Клиент YandexGPT для очистки транскрипта."""

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-lite",
        endpoint: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        temperature: float = 0.1,
        timeout: float = 15.0,
        glossary: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._folder_id = folder_id
        self._model = model
        self._endpoint = endpoint
        self._temperature = temperature
        self._timeout = timeout
        self._glossary = glossary or []

    # ------------------------------------------------------------------
    @property
    def model_uri(self) -> str:
        """URI модели: gpt://<folder_id>/<model>/latest."""
        return f"gpt://{self._folder_id}/{self._model}/latest"

    def _system_prompt(self) -> str:
        glossary = ", ".join(self._glossary) if self._glossary else "(пусто)"
        return SYSTEM_PROMPT.format(glossary=glossary)

    # ------------------------------------------------------------------
    def clean(self, transcript: str) -> str:
        """Очистить транскрипт. При ошибке вернуть исходный текст."""
        transcript = transcript.strip()
        if not transcript:
            return transcript
        if not self._folder_id:
            log.warning("folder_id is not set — skipping post-processing")
            return transcript

        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": self._temperature,
                # Ответ не длиннее исходника с запасом
                "maxTokens": str(max(200, len(transcript))),
            },
            "messages": [
                {"role": "system", "text": self._system_prompt()},
                {"role": "user", "text": transcript},
            ],
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "x-folder-id": self._folder_id,
        }
        try:
            resp = requests.post(
                self._endpoint, json=payload, headers=headers, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            text = (
                data["result"]["alternatives"][0]["message"]["text"].strip()
            )
            return text or transcript
        except Exception:
            log.exception("YandexGPT post-processing failed — using raw transcript")
            return transcript
