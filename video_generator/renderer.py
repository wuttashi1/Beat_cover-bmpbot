import asyncio
import copy
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path

from .config import VideoRenderConfig
from .encoders import encoder_options
from .media import MediaError, probe, require_ffmpeg


@dataclass
class Clip:
    path: str
    start: float
    end: float
    offset: float = 0
    transition: str = "Hard Cut"
    visual: dict | None = None


@dataclass
class RenderCommand:
    args: list[str]
    graph: str
    duration: float
    width: int
    height: int
    fps: float
    hardware: str | None = None


def intervals_enable(intervals, offset=0):
    return (
        "+".join(
            f"between(t,{max(0, a - offset):.6f},{b - offset:.6f})"
            for a, b in intervals
            if b > offset
        )
        or "0"
    )


def darkness_expression(regions, settings, offset=0):
    pieces = []
    for region in regions:
        if not region.get("enabled", True):
            continue
        start = region["start"] - offset
        end = region["end"] - offset + settings.restore_offset_ms / 1000
        if end <= 0 or end <= start:
            continue
        fade = max(settings.fade_ms / 1000, 0.000001)
        restore = max(settings.restore_ms / 1000, 0.000001)
        strength = min(1, region["strength"]) * settings.strength / 100
        if settings.mode == "Full Black":
            strength = 1
        pieces.append(
            f"{strength:.6f}*max(0,min(1,min((t-({start:.6f}))/{fade:.6f},(({end:.6f})-t)/{restore:.6f})))"
        )
    return "min(1," + "+".join(pieces) + ")" if pieces else "0"


def apply_blackout(graph, label, regions, cfg, offset, output):
    darkness = darkness_expression(regions, cfg, offset)
    if darkness == "0":
        return label
    if cfg.mode == "Desaturate":
        graph.append(f"[{label}]eq=saturation='1-({darkness})':eval=frame[{output}]")
    elif cfg.mode == "Blur":
        graph.append(
            f"[{label}]gblur=sigma=25:enable='{intervals_enable([(r['start'], r['end']) for r in regions if r.get('enabled', True)], offset)}'[{output}]"
        )
    else:
        expression = re.sub(r"\bt\b", "T", darkness)
        graph.append(
            f"color=c=black@0:s=16x16:r=120,format=rgba,geq=r=0:g=0:b=0:a='255*({expression})'[{output}alpha]"
        )
        graph.append(
            f"[{output}alpha][{label}]scale2ref=w=iw:h=ih[{output}shade][{output}base]"
        )
        prefix = "gblur=sigma=15," if cfg.mode == "Dark + Blur" else ""
        graph.append(f"[{output}base][{output}shade]overlay=shortest=1[{output}mix]")
        graph.append(f"[{output}mix]{prefix}null[{output}]")
    return output


def anchor_position(anchor, w, h, iw, ih, margin, x=0, y=0):
    if anchor == "Custom":
        return str(x), str(y)
    px = (
        str(margin)
        if "Left" in anchor
        else f"{w}-{iw}-{margin}"
        if "Right" in anchor
        else f"({w}-{iw})/2"
    )
    py = (
        str(margin)
        if "Top" in anchor
        else f"{h}-{ih}-{margin}"
        if "Bottom" in anchor
        else f"({h}-{ih})/2"
    )
    return f"({px})+{x}", f"({py})+{y}"


def color_filter(color):
    values = [
        f"eq=brightness={color.brightness / 100}:contrast={color.contrast / 100}:saturation={color.saturation / 100}:gamma={color.gamma}"
    ]
    if color.temperature or color.tint or color.highlights or color.shadows:
        temp, tint = color.temperature / 200, color.tint / 200
        values.append(
            f"colorbalance=rs={color.shadows / 200}:gs={tint}:bs={-temp}:rh={color.highlights / 200}:rm={temp}:bm={-temp}"
        )
    if color.invert:
        values.append("negate")
    if color.fade:
        values.append(
            f"eq=contrast={1 - color.fade / 200}:brightness={color.fade / 400}"
        )
    return ",".join(values)


