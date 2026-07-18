"""
One-off / re-runnable classifier for resource/songs/*.mp3.

The bundled BGM library has no genre/mood metadata (plain "outputNNN.mp3"
names, no ID3 tags), so app/services/video.py can't pick a track that matches
a content type's mood without something to filter on. This script estimates
tempo, loudness and brightness for every track with plain signal processing
(no ML/audio-tagging model available offline) and buckets them into the
music_palette values used by app/db/seed.py, writing the result to
resource/songs/catalog.json - the file app/services/video.get_bgm_file()
reads at render time.

Buckets are a coarse, automatic proxy for mood, not a human judgment call -
rerun this after adding new tracks to resource/songs, and feel free to
hand-edit catalog.json afterward for any track that lands in the wrong
bucket (e.g. if a track's actual feel doesn't match its assigned palette).

Usage: python scripts/classify_bgm.py
"""

import json
import os
import sys

import numpy as np
from pydub import AudioSegment
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils import utils  # noqa: E402

# Ascending energy order: the calmest tracks go to the first palette, the
# fastest/brightest to the last. This mirrors how the five built-in content
# types actually read (app/db/seed.py): reflective/uplifting, sober/ambient,
# playful, high-energy-current, and energetic/tech - in roughly that order.
PALETTE_ORDER = [
    "cinematic_uplifting",  # motivational
    "tech_ambient",  # world_news
    "upbeat_bright",  # fun_facts
    "viral_trending",  # trending_now
    "tech_energetic",  # ai_news
]


def _analyze(path: str) -> dict:
    audio = AudioSegment.from_mp3(path).set_channels(1)
    samples = np.array(audio.get_array_of_samples()).astype(np.float32)
    samples /= float(1 << (8 * audio.sample_width - 1))
    sr = audio.frame_rate

    # Cap analysis to the first 60s: BGM tracks loop/repeat and this is
    # plenty to characterize tempo/energy/brightness without loading a
    # multi-minute file into memory as float32.
    samples = samples[: sr * 60]

    rms = float(np.sqrt(np.mean(samples**2)))

    # Onset-strength envelope (short-time energy in 20ms hops) -> autocorrelation
    # peak-picking for a coarse tempo estimate. Not beat-accurate like a real
    # onset-detection model, but enough signal to separate "slow" from "fast".
    hop = max(1, sr // 50)
    frame = hop * 2
    n_frames = max(1, (len(samples) - frame) // hop)
    envelope = np.array(
        [np.sqrt(np.mean(samples[i * hop : i * hop + frame] ** 2)) for i in range(n_frames)]
    )
    envelope = envelope - envelope.mean()
    tempo_bpm = 0.0
    if len(envelope) > 10 and envelope.any():
        autocorr = np.correlate(envelope, envelope, mode="full")[len(envelope) - 1 :]
        frame_rate_hz = sr / hop
        min_lag = int(frame_rate_hz * 60 / 200)  # 200 BPM upper bound
        max_lag = int(frame_rate_hz * 60 / 60)  # 60 BPM lower bound
        window = autocorr[min_lag:max_lag]
        if window.size:
            peaks, _ = find_peaks(window)
            if peaks.size:
                best_lag = min_lag + peaks[np.argmax(window[peaks])]
                tempo_bpm = 60.0 * frame_rate_hz / best_lag

    # Spectral centroid on a single FFT of the (capped) signal - a coarse
    # brightness proxy: synth/percussive tracks skew high, warm/orchestral
    # tracks skew low.
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sr)
    brightness = float(np.sum(freqs * spectrum) / max(np.sum(spectrum), 1e-9))

    return {"tempo_bpm": round(tempo_bpm, 1), "energy": round(rms, 4), "brightness": round(brightness, 1)}


def main() -> None:
    song_dir = utils.song_dir()
    files = sorted(f for f in os.listdir(song_dir) if f.lower().endswith(".mp3"))
    if not files:
        print(f"no mp3 files found in {song_dir}")
        return

    analyzed = []
    for name in files:
        path = os.path.join(song_dir, name)
        try:
            features = _analyze(path)
        except Exception as exc:  # noqa: BLE001 - keep classifying the rest
            print(f"skip {name}: {exc}")
            continue
        analyzed.append((name, features))
        print(f"{name}: {features}")

    # Composite score, min-max normalized per feature so tempo/energy/brightness
    # (very different units) contribute roughly equally, then ranked ascending
    # and split into palette-sized buckets.
    def _norm(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            return [0.5 for _ in values]
        return [(v - lo) / (hi - lo) for v in values]

    tempos = _norm([f["tempo_bpm"] for _, f in analyzed])
    energies = _norm([f["energy"] for _, f in analyzed])
    brightnesses = _norm([f["brightness"] for _, f in analyzed])
    scored = [
        (name, features, 0.4 * tempos[i] + 0.3 * energies[i] + 0.3 * brightnesses[i])
        for i, (name, features) in enumerate(analyzed)
    ]
    scored.sort(key=lambda item: item[2])

    n = len(scored)
    n_palettes = len(PALETTE_ORDER)
    catalog = {}
    for i, (name, features, score) in enumerate(scored):
        bucket = min(i * n_palettes // n, n_palettes - 1)
        palette = PALETTE_ORDER[bucket]
        catalog[name] = {**features, "score": round(score, 4), "palette": palette}
        print(f"{name}: score={score:.3f} -> {palette}")

    out_path = os.path.join(song_dir, "catalog.json")
    with open(out_path, "w") as fh:
        json.dump(catalog, fh, indent=2, sort_keys=True)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
