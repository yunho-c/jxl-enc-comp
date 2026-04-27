from __future__ import annotations

import shutil
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .codecs import encode, tool_path
from .corpus import ImageRecord, discover_images
from .reports import write_csv, write_json


@dataclass(frozen=True)
class ProfileConfig:
    corpus: list[Path]
    out_dir: Path
    cjxl: str
    jxl_encoder: str
    encoder: str
    modes: list[str]
    distances: list[float]
    efforts: list[int]
    max_images: int | None
    keep_work: bool
    instrument_stages: bool
    samples: int = 1
    warmups: int = 0


@dataclass(frozen=True)
class ProfileSummary:
    out_dir: Path
    images: int
    total_cases: int
    completed_cases: int
    failed_cases: int
    skipped_cases: int
    encoder: str
    instrument_stages: bool
    tool_status: dict[str, bool]


@dataclass
class ProfileResult:
    image_id: str
    source_path: str
    encoder: str
    mode: str
    effort: int
    distance: float | None
    status: str
    reason: str
    width: int
    height: int
    megapixels: float
    source_format: str
    image_mode: str
    has_alpha: bool
    bit_depth: int | None
    unsupported_reason: str | None = None
    encoded_path: str | None = None
    encoded_bytes: int | None = None
    bits_per_pixel: float | None = None
    encode_seconds: float | None = None
    encode_seconds_per_mp: float | None = None
    encode_seconds_min: float | None = None
    encode_seconds_median: float | None = None
    encode_seconds_max: float | None = None
    encode_seconds_stdev: float | None = None
    sample_count: int = 0
    warmup_count: int = 0
    command: str | None = None
    stderr: str | None = None
    extra: dict[str, Any] | None = None


@dataclass
class ProfileSample:
    image_id: str
    source_path: str
    encoder: str
    mode: str
    effort: int
    distance: float | None
    sample_index: int
    warmup: bool
    status: str
    reason: str
    encoded_path: str | None
    encoded_bytes: int | None
    bits_per_pixel: float | None
    encode_seconds: float | None
    encode_seconds_per_mp: float | None
    command: str | None
    stderr: str | None


def run_profile(config: ProfileConfig) -> ProfileSummary:
    out_dir = config.out_dir
    work_dir = out_dir / "work"
    encoded_dir = work_dir / "encoded"
    for directory in (out_dir, work_dir, encoded_dir):
        directory.mkdir(parents=True, exist_ok=True)

    requested_encoders = _requested_encoders(config.encoder)
    tool_status = {
        "cjxl": tool_path(config.cjxl) is not None,
        "jxl_encoder": tool_path(config.jxl_encoder) is not None,
    }
    images = discover_images(config.corpus, work_dir, config.max_images)
    results: list[ProfileResult] = []
    samples: list[ProfileSample] = []

    for image in images:
        for mode in config.modes:
            distances = [None] if mode == "lossless" else config.distances
            for effort in config.efforts:
                for distance in distances:
                    for encoder_name in requested_encoders:
                        command = config.cjxl if encoder_name == "libjxl" else config.jxl_encoder
                        available = tool_status["cjxl"] if encoder_name == "libjxl" else tool_status["jxl_encoder"]
                        result, case_samples = _profile_case(
                            image=image,
                            encoder_name=encoder_name,
                            encoder_command=command,
                            encoder_available=available,
                            mode=mode,
                            effort=effort,
                            distance=distance,
                            encoded_dir=encoded_dir,
                            samples=config.samples,
                            warmups=config.warmups,
                        )
                        results.append(result)
                        samples.extend(case_samples)

    completed = sum(result.status == "completed" for result in results)
    failed = sum(result.status == "failed" for result in results)
    skipped = sum(result.status == "skipped" for result in results)
    summary = ProfileSummary(
        out_dir=out_dir,
        images=len(images),
        total_cases=len(results),
        completed_cases=completed,
        failed_cases=failed,
        skipped_cases=skipped,
        encoder=config.encoder,
        instrument_stages=config.instrument_stages,
        tool_status=tool_status,
    )

    rows = [asdict(result) for result in results]
    for row in rows:
        row.pop("extra", None)
    sample_rows = [asdict(sample) for sample in samples]
    write_json(out_dir / "profile_summary.json", asdict(summary))
    write_json(out_dir / "profile_results.json", [asdict(result) for result in results])
    write_csv(out_dir / "profile_runs.csv", rows)
    write_json(out_dir / "profile_samples.json", sample_rows)
    write_csv(out_dir / "profile_samples.csv", sample_rows, _csv_fields(ProfileSample))
    write_json(out_dir / "stage_timing.json", _stage_timing_payload(summary, results))
    _write_profiler_commands(out_dir / "profiler_commands.md", config, results)

    if not config.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return summary