def motion_filters(
    cfg, fps, width, height, duration, bpm, seed, section_start, analysis
):
    motion = cfg.motion
    beat = cfg.beat_reaction
    enabled = cfg.transitions.react
    if motion.mode == "None" and not (
        enabled
        and beat.enabled
        and (beat.kick_zoom or beat.shake or beat.drop_effect == "Punch")
    ):
        return ""
    t = f"(on/{fps:.8f}+{section_start:.6f})"
    local_t = f"on/{fps:.8f}"
    length = motion.duration_bars * 240 / max(bpm, 1)
    progress = f"min(1,({local_t if motion.reset_on_section else t})/{length})"
    zoom = "1"
    intensity = motion.intensity / 100
    if motion.mode in ("Slow Zoom In", "Ken Burns"):
        zoom = f"1+{intensity}*({progress})"
    elif motion.mode == "Slow Zoom Out":
        zoom = f"1+{intensity}*(1-({progress}))"
    elif motion.mode != "None":
        zoom = str(1 + intensity)
    if enabled and beat.enabled and beat.drop_effect == "Punch":
        points = [
            d["timestamp"]
            for d in analysis.get("drops", [])
            if d.get("enabled", True)
            and section_start <= d["timestamp"] < section_start + duration
        ]
        punch = "+".join(f"max(0,1-abs({t}-{point})/.18)" for point in points) or "0"
        zoom += f"+0.02*min(1,{punch})"
    if enabled and beat.enabled and beat.kick_zoom:
        period = 60 / max(bpm, 1) * (4 if beat.frequency == "Strong Beats" else 1)
        if beat.frequency in ("Every Beat", "Strong Beats"):
            pulse = f"max(0,1-mod({t},{period})/{beat.duration_ms / 1000})"
        else:
            points = (
                [
                    r["timestamp"]
                    for r in analysis.get("drops", [])
                    if r.get("enabled", True)
                ]
                if beat.frequency == "Drops Only"
                else [s["start"] for s in analysis.get("sections", [])]
            )
            points = [
                p for p in points if section_start <= p < section_start + duration
            ]
            pulse = (
                "+".join(
                    f"max(0,1-abs({t}-{p})/{beat.duration_ms / 1000})" for p in points
                )
                or "0"
            )
        zoom += f"+{beat.strength / 100}*min(1,{pulse})"
    px, py = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    rng = random.Random(seed)
    phase = rng.random() * math.tau
    if motion.mode in ("Float", "Vertical Drift", "Random Drift", "Ken Burns"):
        py += f"+(ih-ih/zoom)/2*sin({local_t}/3+{phase})"
    if motion.mode in ("Float", "Horizontal Drift", "Random Drift", "Ken Burns"):
        px += f"+(iw-iw/zoom)/2*sin({local_t}/4+{phase})"
    if enabled and beat.enabled and beat.shake:
        px += f"+{beat.shake_strength}*sin({t}*31)"
        py += f"+{beat.shake_strength}*cos({t}*29)"
    return f",zoompan=z='{zoom}':x='{px}':y='{py}':d=1:s={width}x{height}:fps={fps:.8f}"


