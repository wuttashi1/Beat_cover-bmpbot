import math
import os
import re
from dataclasses import asdict, dataclass, field, fields


def number(default, low, high, step=1):
    return field(default=default, metadata={"min": low, "max": high, "step": step})


def choice(default, *values):
    return field(default=default, metadata={"choices": values})


@dataclass
class Canvas:
    width: int = number(2560, 320, 7680, 2)
    height: int = number(1440, 180, 4320, 2)
    fps: float = number(0, 0, 120)
    guides: bool = False


@dataclass
class Color:
    brightness: float = number(0, -100, 100)
    contrast: float = number(100, 0, 300)
    saturation: float = number(100, 0, 200)
    gamma: float = number(1, 0.1, 10, 0.1)
    temperature: float = number(0, -100, 100)
    tint: float = number(0, -100, 100)
    highlights: float = number(0, -100, 100)
    shadows: float = number(0, -100, 100)
    invert: bool = False
    fade: float = number(0, 0, 100)


@dataclass
class Foreground:
    crop: str = choice(
        "1:1 Center",
        "1:1 Center",
        "1:1 Custom",
        "Fit",
        "Fill",
        "Original Ratio",
        "Custom Ratio",
    )
    crop_x: float = number(50, 0, 100)
    crop_y: float = number(50, 0, 100)
    width: int = number(1440, 32, 7680, 2)
    height: int = number(1440, 32, 4320, 2)
    lock_ratio: bool = True
    scale: float = number(100, 50, 200)
    x: int = number(0, -7680, 7680)
    y: int = number(0, -4320, 4320)
    rotation: float = number(0, -180, 180)
    opacity: float = number(100, 0, 100)
    speed: float = number(1, 0.25, 4, 0.05)
    loop: bool = True
    start_mode: str = choice("Beginning", "Beginning", "Random", "Custom")
    start_offset: float = number(0, 0, 864000)
    mirror: str = choice("OFF", "OFF", "Horizontal", "Vertical")
    sharpness: float = number(0, 0, 5, 0.1)
    border: bool = False
    border_width: int = number(4, 0, 100)
    border_opacity: float = number(100, 0, 100)
    border_color: str = "#ffffff"


@dataclass
class Background:
    mode: str = choice(
        "Ambient Video",
        "Black",
        "Solid Color",
        "Ambient Video",
        "Ambient Image",
        "Custom Media",
    )
    media: str = ""
    follows_foreground: bool = True
    blur: float = number(50, 0, 100)
    scale: float = number(115, 100, 200)
    brightness: float = number(-20, -100, 100)
    contrast: float = number(100, 0, 300)
    saturation: float = number(75, 0, 200)
    opacity: float = number(100, 0, 100)
    x: int = number(0, -7680, 7680)
    y: int = number(0, -4320, 4320)
    speed: float = number(1, 0.25, 4, 0.05)
    mirror: str = choice("OFF", "OFF", "Horizontal", "Vertical")
    color: str = "#000000"
    tint: float = number(0, 0, 100)


@dataclass
class Overlay:
    source: str = ""
    blend: str = choice(
        "Normal", "Normal", "Screen", "Add", "Lighten", "Multiply", "Overlay"
    )
    opacity: float = number(100, 0, 100)
    scale: float = number(100, 1, 500)
    x: int = number(0, -7680, 7680)
    y: int = number(0, -4320, 4320)
    anchor: str = choice(
        "Center",
        "Center",
        "Top",
        "Bottom",
        "Top Left",
        "Top Right",
        "Bottom Left",
        "Bottom Right",
        "Custom",
        "Full frame",
    )
    margin: int = number(80, 0, 1000)
    rotation: float = number(0, -180, 180)
    timing: str = choice(
        "Whole Track",
        "Whole Track",
        "Intro Only",
        "Drops Only",
        "Selected Sections",
        "Custom Range",
    )
    selected_sections: str = ""
    start: float = number(0, 0, 864000)
    end: float = number(0, 0, 864000)
    animation: str = choice(
        "None",
        "None",
        "Fade In",
        "Fade Out",
        "Fade In/Out",
        "Slow Zoom",
        "Float",
        "Pulse",
        "Beat Pulse",
    )
    intensity: float = number(2, 0, 10, 0.1)
    animation_speed: float = number(1, 0.1, 10, 0.1)


@dataclass
class Motion:
    mode: str = choice(
        "Slow Zoom In",
        "None",
        "Slow Zoom In",
        "Slow Zoom Out",
        "Float",
        "Horizontal Drift",
        "Vertical Drift",
        "Random Drift",
        "Ken Burns",
    )
    intensity: float = number(3, 0, 10, 0.1)
    duration_bars: int = number(16, 1, 128)
    reset_on_section: bool = True