def _profile_case(
    *,
    image: ImageRecord,
    encoder_name: str,
    encoder_command: str,
    encoder_available: bool,
    mode: str,
    effort: int,
    distance: float | None,
    encoded_dir: Path,
    samples: int,
    warmups: int,
) -> tuple[ProfileResult, list[ProfileSample]]:
    result = ProfileResult(
        image_id=image.image_id,
        source_path=str(image.source_path),
        encoder=encoder_name,
        mode=mode,
        effort=effort,
        distance=distance,
        status="pending",
        reason="",
        width=image.width,
        height=image.height,
        megapixels=image.megapixels,
        source_format=image.source_format,
        image_mode=image.mode,
        has_alpha=image.has_alpha,
        bit_depth=image.bit_depth,
        unsupported_reason=image.unsupported_reason,
    )
    if image.unsupported_reason is not None:
        result.status = "skipped"
        result.reason = f"unsupported input format: {image.unsupported_reason}"
        return result, []
    if mode not in {"lossless", "vardct"}:
        result.status = "skipped"
        result.reason = f"unsupported mode: {mode}"
        return result, []
    if not encoder_available:
        result.status = "skipped"
        result.reason = f"encoder command not found: {encoder_command}"
        return result, []

    case_id = _case_id(image.image_id, encoder_name, mode, effort, distance)
    case_samples: list[ProfileSample] = []
    measured_seconds: list[float] = []

    for warmup_index in range(warmups):
        sample = _run_profile_sample(
            image=image,
            encoder_name=encoder_name,
            encoder_command=encoder_command,
            mode=mode,
            effort=effort,
            distance=distance,
            encoded_path=encoded_dir / f"{case_id}-warmup{warmup_index + 1}.jxl",
            sample_index=warmup_index + 1,
            warmup=True,
        )
        case_samples.append(sample)
        if sample.status != "completed":
            result.status = "failed"
            result.reason = "warmup encode failed"
            result.stderr = sample.stderr
            result.command = sample.command
            return result, case_samples

    for sample_index in range(samples):
        sample = _run_profile_sample(
            image=image,
            encoder_name=encoder_name,
            encoder_command=encoder_command,
            mode=mode,
            effort=effort,
            distance=distance,
            encoded_path=encoded_dir / f"{case_id}-sample{sample_index + 1}.jxl",
            sample_index=sample_index + 1,
            warmup=False,
        )
        case_samples.append(sample)
        result.command = sample.command
        result.encoded_path = sample.encoded_path
        if sample.status != "completed":
            result.status = "failed"
            result.reason = "encode failed"
            result.stderr = sample.stderr
            return result, case_samples
        if sample.encode_seconds is not None:
            measured_seconds.append(sample.encode_seconds)
        result.encoded_bytes = sample.encoded_bytes
        result.bits_per_pixel = sample.bits_per_pixel

    result.sample_count = len(measured_seconds)
    result.warmup_count = warmups
    result.encode_seconds = _average(measured_seconds)
    result.encode_seconds_per_mp = (
        result.encode_seconds / image.megapixels
        if result.encode_seconds is not None and image.megapixels > 0
        else None
    )
    result.encode_seconds_min = min(measured_seconds) if measured_seconds else None
    result.encode_seconds_median = statistics.median(measured_seconds) if measured_seconds else None
    result.encode_seconds_max = max(measured_seconds) if measured_seconds else None
    result.encode_seconds_stdev = statistics.stdev(measured_seconds) if len(measured_seconds) > 1 else None
    result.status = "completed"
    result.reason = "ok"
    return result, case_samples


def _run_profile_sample(
    *,
    image: ImageRecord,
    encoder_name: str,
    encoder_command: str,
    mode: str,
    effort: int,
    distance: float | None,
    encoded_path: Path,
    sample_index: int,
    warmup: bool,
) -> ProfileSample:
    encode_result = encode(
        encoder=encoder_name,
        command=encoder_command,
        input_path=_reference_path(image),
        output_path=encoded_path,
        mode=mode,
        effort=effort,
        distance=distance,
    )
    command = encode_result.command_text
    seconds_per_mp = encode_result.elapsed_seconds / image.megapixels if image.megapixels > 0 else None
    if not encode_result.ok:
        return ProfileSample(
            image_id=image.image_id,
            source_path=str(image.source_path),
            encoder=encoder_name,
            mode=mode,
            effort=effort,
            distance=distance,
            sample_index=sample_index,
            warmup=warmup,
            status="failed",
            reason="encode failed",
            encoded_path=str(encoded_path),
            encoded_bytes=None,
            bits_per_pixel=None,
            encode_seconds=encode_result.elapsed_seconds,
            encode_seconds_per_mp=seconds_per_mp,
            command=command,
            stderr=encode_result.stderr.strip()[-4000:],
        )

    encoded_bytes = encoded_path.stat().st_size
    return ProfileSample(
        image_id=image.image_id,
        source_path=str(image.source_path),
        encoder=encoder_name,
        mode=mode,
        effort=effort,
        distance=distance,
        sample_index=sample_index,
        warmup=warmup,
        status="completed",
        reason="ok",
        encoded_path=str(encoded_path),
        encoded_bytes=encoded_bytes,
        bits_per_pixel=(encoded_bytes * 8) / (image.width * image.height),
        encode_seconds=encode_result.elapsed_seconds,
        encode_seconds_per_mp=seconds_per_mp,
        command=command,
        stderr=None,
    )


