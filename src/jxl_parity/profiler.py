from __future__ import annotations

import html
import json
import shutil
import shlex
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .codecs import encode, tool_path, tool_supports_option
from .corpus import ImageRecord, discover_images
from .reports import write_csv, write_json

PROFILE_STAGE_SUMMARY_FIELDS = [
    "encoder",
    "mode",
    "distance",
    "effort",
    "stage",
    "stage_group",
    "cases",
    "avg_calls",
    "avg_seconds",
    "min_seconds",
    "median_seconds",
    "max_seconds",
    "stdev_seconds",
    "avg_seconds_per_mp",
    "percent_of_encode_total",
]

STAGE_GROUPS = {
    "encode_total": "total",
    "input_conversion": "input_color",
    "pixel_layout": "input_color",
    "color_transform": "input_color",
    "reversible_color_transform": "input_color",
    "color_xyb": "input_color",
    "lf_image_generation": "vardct_frontend",
    "block_strategy": "vardct_frontend",
    "transform_selection": "vardct_frontend",
    "adaptive_quantization": "vardct_frontend",
    "butteraugli_rate_control": "vardct_frontend",
    "block_stats": "vardct_frontend",
    "ac_strategy_search": "vardct_frontend",
    "quant_scoring": "vardct_frontend",
    "dct_coefficient_generation": "vardct_coefficients",
    "chroma_from_luma": "vardct_coefficients",
    "gaborish_filtering": "vardct_coefficients",
    "noise_synthesis": "vardct_coefficients",
    "filter_simulation": "vardct_coefficients",
    "transform_quantize": "vardct_coefficients",
    "predictor_selection": "modular_modeling",
    "residual_generation": "modular_modeling",
    "palette_decisions": "modular_modeling",
    "ma_tree_context_modeling": "modular_modeling",
    "coefficient_tokenization": "entropy",
    "histogram_construction": "entropy",
    "histogram_clustering": "entropy",
    "lz77_search": "entropy",
    "ans_huffman_encoding": "entropy",
    "entropy_prepass": "entropy",
    "bit_writing": "bitstream",
    "container_metadata": "bitstream",
    "bitstream_write": "bitstream",
}


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
    samples_per_case: int
    warmups_per_case: int
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
    stage_timing: dict[str, Any] | None = None


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
    stage_timing_supported = (
        config.instrument_stages
        and "jxl-encoder" in requested_encoders
        and tool_status["jxl_encoder"]
        and tool_supports_option(config.jxl_encoder, "--stage-timing-json")
    )
    tool_status["jxl_encoder_stage_timing"] = stage_timing_supported
    images = discover_images(config.corpus, work_dir, config.max_images)
    results: list[ProfileResult] = []
    samples: list[ProfileSample] = []
    progress_total = _profile_progress_total(images, config, requested_encoders)

    with tqdm(
        total=progress_total,
        desc="Profiling",
        unit="encode",
        dynamic_ncols=True,
        disable=None,
    ) as progress:
        for image in images:
            for mode in config.modes:
                distances = [None] if mode == "lossless" else config.distances
                for effort in config.efforts:
                    for distance in distances:
                        for encoder_name in requested_encoders:
                            command = (
                                config.cjxl
                                if encoder_name == "libjxl"
                                else config.jxl_encoder
                            )
                            available = (
                                tool_status["cjxl"]
                                if encoder_name == "libjxl"
                                else tool_status["jxl_encoder"]
                            )
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
                                instrument_stages=config.instrument_stages,
                                stage_timing_supported=stage_timing_supported,
                                progress_update=progress.update,
                            )
                            results.append(result)
                            samples.extend(case_samples)

    tool_status["jxl_encoder_stage_timing_ingested"] = any(
        _stage_timing_samples(result) for result in results
    )

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
        samples_per_case=config.samples,
        warmups_per_case=config.warmups,
        tool_status=tool_status,
    )

    rows = [asdict(result) for result in results]
    for row in rows:
        row.pop("extra", None)
    sample_rows = [asdict(sample) for sample in samples]
    write_json(out_dir / "profile_summary.json", asdict(summary))
    write_json(out_dir / "profile_results.json", [asdict(result) for result in results])
    write_json(out_dir / "profile_runs.json", rows)
    write_csv(out_dir / "profile_runs.csv", rows)
    write_json(out_dir / "profile_samples.json", sample_rows)
    write_csv(out_dir / "profile_samples.csv", sample_rows, _csv_fields(ProfileSample))
    write_csv(
        out_dir / "profile_stage_summary.csv",
        _stage_summary_rows(results),
        PROFILE_STAGE_SUMMARY_FIELDS,
    )
    write_json(out_dir / "stage_timing.json", _stage_timing_payload(summary, results))
    _write_profile_report(out_dir / "profile_report.md", summary, results, config)
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
    instrument_stages: bool,
    stage_timing_supported: bool,
    progress_update: Callable[[int], object] | None = None,
) -> tuple[ProfileResult, list[ProfileSample]]:
    planned_invocations = warmups + samples
    completed_invocations = 0

    def mark_progress(count: int = 1) -> None:
        nonlocal completed_invocations
        completed_invocations += count
        _advance_progress(progress_update, count)

    def complete_planned_progress() -> None:
        remaining = planned_invocations - completed_invocations
        if remaining > 0:
            mark_progress(remaining)

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
        complete_planned_progress()
        return result, []
    if mode not in {"lossless", "vardct"}:
        result.status = "skipped"
        result.reason = f"unsupported mode: {mode}"
        complete_planned_progress()
        return result, []
    if not encoder_available:
        result.status = "skipped"
        result.reason = f"encoder command not found: {encoder_command}"
        complete_planned_progress()
        return result, []

    case_id = _case_id(image.image_id, encoder_name, mode, effort, distance)
    case_samples: list[ProfileSample] = []
    measured_seconds: list[float] = []
    measured_stage_timings: list[dict[str, Any]] = []

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
            stage_timing_path=(
                _stage_timing_path(encoded_dir, case_id, "warmup", warmup_index + 1)
                if _should_collect_stage_timing(
                    instrument_stages, stage_timing_supported, encoder_name
                )
                else None
            ),
        )
        case_samples.append(sample)
        mark_progress()
        if sample.status != "completed":
            result.status = "failed"
            result.reason = "warmup encode failed"
            result.stderr = sample.stderr
            result.command = sample.command
            complete_planned_progress()
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
            stage_timing_path=(
                _stage_timing_path(encoded_dir, case_id, "sample", sample_index + 1)
                if _should_collect_stage_timing(
                    instrument_stages, stage_timing_supported, encoder_name
                )
                else None
            ),
        )
        case_samples.append(sample)
        mark_progress()
        result.command = sample.command
        result.encoded_path = sample.encoded_path
        if sample.status != "completed":
            result.status = "failed"
            result.reason = "encode failed"
            result.stderr = sample.stderr
            complete_planned_progress()
            return result, case_samples
        if sample.encode_seconds is not None:
            measured_seconds.append(sample.encode_seconds)
        if sample.stage_timing is not None:
            measured_stage_timings.append(sample.stage_timing)
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
    result.encode_seconds_median = (
        statistics.median(measured_seconds) if measured_seconds else None
    )
    result.encode_seconds_max = max(measured_seconds) if measured_seconds else None
    result.encode_seconds_stdev = (
        statistics.stdev(measured_seconds) if len(measured_seconds) > 1 else None
    )
    result.status = "completed"
    result.reason = "ok"
    if measured_stage_timings:
        result.extra = {"stage_timing_samples": measured_stage_timings}
    return result, case_samples