@dataclass
class BeatReaction:
    enabled: bool = True
    kick_zoom: bool = True
    strength: float = number(1.5, 0, 10, 0.1)
    duration_ms: int = number(120, 20, 1000, 10)
    frequency: str = choice(
        "Strong Beats", "Every Beat", "Strong Beats", "Section Accents", "Drops Only"
    )
    shake: bool = False
    shake_strength: float = number(2, 0, 6)
    brightness_pulse: bool = False
    overlay_pulse: bool = False
    background_pulse: bool = False
    flash: str = choice("OFF", "OFF", "White", "Black", "Custom Color")
    flash_color: str = "#ffffff"
    flash_opacity: float = number(10, 0, 100)
    flash_frames: int = number(2, 1, 30)
    flash_trigger: str = choice(
        "Drop", "Strong Beat", "Drop", "Section Change", "Manual Markers"
    )
    drop_effect: str = choice(
        "Clean", "Clean", "Punch", "Dark Drop", "Flash Drop", "Hard Cut"
    )


@dataclass
class Blackout:
    enabled: bool = True
    mode: str = choice(
        "Darken", "Darken", "Full Black", "Desaturate", "Blur", "Dark + Blur"
    )
    sensitivity: str = choice("Medium", "Low", "Medium", "High")
    minimum_ms: int = number(200, 50, 5000, 10)
    maximum_ms: int = number(2000, 100, 10000, 10)
    strength: float = number(90, 0, 100)
    fade_ms: int = number(120, 0, 1000, 10)
    restore_ms: int = number(50, 0, 1000, 10)
    only_before_drop: bool = True
    foreground: bool = True
    background: bool = True
    overlay: bool = True
    restore_offset_ms: int = number(0, -500, 500, 10)


@dataclass
class Transitions:
    react: bool = True
    normal: str = choice(
        "Crossfade",
        "Hard Cut",
        "Fade",
        "Crossfade",
        "Dip to Black",
        "Blur",
        "Zoom",
        "None",
    )
    drop: str = choice(
        "Hard Cut",
        "Hard Cut",
        "Fade",
        "Crossfade",
        "Dip to Black",
        "Blur",
        "Zoom",
        "None",
    )
    duration_ms: int = number(250, 0, 2000, 10)
    auto_change: str = choice(
        "Every Section",
        "OFF",
        "Every Section",
        "Every Drop",
        "Every 4 Bars",
        "Every 8 Bars",
        "Every 16 Bars",
    )
    avoid_repeat: bool = True


@dataclass
class TextLayer:
    text: str = ""
    font: str = ""
    size: int = number(64, 8, 500)
    anchor: str = choice(
        "Bottom",
        "Center",
        "Top",
        "Bottom",
        "Top Left",
        "Top Right",
        "Bottom Left",
        "Bottom Right",
        "Custom",
    )
    x: int = number(0, -7680, 7680)
    y: int = number(0, -4320, 4320)
    margin: int = number(80, 0, 1000)
    opacity: float = number(100, 0, 100)
    color: str = "#ffffff"
    start: float = number(0, 0, 864000)
    end: float = number(0, 0, 864000)


@dataclass
class Export:
    codec: str = choice("H.264", "H.264", "H.265")
    crf: int = number(int(os.getenv("VIDEO_CRF", "16")), 0, 51)
    preset: str = choice(
        os.getenv("VIDEO_ENCODE_PRESET", "medium"),
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
    )
    audio_bitrate: int = choice(320, 192, 256, 320)
    threads: int = number(int(os.getenv("VIDEO_THREADS", "2")), 1, 32)
    encoder: str = choice("Auto", "Auto", "CPU", "NVENC", "QuickSync", "VAAPI")
    fallback_cpu: bool = True


@dataclass
class AnalysisSettings:
    bpm_source: str = choice(
        "Existing BPM Bot", "Existing BPM Bot", "Analyze Again", "Manual"
    )
    manual_bpm: float = number(0, 0, 300, 0.1)
    sections: bool = True
    drops: bool = True
    quiet: bool = True
    section_sensitivity: str = choice("Normal", "Conservative", "Normal", "Detailed")
    drop_sensitivity: str = choice("Normal", "Low", "Normal", "High")


CATEGORIES = {
    "canvas": Canvas,
    "foreground": Foreground,
    "background": Background,
    "color": Color,
    "motion": Motion,
    "beat_reaction": BeatReaction,
    "blackout": Blackout,
    "transitions": Transitions,
    "export": Export,
    "analysis": AnalysisSettings,
}