def select_clips(project, videos, duration):
    if not videos:
        raise MediaError("В выбранных папках нет доступных видео.")
    rng = random.Random(project["seed"])
    mode = project.get("video_mode", "single")
    cfg = VideoRenderConfig.from_dict(project["settings"])
    analysis = project.get("analysis") or {}
    if mode == "single":
        selected = next((v for v in videos if v["id"] == project.get("video_id")), None)
        if selected is None:
            raise MediaError("Выбранное видео недоступно.")
        boundaries = [(0, duration, {})]
    elif (
        mode == "random"
        or not cfg.transitions.react
        or cfg.transitions.auto_change == "OFF"
    ):
        selected = rng.choice(videos)
        boundaries = [(0, duration, {})]
    else:
        selected = None
        sections = analysis.get("sections", [])
        if (
            cfg.transitions.auto_change.startswith("Every ")
            and "Bars" in cfg.transitions.auto_change
        ):
            bars = int(cfg.transitions.auto_change.split()[1])
            step = bars * 240 / analysis["bpm"]
            starts = [i * step for i in range(math.ceil(duration / step))]
            boundaries = [(a, min(duration, a + step), {}) for a in starts]
        else:
            sections = [
                s
                for s in sections
                if cfg.transitions.auto_change != "Every Drop"
                or "Drop" in s["type"]
                or s["start"] == 0
            ]
            boundaries = [
                (
                    s["start"],
                    sections[i + 1]["start"] if i + 1 < len(sections) else duration,
                    s,
                )
                for i, s in enumerate(sections)
            ] or [(0, duration, {})]
    if selected and cfg.transitions.react and analysis.get("sections"):
        boundaries = [(s["start"], s["end"], s) for s in analysis["sections"]]
    shared_offset = (
        cfg.foreground.start_offset if cfg.foreground.start_mode == "Custom" else 0
    )
    if selected and cfg.foreground.start_mode == "Random":
        shared_offset = rng.uniform(
            0, max(0, selected["duration"] - duration * cfg.foreground.speed)
        )
    clips, previous = [], None
    for start, end, section in boundaries:
        if section.get("video_id"):
            item = next((v for v in videos if v["id"] == section["video_id"]), None)
            if item is None:
                raise MediaError("Видео для section недоступно.")
        elif selected:
            item = selected
        elif section.get("keep_previous") and previous:
            item = previous
        else:
            candidates = (
                [v for v in videos if not previous or v["id"] != previous["id"]]
                if cfg.transitions.avoid_repeat
                else videos
            )
            item = rng.choice(candidates or videos)
        offset = (
            cfg.foreground.start_offset if cfg.foreground.start_mode == "Custom" else 0
        )
        if cfg.foreground.start_mode == "Random":
            offset = rng.uniform(
                0, max(0, item["duration"] - (end - start) * cfg.foreground.speed)
            )
        if selected:
            offset = shared_offset + start * cfg.foreground.speed
            if cfg.foreground.loop:
                offset %= item["duration"]
            else:
                offset = min(
                    offset, max(0, item["duration"] - 1 / max(item.get("fps") or 30, 1))
                )
        if offset >= item["duration"]:
            raise MediaError("Start offset выходит за продолжительность source video.")
        transition = section.get("transition") or (
            cfg.transitions.drop
            if "Drop" in section.get("type", "")
            else cfg.transitions.normal
        )
        clips.append(
            Clip(item["path"], start, end, offset, transition, section.get("visual"))
        )
        previous = item
    return clips


