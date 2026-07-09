# Flow Dictate

Кросс-платформенный клон Wispr Flow для голосовой диктовки на базе
**Yandex SpeechKit v3** (потоковое распознавание) и **YandexGPT**
(очистка текста).

**Как это работает:** в любом приложении зажмите **right Option**
(правый Alt на Windows/Linux), диктуйте — рядом с курсором появится
overlay с живым текстом в реальном времени. Отпустите клавишу — текст
очистится от «эээ», повторов и само-исправлений, получит пунктуацию
и вставится в активное поле ввода.

```
hold alt_r → микрофон 16kHz → SpeechKit gRPC streaming → партиалы в overlay
release    → финальный текст → YandexGPT (очистка)      → вставка (Cmd/Ctrl+V)
```

## Требования

- Python 3.11+
- Аккаунт [Yandex Cloud](https://console.cloud.yandex.ru/) (новым
  аккаунтам даётся бесплатный стартовый грант)
- Интернет (распознавание и очистка выполняются в облаке Yandex)

## Получение ключа Yandex Cloud

Один API-ключ используется и для SpeechKit, и для YandexGPT.

1. Зарегистрируйтесь в [консоли Yandex Cloud](https://console.cloud.yandex.ru/).
2. Создайте каталог (folder) или используйте `default`. Скопируйте его
   **folder_id** (вида `b1g...`) — он виден в URL консоли и в свойствах каталога.
3. Создайте **сервисный аккаунт**: «Сервисные аккаунты» → «Создать».
   Назначьте ему роли:
   - `ai.speechkit-stt.user` — распознавание речи;
   - `ai.languageModels.user` — YandexGPT.
4. Откройте сервисный аккаунт → «Создать новый ключ» → **API-ключ**.
   Скопируйте секрет (показывается один раз).
5. Введите ключ и folder_id в настройках приложения (иконка в трее →
   «Настройки…»). Ключ сохраняется в системном хранилище секретов
   (Keychain / Credential Locker / Secret Service), **не** в файле.

Альтернатива для headless-окружений: переменная окружения `YC_API_KEY`.

## Установка и запуск

```bash
git clone <этот репозиторий>
cd 123
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m flow
```

При первом запуске откроется окно настроек — введите API-ключ и folder_id.

### macOS: разрешения (обязательно)

Системные настройки → «Конфиденциальность и безопасность»:

| Разрешение | Зачем |
|---|---|
| **Микрофон** | запись голоса |
| **Универсальный доступ** (Accessibility) | эмуляция Cmd+V для вставки |
| **Мониторинг ввода** (Input Monitoring) | глобальный хоткей right Option |

Разрешения выдаются приложению-терминалу (или собранному `.app`).
После выдачи перезапустите программу.

### Windows

Работает из коробки. **Внимание:** right Alt в некоторых раскладках —
это AltGr; короткие нажатия приложение пропускает (порог
`min_hold_seconds`), но при конфликте смените хоткей в настройках
(например, на F9).

### Linux

- **X11**: установите `xdotool` (`sudo apt install xdotool`) — вставка
  надёжнее. Без него используется fallback через pynput.
- **Wayland**: глобальные хоткеи и эмуляция ввода ограничены протоколом.
  Установите `wtype` (`sudo apt install wtype`) или `ydotool`
  (нужен запущенный демон `ydotoold` и права на `/dev/uinput`).
  На чистом Wayland pynput может не видеть глобальные клавиши —
  в этом случае рекомендуем сессию X11/XWayland.

## Использование

| Действие | Результат |
|---|---|
| Зажать right Option и говорить | overlay показывает текст в реалтайме |
| Отпустить right Option | очищенный текст вставляется в активное поле |
| Короткий тап (<0.25 c) | игнорируется — обычная работа клавиши не ломается |
| Трей → «Включено» | пауза/возобновление диктовки |
| Трей → «Настройки…» | хоткей, язык, модель, словарь, микрофон, ключ |

**Режимы:** галочка «Очищать текст через YandexGPT» в настройках
переключает между чистым транскриптом (быстрее) и очисткой LLM
(качественнее). Словарь терминов передаётся и в SpeechKit
(как контекст распознавания), и в промпт YandexGPT.

## Конфигурация

Все несекретные настройки — в YAML (см. `config.example.yaml`):

- macOS: `~/Library/Application Support/flow-dictate/config.yaml`
- Linux: `~/.config/flow-dictate/config.yaml`
- Windows: `%APPDATA%\flow-dictate\config.yaml`

## Архитектура

| Модуль | Назначение |
|---|---|
| `flow/hotkey.py` | глобальный push-to-talk (pynput), защита от коротких тапов |
| `flow/audio.py` | захват микрофона 16 kHz mono int16 (sounddevice) + webrtcvad |
| `flow/asr.py` | gRPC bidirectional streaming к SpeechKit v3, партиалы/финалы |
| `flow/overlay.py` | frameless always-on-top окно с живым текстом |
| `flow/postprocess.py` | YandexGPT: промпт «редактора устной речи» |
| `flow/inject.py` | вставка: clipboard + Cmd/Ctrl+V (+xdotool/wtype/ydotool) |
| `flow/tray.py` | иконка в трее, статусы, меню |
| `flow/settings_ui.py` | окно настроек |
| `flow/app.py` | контроллер: связывает всё сигналами Qt |
| `flow/config.py` | YAML-конфиг + keyring для секретов |

Ключевое решение (как у Wispr Flow): реалтайм-текст показывается только
в собственном overlay, а в целевое приложение вставляется финальный
обработанный текст одним paste — это работает надёжно в любом окне,
в отличие от посимвольной печати.

## Упаковка

### Windows (.exe) и macOS (.app) — PyInstaller

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name "Flow Dictate" \
    --collect-all yandex --collect-submodules grpc \
    -m flow  # или укажите entry-скрипт run.py с from flow.__main__ import main; main()
```

Для macOS после сборки подпишите `.app` (ad-hoc достаточно для личного
использования) и выдайте ему разрешения из раздела выше:

```bash
codesign --force --deep -s - "dist/Flow Dictate.app"
```

### Linux — AppImage

Соберите через `pyinstaller` в onedir-режиме, затем упакуйте
`appimagetool`-ом, либо распространяйте как venv + `.desktop`-файл.
Не забудьте зависимость `xdotool`/`wtype` в целевой системе.

## Диагностика

- **Нет партиалов** — проверьте API-ключ, роль `ai.speechkit-stt.user`
  и что микрофон выбран верно (настройки → Микрофон).
- **Текст не вставляется (macOS)** — не выдано разрешение Accessibility.
- **YandexGPT не срабатывает** — проверьте folder_id и роль
  `ai.languageModels.user`; при ошибке приложение вставляет сырой
  транскрипт (диктовка не теряется).
- Логи пишутся в stdout — запустите из терминала: `python -m flow`.
