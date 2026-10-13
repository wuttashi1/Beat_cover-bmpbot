import asyncio
import copy
import html
import json
import logging
import random
import sqlite3
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from database import video_rows
from . import library, projects
from .config import (
    BACKGROUND_STYLES,
    COLOR_STYLES,
    CATEGORIES,
    Overlay,
    TextLayer,
    VideoRenderConfig,
)
from .media import MediaError, format_time, parse_time, probe, safe_name
from .models import TrackAnalysis, normalize_sections, timestamps
from .queue import RenderQueue

logger = logging.getLogger(__name__)
ACTIVE_USERS = set()
CATEGORIES_LABELS = {
    "foreground": "🎞 Main Video",
    "background": "🌫 Background",
    "color": "🎨 Color",
    "motion": "🎥 Motion",
    "beat_reaction": "⚡ Beat Reaction",
    "blackout": "🌑 Quiet / Blackout",
    "transitions": "🔀 Sections & Transitions",
    "canvas": "📐 Canvas",
    "export": "🎬 Export",
    "analysis": "🧠 Audio Analysis",
}


class VideoMessages(filters.MessageFilter):
    def filter(self, message):
        return (
            message.chat.type == "private"
            and bool(message.from_user)
            and message.from_user.id in ACTIVE_USERS
        )


def keyboard(project, rows):
    prefix = f"vg:{project['id']}:" if project else "vg:"
    return Markup(
        [
            [Button(label, callback_data=prefix + action) for label, action in row]
            for row in rows
        ]
    )


async def screen(update, project, text, rows):
    text = text[:4000]
    markup = keyboard(project, rows)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        except TelegramError as error:
            if "not modified" not in str(error).lower():
                await update.effective_message.reply_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


def current(update, context):
    uid = update.effective_user.id
    pid = context.user_data.get("vg_project")
    if not pid:
        saved = projects.list_projects(uid)
        project = (
            projects.load_project(uid, saved[0]["id"])
            if saved
            else projects.create_project(uid)
        )
        context.user_data["vg_project"] = project["id"]
        return project
    return projects.load_project(uid, pid)


async def home(update, context, project=None):
    project = project or current(update, context)
    analysis = project.get("analysis") or {}
    cfg = VideoRenderConfig.from_dict(project["settings"])
    video = "Не выбрано"
    if project.get("video_id") and project["video_mode"] == "single":
        try:
            item = library.asset(project["user_id"], project["video_id"])
            names = {f["id"]: f["name"] for f in library.folders(project["user_id"])}
            video = f"{names.get(item['folder_id'], '')}/{item['filename']}"
        except MediaError:
            video = "⚠️ Видео недоступно"
    elif project["video_mode"] != "single":
        names = [
            f["name"]
            for f in library.folders(project["user_id"])
            if not project["folder_ids"] or f["id"] in project["folder_ids"]
        ]
        video = f"{project['video_mode']}: " + ", ".join(names)
    overlays = (
        ", ".join(f"{Path(o.source).name} / {o.blend}" for o in cfg.overlays) or "OFF"
    )
    text = (
        f"🎬 Video Generator · {project['name']}\n\n"
        f"Beat: {Path(project['beat']).name if project.get('beat') else 'Отправьте аудио'}\n"
        f"BPM: {analysis.get('bpm', project.get('bpm_hint')) or '—'} · {format_time(analysis.get('duration', 0))}\n"
        f"Video: {video}\nStyle: {cfg.name}\n"
        f"{cfg.canvas.width}×{cfg.canvas.height} / {cfg.canvas.fps or 'Source'} FPS\n"
        f"Ambient: {cfg.background.mode} / Blur {cfg.background.blur:g}\n"
        f"Overlay: {overlays}\nBlackout: {'ON' if cfg.blackout.enabled else 'OFF'} / {cfg.blackout.strength:g}%\n"
        f"Sections: {len(analysis.get('sections', []))} · {'✅ Confirmed' if project.get('sections_confirmed') else 'требуют проверки'}\n"
        f"Export: {cfg.export.codec} / CRF {cfg.export.crf} / AAC {cfg.export.audio_bitrate}\n"
        f"Mode: {'🛠 Advanced' if project['advanced'] else '⚡ Simple'}"
    )
    rows = [
        [("🎵 Beat", "beat"), ("🎞 Change Video", "source")],
        [("🖼 Overlay", "layers:overlays"), ("📁 Library", "library:0")],
        [("📝 Sections", "sections:0"), ("🧠 Analysis", "analysis")],
        [("⚙️ Video Settings", "settings"), ("💾 Presets", "presets")],
        [("👁 Preview", "previews"), ("🚀 Render", "summary")],
        [("📂 Projects", "projects"), ("⏳ Jobs", "jobs")],
        [("Simple ↔ Advanced", "mode"), ("🔐 Connections", "connections")],
        [("↩ Главное меню", "exit")],
    ]
    await screen(update, project, text, rows)


async def open_video(update, context):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "Video Generator доступен в личном чате."
        )
        raise ApplicationHandlerStop
    ACTIVE_USERS.add(update.effective_user.id)
    context.user_data.pop("vg_input", None)
    await home(update, context)
    raise ApplicationHandlerStop


async def exit_on_start(update, context):
    ACTIVE_USERS.discard(update.effective_user.id)
    context.user_data.pop("vg_input", None)


def model_at(cfg, path):
    if "." in path:
        group, index = path.split(".")
        if group not in ("overlays", "branding"):
            raise MediaError("Неизвестная категория.")
        items = getattr(cfg, group)
        if not 0 <= int(index) < len(items):
            raise MediaError("Layer удалён.")
        return items[int(index)]
    if path not in CATEGORIES:
        raise MediaError("Неизвестная категория.")
    return getattr(cfg, path)


async def settings_menu(update, project):
    simple = ("background", "blackout", "beat_reaction", "transitions")
    groups = (
        CATEGORIES_LABELS
        if project["advanced"]
        else {k: CATEGORIES_LABELS[k] for k in simple}
    )
    rows = [[(label, f"category:{name}:0")] for name, label in groups.items()]
    rows += [
        [("🖼 Overlays", "layers:overlays"), ("📝 Branding / Text", "layers:branding")],
        [
            ("💾 Presets", "presets"),
            ("🔧 Advanced" if not project["advanced"] else "⚡ Simple", "mode"),
        ],
        [("↩ Reset to preset", "resetpreset"), ("👁 Preview", "previews")],
        [("✅ Done", "home")],
    ]
    await screen(
        update, project, "⚙️ Video Settings\nИзменения сохраняются автоматически.", rows
    )