def _advance_progress(
    progress_update: Callable[[int], object] | None, count: int
) -> None:
    if progress_update is not None and count > 0:
        progress_update(count)


def _profile_progress_total(
    images: list[ImageRecord], config: ProfileConfig, requested_encoders: list[str]
) -> int:
    invocations_per_case = config.warmups + config.samples
    total = 0
    for _image in images:
        for mode in config.modes:
            distances = [None] if mode == "lossless" else config.distances
            total += (
                len(distances)
                * len(config.efforts)
                * len(requested_encoders)
                * invocations_per_case
            )
    return total


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
    stage_timing_path: Path | None,
) -> ProfileSample:
    if stage_timing_path is not None:
        stage_timing_path.unlink(missing_ok=True)
    encode_result = encode(
        encoder=encoder_name,
        command=encoder_command,
        input_path=_reference_path(image),
        output_path=encoded_path,
        mode=mode,
        effort=effort,
        distance=distance,
        stage_timing_path=stage_timing_path,
    )
    command = encode_result.command_text
    seconds_per_mp = (
        encode_result.elapsed_seconds / image.megapixels
        if image.megapixels > 0
        else None
    )
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
            stage_timing=None,
        )

    encoded_bytes = encoded_path.stat().st_size
    stage_timing = (
        _read_stage_timing(stage_timing_path) if stage_timing_path is not None else None
    )
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
        stage_timing=stage_timing,
    )


