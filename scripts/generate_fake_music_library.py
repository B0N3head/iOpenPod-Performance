from __future__ import annotations

import argparse
import io
import math
import os
import random
import shutil
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3._frames import APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.wave import WAVE
from PIL import Image
from tqdm import tqdm

ADJECTIVES = [
    "Amber",
    "Broken",
    "Circuit",
    "Crimson",
    "Golden",
    "Hidden",
    "Ivory",
    "Lunar",
    "Midnight",
    "Neon",
    "Quiet",
    "Silver",
    "Solar",
    "Velvet",
    "Wild",
]

NOUNS = [
    "Atlas",
    "Bloom",
    "Cascade",
    "Comet",
    "Drift",
    "Echo",
    "Harbor",
    "Meadow",
    "Mirror",
    "Mosaic",
    "Nova",
    "Parade",
    "Signal",
    "Temple",
    "Valley",
]

GENRES = [
    "Ambient",
    "Electronic",
    "Instrumental",
    "Lo-Fi",
    "Synthwave",
]


@dataclass(frozen=True)
class TrackSpec:
    serial: int
    path: Path
    title: str
    artist: str
    album: str
    genre: str
    year: int
    track_number: int
    track_total: int
    art_bytes: bytes


@dataclass(frozen=True)
class GenerationStats:
    created: list[Path]
    elapsed_seconds: float
    total_bytes: int
    workers: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a fake WAV music library with metadata and album art."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/fake_music_library"),
        help="Destination folder for the generated library.",
    )
    parser.add_argument(
        "--albums",
        type=int,
        default=4,
        help="Number of albums to generate.",
    )
    parser.add_argument(
        "--tracks-per-album",
        type=int,
        default=6,
        help="Number of songs per album.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Duration of each WAV file in seconds.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=22050,
        help="Sample rate for generated WAV files.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=160,
        help="Random seed for reproducible names, tones, and artwork.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete the output folder first if it already exists.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min((os.cpu_count() or 4) * 2, 16)),
        help="Number of concurrent track workers.",
    )
    return parser


def pick_unique_pairs(rng: random.Random, count: int) -> list[str]:
    base_names = [f"{adjective} {noun}" for adjective in ADJECTIVES for noun in NOUNS]
    rng.shuffle(base_names)

    if count <= len(base_names):
        return base_names[:count]

    names: list[str] = []
    cycle = 0
    while len(names) < count:
        for base_name in base_names:
            if cycle == 0:
                names.append(base_name)
            else:
                names.append(f"{base_name} {cycle + 1:03d}")
            if len(names) == count:
                break
        cycle += 1
    return names


def safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {" ", "-", "_"} else "_" for ch in text)
    return " ".join(cleaned.split()).strip() or "Untitled"


def gradient_cover_bytes(rng: random.Random, size: int = 640) -> bytes:
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    assert pixels is not None
    color_a = tuple(rng.randint(24, 232) for _ in range(3))
    color_b = tuple(rng.randint(24, 232) for _ in range(3))
    color_c = tuple(rng.randint(24, 232) for _ in range(3))

    for y in range(size):
        v = y / max(size - 1, 1)
        for x in range(size):
            u = x / max(size - 1, 1)
            blend_ab = [int((1.0 - u) * color_a[i] + u * color_b[i]) for i in range(3)]
            blend = [int((1.0 - v) * blend_ab[i] + v * color_c[i]) for i in range(3)]
            pixels[x, y] = tuple(blend)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _mix_u32(value: int) -> int:
    """Return a stable 32-bit mixed integer for deterministic track recipes."""
    value &= 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value


def _build_track_recipe(track_serial: int, segment_count: int) -> list[tuple[float, float, float, float, float]]:
    """Build a deterministic per-segment audio recipe for one fake song.

    Each tuple is:
      (base_hz, harmony_hz, pulse_hz, chirp_span_hz, accent_phase)
    """
    recipe: list[tuple[float, float, float, float, float]] = []
    scale = (0, 2, 3, 5, 7, 8, 10, 12, 14, 15, 17, 19)
    seed = _mix_u32(track_serial + 1)
    for segment_index in range(segment_count):
        mixed = _mix_u32(seed + segment_index * 0x9E3779B9)
        degree = scale[mixed % len(scale)]
        octave = 40 + ((mixed >> 6) % 18)
        midi_note = octave + degree
        base_hz = 55.0 * (2.0 ** (midi_note / 12.0))
        harmony_ratio = 1.20 + (((mixed >> 12) % 11) / 20.0)
        harmony_hz = base_hz * harmony_ratio
        pulse_hz = 2.0 + ((mixed >> 17) % 7)
        chirp_span_hz = 16.0 + ((mixed >> 20) % 120)
        accent_phase = (((mixed >> 27) % 16) / 16.0) * math.tau
        recipe.append((base_hz, harmony_hz, pulse_hz, chirp_span_hz, accent_phase))
    return recipe


