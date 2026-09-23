<div align="center">

# Beat Cover & BPM

Telegram toolkit for music cover artwork, BPM-based timestamps, MP3 metadata and publishing workflows.

[Contributing](CONTRIBUTING.md) · [Branches](https://github.com/wuttashi1/Beat_cover-bmpbot/branches)

</div>

---

## Features

- Cover artwork with configurable styles, watermarks and export settings.
- Timestamps calculated from BPM and track structure.
- Personal and shared presets.
- MP3 metadata editing and Telegram publishing tools.

## Quick start

Use Python 3.11. Create and activate a virtual environment, then:

```bash
python -m pip install -r requirements.txt
python main.py
```

Before starting, create a local `.env` containing `BOT_TOKEN`. Optional settings include `ADMIN_USER_ID`, `ADMIN_USERNAME` and `PUBLISH_CHANNEL`. Audio operations may require FFmpeg installed on the host.

## Project layout

- `main.py` — entry point.
- `bot.py` — primary Telegram application.
- `database.py` — data storage.
- `styles/` and `presets/` — artwork and presets.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch and contribution guidelines.