def _stage_timing_payload(
    summary: ProfileSummary, results: list[ProfileResult]
) -> dict[str, object]:
    has_sidecar_stages = any(_stage_timing_samples(result) for result in results)
    runs = []
    for result in results:
        stages = []
        if result.encode_seconds is not None:
            stages.append(
                {
                    "stage": "encode_total",
                    "stage_group": _stage_group("encode_total"),
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
        stages.extend(_aggregate_result_stage_timings(result))
        run: dict[str, object] = {
            "image_id": result.image_id,
            "encoder": result.encoder,
            "mode": result.mode,
            "distance": result.distance,
            "effort": result.effort,
            "status": result.status,
            "stages": stages,
        }
        stage_accounting = _aggregate_stage_accounting(result)
        if stage_accounting is not None:
            run["stage_accounting"] = stage_accounting
        runs.append(run)

    return {
        "schema_version": 1,
        "summary": asdict(summary),
        "stage_source": (
            "jxl_encoder_stage_sidecar"
            if has_sidecar_stages
            else "wall_clock_encode_total"
        ),
        "note": _stage_timing_note(has_sidecar_stages),
        "runs": runs,
        "aggregates": _aggregate_stage_totals(results),
    }


def _stage_timing_note(has_sidecar_stages: bool) -> str:
    if has_sidecar_stages:
        return (
            "Named stages come from jxl-encoder --stage-timing-json sidecars when "
            "`--instrument-stages` is used with a compatible cjxl-rs binary. "
            "encode_total remains the outer harness wall-clock measurement."
        )
    return (
        "The stock encoder CLIs do not expose internal JPEG XL stage timings. "
        "This file records top-level encode wall time as encode_total; use "
        "profiler_commands.md to capture internal stacks or flamegraphs."
    )


def _read_stage_timing(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    stages = []
    for stage in payload.get("stages", []):
        if not isinstance(stage, dict):
            continue
        name = stage.get("stage")
        seconds = stage.get("wall_seconds")
        if not isinstance(name, str) or not isinstance(seconds, int | float):
            continue
        calls = stage.get("calls", 1)
        stages.append(
            {
                "stage": name,
                "stage_group": _stage_group(name),
                "seconds": float(seconds),
                "calls": int(calls) if isinstance(calls, int | float) else 1,
            }
        )
    if not stages:
        return None
    return {
        "path": str(path),
        "stage_source": payload.get("stage_source"),
        "elapsed_wall_seconds": payload.get("elapsed_wall_seconds"),
        "total_stage_wall_seconds": payload.get("total_stage_wall_seconds"),
        "unattributed_wall_seconds": payload.get("unattributed_wall_seconds"),
        "stages": stages,
    }


def _stage_timing_path(encoded_dir: Path, case_id: str, kind: str, index: int) -> Path:
    return encoded_dir / f"{case_id}-{kind}{index}.stage-timing.json"


def _should_collect_stage_timing(
    instrument_stages: bool, stage_timing_supported: bool, encoder_name: str
) -> bool:
    return (
        instrument_stages
        and stage_timing_supported
        and encoder_name == "jxl-encoder"
    )


def _stage_timing_samples(result: ProfileResult) -> list[dict[str, Any]]:
    if not result.extra:
        return []
    samples = result.extra.get("stage_timing_samples")
    if not isinstance(samples, list):
        return []
    return [sample for sample in samples if isinstance(sample, dict)]


def _aggregate_result_stage_timings(result: ProfileResult) -> list[dict[str, object]]:
    samples = _stage_timing_samples(result)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        for stage in sample.get("stages", []):
            if not isinstance(stage, dict):
                continue
            name = stage.get("stage")
            seconds = stage.get("seconds")
            if isinstance(name, str) and isinstance(seconds, int | float):
                grouped.setdefault(name, []).append(stage)

    stages = []
    for name, stage_samples in sorted(grouped.items()):
        seconds = [float(stage["seconds"]) for stage in stage_samples]
        calls = [
            int(stage.get("calls", 1))
            for stage in stage_samples
            if isinstance(stage.get("calls", 1), int | float)
        ]
        avg_seconds = _average(seconds)
        stages.append(
            {
                "stage": name,
                "stage_group": _stage_group(name),
                "seconds": avg_seconds,
                "seconds_per_mp": (
                    avg_seconds / result.megapixels
                    if avg_seconds is not None and result.megapixels > 0
                    else None
                ),
                "seconds_min": min(seconds) if seconds else None,
                "seconds_median": statistics.median(seconds) if seconds else None,
                "seconds_max": max(seconds) if seconds else None,
                "seconds_stdev": (
                    statistics.stdev(seconds) if len(seconds) > 1 else None
                ),
                "sample_count": len(seconds),
                "warmup_count": result.warmup_count,
                "calls_avg": _average(calls),
            }
        )
    return stages


def _aggregate_stage_accounting(result: ProfileResult) -> dict[str, object] | None:
    samples = _stage_timing_samples(result)
    if not samples:
        return None

    elapsed = _numeric_sample_values(samples, "elapsed_wall_seconds")
    total_stage = _numeric_sample_values(samples, "total_stage_wall_seconds")
    sidecar_unattributed = _numeric_sample_values(samples, "unattributed_wall_seconds")
    avg_total_stage = _average(total_stage)
    return {
        "sample_count": len(samples),
        "sidecar_elapsed_seconds": _average(elapsed),
        "sidecar_total_stage_seconds": avg_total_stage,
        "sidecar_unattributed_seconds": _average(sidecar_unattributed),
        "harness_unattributed_seconds": (
            result.encode_seconds - avg_total_stage
            if result.encode_seconds is not None and avg_total_stage is not None
            else None
        ),
    }


def _numeric_sample_values(samples: list[dict[str, Any]], key: str) -> list[float]:
    return [
        float(value)
        for sample in samples
        if isinstance((value := sample.get(key)), int | float)
    ]


def _aggregate_stage_totals(results: list[ProfileResult]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, float | None, int], list[ProfileResult]] = {}
    for result in results:
        if result.status != "completed":
            continue
        grouped.setdefault(
            (result.encoder, result.mode, result.distance, result.effort), []
        ).append(result)

    aggregates = []
    for (encoder, mode, distance, effort), matches in sorted(
        grouped.items(), key=lambda item: str(item[0])
    ):
        seconds = [
            match.encode_seconds
            for match in matches
            if match.encode_seconds is not None
        ]
        seconds_per_mp = [
            match.encode_seconds_per_mp
            for match in matches
            if match.encode_seconds_per_mp is not None
        ]
        aggregates.append(
            {
                "encoder": encoder,
                "mode": mode,
                "distance": distance,
                "effort": effort,
                "cases": len(matches),
                "stage": "encode_total",
                "stage_group": _stage_group("encode_total"),
                "avg_seconds": _average(seconds),
                "min_seconds": min(seconds) if seconds else None,
                "median_seconds": statistics.median(seconds) if seconds else None,
                "max_seconds": max(seconds) if seconds else None,
                "stdev_seconds": (
                    statistics.stdev(seconds) if len(seconds) > 1 else None
                ),
                "avg_seconds_per_mp": _average(seconds_per_mp),
            }
        )
        stage_values: dict[str, list[tuple[float, float | None, float | None]]] = {}
        for match in matches:
            for stage in _aggregate_result_stage_timings(match):
                seconds_value = stage.get("seconds")
                seconds_per_mp_value = stage.get("seconds_per_mp")
                calls_avg_value = stage.get("calls_avg")
                if isinstance(seconds_value, int | float):
                    stage_values.setdefault(str(stage["stage"]), []).append(
                        (
                            float(seconds_value),
                            (
                                float(seconds_per_mp_value)
                                if isinstance(seconds_per_mp_value, int | float)
                                else None
                            ),
                            (
                                float(calls_avg_value)
                                if isinstance(calls_avg_value, int | float)
                                else None
                            ),
                        )
                    )
        for stage_name, values in sorted(stage_values.items()):
            stage_seconds = [value[0] for value in values]
            stage_seconds_per_mp = [
                value[1] for value in values if value[1] is not None
            ]
            stage_calls = [value[2] for value in values if value[2] is not None]
            aggregates.append(
                {
                    "encoder": encoder,
                    "mode": mode,
                    "distance": distance,
                    "effort": effort,
                    "cases": len(values),
                    "stage": stage_name,
                    "stage_group": _stage_group(stage_name),
                    "avg_calls": _average(stage_calls),
                    "avg_seconds": _average(stage_seconds),
                    "min_seconds": min(stage_seconds) if stage_seconds else None,
                    "median_seconds": (
                        statistics.median(stage_seconds) if stage_seconds else None
                    ),
                    "max_seconds": max(stage_seconds) if stage_seconds else None,
                    "stdev_seconds": (
                        statistics.stdev(stage_seconds)
                        if len(stage_seconds) > 1
                        else None
                    ),
                    "avg_seconds_per_mp": _average(stage_seconds_per_mp),
                }
            )
    return aggregates


def _stage_summary_rows(results: list[ProfileResult]) -> list[dict[str, object]]:
    aggregates = _aggregate_stage_totals(results)
    encode_totals = {
        _stage_group_key(row): row.get("avg_seconds")
        for row in aggregates
        if row.get("stage") == "encode_total"
    }
    rows = []
    for row in aggregates:
        avg_seconds = row.get("avg_seconds")
        encode_total = encode_totals.get(_stage_group_key(row))
        rows.append(
            {
                **row,
                "percent_of_encode_total": _percent(avg_seconds, encode_total),
            }
        )
    return rows


def _stage_group_key(row: dict[str, object]) -> tuple[object, object, object, object]:
    return (row["encoder"], row["mode"], row["distance"], row["effort"])


def _stage_group(stage: str) -> str:
    return STAGE_GROUPS.get(stage, "custom")


def _percent(value: object, total: object) -> float | None:
    value_float = _to_float(value)
    total_float = _to_float(total)
    if value_float is None or total_float in {None, 0.0}:
        return None
    return (value_float / (total_float or 1.0)) * 100.0


def _write_profiler_commands(
    path: Path, config: ProfileConfig, results: list[ProfileResult]
) -> None:
    examples = _profiler_command_examples(config, results)
    lines = [
        "# Profiler Commands",
        "",
        "Use `stage_timing.json` for corpus-level encode totals and `profile_samples.csv` for",
        "per-sample variance. Stock encoder CLIs do not expose named JPEG XL stages.",
        "",
        "For internal stage attribution, run one of these commands around a representative",
        "encoder invocation:",
        "",
    ]
    for label, example in examples:
        lines.extend(
            [
                f"## {label}",
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
            ]
        )
    lines.extend(
        [
            "Compare these stacks with the parity report's size, quality, and pass/fail outputs before",
            "treating a hot `jxl-encoder` stage as representative of libjxl.",
            "",
        ]
    )
    if not config.keep_work:
        lines.extend(
            [
                "The profile run removed its normalized work files. Replace `<reference.png>` with a",
                "representative normalized input, or rerun `jxl-parity profile --keep-work` and use",
                "one of the exact commands recorded in `profile_samples.csv`.",
                "",
            ]
        )
    lines.extend(_stage_instrumentation_guidance_lines())
    path.write_text("\n".join(lines), encoding="utf-8")


def _stage_summary_markdown(stage_rows: list[dict[str, object]]) -> list[str]:
    if not stage_rows:
        return ["No completed profile cases produced stage rows."]

    rows = sorted(
        stage_rows,
        key=lambda row: _to_float(row.get("avg_seconds_per_mp"))
        or _to_float(row.get("avg_seconds"))
        or -1.0,
        reverse=True,
    )
    lines = [
        "All aggregate stage timings by average seconds per megapixel.",
        "",
        "| Encoder | Mode | Distance | Effort | Stage | Group | Cases | Avg calls | Avg seconds | Seconds/MP | % of encode_total |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_stage_row_markdown(row) for row in rows)

    named_rows = [
        row
        for row in stage_rows
        if row.get("stage") != "encode_total"
        and _to_float(row.get("percent_of_encode_total")) is not None
    ]
    lines.extend(["", "### Named Stage Shares", ""])
    if named_rows:
        lines.extend(
            [
                "| Encoder | Mode | Distance | Effort | Stage | Group | Avg calls | Avg seconds | % of encode_total |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in sorted(
            named_rows,
            key=lambda item: _to_float(item.get("percent_of_encode_total")) or -1.0,
            reverse=True,
        ):
            lines.append(
                "| {encoder} | {mode} | {distance} | {effort} | {stage} | {group} | {calls} | {seconds} | {percent} |".format(
                    encoder=row["encoder"],
                    mode=row["mode"],
                    distance=_format_distance(row.get("distance")),
                    effort=row["effort"],
                    stage=row["stage"],
                    group=row["stage_group"],
                    calls=_format_number(_to_float(row.get("avg_calls"))),
                    seconds=_format_number(_to_float(row.get("avg_seconds"))),
                    percent=_format_percent(row.get("percent_of_encode_total")),
                )
            )
    else:
        lines.append(
            "No named sidecar stages were ingested; only `encode_total` is available for this run."
        )
    return lines


def _stage_row_markdown(row: dict[str, object]) -> str:
    return "| {encoder} | {mode} | {distance} | {effort} | {stage} | {group} | {cases} | {calls} | {seconds} | {seconds_per_mp} | {percent} |".format(
        encoder=row["encoder"],
        mode=row["mode"],
        distance=_format_distance(row.get("distance")),
        effort=row["effort"],
        stage=row["stage"],
        group=row["stage_group"],
        cases=row["cases"],
        calls=_format_number(_to_float(row.get("avg_calls"))),
        seconds=_format_number(_to_float(row.get("avg_seconds"))),
        seconds_per_mp=_format_number(_to_float(row.get("avg_seconds_per_mp"))),
        percent=_format_percent(row.get("percent_of_encode_total")),
    )


def _stage_accounting_markdown(results: list[ProfileResult]) -> list[str]:
    rows = []
    for result in results:
        accounting = _aggregate_stage_accounting(result)
        if accounting is None:
            continue
        rows.append((result, accounting))

    if not rows:
        return [
            "No sidecar accounting was available; only `encode_total` timing is present for this run."
        ]

    lines = [
        "Sidecar accounting compares the Rust sidecar clock with the outer harness `encode_total` timing.",
        "",
        "| Image | Encoder | Mode | Distance | Effort | Samples | Sidecar elapsed | Named stage total | Sidecar unattributed | Harness unattributed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result, accounting in rows:
        lines.append(
            "| {image} | {encoder} | {mode} | {distance} | {effort} | {samples} | {elapsed} | {total} | {sidecar_unattributed} | {harness_unattributed} |".format(
                image=result.image_id,
                encoder=result.encoder,
                mode=result.mode,
                distance=_format_distance(result.distance),
                effort=result.effort,
                samples=accounting["sample_count"],
                elapsed=_format_number(
                    _to_float(accounting.get("sidecar_elapsed_seconds"))
                ),
                total=_format_number(
                    _to_float(accounting.get("sidecar_total_stage_seconds"))
                ),
                sidecar_unattributed=_format_number(
                    _to_float(accounting.get("sidecar_unattributed_seconds"))
                ),
                harness_unattributed=_format_number(
                    _to_float(accounting.get("harness_unattributed_seconds"))
                ),
            )
        )
    return lines


def _stage_plot_markdown(stage_plots: list[tuple[str, str]]) -> list[str]:
    if not stage_plots:
        return ["No completed stage rows were available for plots."]
    lines = []
    for title, relative_path in stage_plots:
        lines.extend([f"![{title}]({relative_path})", ""])
    return lines[:-1]


def _write_stage_plots(
    out_dir: Path, stage_rows: list[dict[str, object]]
) -> list[tuple[str, str]]:
    if not stage_rows:
        return []

    plot_dir = out_dir / "profile_plots"
    plot_dir.mkdir(exist_ok=True)
    plots: list[tuple[str, str]] = []
    seconds_svg = _stage_seconds_per_mp_svg(stage_rows)
    if seconds_svg:
        path = plot_dir / "stage-seconds-per-mp.svg"
        path.write_text(seconds_svg, encoding="utf-8")
        plots.append(("Average stage time per megapixel", path.relative_to(out_dir).as_posix()))

    share_svg = _stage_share_svg(stage_rows)
    if share_svg:
        path = plot_dir / "stage-share.svg"
        path.write_text(share_svg, encoding="utf-8")
        plots.append(("Named stage share of encode total", path.relative_to(out_dir).as_posix()))
    return plots


def _stage_seconds_per_mp_svg(stage_rows: list[dict[str, object]]) -> str:
    values = []
    for row in stage_rows:
        value = _to_float(row.get("avg_seconds_per_mp")) or _to_float(row.get("avg_seconds"))
        if value is None:
            continue
        values.append((value, row))
    if not values:
        return ""

    values = sorted(values, key=lambda item: item[0], reverse=True)[:20]
    width = 900
    left = 330
    row_height = 24
    height = 48 + row_height * len(values) + 30
    max_value = max(value for value, _row in values)
    pieces = [
        '<text x="16" y="25" font-size="16" font-weight="600">Average stage seconds per megapixel</text>',
        f'<line x1="{left}" y1="38" x2="{left}" y2="{height - 24}" stroke="#71717a" />',
    ]
    for index, (value, row) in enumerate(values):
        y = 46 + index * row_height
        bar_width = 1 if max_value == 0 else (value / max_value) * (width - left - 90)
        label = html.escape(_stage_plot_label(row))
        fill = "#0f766e" if row.get("stage") == "encode_total" else "#7c3aed"
        pieces.append(f'<text x="16" y="{y + 13}" font-size="12">{label}</text>')
        pieces.append(
            f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="14" fill="{fill}" />'
        )
        pieces.append(
            f'<text x="{left + bar_width + 6:.1f}" y="{y + 12}" font-size="12">{value:.4g}s/MP</text>'
        )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">{"".join(pieces)}</svg>'


def _stage_share_svg(stage_rows: list[dict[str, object]]) -> str:
    named_rows = [
        row
        for row in stage_rows
        if row.get("stage") != "encode_total"
        and _to_float(row.get("percent_of_encode_total")) is not None
    ]
    if not named_rows:
        return ""

    grouped: dict[tuple[object, object, object, object], list[dict[str, object]]] = {}
    for row in named_rows:
        grouped.setdefault(_stage_group_key(row), []).append(row)
    groups = sorted(
        grouped.items(),
        key=lambda item: sum(
            _to_float(row.get("percent_of_encode_total")) or 0.0
            for row in item[1]
        ),
        reverse=True,
    )[:12]

    width = 900
    left = 230
    plot_width = 560
    row_height = 26
    height = 54 + row_height * len(groups) + 44
    palette = [
        "#2563eb",
        "#dc2626",
        "#16a34a",
        "#ca8a04",
        "#7c3aed",
        "#0891b2",
        "#db2777",
        "#4b5563",
    ]
    stage_names = sorted({str(row["stage"]) for _key, rows in groups for row in rows})
    stage_colors = {
        stage: palette[index % len(palette)] for index, stage in enumerate(stage_names)
    }
    pieces = [
        '<text x="16" y="25" font-size="16" font-weight="600">Named stage share of encode_total</text>',
        f'<line x1="{left}" y1="38" x2="{left + plot_width}" y2="38" stroke="#71717a" />',
        f'<text x="{left}" y="{height - 18}" font-size="12">0%</text>',
        f'<text x="{left + plot_width - 34}" y="{height - 18}" font-size="12">100%</text>',
    ]
    for index, (key, rows) in enumerate(groups):
        y = 48 + index * row_height
        pieces.append(
            f'<text x="16" y="{y + 13}" font-size="12">{html.escape(_stage_group_label(key))}</text>'
        )
        x = float(left)
        for row in sorted(rows, key=lambda item: str(item["stage"])):
            percent = min(_to_float(row.get("percent_of_encode_total")) or 0.0, 100.0)
            segment_width = (percent / 100.0) * plot_width
            if segment_width <= 0:
                continue
            stage = str(row["stage"])
            color = stage_colors[stage]
            pieces.append(
                f'<rect x="{x:.1f}" y="{y}" width="{segment_width:.1f}" height="15" fill="{color}">'
                f"<title>{html.escape(stage)} {percent:.1f}%</title></rect>"
            )
            if segment_width >= 54:
                pieces.append(
                    f'<text x="{x + 4:.1f}" y="{y + 12}" font-size="11" fill="#fff">{html.escape(stage[:14])}</text>'
                )
            x += segment_width

    legend_x = left
    legend_y = height - 38
    for index, stage in enumerate(stage_names[:8]):
        x = legend_x + index * 102
        pieces.append(
            f'<rect x="{x}" y="{legend_y}" width="10" height="10" fill="{stage_colors[stage]}" />'
        )
        pieces.append(
            f'<text x="{x + 14}" y="{legend_y + 10}" font-size="11">{html.escape(stage[:12])}</text>'
        )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">{"".join(pieces)}</svg>'


def _stage_plot_label(row: dict[str, object]) -> str:
    return (
        f"{row['encoder']} {row['mode']} {_format_distance(row.get('distance'))} "
        f"e{row['effort']} {row['stage']}"
    )


def _stage_group_label(key: tuple[object, object, object, object]) -> str:
    encoder, mode, distance, effort = key
    return f"{encoder} {mode} {_format_distance(distance)} e{effort}"


def _write_profile_report(
    path: Path,
    summary: ProfileSummary,
    results: list[ProfileResult],
    config: ProfileConfig,
) -> None:
    completed = [result for result in results if result.status == "completed"]
    has_sidecar_stages = any(_stage_timing_samples(result) for result in results)
    stage_rows = _stage_summary_rows(results)
    stage_plots = _write_stage_plots(path.parent, stage_rows)
    slowest = sorted(
        completed,
        key=lambda result: (
            result.encode_seconds_per_mp
            if result.encode_seconds_per_mp is not None
            else -1.0
        ),
        reverse=True,
    )[:10]

    intro = (
        "This report summarizes encode-total profiling runs and named stages collected from "
        "`jxl-encoder` sidecars."
        if has_sidecar_stages
        else (
            "This report summarizes encode-total profiling runs. It does not contain named JPEG XL\n"
            "internal stages because the stock encoder CLIs do not expose them."
        )
    )
    stage_artifact = (
        "encode-total timing plus ingested jxl-encoder sidecar stages."
        if has_sidecar_stages
        else "encode-total timing shaped as stage data for downstream tools."
    )
    feasibility_lines = (
        [
            "This run ingested named stages from `cjxl-rs --stage-timing-json` sidecars.",
            "Use `encode_total` as the outer wall-clock reference; named stages cover the",
            "instrumented Rust encoder spans and may leave unattributed setup or I/O time.",
            "Known sidecar stages are also tagged with a reporting group so granular leaf",
            "stages can be compared without measuring parent spans twice.",
        ]
        if has_sidecar_stages
        else [
            "Current runs can compare whole encode time across images, modes, distances, and efforts,",
            "but cannot attribute time to leaf stages such as input conversion, color transform,",
            "LF generation, transform selection, quantization, tokenization, histogram work,",
            "entropy coding, bit writing, or container wrapping.",
            "",
            "Getting those timings requires a custom `jxl-encoder` build that records flat leaf",
            "spans inside the Rust encoder and emits structured timing data for this harness",
            "to ingest. Keep `encode_total` as the outer reference rather than replacing it.",
        ]
    )

    lines = [
        "# Profile Report",
        "",
        *intro.splitlines(),
        "",
        "## Summary",
        "",
        f"- Images: {summary.images}",
        f"- Cases: {summary.total_cases}",
        f"- Completed: {summary.completed_cases}",
        f"- Failed: {summary.failed_cases}",
        f"- Skipped: {summary.skipped_cases}",
        f"- Encoder selection: {summary.encoder}",
        f"- Measured samples per case: {summary.samples_per_case}",
        f"- Warmups per case: {summary.warmups_per_case}",
        "",
        "## Artifacts",
        "",
        "- `profile_runs.csv` / `profile_runs.json`: one aggregate row per image/settings/encoder case.",
        "- `profile_samples.csv` / `profile_samples.json`: one row per warmup and measured encode invocation.",
        "- `profile_stage_summary.csv`: per-stage aggregate timing rows used by the tables and plots below.",
        f"- `stage_timing.json`: {stage_artifact}",
        "- `profile_plots/`: SVG plots embedded in this markdown report.",
        "- `profiler_commands.md`: perf/samply/flamegraph command templates for stack attribution.",
        "",
        "## Stage Timing Feasibility",
        "",
        *feasibility_lines,
        "",
        "## Per-Stage Summary",
        "",
        *_stage_summary_markdown(stage_rows),
        "",
        "## Stage Accounting",
        "",
        *_stage_accounting_markdown(results),
        "",
        "## Per-Stage Plots",
        "",
        *_stage_plot_markdown(stage_plots),
        "",
        "## Slowest Completed Cases",
        "",
    ]
    if slowest:
        lines.extend(
            [
                "| Image | Encoder | Mode | Distance | Effort | Avg seconds | Seconds/MP | Stdev | Samples |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            "| {image} | {encoder} | {mode} | {distance} | {effort} | {seconds} | {seconds_per_mp} | {stdev} | {samples} |".format(
                image=result.image_id,
                encoder=result.encoder,
                mode=result.mode,
                distance="" if result.distance is None else result.distance,
                effort=result.effort,
                seconds=_format_number(result.encode_seconds),
                seconds_per_mp=_format_number(result.encode_seconds_per_mp),
                stdev=_format_number(result.encode_seconds_stdev),
                samples=result.sample_count,
            )
            for result in slowest
        )
    else:
        lines.append("No completed profile cases.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _example_command(config: ProfileConfig) -> str:
    return _example_commands(config)[0][1]


def _profiler_command_examples(
    config: ProfileConfig,
    results: list[ProfileResult],
) -> list[tuple[str, str]]:
    if config.keep_work:
        completed = [
            result
            for result in results
            if result.status == "completed" and result.command
        ]
        if completed:
            examples: list[tuple[str, str]] = []
            seen: set[str] = set()
            for result in completed:
                if result.encoder in seen or result.command is None:
                    continue
                seen.add(result.encoder)
                examples.append((f"{result.encoder} exact command", result.command))
            return examples
    return _example_commands(config)


def _example_commands(config: ProfileConfig) -> list[tuple[str, str]]:
    return [
        (
            f"{encoder_name} fallback command",
            _example_command_for_encoder(config, encoder_name),
        )
        for encoder_name in _requested_encoders(config.encoder)
    ]


def _example_command_for_encoder(config: ProfileConfig, encoder_name: str) -> str:
    command = config.cjxl if encoder_name == "libjxl" else config.jxl_encoder
    output = config.out_dir / "work" / "encoded" / f"profile-example-{encoder_name}.jxl"
    input_path = "<reference.png>"
    mode = config.modes[0]
    distance = config.distances[0] if config.distances else None
    if encoder_name == "libjxl":
        distance_arg = "0.0" if mode == "lossless" else str(distance)
        return _shell_join(
            [
                command,
                input_path,
                output,
                "--quiet",
                "-e",
                config.efforts[0],
                "-d",
                distance_arg,
            ]
        )
    if mode == "lossless":
        return _shell_join(
            [command, input_path, output, "-e", config.efforts[0], "--lossless"]
        )
    return _shell_join(
        [command, input_path, output, "-e", config.efforts[0], "-d", distance]
    )


def _stage_instrumentation_guidance_lines() -> list[str]:
    return [
        "## Named Stage Timing",
        "",
        "The current harness cannot produce true named-stage timings from stock `cjxl-rs`.",
        "`--instrument-stages` marks the run and emits profiling guidance; it does not",
        "change the encoder binary or expose internal spans by itself.",
        "",
        "To get granular timings for the VarDCT and Modular pipelines, use a custom",
        "`jxl-encoder` build that emits flat leaf stages such as `input_conversion`,",
        "`color_transform`, `block_strategy`, `transform_selection`, `adaptive_quantization`,",
        "`dct_coefficient_generation`, `histogram_construction`, `ans_huffman_encoding`,",
        "and `bit_writing`. Keep coarse phases as reporting groups rather than measured",
        "parent spans so named-stage totals stay interpretable.",
        "",
        "That build should:",
        "",
        "- add a low-overhead stage timer in the Rust encoder;",
        "- wrap the relevant VarDCT and Modular functions with stable leaf stage names;",
        "- emit per-stage JSON through `cjxl-rs --stage-timing-json`; and",
        "- run `jxl-parity profile --instrument-stages` to merge sidecars into `stage_timing.json`.",
        "",
    ]


def _requested_encoders(value: str) -> list[str]:
    if value == "both":
        return ["libjxl", "jxl-encoder"]
    if value in {"libjxl", "jxl-encoder"}:
        return [value]
    raise ValueError(f"unknown encoder: {value}")


def _case_id(
    image_id: str, encoder: str, mode: str, effort: int, distance: float | None
) -> str:
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


def _format_number(value: float | None) -> str:
    return "" if value is None else f"{value:.6g}"


def _format_percent(value: object) -> str:
    number = _to_float(value)
    return "" if number is None else f"{number:.1f}%"


def _format_distance(value: object) -> str:
    return "lossless" if value is None or value == "" else str(value)


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)
