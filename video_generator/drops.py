import numpy as np
from .models import Drop


def score_drop(pre, post, grid_confidence, build=0):
    def rise(key):
        a, b = float(pre[key]), float(post[key])
        return float(np.clip((b - a) / max(abs(a), 0.1), 0, 1))

    energy = rise("rms")
    bass = rise("low")
    transients = rise("onsets")
    spectral = rise("flux")
    contrast = float(
        np.clip((post["rms"] - pre["minimum"]) / max(post["rms"], 0.01), 0, 1)
    )
    score = (
        0.26 * energy
        + 0.24 * bass
        + 0.16 * transients
        + 0.12 * spectral
        + 0.12 * contrast
        + 0.06 * grid_confidence
        + 0.04 * build
    )
    # Energy alone cannot establish a drop; bass and transient evidence are required.
    if energy < 0.15 or bass < 0.12 or transients < 0.1:
        score *= 0.4
    return float(np.clip(score, 0, 1))


def detect_drops(
    times, features, candidates, bpm, grid_confidence, sensitivity="Normal"
):
    result = []
    threshold = {"Low": 0.78, "Normal": 0.68, "High": 0.58}[sensitivity]
    window = min(2.5, max(0.5, 120 / bpm))
    for point in candidates:
        before = (times >= point - window) & (times < point)
        after = (times >= point) & (times < point + window)
        if before.sum() < 2 or after.sum() < 2:
            continue
        pre = {
            k: float(np.mean(features[k][before]))
            for k in ("rms", "low", "onsets", "flux")
        }
        post = {
            k: float(np.mean(features[k][after]))
            for k in ("rms", "low", "onsets", "flux")
        }
        pre["minimum"] = float(np.percentile(features["rms"][before], 20))
        build = float(np.clip(np.mean(features["flux"][before]) / 0.5, 0, 1))
        score = score_drop(pre, post, grid_confidence, build)
        if score >= threshold:
            result.append(Drop(float(point), score, pre["rms"], post["rms"]))
    return result
