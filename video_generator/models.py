import math
from dataclasses import asdict, dataclass, field

from .media import format_time


@dataclass
class Section:
    start: float
    end: float
    type: str
    confidence: float = 0
    energy: float = 0
    video_id: int | None = None
    transition: str | None = None
    keep_previous: bool = False
    visual: dict = field(default_factory=dict)


@dataclass
class Drop:
    timestamp: float
    confidence: float
    pre_energy: float
    post_energy: float
    enabled: bool = True


@dataclass
class QuietRegion:
    start: float
    end: float
    strength: float
    enabled: bool = True
    manual: bool = False


@dataclass
class TrackAnalysis:
    duration: float
    bpm: float
    beats: list[float] = field(default_factory=list)
    bars: list[float] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    quiet_regions: list[QuietRegion] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    integrated_loudness: float = 0
    grid_confidence: float = 0
    analysis_version: int = 1
    markers: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        result = cls(
            **{
                k: v
                for k, v in data.items()
                if k in cls.__dataclass_fields__
                and k not in ("sections", "drops", "quiet_regions")
            }
        )
        for name, model in [
            ("sections", Section),
            ("drops", Drop),
            ("quiet_regions", QuietRegion),
        ]:
            setattr(
                result,
                name,
                [
                    model(
                        **{
                            k: v
                            for k, v in item.items()
                            if k in model.__dataclass_fields__
                        }
                    )
                    for item in data.get(name, [])
                ],
            )
        result.validate()
        return result

    def validate(self):
        if (
            not math.isfinite(self.duration)
            or self.duration <= 0
            or not math.isfinite(self.bpm)
            or not 1 <= self.bpm <= 300
        ):
            raise ValueError("Некорректная duration/BPM.")
        for group in (self.beats, self.bars):
            if any(
                not math.isfinite(x) or not 0 <= x <= self.duration for x in group
            ) or group != sorted(set(group)):
                raise ValueError("Beat/bar grid некорректен.")
        if self.sections:
            if (
                abs(self.sections[0].start) > 0.001
                or abs(self.sections[-1].end - self.duration) > 0.001
            ):
                raise ValueError("Sections должны покрывать трек от 00:00 до конца.")
            for i, section in enumerate(self.sections):
                if (
                    not 0 <= section.start < section.end <= self.duration
                    or not section.type.strip()
                    or len(section.type) > 100
                ):
                    raise ValueError("Некорректный section timestamp/name.")
                if section.visual:
                    from .config import VideoRenderConfig

                    base = VideoRenderConfig().to_dict()
                    for category, values in section.visual.items():
                        if category not in (
                            "foreground",
                            "background",
                            "color",
                            "motion",
                            "beat_reaction",
                            "blackout",
                        ) or not isinstance(values, dict):
                            raise ValueError("Некорректные section visual overrides.")
                        base[category].update(values)
                    VideoRenderConfig.from_dict(base)
                if i and abs(self.sections[i - 1].end - section.start) > 0.001:
                    raise ValueError("Sections должны быть непрерывными.")
        for item in self.drops:
            if (
                not math.isfinite(item.timestamp)
                or not 0 <= item.timestamp < self.duration
                or not 0 <= item.confidence <= 1
            ):
                raise ValueError("Некорректный Drop.")
        for item in self.quiet_regions:
            if (
                not 0 <= item.start < item.end <= self.duration
                or not 0 <= item.strength <= 1
            ):
                raise ValueError("Некорректный Quiet Gap.")


def normalize_sections(items, duration):
    items = sorted(items, key=lambda x: x["start"])
    if not items or items[0]["start"] != 0:
        raise ValueError("Первый section должен начинаться в 00:00.")
    for i, item in enumerate(items):
        item["end"] = items[i + 1]["start"] if i + 1 < len(items) else duration
    test = TrackAnalysis(
        duration,
        120,
        sections=[
            Section(**{k: v for k, v in s.items() if k in Section.__dataclass_fields__})
            for s in items
        ],
    )
    test.validate()
    return items


def timestamps(analysis):
    return "\n".join(
        f"{format_time(s['start'])} {s['type']}" for s in analysis["sections"]
    )