def build_command(
    audio,
    clips,
    cfg,
    analysis,
    output,
    workdir,
    seed=0,
    preview_range=None,
    metadata=None,
):
    require_ffmpeg()
    duration = probe(audio, "audio")["duration"]
    if duration <= 0:
        raise MediaError("Audio duration должна быть больше нуля.")
    cfg.validate(duration)
    start, end = preview_range or (0, duration)
    if not 0 <= start < end <= duration + 0.001:
        raise MediaError("Некорректный preview range.")
    total = end - start
    scale = min(1, 720 / cfg.canvas.height) if preview_range else 1
    w, h = (
        int(cfg.canvas.width * scale) // 2 * 2,
        int(cfg.canvas.height * scale) // 2 * 2,
    )
    fps = cfg.canvas.fps or min(120, probe(clips[0].path, "video")["fps"] or 30)
    fg = cfg.foreground
    args = ["ffmpeg", "-hide_banner", "-y", "-nostdin", "-filter_complex_threads", "1"]
    visible = [c for c in clips if c.end > start and c.start < end]
    if not visible:
        raise MediaError("Нет видео для выбранного диапазона.")
    graph, labels, segment_lengths, transition_lengths = [], [], [], []
    extra_inputs = []
    next_input = len(visible)
    quiet = list(analysis.get("quiet_regions", [])) if cfg.blackout.enabled else []
    if (
        cfg.beat_reaction.enabled
        and cfg.transitions.react
        and cfg.beat_reaction.drop_effect == "Dark Drop"
        and cfg.blackout.enabled
    ):
        quiet += [
            dict(
                start=max(0, d["timestamp"] - 0.25),
                end=d["timestamp"],
                strength=1,
                enabled=True,
                manual=True,
            )
            for d in analysis.get("drops", [])
            if d.get("enabled", True)
        ]
    if cfg.blackout.only_before_drop:
        drops = [
            d["timestamp"] for d in analysis.get("drops", []) if d.get("enabled", True)
        ]
        quiet = [
            r
            for r in quiet
            if r.get("manual") or any(-0.1 <= t - r["end"] <= 1.5 for t in drops)
        ]
    global_darkness = (
        cfg.blackout.foreground and cfg.blackout.background and cfg.blackout.overlay
    )
    for i, clip in enumerate(visible):
        clip_cfg = VideoRenderConfig.from_dict(cfg.to_dict())
        if clip.visual:
            data = clip_cfg.to_dict()
            for category, values in clip.visual.items():
                data[category].update(values)
            clip_cfg = VideoRenderConfig.from_dict(data)
        fg = clip_cfg.foreground
        source = probe(clip.path, "video")
        a, b = max(start, clip.start), min(end, clip.end)
        transition_time = 0
        if (
            i
            and clip.path != visible[i - 1].path
            and clip.transition not in ("Hard Cut", "None")
            and cfg.transitions.react
        ):
            transition_time = min(
                cfg.transitions.duration_ms / 1000,
                (b - a) / 2,
                (min(end, visible[i - 1].end) - max(start, visible[i - 1].start)) / 2,
            )
        a -= transition_time
        segment = b - a
        segment_lengths.append(segment)
        transition_lengths.append(transition_time)
        offset = (
            (clip.offset + (a - clip.start) * fg.speed) % source["duration"]
            if fg.loop
            else clip.offset + (a - clip.start) * fg.speed
        )
        args += (["-stream_loop", "-1"] if fg.loop else []) + [
            "-ss",
            str(offset),
            "-i",
            str(clip.path),
        ]
        chain = f"[{i}:v]setpts=(PTS-STARTPTS)/{fg.speed},fps={fps:.8f}"
        if not fg.loop:
            chain += f",tpad=stop_mode=clone:stop_duration={segment}"
        chain += f",trim=duration={segment},setsar=1,split=2[bg{i}][fg{i}]"
        graph.append(chain)
        bg = clip_cfg.background
        external = bg.mode in ("Ambient Image", "Custom Media") or (
            bg.mode == "Ambient Video" and not bg.follows_foreground
        )
        background_input = f"bg{i}"
        if external:
            if not bg.media or not Path(bg.media).is_file():
                raise MediaError("Выберите отдельный background media.")
            graph.append(f"[bg{i}]nullsink")
            if bg.mode == "Ambient Image":
                extra_inputs += ["-loop", "1", "-framerate", str(fps), "-i", bg.media]
            else:
                extra_inputs += [
                    "-stream_loop",
                    "-1",
                    "-ss",
                    str(a * bg.speed),
                    "-i",
                    bg.media,
                ]
            background_input = f"external{i}"
            graph.append(
                f"[{next_input}:v]setpts=(PTS-STARTPTS)/{bg.speed},fps={fps:.8f},trim=duration={segment},setsar=1[{background_input}]"
            )
            next_input += 1
        if bg.mode not in ("Black", "Solid Color"):
            bw, bh = (
                math.ceil(w * bg.scale / 100 / 2) * 2,
                math.ceil(h * bg.scale / 100 / 2) * 2,
            )
            flip = (
                ",hflip"
                if bg.mirror == "Horizontal"
                else ",vflip"
                if bg.mirror == "Vertical"
                else ""
            )
            graph.append(
                f"[{background_input}]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={w}:{h}:x='clip((iw-ow)/2+{bg.x * scale},0,iw-ow)':y='clip((ih-oh)/2+{bg.y * scale},0,ih-oh)'{flip},gblur=sigma={bg.blur * scale},eq=brightness={bg.brightness / 100}:contrast={bg.contrast / 100}:saturation={bg.saturation / 100}[back{i}]"
            )
            if bg.opacity < 100 or bg.tint:
                graph.append(
                    f"color=c={bg.color}:s={w}x{h}:r={fps:.8f}:d={segment}[bc{i}]"
                )
                graph.append(
                    f"[bc{i}][back{i}]blend=all_mode=normal:all_opacity={bg.opacity / 100 * (1 - bg.tint / 100)}[bt{i}]"
                )
                back = f"bt{i}"
            else:
                back = f"back{i}"
        else:
            graph.append(f"[bg{i}]nullsink")
            graph.append(
                f"color=c={bg.color if bg.mode == 'Solid Color' else 'black'}:s={w}x{h}:r={fps:.8f}:d={segment}[back{i}]"
            )
            back = f"back{i}"
        if quiet and not global_darkness and cfg.blackout.background:
            back = apply_blackout(graph, back, quiet, cfg.blackout, a, f"bd{i}")
        fw, fh = (
            max(2, int(fg.width * scale * fg.scale / 100) // 2 * 2),
            max(2, int(fg.height * scale * fg.scale / 100) // 2 * 2),
        )
        cropx, cropy = (50, 50) if fg.crop == "1:1 Center" else (fg.crop_x, fg.crop_y)
        if fg.crop in ("1:1 Center", "1:1 Custom", "Fill", "Custom Ratio"):
            chain = f"scale={fw}:{fh}:force_original_aspect_ratio=increase,crop={fw}:{fh}:x=(iw-ow)*{cropx / 100}:y=(ih-oh)*{cropy / 100}"
        else:
            chain = f"scale={fw}:{fh}:force_original_aspect_ratio=decrease,format=rgba,pad={fw}:{fh}:(ow-iw)/2:(oh-ih)/2:color=none"
        chain += "," + color_filter(clip_cfg.color)
        if fg.mirror != "OFF":
            chain += ",hflip" if fg.mirror == "Horizontal" else ",vflip"
        if fg.sharpness:
            chain += f",unsharp=5:5:{fg.sharpness}"
        if fg.rotation:
            chain += f",rotate={fg.rotation}*PI/180:ow=rotw({fg.rotation}*PI/180):oh=roth({fg.rotation}*PI/180):c=none"
        chain += motion_filters(
            clip_cfg,
            fps,
            fw,
            fh,
            segment,
            analysis.get("bpm", 120),
            seed + i,
            a,
            analysis,
        )
        chain += f",format=rgba,colorchannelmixer=aa={fg.opacity / 100}"
        if fg.border:
            border = max(1, round(fg.border_width * scale))
            chain += f",pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:color={fg.border_color}@{fg.border_opacity / 100}"
        graph.append(f"[fg{i}]{chain}[front{i}]")
        front = f"front{i}"
        if quiet and not global_darkness and cfg.blackout.foreground:
            front = apply_blackout(graph, front, quiet, cfg.blackout, a, f"fd{i}")
        if (
            cfg.beat_reaction.enabled
            and cfg.transitions.react
            and cfg.beat_reaction.background_pulse
        ):
            period = 240 / max(analysis.get("bpm", 120), 1)
            graph.append(
                f"[{back}]eq=brightness='0.02*max(0,1-mod(t+{a},{period})/0.12)':eval=frame[bpulse{i}]"
            )
            back = f"bpulse{i}"
        if (
            cfg.beat_reaction.enabled
            and cfg.transitions.react
            and cfg.beat_reaction.brightness_pulse
        ):
            period = 240 / max(analysis.get("bpm", 120), 1)
            graph.append(
                f"[{front}]eq=brightness='0.025*max(0,1-mod(t+{a},{period})/0.12)':eval=frame[fpulse{i}]"
            )
            front = f"fpulse{i}"
        graph.append(
            f"[{back}][{front}]overlay=x=(W-w)/2+{fg.x * scale}:y=(H-h)/2+{fg.y * scale}:shortest=1,format=yuv420p,trim=duration={segment},setpts=PTS-STARTPTS,fps={fps:.8f},settb=AVTB[scene{i}]"
        )
        labels.append(f"scene{i}")
    args += extra_inputs
    scene = labels[0]
    elapsed = segment_lengths[0]
    transitions = {
        "Fade": "fade",
        "Crossfade": "fade",
        "Dip to Black": "fadeblack",
        "Blur": "hblur",
        "Zoom": "zoomin",
    }
    for i in range(1, len(labels)):
        transition_time = transition_lengths[i]
        if transition_time:
            graph.append(
                f"[{scene}][{labels[i]}]xfade=transition={transitions[visible[i].transition]}:duration={transition_time}:offset={elapsed - transition_time}[join{i}]"
            )
        else:
            graph.append(f"[{scene}][{labels[i]}]concat=n=2:v=1:a=0[join{i}]")
        scene = f"join{i}"
        elapsed += segment_lengths[i] - transition_time
    input_index = next_input
    for i, overlay in enumerate(cfg.overlays):
        if not Path(overlay.source).is_file():
            raise MediaError("Overlay asset недоступен.")
        args += ["-loop", "1", "-framerate", str(fps), "-i", overlay.source]
        from PIL import Image

        with Image.open(overlay.source) as im:
            ow, oh = im.size
        factor = overlay.scale / 100 * scale
        if overlay.anchor == "Full frame":
            factor = min(w / ow, h / oh) * overlay.scale / 100
        ow, oh = (
            max(2, round(ow * factor) // 2 * 2),
            max(2, round(oh * factor) // 2 * 2),
        )
        rotation = (
            f",rotate={overlay.rotation}*PI/180:ow=rotw({overlay.rotation}*PI/180):oh=roth({overlay.rotation}*PI/180):c=none"
            if overlay.rotation
            else ""
        )
        chain = f"[{input_index + i}:v]scale={ow}:{oh},format=rgba{rotation},colorchannelmixer=aa={overlay.opacity / 100}"
        if overlay.animation in ("Fade In", "Fade In/Out"):
            chain += f",fade=t=in:st={max(0, overlay.start - start)}:d={1 / overlay.animation_speed}:alpha=1"
        if overlay.animation in ("Fade Out", "Fade In/Out"):
            chain += f",fade=t=out:st={max(0, (overlay.end or end) - start - 1 / overlay.animation_speed)}:d={1 / overlay.animation_speed}:alpha=1"
        if overlay.animation in ("Slow Zoom", "Pulse", "Beat Pulse") or (
            cfg.beat_reaction.enabled and cfg.beat_reaction.overlay_pulse
        ):
            intensity = overlay.intensity / 100
            frequency = (
                analysis.get("bpm", 120) / 60
                if overlay.animation == "Beat Pulse" or cfg.beat_reaction.overlay_pulse
                else overlay.animation_speed
            )
            z = (
                f"1+{intensity}*min(1,on/{fps:.8f}/{total})"
                if overlay.animation == "Slow Zoom"
                else f"1+{intensity}*(1+sin(on/{fps:.8f}*{frequency}*2*PI))/2"
            )
            chain += f",zoompan=z='{z}':d=1:s={ow}x{oh}:fps={fps:.8f}"
        graph.append(chain + f"[ol{i}]")
        layer = f"ol{i}"
        if quiet and not global_darkness and cfg.blackout.overlay:
            layer = apply_blackout(graph, layer, quiet, cfg.blackout, start, f"od{i}")
        if overlay.timing == "Intro Only":
            ranges = [
                (s["start"], s["end"])
                for s in analysis.get("sections", [])
                if s["type"] == "Intro"
            ]
        elif overlay.timing == "Drops Only":
            ranges = [
                (s["start"], s["end"])
                for s in analysis.get("sections", [])
                if "Drop" in s["type"]
            ]
        elif overlay.timing == "Selected Sections":
            indices = {
                int(x.strip())
                for x in overlay.selected_sections.split(",")
                if x.strip().isdigit()
            }
            ranges = [
                (s["start"], s["end"])
                for j, s in enumerate(analysis.get("sections", []), 1)
                if j in indices
            ]
        else:
            ranges = [(overlay.start, overlay.end or duration)]
        enabled = intervals_enable(ranges, start)
        x, y = anchor_position(
            overlay.anchor,
            w,
            h,
            "w",
            "h",
            overlay.margin * scale,
            overlay.x * scale,
            overlay.y * scale,
        )
        if overlay.animation == "Float":
            y += f"+{overlay.intensity * scale}*sin(t*{overlay.animation_speed})"
        if overlay.blend == "Normal":
            graph.append(
                f"[{scene}][{layer}]overlay=x='{x}':y='{y}':enable='{enabled}':shortest=1[comp{i}]"
            )
        else:
            neutral = (
                "white"
                if overlay.blend == "Multiply"
                else "0x808080"
                if overlay.blend == "Overlay"
                else "black"
            )
            graph.append(
                f"color=c={neutral}:s={w}x{h}:r={fps:.8f}:d={total}[neutral{i}]"
            )
            graph.append(
                f"[neutral{i}][{layer}]overlay=x='{x}':y='{y}':shortest=1[blend{i}]"
            )
            modes = {
                "Screen": "screen",
                "Add": "addition",
                "Lighten": "lighten",
                "Multiply": "multiply",
                "Overlay": "overlay",
            }
            graph.append(f"[{scene}]format=gbrp[blendbase{i}]")
            graph.append(f"[blend{i}]format=gbrp[blendrgb{i}]")
            graph.append(
                f"[blendbase{i}][blendrgb{i}]blend=all_mode={modes[overlay.blend]}:enable='{enabled}'[comp{i}]"
            )
        scene = f"comp{i}"
    metadata = dict(
        metadata or {}, bpm=f"{analysis.get('bpm', 0):g}", duration=duration
    )
    for i, layer in enumerate(cfg.branding):
        text = layer.text
        for key in ("artist", "title", "bpm", "key", "duration", "year"):
            text = text.replace("{" + key + "}", str(metadata.get(key) or ""))
        textpath = Path(workdir) / f"text_{i}.txt"
        textpath.write_text(text, encoding="utf-8")
        font = (
            f":fontfile='{layer.font}'"
            if layer.font and Path(layer.font).is_file() and "'" not in layer.font
            else ""
        )
        x, y = anchor_position(
            layer.anchor,
            w,
            h,
            "text_w",
            "text_h",
            layer.margin * scale,
            layer.x * scale,
            layer.y * scale,
        )
        graph.append(
            f"[{scene}]drawtext=textfile='{textpath}':expansion=none{font}:fontsize={layer.size * scale}:fontcolor={layer.color}@{layer.opacity / 100}:x='{x}':y='{y}':enable='{intervals_enable([(layer.start, layer.end or duration)], start)}'[text{i}]"
        )
        scene = f"text{i}"
    if quiet and global_darkness:
        scene = apply_blackout(graph, scene, quiet, cfg.blackout, start, "dark")
    reaction = copy.deepcopy(cfg.beat_reaction)
    if reaction.drop_effect == "Flash Drop" and cfg.transitions.react:
        reaction.flash = "White"
        reaction.flash_trigger = "Drop"
    if reaction.enabled and cfg.transitions.react and reaction.flash != "OFF":
        if reaction.flash_trigger == "Strong Beat":
            points = analysis.get("bars", [])
        elif reaction.flash_trigger == "Section Change":
            points = [s["start"] for s in analysis.get("sections", [])]
        elif reaction.flash_trigger == "Manual Markers":
            points = [
                m["timestamp"]
                for m in analysis.get("markers", [])
                if m["type"] == "Flash"
            ]
        else:
            points = [
                d["timestamp"]
                for d in analysis.get("drops", [])
                if d.get("enabled", True)
            ]
        intervals = [
            (t, t + reaction.flash_frames / fps) for t in points if start <= t < end
        ]
        color = (
            "white"
            if reaction.flash == "White"
            else "black"
            if reaction.flash == "Black"
            else reaction.flash_color
        )
        graph.append(
            f"[{scene}]drawbox=x=0:y=0:w=iw:h=ih:color={color}@{reaction.flash_opacity / 100}:t=fill:enable='{intervals_enable(intervals, start)}'[flashed]"
        )
        scene = "flashed"
    if preview_range and cfg.canvas.guides:
        graph.append(
            f"[{scene}]drawbox=x={(w - h) / 2}:y=0:w={h}:h={h}:color=white@0.5:t=2,drawbox=x={w * 0.05}:y={h * 0.05}:w={w * 0.9}:h={h * 0.9}:color=yellow@0.5:t=2[guides]"
        )
        scene = "guides"
    hardware = encoder_options(cfg.export, bool(preview_range))
    if cfg.export.encoder not in ("CPU", "Auto") and not hardware and not preview_range:
        if not cfg.export.fallback_cpu:
            raise MediaError("Выбранный hardware encoder недоступен.")
    graph.append(
        f"[{scene}]format=nv12,hwupload[out]"
        if hardware and hardware["encoder"].endswith("_vaapi")
        else f"[{scene}]format=yuv420p[out]"
    )
    if hardware and hardware["device"]:
        args[1:1] = hardware["device"]
    audio_index = next_input + len(cfg.overlays)
    args += ["-ss", str(start), "-i", str(audio)]
    graph_text = ";\n".join(graph)
    graphpath = Path(workdir) / "filters.txt"
    graphpath.write_text(graph_text, encoding="utf-8")
    args += [
        "-filter_complex_script",
        str(graphpath),
        "-map",
        "[out]",
        "-map",
        f"{audio_index}:a:0",
        "-t",
        str(total),
    ]
    crf = 23 if preview_range else cfg.export.crf
    if hardware:
        args += ["-c:v", hardware["encoder"]]
        if hardware["encoder"].endswith("_nvenc"):
            args += ["-cq", str(crf), "-b:v", "0"]
        elif hardware["encoder"].endswith("_qsv"):
            args += ["-global_quality", str(crf)]
        else:
            args += ["-qp", str(crf)]
    else:
        args += [
            "-c:v",
            "libx264" if cfg.export.codec == "H.264" else "libx265",
            "-preset",
            "veryfast" if preview_range else cfg.export.preset,
            "-crf",
            str(crf),
        ]
    if not hardware and cfg.export.codec == "H.265":
        args += [
            "-x265-params",
            f"pools={cfg.export.threads}:frame-threads={min(4, cfg.export.threads)}:log-level=error",
        ]
    args += ["-threads", str(cfg.export.threads)]
    if not hardware or not hardware["encoder"].endswith("_vaapi"):
        args += ["-pix_fmt", "yuv420p"]
    args += [
        "-c:a",
        "aac",
        "-b:a",
        f"{cfg.export.audio_bitrate}k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    return RenderCommand(
        args, graph_text, total, w, h, fps, hardware["encoder"] if hardware else None
    )


async def run_render(command, log_path, cancel, progress=None):
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    process = None
    with open(log_path, "wb") as log:
        try:
            process = await asyncio.create_subprocess_exec(
                *command.args,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                start_new_session=True,
            )

            async def watch_cancel():
                await cancel.wait()
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        process.kill()

            watcher = asyncio.create_task(watch_cancel())
            try:
                while line := await process.stdout.readline():
                    text = line.decode(errors="replace").strip()
                    if text.startswith("out_time_us=") and progress:
                        value = text.split("=", 1)[1]
                        if value.isdigit():
                            await progress(
                                min(0.99, int(value) / 1e6 / command.duration)
                            )
                await process.wait()
                if cancel.is_set():
                    raise asyncio.CancelledError
                if process.returncode:
                    raise MediaError(f"FFmpeg не смог создать видео. Log: {log_path}")
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