async def category_menu(update, project, path, page=0):
    cfg = VideoRenderConfig.from_dict(project["settings"])
    model = model_at(cfg, path)
    descriptors = list(fields(model))
    page = max(0, min(page, (len(descriptors) - 1) // 8))
    rows = []
    for descriptor in descriptors[page * 8 : page * 8 + 8]:
        value = getattr(model, descriptor.name)
        label = "ON" if value is True else "OFF" if value is False else str(value)
        if isinstance(value, str) and (
            "source" == descriptor.name
            or "font" == descriptor.name
            or "media" == descriptor.name
        ):
            label = Path(value).name if value else "—"
        rows.append(
            [(f"{descriptor.name}: {label[:35]}", f"field:{path}:{descriptor.name}")]
        )
    rows.append(
        [
            ("⬅️", f"category:{path}:{max(0, page - 1)}"),
            ("➡️", f"category:{path}:{min((len(descriptors) - 1) // 8, page + 1)}"),
        ]
    )
    if path == "background":
        rows.append([("🎨 Styles", "bgstyles")])
    if path == "color":
        rows.append([("🎨 Color Presets", "colorstyles")])
    if hasattr(model, "x") and hasattr(model, "y"):
        rows.append([("↔ Position", f"position:{path}")])
    if path == "foreground":
        rows.append([("📏 Center Size", "centersizes")])
    if path == "canvas":
        rows.append([("📐 Resolutions", "resolutions"), ("🎞 FPS", "fpspresets")])
    if path == "export":
        rows.append([("🎬 Quality Presets", "qualities")])
    rows += [
        [("↩ Reset category", f"resetcat:{path}"), ("👁 Preview", "previews")],
        [("✅ Done", "settings")],
    ]
    await screen(
        update,
        project,
        f"⚙️ {path} · {page + 1}/{(len(descriptors) - 1) // 8 + 1}",
        rows,
    )


async def field_menu(update, project, path, name):
    cfg = VideoRenderConfig.from_dict(project["settings"])
    model = model_at(cfg, path)
    descriptor = next((f for f in fields(model) if f.name == name), None)
    if descriptor is None:
        raise MediaError("Неизвестный параметр.")
    value = getattr(model, name)
    meta = descriptor.metadata
    if descriptor.type is bool:
        rows = [
            [("ON", f"set:{path}:{name}:true"), ("OFF", f"set:{path}:{name}:false")]
        ]
    elif "choices" in meta:
        rows = [
            [(str(option), f"set:{path}:{name}:{option}")] for option in meta["choices"]
        ]
    elif "min" in meta:
        step = meta.get("step", 1)
        rows = [
            [
                (f"{delta:+g}", f"adjust:{path}:{name}:{delta:g}")
                for delta in (-10 * step, -step, step, 10 * step)
            ],
            [("⌨️ Enter manually", f"inputfield:{path}:{name}")],
        ]
    else:
        rows = [[("⌨️ Enter manually", f"inputfield:{path}:{name}")]]
        if name == "font":
            rows = [[("Choose installed font", f"fonts:{path}:0")]]
        if name == "source":
            rows = [[("📤 Загрузить изображение", f"uploadlayer:{path}")]]
        if name == "media":
            rows = [
                [
                    ("🎞 Library video", "backgroundselect:0"),
                    ("📤 Image", "uploadbackground"),
                ]
            ]
    rows += [[("↩ Назад", f"category:{path}:0")]]
    await screen(
        update,
        project,
        f"{path}.{name}\nТекущее значение: {value}\n"
        + (f"Range: {meta['min']}..{meta['max']}" if "min" in meta else ""),
        rows,
    )


def mutate_field(project, path, name, value, delta=False):
    cfg = VideoRenderConfig.from_dict(project["settings"])
    model = model_at(cfg, path)
    descriptor = next((f for f in fields(model) if f.name == name), None)
    if not descriptor:
        raise MediaError("Неизвестный параметр.")
    if descriptor.type is bool:
        if str(value).lower() not in ("true", "false", "on", "off", "1", "0"):
            raise ValueError("Используйте ON/OFF.")
        parsed = str(value).lower() in ("true", "on", "1")
    elif descriptor.type in (int, float):
        parsed = (
            parse_time(value)
            if name in ("start", "end", "start_offset")
            else float(value)
        )
        if delta:
            parsed += getattr(model, name)
        if descriptor.type is int:
            if parsed != int(parsed):
                raise ValueError("Нужно целое число.")
            parsed = int(parsed)
    else:
        parsed = value
    if name in ("source", "media", "font"):
        raise MediaError(
            "Медиа выбирается из Library или загружается, font — через admin config."
        )
    setattr(model, name, parsed)
    if path == "foreground" and name in ("width", "height") and model.lock_ratio:
        other = "height" if name == "width" else "width"
        old = model_at(VideoRenderConfig.from_dict(project["settings"]), path)
        setattr(
            model,
            other,
            max(32, round(getattr(old, other) * parsed / getattr(old, name)) // 2 * 2),
        )
    cfg.validate((project.get("analysis") or {}).get("duration"))
    project["settings"] = cfg.to_dict()
    if path == "analysis" or (
        path == "blackout" and name in ("sensitivity", "minimum_ms", "maximum_ms")
    ):
        project["sections_confirmed"] = False
    projects.save_project(project)


async def prompt(update, context, project, action, text):
    context.user_data["vg_input"] = {"project_id": project["id"], "action": action}
    await update.effective_message.reply_text(
        text, reply_markup=keyboard(project, [[("↩ Отмена", "home")]])
    )


async def library_menu(update, project, page=0):
    folders = library.folders(project["user_id"])
    rows = [
        [(f"📁 {f['name']}", f"folder:{f['id']}:0")]
        for f in folders[page * 8 : page * 8 + 8]
    ]
    rows += [
        [("⬅️", f"library:{max(0, page - 1)}"), ("➡️", f"library:{page + 1}")],
        [("➕ Create folder", "createfolder"), ("🎲 Random video", "randomvideo")],
        [("↩ Назад", "home")],
    ]
    await screen(
        update, project, "📁 Video Library\nПапки не ограничены категориями.", rows
    )


async def folder_menu(update, project, fid, page=0):
    folder = library.folder(project["user_id"], fid)
    videos = library.assets(project["user_id"], [fid])
    rows = [
        [(f"🎞 {v['filename'][:35]} · {format_time(v['duration'])}", f"asset:{v['id']}")]
        for v in videos[page * 8 : page * 8 + 8]
    ]
    rows += [
        [("⬅️", f"folder:{fid}:{max(0, page - 1)}"), ("➡️", f"folder:{fid}:{page + 1}")],
        [("➕ Add video", f"addvideo:{fid}"), ("✏️ Rename", f"renamefolder:{fid}")],
        [
            ("🎲 Random from folder", f"usefolder:{fid}:random"),
            ("🔀 Sequence", f"usefolder:{fid}:sequence"),
        ],
        [("🗑 Delete empty folder", f"deletefolder:{fid}"), ("↩ Folders", "library:0")],
    ]
    await screen(update, project, f"📁 {folder['name']}\nVideos: {len(videos)}", rows)


async def source_menu(update, project):
    rows = [
        [("🎞 One video", "choosevideo:0")],
        [("📁 Random from folder", "selectfolders:random")],
        [("🔀 Sequence from folder", "selectfolders:sequence")],
        [("🏷 Tags", "inputtags"), ("🎲 Seed", "inputseed")],
        [("📁 Library", "library:0")],
        [("↩ Назад", "home")],
    ]
    await screen(
        update,
        project,
        f"Video source: {project['video_mode']}\nSeed: {project['seed']}",
        rows,
    )


async def video_picker(update, project, page=0, action="usevideo"):
    items = library.assets(project["user_id"])
    rows = [
        [(f"{v['folder']}/{v['filename'][:30]}", f"{action}:{v['id']}")]
        for v in items[page * 8 : page * 8 + 8]
    ]
    route = "backgroundselect" if action == "usebackground" else "choosevideo"
    rows += [
        [("⬅️", f"{route}:{max(0, page - 1)}"), ("➡️", f"{route}:{page + 1}")],
        [("📁 Library", "library:0"), ("↩ Назад", "source")],
    ]
    await screen(update, project, "🎞 Выберите видео", rows)


async def position_menu(update, project, path):
    cfg = VideoRenderConfig.from_dict(project["settings"])
    model = model_at(cfg, path)
    rows = [
        [("⬆️", f"positionstep:{path}:y:-10")],
        [
            ("⬅️", f"positionstep:{path}:x:-10"),
            ("CENTER", f"positioncenter:{path}"),
            ("➡️", f"positionstep:{path}:x:10"),
        ],
        [("⬇️", f"positionstep:{path}:y:10")],
        [("Set X", f"inputfield:{path}:x"), ("Set Y", f"inputfield:{path}:y")],
        [("↩ Category", f"category:{path}:0")],
    ]
    await screen(
        update, project, f"Position · {path}\nX: {model.x}\nY: {model.y}", rows
    )


async def sections_menu(update, project, page=0):
    analysis = project.get("analysis")
    if not analysis:
        raise MediaError("Сначала загрузите beat и дождитесь анализа.")
    sections = analysis["sections"]
    rows = [
        [(f"{i + 1}. {format_time(s['start'])} {s['type']}", f"section:{i}")]
        for i, s in enumerate(sections)
        if page * 8 <= i < (page + 1) * 8
    ]
    rows += [
        [("⬅️", f"sections:{max(0, page - 1)}"), ("➡️", f"sections:{page + 1}")],
        [("➕ Add", "addsection"), ("✅ Confirm", "confirmsections")],
        [("📋 Timestamps", "timestamps"), ("↩ Назад", "home")],
    ]
    await screen(
        update,
        project,
        "📝 Sections\nЭвристические labels — проверьте перед Render.",
        rows,
    )


async def section_menu(update, project, index):
    section = project["analysis"]["sections"][index]
    rows = [
        [
            ("✏️ Timestamp + name", f"editsection:{index}"),
            ("↕ Change type", f"sectiontypes:{index}"),
        ],
        [
            ("🎞 Change video", f"sectionvideos:{index}:0"),
            ("AUTO", f"sectionauto:{index}"),
        ],
        [
            ("Keep previous", f"sectionkeep:{index}"),
            ("🔀 Transition", f"sectiontransition:{index}"),
        ],
        [
            ("🎨 Visual Overrides", f"sectionvisual:{index}"),
            ("↩ Reset Overrides", f"resetsection:{index}"),
        ],
        [("🗑 Delete", f"delsection:{index}"), ("↩ Sections", "sections:0")],
    ]
    await screen(
        update,
        project,
        f"{index + 1}. {format_time(section['start'], True)} {section['type']}\n"
        f"Confidence: {round(section['confidence'] * 100)}%\nVideo: {section.get('video_id') or 'AUTO'}\nTransition: {section.get('transition') or 'Preset'}",
        rows,
    )


async def analysis_menu(update, project):
    analysis = project.get("analysis") or {}
    rows = [
        [("📝 Sections", "sections:0"), ("💥 Drops", "events:drops:0")],
        [("🌑 Quiet Gaps", "events:quiet_regions:0"), ("📍 Markers", "markers")],
        [("🔄 Re-analyze", "reanalyze"), ("🧠 Settings", "category:analysis:0")],
        [("↩ Назад", "home")],
    ]
    await screen(
        update,
        project,
        f"🧠 Analysis Result\nBPM: {analysis.get('bpm', '—')}\n"
        f"Sections: {len(analysis.get('sections', []))}\nDrops: {len(analysis.get('drops', []))}\n"
        f"Quiet gaps: {len(analysis.get('quiet_regions', []))}\nЛокальный heuristic analysis, не гарантия точной классификации.",
        rows,
    )


async def events_menu(update, project, group, page=0):
    items = (project.get("analysis") or {}).get(group, [])
    rows = []
    for i, item in enumerate(items):
        if not page * 8 <= i < (page + 1) * 8:
            continue
        position = (
            format_time(item["timestamp"], True)
            if group == "drops"
            else f"{format_time(item['start'], True)} → {format_time(item['end'], True)}"
        )
        score = item.get("confidence", item.get("strength", 0))
        rows.append(
            [
                (
                    f"{'✅' if item.get('enabled', True) else '⬜'} #{i + 1} {position} {round(score * 100)}%",
                    f"event:{group}:{i}",
                )
            ]
        )
    rows += [
        [
            ("⬅️", f"events:{group}:{max(0, page - 1)}"),
            ("➡️", f"events:{group}:{page + 1}"),
        ],
        [("➕ Add", f"addevent:{group}"), ("↩ Analysis", "analysis")],
    ]
    await screen(update, project, group, rows)


async def callback(update, context):
    query = update.callback_query
    if update.effective_chat.type != "private":
        await query.answer("Откройте личный чат.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    ACTIVE_USERS.add(update.effective_user.id)
    parts = query.data.split(":")[1:]
    try:
        if parts[0].isdigit():
            pid = int(parts.pop(0))
            context.user_data["vg_project"] = pid
            project = projects.load_project(update.effective_user.id, pid)
        else:
            project = current(update, context)
        action = parts.pop(0)
        if action not in ("inputfield", "uploadlayer"):
            context.user_data.pop("vg_input", None)
        uid = update.effective_user.id
        visual_index = (
            context.user_data.get("vg_visual_section")
            if context.user_data.get("vg_visual_project") == project["id"]
            else None
        )
        if visual_index is not None and action in (
            "category",
            "field",
            "set",
            "adjust",
            "inputfield",
            "resetcat",
        ):
            allowed = (
                "foreground",
                "background",
                "color",
                "motion",
                "beat_reaction",
                "blackout",
            )
            if parts and parts[0] not in allowed:
                raise MediaError("Эта category задаётся глобально.")
            base = copy.deepcopy(project["settings"])
            section = project["analysis"]["sections"][visual_index]
            for category, values in section.get("visual", {}).items():
                project["settings"][category].update(values)
            if action in ("set", "adjust"):
                mutate_field(
                    project, parts[0], parts[1], ":".join(parts[2:]), action == "adjust"
                )
                section.setdefault("visual", {})[parts[0]] = copy.deepcopy(
                    project["settings"][parts[0]]
                )
                project["settings"] = base
                projects.save_project(project)
                project["settings"][parts[0]].update(section["visual"][parts[0]])
                await field_menu(update, project, parts[0], parts[1])
            elif action == "category":
                await category_menu(update, project, parts[0], int(parts[1]))
            elif action == "field":
                await field_menu(update, project, *parts)
            elif action == "inputfield":
                await prompt(
                    update,
                    context,
                    project,
                    f"sectionfield:{visual_index}:{parts[0]}:{parts[1]}",
                    f"Введите section {parts[0]}.{parts[1]}:",
                )
            else:
                section.get("visual", {}).pop(parts[0], None)
                project["settings"] = base
                projects.save_project(project)
                await section_menu(update, project, visual_index)
            raise ApplicationHandlerStop
        if action not in (
            "sectionvisual",
            "category",
            "field",
            "set",
            "adjust",
            "inputfield",
            "resetcat",
        ):
            context.user_data.pop("vg_visual_section", None)
        from .connection_handlers import handle_callback

        if await handle_callback(update, context, project, action, parts):
            raise ApplicationHandlerStop
        if action == "open":
            context.user_data["vg_project"] = int(parts[0])
            await home(update, context)
        elif action == "home":
            await home(update, context, project)
        elif action == "exit":
            ACTIVE_USERS.discard(uid)
            from menu_manager import get_main_keyboard

            await update.effective_message.reply_text(
                "🎛 Главное меню", reply_markup=get_main_keyboard()
            )
        elif action == "mode":
            project["advanced"] = not project["advanced"]
            projects.save_project(project)
            await settings_menu(update, project)
        elif action == "beat":
            await prompt(
                update,
                context,
                project,
                "beat",
                "Отправьте audio/document: WAV, MP3, FLAC, M4A. BPM будет взят из имени, metadata или последнего BPM результата. Можно задать вручную в Audio Analysis.",
            )
        elif action == "source":
            await source_menu(update, project)
        elif action == "fonts":
            path, page = parts[0], int(parts[1])
            fonts = sorted(
                str(p)
                for p in Path("/usr/share/fonts").rglob("*.ttf")
                if "'" not in str(p)
            )
            rows = [
                [(Path(font).stem[:40], f"usefont:{path}:{index}")]
                for index, font in enumerate(fonts)
                if page * 8 <= index < (page + 1) * 8
            ]
            await screen(
                update,
                project,
                "Font",
                rows
                + [
                    [("Default", f"usefont:{path}:-1")],
                    [
                        ("⬅️", f"fonts:{path}:{max(0, page - 1)}"),
                        ("➡️", f"fonts:{path}:{page + 1}"),
                    ],
                    [("↩ Branding", "layers:branding")],
                ],
            )
        elif action == "usefont":
            path, index = parts[0], int(parts[1])
            fonts = sorted(
                str(p)
                for p in Path("/usr/share/fonts").rglob("*.ttf")
                if "'" not in str(p)
            )
            cfg = VideoRenderConfig.from_dict(project["settings"])
            model_at(cfg, path).font = fonts[index] if index >= 0 else ""
            project["settings"] = cfg.to_dict()
            projects.save_project(project)
            await category_menu(update, project, path)
        elif action == "inputseed":
            await prompt(
                update,
                context,
                project,
                "seed",
                "Введите seed: целое число 0..4294967295. Меняет Random / Mix / deterministic motion.",
            )
        elif action == "position":
            await position_menu(update, project, parts[0])
        elif action == "positionstep":
            mutate_field(project, parts[0], parts[1], parts[2], True)
            await position_menu(update, project, parts[0])
        elif action == "positioncenter":
            mutate_field(project, parts[0], "x", 0)
            mutate_field(project, parts[0], "y", 0)
            await position_menu(update, project, parts[0])
        elif action == "centersizes":
            await screen(
                update,
                project,
                "Center Size",
                [
                    [("Small", "centersize:60"), ("Medium", "centersize:80")],
                    [("Full Height", "centersize:100")],
                    [("Custom", "category:foreground:0")],
                ],
            )
        elif action == "centersize":
            size = (
                int(project["settings"]["canvas"]["height"] * int(parts[0]) / 100)
                // 2
                * 2
            )
            project["settings"]["foreground"]["width"] = size
            project["settings"]["foreground"]["height"] = size
            projects.save_project(project)
            await category_menu(update, project, "foreground")
        elif action == "fpspresets":
            await screen(
                update,
                project,
                "FPS",
                [
                    [(str(fps or "Source"), f"set:canvas:fps:{fps}")]
                    for fps in (0, 24, 25, 30, 50, 60)
                ]
                + [[("↩ Canvas", "category:canvas:0")]],
            )
        elif action == "settings":
            await settings_menu(update, project)
        elif action == "category":
            await category_menu(update, project, parts[0], int(parts[1]))
        elif action == "field":
            await field_menu(update, project, *parts)
        elif action in ("set", "adjust"):
            path, name, value = parts[0], parts[1], ":".join(parts[2:])
            mutate_field(project, path, name, value, action == "adjust")
            await field_menu(update, project, path, name)
        elif action == "inputfield":
            await prompt(
                update,
                context,
                project,
                f"field:{parts[0]}:{parts[1]}",
                f"Введите {parts[0]}.{parts[1]}:",
            )
        elif action == "resetcat":
            cfg = VideoRenderConfig.from_dict(project["settings"])
            base = VideoRenderConfig.from_dict(project["preset_settings"])
            path = parts[0]
            if "." in path:
                group, index = path.split(".")
                items = getattr(base, group)
                getattr(cfg, group)[int(index)] = (
                    copy.deepcopy(items[int(index)])
                    if int(index) < len(items)
                    else (Overlay() if group == "overlays" else TextLayer())
                )
            else:
                setattr(cfg, path, copy.deepcopy(getattr(base, path)))
            project["settings"] = cfg.to_dict()
            projects.save_project(project)
            await category_menu(update, project, path)
        elif action == "resetpreset":
            project["settings"] = copy.deepcopy(project["preset_settings"])
            projects.save_project(project)
            await settings_menu(update, project)
        elif action in ("bgstyles", "colorstyles"):
            styles = BACKGROUND_STYLES if action == "bgstyles" else COLOR_STYLES
            await screen(
                update,
                project,
                "Style",
                [[(name, f"applystyle:{action}:{name}")] for name in styles]
                + [[("↩ Settings", "settings")]],
            )
        elif action == "applystyle":
            cfg = VideoRenderConfig.from_dict(project["settings"])
            if parts[0] == "bgstyles":
                (
                    cfg.background.blur,
                    cfg.background.brightness,
                    cfg.background.saturation,
                ) = BACKGROUND_STYLES[parts[1]]
            else:
                (
                    cfg.color.brightness,
                    cfg.color.contrast,
                    cfg.color.saturation,
                    cfg.color.temperature,
                ) = COLOR_STYLES[parts[1]]
            project["settings"] = cfg.to_dict()
            projects.save_project(project)
            await settings_menu(update, project)
        elif action == "resolutions":
            await screen(
                update,
                project,
                "📐 Canvas",
                [
                    [("YouTube 1440p", "resolution:2560:1440")],
                    [("YouTube 1080p", "resolution:1920:1080")],
                    [("YouTube 4K", "resolution:3840:2160")],
                    [("↩ Settings", "settings")],
                ],
            )
        elif action == "resolution":
            cfg = VideoRenderConfig.from_dict(project["settings"])
            w, h = map(int, parts)
            ratio = h / cfg.canvas.height
            cfg.foreground.width = round(cfg.foreground.width * ratio) // 2 * 2
            cfg.foreground.height = round(cfg.foreground.height * ratio) // 2 * 2
            cfg.canvas.width = w
            cfg.canvas.height = h
            project["settings"] = cfg.to_dict()
            projects.save_project(project)
            await category_menu(update, project, "canvas")
        elif action == "qualities":
            await screen(
                update,
                project,
                "🎬 Export Quality",
                [
                    [("Fast", "quality:23")],
                    [("Normal", "quality:19")],
                    [("High", "quality:16")],
                    [("Master", "quality:12")],
                    [("↩ Export", "category:export:0")],
                ],
            )
        elif action == "quality":
            mutate_field(project, "export", "crf", parts[0])
            await category_menu(update, project, "export")
        elif action == "library":
            await library_menu(update, project, int(parts[0]))
        elif action == "folder":
            await folder_menu(update, project, int(parts[0]), int(parts[1]))
        elif action == "createfolder":
            await prompt(
                update, context, project, "createfolder", "Введите название папки:"
            )
        elif action == "renamefolder":
            await prompt(
                update,
                context,
                project,
                f"renamefolder:{parts[0]}",
                "Новое название папки:",
            )
        elif action == "deletefolder":
            library.delete_folder(uid, int(parts[0]))
            await library_menu(update, project)
        elif action == "addvideo":
            await prompt(
                update,
                context,
                project,
                f"video:{parts[0]}",
                "Отправьте видео/document. Продолжительность не ограничена pipeline; размер download ограничен вашим Telegram Bot API. Для локальных файлов используйте /videoimport (admin).",
            )
        elif action == "asset":
            item = library.asset(uid, int(parts[0]))
            rows = [
                [
                    ("✅ Use video", f"usevideo:{item['id']}"),
                    ("↔ Move", f"movevideo:{item['id']}"),
                ],
                [
                    ("🏷 Tags", f"assettags:{item['id']}"),
                    ("🗑 Delete", f"deletevideoask:{item['id']}"),
                ],
                [("↩ Library", "library:0")],
            ]
            await screen(
                update,
                project,
                f"{item['filename']}\n{item['width']}×{item['height']} / {item['fps']:.2f} FPS / {item['codec']}\n{format_time(item['duration'])}\nTags: {item['tags']}",
                rows,
            )
        elif action == "deletevideoask":
            await screen(
                update,
                project,
                "Удалить video из Library?",
                [[("🗑 Delete", f"deletevideo:{parts[0]}"), ("↩ Отмена", "library:0")]],
            )
        elif action == "deletevideo":
            library.delete_video(uid, int(parts[0]))
            await library_menu(update, project)
        elif action == "movevideo":
            await screen(
                update,
                project,
                "Переместить в:",
                [
                    [(f["name"], f"moveto:{parts[0]}:{f['id']}")]
                    for f in library.folders(uid)
                ]
                + [[("↩ Library", "library:0")]],
            )
        elif action == "moveto":
            library.move_video(uid, *map(int, parts))
            await library_menu(update, project)
        elif action == "assettags":
            await prompt(
                update,
                context,
                project,
                f"assettags:{parts[0]}",
                "Tags через запятую: night, car, blue",
            )
        elif action == "inputtags":
            await prompt(
                update,
                context,
                project,
                "inputtags",
                "AUTO фильтр tags через запятую (пустая строка: напишите -).",
            )
        elif action == "choosevideo":
            await video_picker(update, project, int(parts[0]))
        elif action == "backgroundselect":
            await video_picker(update, project, int(parts[0]), "usebackground")
        elif action == "usebackground":
            item = library.asset(uid, int(parts[0]))
            project["settings"]["background"]["media"] = item["path"]
            project["settings"]["background"]["mode"] = "Custom Media"
            projects.save_project(project)
            await category_menu(update, project, "background")
        elif action == "usevideo":
            item = library.asset(uid, int(parts[0]))
            project["video_id"] = item["id"]
            project["video_mode"] = "single"
            project["folder_ids"] = []
            projects.save_project(project)
            await home(update, context, project)
        elif action == "randomvideo":
            videos = library.assets(uid)
            if not videos:
                raise MediaError("Library пуста.")
            project["video_id"] = random.Random(project["seed"]).choice(videos)["id"]
            project["video_mode"] = "single"
            projects.save_project(project)
            await home(update, context, project)
        elif action == "usefolder":
            library.folder(uid, int(parts[0]))
            project["folder_ids"] = [int(parts[0])]
            project["video_mode"] = parts[1]
            projects.save_project(project)
            await home(update, context, project)
        elif action == "selectfolders":
            project["video_mode"] = parts[0]
            projects.save_project(project)
            rows = [
                [
                    (
                        f"{'✅' if f['id'] in project['folder_ids'] else '⬜'} {f['name']}",
                        f"togglefolder:{f['id']}",
                    )
                ]
                for f in library.folders(uid)
            ]
            await screen(
                update,
                project,
                "AUTO source folders\nПустой выбор = All videos.",
                rows + [[("All videos", "allfolders"), ("✅ Done", "home")]],
            )
        elif action == "togglefolder":
            fid = int(parts[0])
            library.folder(uid, fid)
            if fid in project["folder_ids"]:
                project["folder_ids"].remove(fid)
            else:
                project["folder_ids"].append(fid)
            projects.save_project(project)
            rows = [
                [
                    (
                        f"{'✅' if f['id'] in project['folder_ids'] else '⬜'} {f['name']}",
                        f"togglefolder:{f['id']}",
                    )
                ]
                for f in library.folders(uid)
            ]
            await screen(
                update,
                project,
                "AUTO source folders",
                rows + [[("All videos", "allfolders"), ("✅ Done", "home")]],
            )
        elif action == "allfolders":
            project["folder_ids"] = []
            projects.save_project(project)
            await home(update, context, project)
        elif action == "layers":
            group = parts[0]
            if group not in ("overlays", "branding"):
                raise MediaError("Unknown layer group.")
            rows = [
                [
                    (
                        f"#{i + 1} {Path(x.get('source', '')).name if group == 'overlays' else x['text'][:25]}",
                        f"category:{group}.{i}:0",
                    ),
                    ("🗑", f"dellayer:{group}:{i}"),
                ]
                for i, x in enumerate(project["settings"][group])
            ]
            rows += [[("➕ Add", f"addlayer:{group}"), ("↩ Назад", "home")]]
            if group == "branding":
                rows.insert(-1, [("Text Templates", "texttemplates")])
            await screen(
                update,
                project,
                "🖼 Overlays" if group == "overlays" else "📝 Branding / Text",
                rows,
            )
        elif action == "addlayer":
            group = parts[0]
            cfg = VideoRenderConfig.from_dict(project["settings"])
            layers = getattr(cfg, group)
            if len(layers) >= 5:
                raise MediaError("Максимум 5 layers.")
            from dataclasses import asdict

            if group == "overlays":
                await prompt(
                    update,
                    context,
                    project,
                    "overlay",
                    "Отправьте PNG/WEBP/JPG (document сохраняет alpha).",
                )
            else:
                layers.append(TextLayer(text="{artist} — {title}"))
                project["settings"] = cfg.to_dict()
                projects.save_project(project)
                await category_menu(update, project, f"branding.{len(layers) - 1}")
        elif action == "uploadlayer":
            await prompt(
                update,
                context,
                project,
                f"replaceoverlay:{parts[0].split('.')[1]}",
                "Отправьте PNG/WEBP/JPG как document.",
            )
        elif action == "uploadbackground":
            await prompt(
                update,
                context,
                project,
                "backgroundimage",
                "Отправьте background image.",
            )
        elif action == "dellayer":
            group, index = parts[0], int(parts[1])
            project["settings"][group].pop(index)
            projects.save_project(project)
            await home(update, context, project)
        elif action == "texttemplates":
            await screen(
                update,
                project,
                "📝 Text Templates",
                [
                    [("WUTSHY Classic", "texttemplate:classic")],
                    [("Minimal", "texttemplate:minimal")],
                    [("Copyright", "texttemplate:copyright")],
                    [("Track Info", "texttemplate:info")],
                    [("None", "texttemplate:none")],
                    [("↩ Назад", "layers:branding")],
                ],
            )
        elif action == "texttemplate":
            from dataclasses import asdict

            templates = {
                "classic": [
                    TextLayer(text="WUTSHY", anchor="Center"),
                    TextLayer(text="© {year} {artist}", size=24),
                ],
                "minimal": [TextLayer(text="{artist}", size=36)],
                "copyright": [TextLayer(text="© {year} {artist}", size=24)],
                "info": [
                    TextLayer(text="{artist} — {title}\n{bpm} BPM · {key}", size=40)
                ],
                "none": [],
            }
            project["settings"]["branding"] = [asdict(x) for x in templates[parts[0]]]
            projects.save_project(project)
            await home(update, context, project)
        elif action == "sections":
            await sections_menu(update, project, int(parts[0]))
        elif action == "section":
            await section_menu(update, project, int(parts[0]))
        elif action in ("addsection", "editsection"):
            await prompt(
                update,
                context,
                project,
                action + (":" + parts[0] if parts else ""),
                "Введите timestamp и название:\n00:28.170 Drop",
            )
        elif action == "delsection":
            index = int(parts[0])
            if not index:
                raise MediaError(
                    "Первый section нельзя удалить. Измените его название."
                )
            project["analysis"]["sections"].pop(index)
            project["analysis"]["sections"] = normalize_sections(
                project["analysis"]["sections"], project["analysis"]["duration"]
            )
            project["sections_confirmed"] = False
            projects.save_project(project)
            await sections_menu(update, project)
        elif action == "sectionvisual":
            index = int(parts[0])
            context.user_data["vg_visual_section"] = index
            context.user_data["vg_visual_project"] = project["id"]
            await screen(
                update,
                project,
                "Section visual overrides · изменения только для выбранной section",
                [
                    [(CATEGORIES_LABELS[name], f"category:{name}:0")]
                    for name in (
                        "foreground",
                        "background",
                        "color",
                        "motion",
                        "beat_reaction",
                        "blackout",
                    )
                ]
                + [[("↩ Sections", "sections:0")]],
            )
        elif action == "resetsection":
            project["analysis"]["sections"][int(parts[0])]["visual"] = {}
            projects.save_project(project)
            await section_menu(update, project, int(parts[0]))
        elif action == "sectiontypes":
            rows = [
                [(name, f"sectiontype:{parts[0]}:{name}")]
                for name in (
                    "Intro",
                    "Build",
                    "Drop",
                    "Chorus",
                    "Verse",
                    "Break",
                    "Bridge",
                    "Outro",
                    "Section",
                )
            ]
            await screen(
                update, project, "Section Type", rows + [[("↩ Sections", "sections:0")]]
            )
        elif action == "sectiontype":
            project["analysis"]["sections"][int(parts[0])]["type"] = parts[1]
            project["sections_confirmed"] = False
            projects.save_project(project)
            await section_menu(update, project, int(parts[0]))
        elif action in ("sectionauto", "sectionkeep"):
            section = project["analysis"]["sections"][int(parts[0])]
            section["video_id"] = None
            section["keep_previous"] = action == "sectionkeep"
            projects.save_project(project)
            await section_menu(update, project, int(parts[0]))
        elif action == "sectionvideos":
            index, page = map(int, parts)
            videos = library.assets(uid)
            rows = [
                [(v["filename"][:40], f"sectionvideo:{index}:{v['id']}")]
                for v in videos[page * 8 : page * 8 + 8]
            ]
            await screen(
                update,
                project,
                "Section Video",
                rows
                + [
                    [
                        ("⬅️", f"sectionvideos:{index}:{max(0, page - 1)}"),
                        ("➡️", f"sectionvideos:{index}:{page + 1}"),
                    ],
                    [("AUTO", f"sectionauto:{index}")],
                ],
            )
        elif action == "sectionvideo":
            index, aid = map(int, parts)
            library.asset(uid, aid)
            project["analysis"]["sections"][index]["video_id"] = aid
            project["video_mode"] = "sequence"
            project["folder_ids"] = []
            projects.save_project(project)
            await section_menu(update, project, index)
        elif action == "sectiontransition":
            await screen(
                update,
                project,
                "Transition",
                [
                    [(name, f"settransition:{parts[0]}:{name}")]
                    for name in (
                        "Hard Cut",
                        "Fade",
                        "Crossfade",
                        "Dip to Black",
                        "Blur",
                        "Zoom",
                        "None",
                    )
                ]
                + [[("↩ Sections", "sections:0")]],
            )
        elif action == "settransition":
            project["analysis"]["sections"][int(parts[0])]["transition"] = parts[1]
            projects.save_project(project)
            await section_menu(update, project, int(parts[0]))
        elif action == "confirmsections":
            TrackAnalysis.from_dict(project["analysis"])
            analysis = project["analysis"]
            original = analysis["drops"]
            confirmed = []
            for section in analysis["sections"]:
                if "drop" not in section["type"].lower():
                    continue
                nearest = (
                    min(original, key=lambda d: abs(d["timestamp"] - section["start"]))
                    if original
                    else None
                )
                if (
                    nearest
                    and abs(nearest["timestamp"] - section["start"])
                    <= 240 / analysis["bpm"]
                ):
                    drop = copy.deepcopy(nearest)
                    drop["timestamp"] = section["start"]
                else:
                    drop = {
                        "timestamp": section["start"],
                        "confidence": 1,
                        "pre_energy": 0,
                        "post_energy": 0,
                        "enabled": True,
                    }
                confirmed.append(drop)
            analysis["drops"] = confirmed
            project["sections_confirmed"] = True
            projects.save_project(project)
            await home(update, context, project)
        elif action == "timestamps":
            if not project.get("analysis"):
                raise MediaError("Нет анализа.")
            await update.effective_message.reply_text(
                "<pre>" + html.escape(timestamps(project["analysis"])) + "</pre>",
                parse_mode="HTML",
            )
        elif action == "jobtimestamps":
            job = projects.job(uid, int(parts[0]))
            payload = json.loads(job["payload"])
            analysis = payload["project"].get("analysis")
            if not analysis:
                raise MediaError("Нет timestamps для job.")
            await update.effective_message.reply_text(
                "<pre>" + html.escape(timestamps(analysis)) + "</pre>",
                parse_mode="HTML",
            )
        elif action == "analysis":
            await analysis_menu(update, project)
        elif action == "reanalyze":
            jid = await context.application.bot_data["video_queue"].enqueue(
                project, update.effective_chat.id, "analysis"
            )
            await queued(update, project, jid)
        elif action == "events":
            await events_menu(update, project, parts[0], int(parts[1]))
        elif action == "event":
            group, index = parts[0], int(parts[1])
            item = project["analysis"][group][index]
            await screen(
                update,
                project,
                json.dumps(item, ensure_ascii=False, indent=2),
                [
                    [
                        ("Enable / Disable", f"toggleevent:{group}:{index}"),
                        ("✏️ Edit", f"editevent:{group}:{index}"),
                    ],
                    [
                        ("🗑 Delete", f"delevent:{group}:{index}"),
                        ("↩ Back", f"events:{group}:0"),
                    ],
                ],
            )
        elif action == "toggleevent":
            item = project["analysis"][parts[0]][int(parts[1])]
            item["enabled"] = not item.get("enabled", True)
            projects.save_project(project)
            await events_menu(update, project, parts[0])
        elif action == "delevent":
            project["analysis"][parts[0]].pop(int(parts[1]))
            projects.save_project(project)
            await events_menu(update, project, parts[0])
        elif action in ("editevent", "addevent"):
            group = parts[0]
            await prompt(
                update,
                context,
                project,
                action + ":" + ":".join(parts),
                "Введите timestamp confidence (0..1), например 00:28.170 0.91"
                if group == "drops"
                else "Введите start end strength (0..1), например 00:27.42 00:27.91 0.92",
            )
        elif action == "markers":
            await screen(
                update,
                project,
                "📍 Markers\n"
                + "\n".join(
                    f"{format_time(m['timestamp'], True)} {m['type']}"
                    for m in (project.get("analysis") or {}).get("markers", [])
                ),
                [
                    [("➕ Add", "addmarker")],
                    [("🗑 Clear markers", "clearmarkers"), ("↩ Analysis", "analysis")],
                ],
            )
        elif action == "addmarker":
            await prompt(
                update,
                context,
                project,
                "marker",
                "Введите timestamp type: Drop, Section, Blackout, Clip, Flash, Overlay.\nBlackout: 00:27.4 Blackout 00:27.9\nSection: 00:28 Section Drop\nClip: 00:28 Clip 12 (video ID)\nOverlay: 00:28 Overlay 1 (layer number)",
            )
        elif action == "clearmarkers":
            project["analysis"]["markers"] = []
            projects.save_project(project)
            await analysis_menu(update, project)
        elif action == "previews":
            await screen(
                update,
                project,
                "👁 Preview · 720p\nFinal export settings не меняются.",
                [
                    [("Drop Preview", "preview:drop")],
                    [("First 10 sec", "preview:first")],
                    [("Current Section", "previewsections")],
                    [("Custom Range", "previewcustom")],
                    [("↩ Home", "home")],
                ],
            )
        elif action == "previewsections":
            rows = [
                [(f"{format_time(s['start'])} {s['type']}", f"previewsection:{i}")]
                for i, s in enumerate(
                    (project.get("analysis") or {}).get("sections", [])
                )
            ]
            await screen(
                update, project, "Preview section", rows + [[("↩ Preview", "previews")]]
            )
        elif action == "previewcustom":
            await prompt(
                update,
                context,
                project,
                "previewrange",
                "Введите start end (до 15 секунд): 00:25 00:35",
            )
        elif action in ("preview", "previewsection"):
            if not project.get("analysis"):
                raise MediaError("Дождитесь анализа.")
            duration = project["analysis"]["duration"]
            range_ = None
            if action == "previewsection":
                section = project["analysis"]["sections"][int(parts[0])]
                range_ = [section["start"], min(section["end"], section["start"] + 10)]
            elif parts[0] == "first":
                range_ = [0, min(duration, 10)]
            jid = await context.application.bot_data["video_queue"].enqueue(
                project, update.effective_chat.id, "preview", range_
            )
            await queued(update, project, jid)
        elif action == "summary":
            if not project.get("analysis"):
                raise MediaError("Дождитесь анализа.")
            if not project.get("sections_confirmed"):
                await sections_menu(update, project)
            else:
                cfg = VideoRenderConfig.from_dict(project["settings"])
                await screen(
                    update,
                    project,
                    f"🎬 {cfg.name}\n{cfg.canvas.width}×{cfg.canvas.height} / {cfg.canvas.fps or 'Source'} FPS\nCrop: {cfg.foreground.crop}\nAmbient: {cfg.background.mode} / Blur {cfg.background.blur}\nBlackout: {cfg.blackout.enabled} / {cfg.blackout.strength}%\nSections: {len(project['analysis']['sections'])}\nExport: {cfg.export.codec} / CRF {cfg.export.crf}\nAudio: AAC {cfg.export.audio_bitrate} kbps, без обработки.",
                    [
                        [("👁 Preview", "previews"), ("⚙️ Edit", "settings")],
                        [("🚀 Render", "render")],
                        [("↩ Home", "home")],
                    ],
                )
        elif action == "render":
            jid = await context.application.bot_data["video_queue"].enqueue(
                project, update.effective_chat.id
            )
            await queued(update, project, jid)
        elif action == "cancel":
            context.application.bot_data["video_queue"].cancel(uid, int(parts[0]))
            await screen(
                update,
                project,
                "❌ Отмена запрошена.",
                [[("⏳ Jobs", "jobs"), ("↩ Home", "home")]],
            )
        elif action == "jobs":
            jobs = video_rows(
                "SELECT id,status,progress,output FROM video_render_jobs WHERE user_id=? ORDER BY id DESC LIMIT 15",
                (uid,),
            )
            rows = [
                [
                    (
                        f"#{j['id']} {j['status']} {round(j['progress'] * 100)}%",
                        f"job:{j['id']}",
                    )
                ]
                for j in jobs
            ]
            await screen(update, project, "⏳ Jobs", rows + [[("↩ Home", "home")]])
        elif action == "job":
            job = projects.job(uid, int(parts[0]))
            rows = []
            if job["status"] in ("queued", "analyzing", "rendering"):
                rows.append([("❌ Cancel", f"cancel:{job['id']}")])
            if job["output"]:
                rows.append(
                    [
                        ("📤 Send again", f"resend:{job['id']}"),
                        ("📋 Timestamps", f"jobtimestamps:{job['id']}"),
                    ]
                )
            await screen(
                update,
                project,
                f"Job #{job['id']}\n{job['status']} / {job['progress'] * 100:.0f}%\n{job['error'] or ''}\n{job['output'] or ''}",
                rows + [[("↩ Jobs", "jobs")]],
            )
        elif action == "resend":
            job = projects.job(uid, int(parts[0]))
            data = json.loads(job["payload"])
            output = job["output"]
            if not output or not Path(output).is_file():
                raise MediaError("Output недоступен.")
            await context.application.bot_data["video_queue"].deliver(
                update.effective_chat.id,
                output,
                data["project"]["analysis"],
                job["id"],
                data["kind"] == "preview",
            )
        elif action == "projects":
            rows = [
                [(f"{p['name'][:35]}", f"open:{p['id']}")]
                for p in projects.list_projects(uid)[:20]
            ]
            await screen(
                update,
                project,
                "📂 Render Projects · auto save",
                rows
                + [
                    [
                        ("➕ New", "newproject"),
                        ("📄 Duplicate current", "duplicateproject"),
                    ],
                    [
                        ("✏️ Rename current", "renameproject"),
                        ("🗑 Delete current", "deleteprojectask"),
                    ],
                    [("↩ Home", "home")],
                ],
            )
        elif action == "newproject":
            new = projects.create_project(uid)
            context.user_data["vg_project"] = new["id"]
            await home(update, context, new)
        elif action == "duplicateproject":
            new = projects.duplicate_project(uid, project["id"])
            context.user_data["vg_project"] = new["id"]
            await home(update, context, new)
        elif action == "renameproject":
            await prompt(
                update, context, project, "renameproject", "Новое имя project:"
            )
        elif action == "resetprojectask":
            await screen(
                update,
                project,
                "Сбросить весь project? Beat и настройки project будут очищены. Library и outputs сохранятся.",
                [[("✅ Confirm Reset", "resetproject"), ("↩ Отмена", "projects")]],
            )
        elif action == "resetproject":
            fresh = projects.create_project(uid, project["name"])
            fresh_id = fresh["id"]
            fresh["id"] = project["id"]
            projects.save_project(fresh)
            projects.delete_project(uid, fresh_id)
            await home(update, context, fresh)
        elif action == "deleteprojectask":
            await screen(
                update,
                project,
                "Удалить project? Assets и renders сохранятся.",
                [[("🗑 Confirm delete", "deleteproject"), ("↩ Отмена", "projects")]],
            )
        elif action == "deleteproject":
            projects.delete_project(uid, project["id"])
            context.user_data.pop("vg_project", None)
            await home(update, context)
        elif action == "presets":
            rows = [
                [
                    (
                        f"{'⭐' if p['is_default'] else '💾'} {p['name']}",
                        f"preset:{p['id']}",
                    )
                ]
                for p in projects.presets(uid)
            ]
            await screen(
                update,
                project,
                "💾 Presets · settings only",
                rows
                + [
                    [("Save Current", "savecurrentpreset"), ("Save As", "savepreset")],
                    [("↩ Home", "home")],
                ],
            )
        elif action == "preset":
            item = projects.preset(uid, int(parts[0]))
            rows = [
                [
                    ("Load", f"loadpreset:{item['id']}"),
                    ("Duplicate", f"duplicatepreset:{item['id']}"),
                ],
                [("Set Default", f"defaultpreset:{item['id']}")],
            ]
            if item["id"]:
                rows.append(
                    [
                        ("Rename", f"renamepreset:{item['id']}"),
                        ("Delete", f"deletepreset:{item['id']}"),
                    ]
                )
            await screen(
                update, project, item["name"], rows + [[("↩ Presets", "presets")]]
            )
        elif action == "loadpreset":
            data = json.loads(projects.preset(uid, int(parts[0]))["payload"])
            project["settings"] = data
            project["preset_settings"] = copy.deepcopy(data)
            projects.save_project(project)
            await home(update, context, project)
        elif action == "savepreset":
            await prompt(
                update, context, project, "savepreset", "Название нового preset:"
            )
        elif action == "savecurrentpreset":
            projects.save_preset(
                uid, project["settings"]["name"], project["settings"], True
            )
            await home(update, context, project)
        elif action in ("duplicatepreset", "renamepreset"):
            await prompt(
                update,
                context,
                project,
                action + ":" + parts[0],
                "Новое название preset:",
            )
        elif action == "deletepreset":
            projects.delete_preset(uid, int(parts[0]))
            await home(update, context, project)
        elif action == "defaultpreset":
            projects.default_preset(uid, int(parts[0]))
            await home(update, context, project)
        else:
            raise MediaError("Кнопка устарела. Откройте /video.")
    except (
        MediaError,
        ValueError,
        KeyError,
        IndexError,
        sqlite3.IntegrityError,
    ) as error:
        await update.effective_message.reply_text(
            f"❌ {str(error)[:1000]}",
            reply_markup=keyboard(None, [[("🎬 /video", "home")]]),
        )
    raise ApplicationHandlerStop


async def queued(update, project, jid):
    await screen(
        update,
        project,
        f"⏳ Job #{jid}: queued",
        [
            [("❌ Cancel render", f"cancel:{jid}"), ("⏳ Jobs", "jobs")],
            [("↩ Home", "home")],
        ],
    )


async def download(update, target, context=None):
    message = update.effective_message
    media = (
        message.document
        or message.audio
        or message.video
        or (message.photo[-1] if message.photo else None)
    )
    if not media:
        raise MediaError("Отправьте медиафайл.")
    from .runtime import load as runtime_settings

    download_limit = runtime_settings().download_limit_mb
    if media.file_size and media.file_size > download_limit * 1024 * 1024:
        from .connections import load

        uid = update.effective_user.id
        data = load(uid)
        client = (
            context.application.bot_data.get("video_mtproto", {}).get(uid)
            if context
            else None
        )
        if not client or not data.get("tg_peer_hash"):
            raise MediaError(
                f"Файл превышает лимит download Telegram Bot API ({download_limit} MB). Подключите MTProto в Connections или используйте /videoimport. Duration pipeline не ограничена."
            )
        cache = context.application.bot_data.get("video_mtproto_messages", {})
        source = cache.get((uid, message.message_id))
        if source is None:
            from telethon.tl.types import InputPeerUser

            source = await client.get_messages(
                InputPeerUser(uid, data["tg_peer_hash"]), ids=message.message_id
            )
        if not source or not source.media:
            raise MediaError(
                "MTProto не нашёл медиа. Подключите MTProto и отправьте файл снова."
            )
        await client.download_media(source, file=str(target))
        return media, getattr(media, "file_name", None) or "media"
    telegram_file = await media.get_file()
    await telegram_file.download_to_drive(target)
    return media, getattr(media, "file_name", None) or (
        "image.jpg" if message.photo else "media"
    )


async def message(update, context):
    try:
        project = current(update, context)
        pending = context.user_data.get("vg_input")
        if pending and pending["project_id"] != project["id"]:
            pending = None
            context.user_data.pop("vg_input", None)
        if update.effective_message.text and not pending:
            if update.effective_message.text == "🎬 Video Generator":
                await home(update, context, project)
            elif update.effective_message.text == "⬅️ Назад":
                ACTIVE_USERS.discard(update.effective_user.id)
                from menu_manager import get_main_keyboard

                await update.effective_message.reply_text(
                    "🎛 Главное меню", reply_markup=get_main_keyboard()
                )
            else:
                await home(update, context, project)
        elif pending and update.effective_message.text:
            await text_input(update, context, project, pending["action"])
        else:
            action = pending["action"] if pending else "beat"
            await media_input(update, context, project, action)
    except (MediaError, ValueError, sqlite3.IntegrityError) as error:
        await update.effective_message.reply_text(f"❌ {str(error)[:1200]}")
    except TelegramError as error:
        logger.warning("Video upload/download failed: %s", error)
        await update.effective_message.reply_text(
            "❌ Telegram не смог передать файл. Проверьте лимит Bot API или используйте локальную Library."
        )
    raise ApplicationHandlerStop


async def media_input(update, context, project, action):
    from .connection_handlers import media_input as connection_media

    if await connection_media(update, context, project, action):
        return
    uid = project["user_id"]
    root = library.storage()
    with tempfile.TemporaryDirectory(prefix="upload_", dir=root / "temp") as work:
        target = Path(work) / "upload"
        media, filename = await download(update, target, context)
        if action.startswith("video:"):
            fid = int(action.split(":")[1])
            library.add_video(uid, fid, target, filename)
            context.user_data.pop("vg_input", None)
            await folder_menu(update, project, fid)
            return
        if action in ("overlay", "backgroundimage") or action.startswith(
            "replaceoverlay:"
        ):
            from PIL import Image, UnidentifiedImageError

            try:
                with Image.open(target) as im:
                    if (
                        im.format not in ("PNG", "WEBP", "JPEG")
                        or im.width * im.height > 40000000
                    ):
                        raise MediaError(
                            "Поддерживаются PNG/WEBP/JPG до 40 megapixels."
                        )
                    alpha = "A" in im.getbands() or "transparency" in im.info
                    extension = "." + (
                        "jpg" if im.format == "JPEG" else im.format.lower()
                    )
                    im.verify()
            except UnidentifiedImageError:
                raise MediaError("Изображение повреждено.")
            dest = root / "overlays" / str(uid) / (media.file_unique_id + extension)
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(target, dest)
            cfg = VideoRenderConfig.from_dict(project["settings"])
            if action == "backgroundimage":
                cfg.background.media = str(dest)
                cfg.background.mode = "Ambient Image"
            elif action.startswith("replaceoverlay:"):
                cfg.overlays[int(action.split(":")[1])].source = str(dest)
            else:
                if len(cfg.overlays) >= 5:
                    raise MediaError("Максимум 5 overlays.")
                cfg.overlays.append(Overlay(source=str(dest), blend="Normal"))
            project["settings"] = cfg.to_dict()
            projects.save_project(project)
            context.user_data.pop("vg_input", None)
            if action == "overlay" and not alpha:
                await update.effective_message.reply_text(
                    "Изображение без alpha. Для чёрного фона можно выбрать Screen в Overlay → blend."
                )
            await home(update, context, project)
            return
        if action != "beat":
            raise MediaError("Для текущего поля требуется текст.")
        info = await asyncio.to_thread(probe, target, "audio")
        if info["duration"] <= 0:
            raise MediaError("Некорректный beat.")
        dest = (
            root / "beats" / str(uid) / f"{media.file_unique_id}_{safe_name(filename)}"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(target, dest)
        from bot import parse_audio_filename

        meta = parse_audio_filename(filename)
        hint = (
            meta.get("bpm")
            or info["tags"].get("TBPM")
            or info["tags"].get("BPM")
            or context.user_data.get("last_bpm")
        )
        project["beat"] = str(dest)
        project["name"] = meta["title"]
        project["bpm_hint"] = float(hint) if hint else None
        project["metadata"] = {
            "artist": getattr(media, "performer", None)
            or info["tags"].get("artist", ""),
            "title": getattr(media, "title", None) or meta["title"],
            "key": meta["key"],
            "year": datetime.now().year,
        }
        project["analysis"] = None
        project["sections_confirmed"] = False
        projects.save_project(project)
        context.user_data.pop("vg_input", None)
        jid = await context.application.bot_data["video_queue"].enqueue(
            project, update.effective_chat.id, "analysis"
        )
        await queued(update, project, jid)


async def text_input(update, context, project, action):
    from .connection_handlers import text_input as connection_text

    if await connection_text(update, context, project, action):
        return
    text = update.effective_message.text.strip()
    parts = action.split(":")
    uid = project["user_id"]
    if parts[0] == "sectionfield":
        index = int(parts[1])
        base = copy.deepcopy(project["settings"])
        section = project["analysis"]["sections"][index]
        for category, values in section.get("visual", {}).items():
            project["settings"][category].update(values)
        mutate_field(project, parts[2], parts[3], text)
        section.setdefault("visual", {})[parts[2]] = copy.deepcopy(
            project["settings"][parts[2]]
        )
        project["settings"] = base
        projects.save_project(project)
        context.user_data.pop("vg_input", None)
        await section_menu(update, project, index)
        return
    if parts[0] == "field":
        mutate_field(project, parts[1], parts[2], text)
        context.user_data.pop("vg_input", None)
        await field_menu(update, project, parts[1], parts[2])
        return
    if action == "seed":
        if not text.isdigit() or not 0 <= int(text) <= 4294967295:
            raise MediaError("Seed: целое число 0..4294967295.")
        project["seed"] = int(text)
        projects.save_project(project)
    elif action == "createfolder":
        library.create_folder(uid, text)
    elif parts[0] == "renamefolder":
        library.rename_folder(uid, int(parts[1]), text)
    elif parts[0] == "assettags":
        library.set_tags(
            uid,
            int(parts[1]),
            [safe_name(x.strip()) for x in text.split(",") if x.strip()][:20],
        )
    elif action == "inputtags":
        project["tags"] = (
            []
            if text == "-"
            else [safe_name(x.strip()) for x in text.split(",") if x.strip()][:20]
        )
        projects.save_project(project)
    elif action == "renameproject":
        project["name"] = safe_name(text)
        projects.save_project(project)
    elif action == "savepreset":
        projects.save_preset(uid, text, project["settings"])
    elif parts[0] == "renamepreset":
        projects.rename_preset(uid, int(parts[1]), text)
    elif parts[0] == "duplicatepreset":
        projects.save_preset(
            uid, text, json.loads(projects.preset(uid, int(parts[1]))["payload"])
        )
    elif parts[0] in ("addsection", "editsection"):
        values = text.split(maxsplit=1)
        if len(values) != 2:
            raise MediaError("Введите timestamp и section name.")
        start = parse_time(values[0])
        name = values[1]
        analysis = project["analysis"]
        if parts[0] == "editsection":
            analysis["sections"][int(parts[1])].update(
                start=start, type=name, confidence=1
            )
        else:
            analysis["sections"].append(
                {
                    "start": start,
                    "end": analysis["duration"],
                    "type": name,
                    "confidence": 1,
                    "energy": 0,
                }
            )
        analysis["sections"] = normalize_sections(
            analysis["sections"], analysis["duration"]
        )
        project["sections_confirmed"] = False
        projects.save_project(project)
    elif parts[0] in ("editevent", "addevent"):
        group = parts[1]
        values = text.split()
        analysis = project["analysis"]
        if group == "drops" and len(values) == 2:
            item = {
                "timestamp": parse_time(values[0]),
                "confidence": float(values[1]),
                "pre_energy": 0,
                "post_energy": 0,
                "enabled": True,
            }
        elif group == "quiet_regions" and len(values) == 3:
            item = {
                "start": parse_time(values[0]),
                "end": parse_time(values[1]),
                "strength": float(values[2]),
                "enabled": True,
                "manual": True,
            }
        else:
            raise MediaError("Неверный формат.")
        if parts[0] == "editevent":
            analysis[group][int(parts[2])] = item
        else:
            analysis[group].append(item)
        analysis[group].sort(key=lambda x: x.get("timestamp", x.get("start")))
        TrackAnalysis.from_dict(analysis)
        projects.save_project(project)
    elif action == "marker":
        analysis = project.get("analysis")
        if not analysis:
            raise MediaError("Дождитесь анализа.")
        values = text.split(maxsplit=2)
        if len(values) < 2:
            raise MediaError("Введите timestamp type.")
        point = parse_time(values[0])
        kind = values[1]
        if not 0 <= point < analysis["duration"]:
            raise MediaError("Marker выходит за трек.")
        if kind == "Drop":
            analysis["drops"].append(
                {
                    "timestamp": point,
                    "confidence": 1,
                    "pre_energy": 0,
                    "post_energy": 0,
                    "enabled": True,
                }
            )
        elif kind == "Blackout":
            if len(values) < 3:
                raise MediaError("Нужен Blackout end timestamp.")
            analysis["quiet_regions"].append(
                {
                    "start": point,
                    "end": parse_time(values[2]),
                    "strength": 1,
                    "enabled": True,
                    "manual": True,
                }
            )
        elif kind == "Section":
            analysis["sections"].append(
                {
                    "start": point,
                    "end": analysis["duration"],
                    "type": values[2] if len(values) > 2 else "Section",
                    "confidence": 1,
                    "energy": 0,
                }
            )
            analysis["sections"] = normalize_sections(
                analysis["sections"], analysis["duration"]
            )
            project["sections_confirmed"] = False
        elif kind == "Clip":
            if len(values) < 3:
                raise MediaError("Нужен library video ID после Clip.")
            aid = int(values[2])
            library.asset(uid, aid)
            previous = next(
                s for s in analysis["sections"] if s["start"] <= point < s["end"]
            )
            if previous["start"] == point:
                previous["video_id"] = aid
            else:
                section = copy.deepcopy(previous)
                section["start"] = point
                section["video_id"] = aid
                analysis["sections"].append(section)
            analysis["sections"] = normalize_sections(
                analysis["sections"], analysis["duration"]
            )
            project["video_mode"] = "sequence"
            project["folder_ids"] = []
            project["sections_confirmed"] = False
        elif kind == "Overlay":
            if len(values) < 3:
                raise MediaError("Нужен layer number после Overlay.")
            index = int(values[2]) - 1
            if not 0 <= index < len(project["settings"]["overlays"]):
                raise MediaError("Overlay layer не найден.")
            project["settings"]["overlays"][index]["timing"] = "Custom Range"
            project["settings"]["overlays"][index]["start"] = point
        elif kind == "Flash":
            project["settings"]["beat_reaction"]["flash"] = "White"
            project["settings"]["beat_reaction"]["flash_trigger"] = "Manual Markers"
        elif kind not in ("Custom",):
            raise MediaError(
                "Используйте Drop, Section, Blackout, Flash, Overlay, Clip, Custom."
            )
        analysis["markers"].append({"timestamp": point, "type": kind})
        TrackAnalysis.from_dict(analysis)
        projects.save_project(project)
    elif action == "previewrange":
        values = text.split()
        if len(values) != 2:
            raise MediaError("Введите start end.")
        a, b = map(parse_time, values)
        if not 0 <= a < b <= project["analysis"]["duration"] or b - a > 15:
            raise MediaError("Preview range: 0..duration, максимум 15 секунд.")
        jid = await context.application.bot_data["video_queue"].enqueue(
            project, update.effective_chat.id, "preview", [a, b]
        )
        context.user_data.pop("vg_input", None)
        await queued(update, project, jid)
        return
    else:
        raise MediaError("Отправьте ожидаемый медиафайл.")
    context.user_data.pop("vg_input", None)
    await home(update, context, project)


async def import_local(update, context):
    from bot import is_admin_user

    if not is_admin_user(update):
        await update.effective_message.reply_text("Команда доступна администратору.")
        raise ApplicationHandlerStop
    try:
        text = update.effective_message.text.split(maxsplit=2)
        if len(text) != 3:
            raise MediaError(
                "/videoimport folder_name /absolute/path/video_or_directory"
            )
        folder_name, path = text[1], Path(text[2]).expanduser().resolve()
        found = next(
            (
                f
                for f in library.folders(update.effective_user.id)
                if f["name"] == safe_name(folder_name)
            ),
            None,
        )
        fid = (
            found["id"]
            if found
            else library.create_folder(update.effective_user.id, folder_name)
        )
        sources = sorted(path.iterdir()) if path.is_dir() else [path]
        count = 0
        errors = []
        for source in sources:
            if not source.is_file():
                continue
            try:
                await asyncio.to_thread(probe, source, "video")
                library.add_video(update.effective_user.id, fid, source)
                count += 1
            except (MediaError, OSError) as error:
                errors.append(f"{source.name}: {error}")
        await update.effective_message.reply_text(
            f"✅ Imported: {count}\n" + "\n".join(errors)[:2000]
        )
    except (MediaError, ValueError, OSError) as error:
        await update.effective_message.reply_text(f"❌ {error}")
    raise ApplicationHandlerStop


async def local_beat(update, context):
    from bot import is_admin_user, parse_audio_filename
    from telegram.ext import ApplicationHandlerStop

    try:
        if not is_admin_user(update):
            raise MediaError("Только administrator.")
        command = update.effective_message.text.split(maxsplit=1)
        if len(command) != 2:
            raise MediaError("/videobeat /absolute/path/beat.wav")
        source = Path(command[1]).expanduser().resolve()
        info = await asyncio.to_thread(probe, source, "audio")
        if info["duration"] <= 0:
            raise MediaError("Некорректный beat.")
        project = current(update, context)
        from .media import file_hash

        digest = await asyncio.to_thread(file_hash, source)
        dest = (
            library.storage()
            / "beats"
            / str(project["user_id"])
            / (digest[:16] + "_" + safe_name(source.name))
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        await asyncio.to_thread(shutil.copy2, source, dest)
        meta = parse_audio_filename(source.name)
        project.update(
            beat=str(dest),
            bpm_hint=float(meta["bpm"])
            if meta["bpm"]
            else context.user_data.get("last_bpm"),
            analysis=None,
            sections_confirmed=False,
            name=meta["title"],
        )
        project["metadata"] = {
            "artist": info["tags"].get("artist", ""),
            "title": meta["title"],
            "key": meta["key"],
            "year": datetime.now().year,
        }
        projects.save_project(project)
        ACTIVE_USERS.add(project["user_id"])
        jid = await context.application.bot_data["video_queue"].enqueue(
            project, update.effective_chat.id, "analysis"
        )
        await queued(update, project, jid)
    except (MediaError, OSError, ValueError) as error:
        await update.effective_message.reply_text(f"❌ {error}")
    raise ApplicationHandlerStop


async def debug_analysis(update, context):
    from bot import is_admin_user

    if not is_admin_user(update):
        await update.effective_message.reply_text("Только administrator.")
        raise ApplicationHandlerStop
    try:
        data = current(update, context).get("analysis")
        if not data:
            raise MediaError("Нет analysis.")
        import io

        await update.effective_message.reply_document(
            io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode()),
            filename="analysis.json",
        )
    except MediaError as error:
        await update.effective_message.reply_text(str(error))
    raise ApplicationHandlerStop


def register(application):
    from .connection_handlers import command

    application.add_handler(CommandHandler("videoconnect", command), group=-1)
    application.add_handler(CommandHandler("video", open_video), group=-1)
    application.add_handler(CommandHandler("videoimport", import_local), group=-1)
    application.add_handler(CommandHandler("videobeat", local_beat), group=-1)
    application.add_handler(CommandHandler("videoanalysis", debug_analysis), group=-1)
    application.add_handler(CommandHandler("start", exit_on_start), group=-1)
    application.add_handler(CallbackQueryHandler(callback, pattern=r"^vg:"), group=-1)
    application.add_handler(
        MessageHandler(filters.Regex("^🎬 Video Generator$"), open_video), group=-1
    )
    application.add_handler(
        MessageHandler(VideoMessages() & ~filters.COMMAND, message), group=-1
    )
    previous_init = application.post_init

    async def initialize(app):
        from .runtime import load as runtime_settings

        queue = RenderQueue(app.bot, runtime_settings().concurrent_renders)
        app.bot_data["video_queue"] = queue
        await queue.start()
        from bot import ADMIN_USER_ID
        from .connections import load
        from .connection_handlers import start_listener

        if ADMIN_USER_ID and load(ADMIN_USER_ID).get("tg_session"):
            try:
                await start_listener(app, ADMIN_USER_ID)
            except MediaError:
                logger.warning(
                    "MTProto unavailable at startup; reconnect through /videoconnect."
                )
        if previous_init:
            await previous_init(app)

    async def shutdown(app):
        await app.bot_data["video_queue"].close()
        for client in app.bot_data.get("video_mtproto", {}).values():
            await client.disconnect()

    application.post_init = initialize
    application.post_stop = shutdown
