from __future__ import annotations

import json
import shutil
import shlex
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .codecs import encode, tool_path, tool_supports_option
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
            stage_timing_path=(
                _stage_timing_path(encoded_dir, case_id, "sample", sample_index + 1)
                if _should_collect_stage_timing(
                    instrument_stages, stage_timing_supported, encoder_name
                )
                else None
            ),
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
        stage_values: dict[str, list[tuple[float, float | None]]] = {}
        for match in matches:
            for stage in _aggregate_result_stage_timings(match):
                seconds_value = stage.get("seconds")
                seconds_per_mp_value = stage.get("seconds_per_mp")
                if isinstance(seconds_value, int | float):
                    stage_values.setdefault(str(stage["stage"]), []).append(
                        (
                            float(seconds_value),
                            (
                                float(seconds_per_mp_value)
                                if isinstance(seconds_per_mp_value, int | float)
                                else None
                            ),
                        )
                    )
        for stage_name, values in sorted(stage_values.items()):
            stage_seconds = [value[0] for value in values]
            stage_seconds_per_mp = [
                value[1] for value in values if value[1] is not None
            ]
            aggregates.append(
                {
                    "encoder": encoder,
                    "mode": mode,
                    "distance": distance,
                    "effort": effort,
                    "cases": len(values),
                    "stage": stage_name,
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


def _write_profiler_commands(
    path: Path, config: ProfileConfig, results: list[ProfileResult]
) -> None:
    examples = _profiler_command_examples(config, results)
    lines = [
        "# Profiler Commands",
        "",
        "Use `stage_timing.json` for corpus-level encode totals and `profile_samples.csv` for",
        "per-sample variance. The stock encoder CLIs do not expose named JPEG XL stages.",
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


def _write_profile_report(
    path: Path,
    summary: ProfileSummary,
    results: list[ProfileResult],
    config: ProfileConfig,
) -> None:
    completed = [result for result in results if result.status == "completed"]
    has_sidecar_stages = any(_stage_timing_samples(result) for result in results)
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
        ]
        if has_sidecar_stages
        else [
            "Current runs can compare whole encode time across images, modes, distances, and efforts,",
            "but cannot attribute time to color transform, block statistics, DCT/IDCT candidate",
            "transforms, quantization scoring, filter simulation, or histogram prepass.",
            "",
            "Getting those timings requires a custom `jxl-encoder` build that records spans inside",
            "the Rust encoder and emits structured timing data for this harness to ingest.",
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
        f"- `stage_timing.json`: {stage_artifact}",
        "- `profiler_commands.md`: perf/samply/flamegraph command templates for stack attribution.",
        "",
        "## Stage Timing Feasibility",
        "",
        *feasibility_lines,
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
        "To get timings for color transform, block statistics, DCT/IDCT candidate transforms,",
        "quantization scoring, filter simulation, and histogram prepass, use a custom",
        "`jxl-encoder` build that:",
        "",
        "- adds a low-overhead stage timer in the Rust encoder;",
        "- wraps the relevant VarDCT and modular functions with stable stage names;",
        "- emits per-stage JSON through `cjxl-rs --stage-timing-json`; and",
        "- runs `jxl-parity profile --instrument-stages` to merge sidecars into `stage_timing.json`.",
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


def _shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)