@dataclass
class VideoRenderConfig:
    config_version: int = 1
    name: str = "WUTSHY_CLASSIC"
    canvas: Canvas = field(default_factory=Canvas)
    foreground: Foreground = field(default_factory=Foreground)
    background: Background = field(default_factory=Background)
    color: Color = field(default_factory=Color)
    motion: Motion = field(default_factory=Motion)
    beat_reaction: BeatReaction = field(default_factory=BeatReaction)
    blackout: Blackout = field(default_factory=Blackout)
    transitions: Transitions = field(default_factory=Transitions)
    export: Export = field(default_factory=Export)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    overlays: list[Overlay] = field(default_factory=list)
    branding: list[TextLayer] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if data.get("config_version", 1) != 1:
            raise ValueError("Неизвестная версия preset/project configuration.")
        result = cls()
        result.name = str(data.get("name", result.name))[:100]
        for name, model in CATEGORIES.items():
            values = data.get(name, {})
            setattr(
                result,
                name,
                model(
                    **{
                        f.name: values[f.name]
                        for f in fields(model)
                        if f.name in values
                    }
                ),
            )
        for name, model in [("overlays", Overlay), ("branding", TextLayer)]:
            setattr(
                result,
                name,
                [
                    model(
                        **{
                            f.name: item[f.name]
                            for f in fields(model)
                            if f.name in item
                        }
                    )
                    for item in data.get(name, [])
                ],
            )
        result.validate()
        return result

    def validate(self, duration=None):
        if len(self.overlays) > 5 or len(self.branding) > 5:
            raise ValueError("Максимум 5 overlays и 5 text layers.")
        for name in CATEGORIES:
            validate_model(getattr(self, name), name)
        for index, model in enumerate(self.overlays + self.branding):
            validate_model(model, f"Layer {index + 1}")
            if model.end and model.start >= model.end:
                raise ValueError("Overlay/text start time is after end time.")
            if duration and (model.start >= duration or model.end > duration):
                raise ValueError(
                    "Overlay/text timestamp выходит за продолжительность трека."
                )
        if self.canvas.width % 2 or self.canvas.height % 2:
            raise ValueError("Canvas width/height должны быть чётными для yuv420p.")
        if self.blackout.maximum_ms < self.blackout.minimum_ms:
            raise ValueError("Blackout maximum duration меньше minimum duration.")
        if duration and self.foreground.start_offset < 0:
            raise ValueError("Start offset должен быть неотрицательным.")


def validate_model(model, label):
    for descriptor in fields(model):
        value = getattr(model, descriptor.name)
        key = f"{label}.{descriptor.name}"
        metadata = descriptor.metadata
        if descriptor.type is bool and type(value) is not bool:
            raise ValueError(f"{key}: нужен ON/OFF.")
        if "min" in metadata:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{key}: нужно конечное число.")
            if descriptor.type is int and int(value) != value:
                raise ValueError(f"{key}: нужно целое число.")
            if not metadata["min"] <= value <= metadata["max"]:
                raise ValueError(
                    f"{key}: допустимо {metadata['min']}..{metadata['max']}."
                )
        if "choices" in metadata and value not in metadata["choices"]:
            raise ValueError(f"{key}: недопустимое значение.")
        if descriptor.name in (
            "color",
            "border_color",
            "flash_color",
        ) and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"{key}: нужен HEX вида #151515.")
        if isinstance(value, str) and ("\x00" in value or len(value) > 4000):
            raise ValueError(f"{key}: недопустимая строка.")


BACKGROUND_STYLES = {
    "Clean": (20, -10, 90),
    "Soft Blur": (40, -15, 85),
    "Heavy Blur": (70, -20, 75),
    "Dark": (50, -35, 60),
    "Dreamy": (60, -5, 85),
    "VHS Glow": (30, -10, 110),
    "Cold": (45, -20, 70),
    "Warm": (45, -15, 90),
}
COLOR_STYLES = {
    "Original": (0, 100, 100, 0),
    "Dark": (-20, 110, 85, 0),
    "Cold": (-5, 105, 90, -30),
    "Warm": (0, 100, 100, 30),
    "Desaturated": (0, 100, 40, 0),
    "High Contrast": (0, 130, 100, 0),
    "Blue Night": (-15, 120, 70, -60),
    "Red": (-5, 110, 100, 60),
    "B&W": (0, 100, 0, 0),
    "Washed": (10, 80, 60, 0),
}
MAX_RENDERS = 1