def _stage_timing_payload(summary: ProfileSummary, results: list[ProfileResult]) -> dict[str, object]:
    runs = []
    for result in results:
        stages = []
        if result.encode_seconds is not None:
            stages.append(
                {
                    "stage": "encode_total",
                    "seconds": result.encode_seconds,
                    "seconds_per_mp": result.encode_seconds_per_mp,
                    "seconds_min": result.encode_seconds_min,
                    "seconds_median": result.encode_seconds_median,
                    "seconds_max": result.encode_seconds_max,
                    "seconds_stdev": result.encode_seconds_stdev,
                    "sample_count": result.sample_count,
                    "warmup_count": result.warmup_count,
                }
            )
        runs.append(
            {
                "image_id": result.image_id,
                "encoder": result.encoder,
                "mode": result.mode,
                "distance": result.distance,
                "effort": result.effort,
                "status": result.status,
                "stages": stages,
            }
        )

    return {
        "schema_version": 1,
        "summary": asdict(summary),
        "stage_source": "wall_clock_encode_total",
        "note": (
            "The stock encoder CLIs do not expose internal JPEG XL stage timings. "
            "This file records top-level encode wall time as encode_total; use "
            "profiler_commands.md to capture internal stacks or flamegraphs."
        ),
        "runs": runs,
        "aggregates": _aggregate_stage_totals(results),
    }


def _aggregate_stage_totals(results: list[ProfileResult]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, float | None, int], list[ProfileResult]] = {}
    for result in results:
        if result.status != "completed":
            continue
        grouped.setdefault((result.encoder, result.mode, result.distance, result.effort), []).append(result)

    aggregates = []
    for (encoder, mode, distance, effort), matches in sorted(grouped.items(), key=lambda item: str(item[0])):
        seconds = [match.encode_seconds for match in matches if match.encode_seconds is not None]
        seconds_per_mp = [
            match.encode_seconds_per_mp for match in matches if match.encode_seconds_per_mp is not None
        ]
        aggregates.append(
            {
                "encoder": encoder,
                "mode": mode,
                "distance": distance,
                "effort": effort,
                "cases": len(matches),
                "stage": "encode_total",
                "avg_seconds": _average(seconds),
                "min_seconds": min(seconds) if seconds else None,
                "median_seconds": statistics.median(seconds) if seconds else None,
                "max_seconds": max(seconds) if seconds else None,
                "stdev_seconds": statistics.stdev(seconds) if len(seconds) > 1 else None,
                "avg_seconds_per_mp": _average(seconds_per_mp),
            }
        )
    return aggregates


def _write_profiler_commands(path: Path, config: ProfileConfig, results: list[ProfileResult]) -> None:
    completed = [result for result in results if result.status == "completed" and result.command]
    example = completed[0].command if completed else _example_command(config)
    lines = [
        "# Profiler Commands",
        "",
        "Use `stage_timing.json` for corpus-level encode totals. For internal stage attribution,",
        "run one of these commands around a representative encoder invocation:",
        "",
        "```bash",
        f"perf record --call-graph dwarf -- {example}",
        "perf report",
        "```",
        "",
        "```bash",
        f"samply record -- {example}",
        "```",
        "",
        "```bash",
        f"flamegraph -- {example}",
        "```",
        "",
        "Compare these stacks with the parity report's size, quality, and pass/fail outputs before",
        "treating a hot `jxl-encoder` stage as representative of libjxl.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _example_command(config: ProfileConfig) -> str:
    command = config.cjxl if config.encoder == "libjxl" else config.jxl_encoder
    output = str(config.out_dir / "work" / "encoded" / "profile-example.jxl")
    input_path = "<reference.png>"
    if config.encoder == "libjxl":
        return f"{command} {input_path} {output} --quiet -e {config.efforts[0]} -d 0.0"
    return f"{command} {input_path} {output} -e {config.efforts[0]} --lossless"


def _requested_encoders(value: str) -> list[str]:
    if value == "both":
        return ["libjxl", "jxl-encoder"]
    if value in {"libjxl", "jxl-encoder"}:
        return [value]
    raise ValueError(f"unknown encoder: {value}")


def _case_id(image_id: str, encoder: str, mode: str, effort: int, distance: float | None) -> str:
    quality = "lossless" if distance is None else f"d{distance:g}".replace(".", "p")
    return f"{image_id}-{encoder}-{mode}-{quality}-e{effort}".replace("/", "-")


def _average(values: list[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _reference_path(image: ImageRecord) -> Path:
    if image.reference_path is None:
        raise ValueError(f"image has no reference path: {image.source_path}")
    return image.reference_path


def _csv_fields(data_class: type[object]) -> list[str]:
    return [field.name for field in fields(data_class)]
