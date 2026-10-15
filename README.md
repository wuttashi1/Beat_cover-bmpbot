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

## Video Generator

### Studio navigation

Open `/menu` (or `/start`) for the shared Studio panel. Switch between **Обложка**,
**Видео** and **BPM** using the bottom buttons, or `/cover`, `/video`, `/bpm`.
Menus edit the existing panel; numeric settings offer small/large steps, a visual
gauge and exact entry. Switching tools clears unfinished input without resetting
saved video projects or artwork settings.

Artwork accepts photos and image documents. Its original is retained in
`data/cover_sources/`; **Обновить превью** reuses it and updates the existing preview.
Render stages and the progress bar update one status message per job. Output files
and explicitly requested copies of timestamps remain separate messages.

### Creating a video

Open **🎬 Video Generator** or `/video` in a private chat. Send a beat, select a
library video/folder, optionally add overlays, review **Sections → Confirm**, then
use **Preview** or **Render**. Random selects one video per track; Mix/Sequence
selects clips by sections without immediate repetition. The saved project seed
makes selection repeatable. Simple/Advanced mode keeps the same settings.

Install FFmpeg/ffprobe on the server in addition to the Python requirements:

```bash
sudo apt-get install ffmpeg
python -m pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v
```

Existing `.env`, BPM commands, MP3 publishing and SQLite user settings continue
working. Video tables are created additively in the same `users.db`; do not replace
that database when deploying. Stop the old process before starting the new one.

Video settings, user presets, projects, analysis corrections and the render queue
are stored in SQLite. Every project edit is saved automatically. Render defaults
are 2560×1440, source FPS, H.264/yuv420p, CRF 16, medium, AAC 320 kbps/48 kHz.
Audio is encoded to AAC without normalization, compression or EQ. FFmpeg performs
the final composition in one process. Short videos loop; disabling Loop holds the
last frame. Section transitions support hard cuts, crossfade, dip, blur and zoom.

Files are kept under `data/`:

- `video_library/<user_id>/<folder_id>/`: originals and ffprobe metadata in SQLite.
- `beats/`, `overlays/`: retained original audio and graphics.
- `renders/<user_id>/<job_id>/`: final MP4 and FFmpeg logs.
- `previews/<user_id>/`: cached short previews; final settings are unchanged.
- `temp/`: job working files, removed on success, failure or cancellation.

Admin commands:

- `/videoimport folder_name /absolute/path/video_or_directory` imports local files.
- `/videobeat /absolute/path/beat.wav` starts a project with local audio.
- `/videoanalysis` exports the current project's `analysis.json`.
- `/videoconnect` configures encrypted connections and server limits inside Telegram.

**Connections** accepts Telegram API ID/hash for MTProto transfer of files larger
than Bot API limits. It authorizes the **bot**, without a personal Telegram login.
Connect MTProto, then send the bot another message to register your private chat.
For a large review-channel upload, add the bot as an administrator of a private
channel, configure its `-100…` ID, and send a test message there after connecting.
Auto upload is OFF until enabled. A Telegram channel has no native draft status;
a private review channel provides the staging destination.

Optional YouTube upload uses OAuth, not an API key. Enable YouTube Data API v3 in
Google Cloud, create a **Desktop app** OAuth client and upload its client JSON to
Connections. Complete Google consent in your browser and paste the full localhost
redirect URL into the bot. Videos upload as **Private**. Google project/consent
setup must happen in Google's console; no new `.env` entries are needed. OAuth in
testing mode can have token expiry and API project quota restrictions.

Connections are admin-only and encrypted in SQLite. `data/connections.key` is
created with mode 0600. Back up it together with `users.db`; losing the key requires
reconnecting providers. Keep `data/`, databases, sessions and keys out of Git.

Local analysis uses mono 22050 Hz audio, NumPy/SciPy spectral/chroma/MFCC features,
bar-aligned novelty, recurrence, multi-feature drop scoring and relative quiet-gap
thresholds with hysteresis. Labels are heuristic; uncertain sections stay generic.
Analysis and previews are cached. One heavy job runs by default; concurrency and
Telegram file limits are editable in **Connections → Server Settings**. Changing a
file-limit setting does not lift limits imposed by Telegram itself.

Hardware encoders are tested before use. Auto falls back to CPU if hardware is
unavailable; a failed hardware render retries on CPU only when fallback is enabled.
Loop crossfade/ping-pong, video-motion-based “Best segment”, rounded corners and
LUT uploads are not implemented. The UI exposes only implemented source modes.
