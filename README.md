<div align="center">

# Beat Cover & BPM

Telegram-бот для оформления музыкальных обложек, расчёта таймкодов по BPM и редактирования MP3-тегов.

[Правила разработки](CONTRIBUTING.md) · [Ветки](https://github.com/wuttashi1/Beat_cover-bmpbot/branches)

</div>

---

## Возможности

- Оформление обложек, настройка водяных знаков, качества и формата.
- Расчёт таймкодов по BPM и структуре трека.
- Личные и общие пресеты.
- Редактирование MP3-метаданных и подготовка публикаций в Telegram.

## Запуск

Установите Python 3.11 и зависимости в виртуальном окружении:

```bash
python -m venv .venv
# Активируйте .venv для вашей оболочки
python -m pip install -r requirements.txt
python main.py
```

Перед запуском создайте локальный `.env` с `BOT_TOKEN`. Дополнительные параметры: `ADMIN_USER_ID`, `ADMIN_USERNAME`, `PUBLISH_CHANNEL`. Для операций с аудио может понадобиться установленный FFmpeg.

## Навигация

- `main.py` — точка входа.
- `bot.py` — основной Telegram-бот.
- `database.py` — хранение данных.
- `styles/` и `presets/` — оформление и пресеты.

## Разработка

Соглашения по веткам и изменениям: [CONTRIBUTING.md](CONTRIBUTING.md).
