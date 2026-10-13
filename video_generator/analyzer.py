import argparse
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.fft import dct
from scipy.ndimage import median_filter, gaussian_filter1d
from scipy.signal import correlate, find_peaks

from .drops import detect_drops
from .media import MediaError, probe, require_ffmpeg
from .models import QuietRegion, Section, TrackAnalysis

ANALYSIS_VERSION = 1
SR = 22050
HOP = 512
FFT = 2048


def quiet_regions(
    times,
    loudness,
    rms,
    duration,
    sensitivity="Medium",
    minimum_ms=200,
    maximum_ms=2000,
):
    if len(times) < 8:
        return []
    step = float(np.median(np.diff(times)))
    median = median_filter(
        loudness, size=max(3, int(5 / step) // 2 * 2 + 1), mode="nearest"
    )
    relative = loudness - median
    threshold = {"Low": -18, "Medium": -13, "High": -9}[sensitivity]
    integrated = 10 * np.log10(max(np.mean(rms * rms), 1e-12))
    active = False
    start = 0
    found = []
    for i, value in enumerate(relative):
        if not active and value < threshold and median[i] > integrated - 12:
            active = True
            start = i
        elif active and (value > threshold + 4 or i == len(times) - 1):
            end = i
            length = (end - start) * step
            future = rms[end : min(len(rms), end + max(1, int(1.5 / step)))]
            prior = rms[max(0, start - int(2 / step)) : start]
            energetic = float(np.percentile(rms, 65))
            if (
                minimum_ms / 1000 <= length <= maximum_ms / 1000
                and len(future)
                and len(prior)
                and np.max(future) > energetic * 0.8
                and np.median(prior) > energetic * 0.35
                and times[start] > 1
                and times[end] < duration - 1
            ):
                strength = float(np.clip(-np.min(relative[start:end]) / 30, 0.5, 1))
                found.append(
                    QuietRegion(
                        float(times[start]), float(min(duration, times[end])), strength
                    )
                )
            active = False
    return found


def mel_bank(freqs):
    mel = lambda hz: 2595 * np.log10(1 + hz / 700)
    hz = lambda m: 700 * (10 ** (m / 2595) - 1)
    points = hz(np.linspace(mel(40), mel(SR / 2), 34))
    return np.array(
        [
            np.maximum(
                0,
                np.minimum(
                    (freqs - points[i]) / (points[i + 1] - points[i]),
                    (points[i + 2] - freqs) / (points[i + 2] - points[i + 1]),
                ),
            )
            for i in range(32)
        ]
    )


def extract_features(path, work):
    pcm = Path(work) / "analysis.f32"
    logpath = Path(work) / "loudness.log"
    with logpath.open("wb") as log:
        decoded = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "info",
                "-y",
                "-i",
                str(path),
                "-vn",
                "-af",
                "ebur128=framelog=verbose",
                "-ac",
                "1",
                "-ar",
                str(SR),
                "-f",
                "f32le",
                str(pcm),
            ],
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
    loudness_summary = logpath.read_text(errors="replace")[-4000:]
    measured = re.findall(r"I:\s*(-?\d+(?:\.\d+)?) LUFS", loudness_summary)
    integrated_lufs = float(measured[-1]) if measured else None
    if decoded.returncode or not pcm.is_file() or pcm.stat().st_size < FFT * 4:
        raise MediaError(
            "Audio невозможно декодировать или он слишком короткий для анализа."
        )
    samples = np.memmap(pcm, dtype="<f4", mode="r")
    frequencies = np.fft.rfftfreq(FFT, 1 / SR)
    low = (frequencies >= 30) & (frequencies <= 180)
    bank = mel_bank(frequencies)
    pitches = np.zeros(len(frequencies), dtype=int)
    positive = frequencies > 0
    pitches[positive] = (
        np.round(69 + 12 * np.log2(frequencies[positive] / 440)).astype(int) % 12
    )
    window = np.hanning(FFT)
    rows = []
    previous = np.zeros(FFT // 2 + 1)
    for block in range(0, len(samples) - FFT + 1, HOP * 256):
        positions = np.arange(
            block, min(len(samples) - FFT + 1, block + HOP * 256), HOP
        )
        frames = np.array([samples[p : p + FFT] for p in positions])
        spectrum = np.abs(np.fft.rfft(frames * window, axis=1))
        power = spectrum * spectrum
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        norm = spectrum / np.maximum(np.linalg.norm(spectrum, axis=1)[:, None], 1e-9)
        flux = np.maximum(0, norm - np.vstack([previous, norm[:-1]])).sum(axis=1)
        previous = norm[-1]
        chroma = np.stack(
            [power[:, (pitches == i) & positive].sum(axis=1) for i in range(12)], axis=1
        )
        chroma /= np.maximum(np.linalg.norm(chroma, axis=1)[:, None], 1e-9)
        mfcc = dct(
            np.log(np.maximum(power @ bank.T, 1e-9)), type=2, norm="ortho", axis=1
        )[:, :13]
        centroid = (
            (power * frequencies).sum(axis=1) / np.maximum(power.sum(axis=1), 1e-9) / SR
        )
        low_energy = np.sqrt(power[:, low].sum(axis=1)) / FFT
        rows.append(
            np.column_stack(
                [positions / SR, rms, low_energy, flux, centroid, chroma, mfcc]
            )
        )
    data = np.vstack(rows)
    del samples
    pcm.unlink(missing_ok=True)
    times = data[:, 0]
    features = {
        "rms": data[:, 1],
        "low": data[:, 2],
        "flux": data[:, 3],
        "spectral": data[:, 4],
        "chroma": data[:, 5:17],
        "mfcc": data[:, 17:30],
    }
    features["loudness"] = 20 * np.log10(np.maximum(features["rms"], 1e-8))
    peaks, _ = find_peaks(
        features["flux"],
        distance=max(1, int(0.08 * SR / HOP)),
        prominence=max(0.05, float(np.median(features["flux"])) * 0.5),
    )
    impulses = np.zeros(len(times))
    impulses[peaks] = 1
    features["onsets"] = gaussian_filter1d(impulses, sigma=SR / HOP * 0.25) * SR / HOP
    return times, features, integrated_lufs


def beat_grid(times, features, bpm=None):
    step = HOP / SR
    flux = features["flux"]
    centered = flux - np.mean(flux)
    if not bpm:
        autocorrelation = correlate(centered, centered, mode="full", method="fft")[
            len(centered) - 1 :
        ]
        lo, hi = int(60 / 220 / step), int(60 / 60 / step)
        lag = lo + int(np.argmax(autocorrelation[lo : min(hi, len(autocorrelation))]))
        bpm = 60 / (lag * step)
    bpm = float(bpm)
    if not 1 <= bpm <= 300:
        raise MediaError("BPM должен быть в диапазоне 1..300.")
    period = 60 / bpm
    phases = np.linspace(0, min(period, float(times[-1])), 80, endpoint=False)
    scores = []
    for phase in phases:
        grid = np.arange(phase, times[-1], period)
        scores.append(float(np.mean(np.interp(grid, times, flux))))
    phase = float(phases[int(np.argmax(scores))])
    beats = np.arange(phase, times[-1], period)
    strengths = np.interp(beats, times, features["low"] + features["rms"])
    bar_phase = max(
        range(4),
        key=lambda i: float(np.mean(strengths[i::4])) if len(strengths[i::4]) else 0,
    )
    confidence = float(
        np.clip((max(scores) - np.mean(scores)) / max(np.std(scores) * 3, 1e-9), 0, 1)
    )
    return bpm, beats.tolist(), beats[bar_phase::4].tolist(), confidence


def structure(
    times, features, bars, bpm, duration, grid_confidence, sensitivity, drops
):
    matrix = np.column_stack(
        [
            np.log(np.maximum(features["rms"], 1e-8)),
            np.log(np.maximum(features["low"], 1e-8)),
            features["flux"],
            features["onsets"],
            features["spectral"],
            features["chroma"],
            features["mfcc"],
        ]
    )
    matrix = (matrix - np.mean(matrix, axis=0)) / np.maximum(
        np.std(matrix, axis=0), 1e-6
    )
    # Recurrence compares bar summaries, avoiding a quadratic frame-by-frame matrix.
    bar_times = np.array([0] + [float(b) for b in bars if b > 0] + [duration])
    summaries = []
    for a, b in zip(bar_times[:-1], bar_times[1:]):
        mask = (times >= a) & (times < b)
        summaries.append(
            matrix[mask].mean(axis=0) if mask.any() else np.zeros(matrix.shape[1])
        )
    summaries = np.array(summaries)
    novelty = np.zeros(len(bar_times) - 1)
    for i in range(2, len(summaries) - 2):
        novelty[i] = np.linalg.norm(
            np.mean(summaries[i - 2 : i], axis=0)
            - np.mean(summaries[i : i + 2], axis=0)
        )
        if i >= 4 and i + 4 < len(summaries):
            novelty[i] += 0.25 * np.linalg.norm(summaries[i] - summaries[i - 4])
    threshold = (
        np.percentile(
            novelty, {"Conservative": 90, "Normal": 78, "Detailed": 65}[sensitivity]
        )
        if len(novelty)
        else 0
    )
    peaks, _ = find_peaks(
        novelty,
        distance={"Conservative": 8, "Normal": 4, "Detailed": 2}[sensitivity],
        prominence=max(0.4, float(threshold) * 0.25),
    )
    starts = [0.0]
    minimum = {"Conservative": 8, "Normal": 4, "Detailed": 2}[sensitivity] * 240 / bpm
    candidates = sorted(
        set(
            [float(bar_times[i]) for i in peaks if novelty[i] >= threshold]
            + [d.timestamp for d in drops]
        )
    )
    for point in candidates:
        if point - starts[-1] >= minimum * 0.8 and duration - point > 240 / bpm:
            starts.append(point)
    starts.append(duration)
    result = []
    energies = []
    descriptors = []
    for a, b in zip(starts[:-1], starts[1:]):
        mask = (times >= a) & (times < b)
        energies.append(float(np.mean(features["rms"][mask])) if mask.any() else 0)
        descriptors.append(
            np.mean(matrix[mask], axis=0) if mask.any() else np.zeros(matrix.shape[1])
        )
    high = max(float(np.percentile(features["rms"], 70)), 1e-8)
    for i, (a, b) in enumerate(zip(starts[:-1], starts[1:])):
        energy = energies[i] / high
        label = f"Section {i + 1}"
        confidence = 0.3
        if i == 0 and energy < 0.75:
            label = "Intro"
            confidence = 0.65
        elif i == len(energies) - 1 and energy < 0.65:
            label = "Outro"
            confidence = 0.65
        elif any(abs(d.timestamp - a) < 240 / bpm for d in drops):
            label = "Drop"
            confidence = max(
                d.confidence for d in drops if abs(d.timestamp - a) < 240 / bpm
            )
        elif energy < 0.4:
            label = "Break"
            confidence = 0.6
        elif i + 1 < len(energies) and energies[i + 1] > energies[i] * 1.4:
            label = "Build"
            confidence = 0.55
        elif (
            i > 0
            and energy > 0.8
            and any(
                np.linalg.norm(descriptors[i] - descriptors[j])
                / math.sqrt(len(descriptors[i]))
                < 0.55
                for j in range(i)
            )
        ):
            label = "Chorus"
            confidence = 0.6
        elif i > 0 and 0.4 < energy < 0.85 and drops:
            label = "Verse"
            confidence = 0.5
        result.append(Section(a, b, label, confidence, energy))
    return result, novelty.tolist(), bar_times[:-1].tolist()


def analyze(path, bpm=None, settings=None):
    from .config import VideoRenderConfig

    cfg = VideoRenderConfig.from_dict(settings or {})
    duration = probe(path, "audio")["duration"]
    require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="vg_analysis_") as work:
        times, features, integrated_lufs = extract_features(path, work)
    source = cfg.analysis.bpm_source
    if source == "Manual" and cfg.analysis.manual_bpm <= 0:
        raise MediaError("Введите положительный Manual BPM в Audio Analysis.")
    hint = (
        cfg.analysis.manual_bpm
        if source == "Manual"
        else None
        if source == "Analyze Again"
        else bpm
    )
    bpm, beats, bars, confidence = beat_grid(times, features, hint)
    # Additional onset candidates catch drops inside bars when grid alignment is uncertain.
    onset_peaks, _ = find_peaks(
        features["onsets"], distance=max(1, int(120 / bpm * SR / HOP))
    )
    candidates = (
        bars
        if confidence > 0.25
        else sorted(set(bars + [float(times[i]) for i in onset_peaks]))
    )
    drops = (
        detect_drops(
            times, features, candidates, bpm, confidence, cfg.analysis.drop_sensitivity
        )
        if cfg.analysis.drops
        else []
    )
    sections, novelty, novelty_times = (
        structure(
            times,
            features,
            bars,
            bpm,
            duration,
            confidence,
            cfg.analysis.section_sensitivity,
            drops,
        )
        if cfg.analysis.sections
        else ([Section(0, duration, "Section 1")], [], [])
    )
    quiet = (
        quiet_regions(
            times,
            features["loudness"],
            features["rms"],
            duration,
            cfg.blackout.sensitivity,
            cfg.blackout.minimum_ms,
            cfg.blackout.maximum_ms,
        )
        if cfg.analysis.quiet
        else []
    )
    stride = max(1, round(0.1 * SR / HOP))
    compact = {
        k: features[k][::stride].tolist()
        for k in ("rms", "loudness", "low", "flux", "onsets", "spectral")
    }
    compact["times"] = times[::stride].tolist()
    compact["novelty"] = novelty
    compact["novelty_times"] = novelty_times
    integrated = (
        integrated_lufs
        if integrated_lufs is not None
        else float(10 * np.log10(max(np.mean(features["rms"] ** 2), 1e-12)))
    )
    result = TrackAnalysis(
        duration,
        bpm,
        beats,
        bars,
        sections,
        drops,
        quiet,
        compact,
        integrated,
        confidence,
        ANALYSIS_VERSION,
    )
    result.validate()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("output")
    parser.add_argument("--bpm", type=float)
    parser.add_argument("--settings")
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text()) if args.settings else None
    Path(args.output).write_text(
        json.dumps(
            analyze(args.audio, args.bpm, settings).to_dict(), ensure_ascii=False
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