def write_tone_wav(
    path: Path,
    *,
    sample_rate: int,
    duration: float,
    track_serial: int,
) -> None:
    frame_count = max(int(sample_rate * duration), 1)
    amplitude = 0.38 * 32767.0
    fade_frames = max(int(sample_rate * 0.08), 1)
    frames = bytearray()
    segment_count = max(6, min(12, int(round(duration / 0.4))))
    recipe = _build_track_recipe(track_serial, segment_count)
    segment_frames = max(frame_count // segment_count, 1)
    click_width = max(int(sample_rate * 0.012), 1)

    for index in range(frame_count):
        _t = index / sample_rate
        segment_index = min(index // segment_frames, segment_count - 1)
        base_hz, harmony_hz, pulse_hz, chirp_span_hz, accent_phase = recipe[segment_index]
        segment_start = segment_index * segment_frames
        local_index = max(0, index - segment_start)
        local_t = local_index / sample_rate
        local_progress = min(local_index / max(segment_frames - 1, 1), 1.0)
        envelope = 1.0
        if index < fade_frames:
            envelope = index / fade_frames
        elif index >= frame_count - fade_frames:
            envelope = (frame_count - index - 1) / fade_frames
        envelope = max(envelope, 0.0)

        segment_fade = min(local_progress / 0.12, 1.0) * min((1.0 - local_progress) / 0.12, 1.0)
        segment_fade = max(segment_fade, 0.0)
        sweep_hz = base_hz + chirp_span_hz * local_progress
        wobble = 1.0 + 0.035 * math.sin(math.tau * (0.7 + segment_index * 0.11) * local_t + accent_phase)
        pulse = 0.58 + 0.42 * math.sin(math.tau * pulse_hz * local_t + accent_phase)
        pulse = max(0.18, pulse)
        click_env = 0.0
        if local_index < click_width:
            click_env = 1.0 - (local_index / click_width)
        elif segment_frames - local_index <= click_width:
            click_env = max(click_env, 1.0 - ((segment_frames - local_index) / click_width))

        sample = (
            0.72 * math.sin(math.tau * sweep_hz * wobble * local_t)
            + 0.28 * math.sin(math.tau * harmony_hz * local_t + 0.35 * math.sin(math.tau * 3.0 * local_t))
            + 0.16 * math.sin(math.tau * (base_hz / 2.0) * local_t)
            + 0.10 * math.sin(math.tau * (2400.0 + track_serial * 3.0 + segment_index * 47.0) * local_t) * click_env
        )
        value = int(amplitude * envelope * segment_fade * pulse * sample)
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


def tag_wav(
    path: Path,
    *,
    title: str,
    artist: str,
    album: str,
    genre: str,
    year: int,
    track_number: int,
    track_total: int,
    art_bytes: bytes,
) -> None:
    audio = WAVE(str(path))
    if audio.tags is None:
        audio.add_tags()

    assert audio.tags is not None
    audio.tags.delall("TIT2")
    audio.tags.delall("TPE1")
    audio.tags.delall("TPE2")
    audio.tags.delall("TALB")
    audio.tags.delall("TCON")
    audio.tags.delall("TDRC")
    audio.tags.delall("TRCK")
    audio.tags.delall("APIC")

    audio.tags.add(TIT2(encoding=3, text=[title]))
    audio.tags.add(TPE1(encoding=3, text=[artist]))
    audio.tags.add(TPE2(encoding=3, text=[artist]))
    audio.tags.add(TALB(encoding=3, text=[album]))
    audio.tags.add(TCON(encoding=3, text=[genre]))
    audio.tags.add(TDRC(encoding=3, text=[str(year)]))
    audio.tags.add(TRCK(encoding=3, text=[f"{track_number}/{track_total}"]))
    audio.tags.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=art_bytes,
        )
    )
    audio.save()


def generate_track_file(
    spec: TrackSpec,
    *,
    sample_rate: int,
    duration: float,
) -> Path:
    write_tone_wav(
        spec.path,
        sample_rate=sample_rate,
        duration=duration,
        track_serial=spec.serial,
    )
    tag_wav(
        spec.path,
        title=spec.title,
        artist=spec.artist,
        album=spec.album,
        genre=spec.genre,
        year=spec.year,
        track_number=spec.track_number,
        track_total=spec.track_total,
        art_bytes=spec.art_bytes,
    )
    return spec.path


def generate_library(
    output: Path,
    *,
    albums: int,
    tracks_per_album: int,
    duration: float,
    sample_rate: int,
    seed: int,
    force: bool,
    workers: int,
) -> GenerationStats:
    rng = random.Random(seed)
    if output.exists():
        if not force:
            raise FileExistsError(
                f"{output} already exists. Re-run with --force to replace it."
            )
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    total_started_at = time.perf_counter()

    artist_names = pick_unique_pairs(rng, albums)
    album_names = pick_unique_pairs(rng, albums)
    song_names = pick_unique_pairs(rng, albums * tracks_per_album)
    track_specs: list[TrackSpec] = []
    song_index = 0

    with tqdm(
        total=albums,
        desc="Preparing albums",
        unit="album",
        dynamic_ncols=True,
        mininterval=0.1,
    ) as prep_bar:
        for album_index in range(albums):
            artist = artist_names[album_index]
            album = album_names[album_index]
            genre = rng.choice(GENRES)
            year = 1998 + album_index * 4 + rng.randint(0, 2)
            art_bytes = gradient_cover_bytes(rng)

            album_dir = output / safe_name(artist) / safe_name(album)
            album_dir.mkdir(parents=True, exist_ok=True)

            for track_number in range(1, tracks_per_album + 1):
                title = song_names[song_index]
                song_index += 1
                track_serial = album_index * tracks_per_album + (track_number - 1)
                filename = f"{track_number:02d} - {safe_name(title)}.wav"
                wav_path = album_dir / filename

                track_specs.append(
                    TrackSpec(
                        serial=track_serial,
                        path=wav_path,
                        title=title,
                        artist=artist,
                        album=album,
                        genre=genre,
                        year=year,
                        track_number=track_number,
                        track_total=tracks_per_album,
                        art_bytes=art_bytes,
                    )
                )
            prep_bar.set_postfix_str(f"{artist} - {album}"[:40], refresh=False)
            prep_bar.update(1)

    if not track_specs:
        return GenerationStats(
            created=[],
            elapsed_seconds=0.0,
            total_bytes=0,
            workers=0,
        )

    worker_count = max(1, min(workers, len(track_specs)))
    created_by_serial: dict[int, Path] = {}

    with tqdm(
        total=len(track_specs),
        desc="Generating tracks",
        unit="track",
        dynamic_ncols=True,
        mininterval=0.1,
    ) as track_bar:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    generate_track_file,
                    spec,
                    sample_rate=sample_rate,
                    duration=duration,
                ): spec
                for spec in track_specs
            }
            for future in as_completed(futures):
                spec = futures[future]
                created_path = future.result()
                created_by_serial[spec.serial] = created_path
                track_bar.set_postfix_str(created_path.name[:40], refresh=False)
                track_bar.update(1)

    created = [created_by_serial[spec.serial] for spec in track_specs]
    elapsed = time.perf_counter() - total_started_at
    total_bytes = sum(path.stat().st_size for path in created)
    return GenerationStats(
        created=created,
        elapsed_seconds=elapsed,
        total_bytes=total_bytes,
        workers=worker_count,
    )


def main() -> None:
    args = build_parser().parse_args()
    total_tracks = args.albums * args.tracks_per_album
    print(
        f"Planning {args.albums} albums and {total_tracks} tracks"
        f" with {args.workers} workers..."
    )
    stats = generate_library(
        args.output,
        albums=args.albums,
        tracks_per_album=args.tracks_per_album,
        duration=args.duration,
        sample_rate=args.sample_rate,
        seed=args.seed,
        force=args.force,
        workers=args.workers,
    )
    track_count = len(stats.created)
    total_mib = stats.total_bytes / (1024 * 1024)
    print(
        f"Created {track_count} fake songs in {args.output}"
        f" using {stats.workers} worker"
        f"{'s' if stats.workers != 1 else ''}."
    )
    print(
        f"Elapsed: {stats.elapsed_seconds:.2f}s"
        f" | Throughput: {track_count / max(stats.elapsed_seconds, 1e-9):.1f} tracks/s"
        f" | Size: {total_mib:.2f} MiB"
    )
    if stats.created:
        print(f"First track: {stats.created[0]}")


if __name__ == "__main__":
    main()
