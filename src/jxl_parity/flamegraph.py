from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .codecs import (
    build_encode_args,
    run_command,
    tool_path,
    tool_supports_option,
)
from .corpus import ImageRecord, discover_images
from .reports import write_json


@dataclass(frozen=True)
class FlamegraphConfig:
    corpus: list[Path]
    out_dir: Path
    cjxl: str
    jxl_encoder: str
    encoder: str
    mode: str
    distance: float | None
    effort: int
    max_images: int | None
    flamegraph: str
    dry_run: bool
    instrument_stages: bool


@dataclass(frozen=True)
class FlamegraphSummary:
    out_dir: Path
    image_id: str
    source_path: str
    reference_path: str
    encoded_path: str
    svg_path: str
    stage_timing_path: str | None
    encoder: str
    mode: str
    distance: float | None
    effort: int
    status: str
    reason: str
    returncode: int | None
    elapsed_seconds: float | None
    encoder_command: str
    profiler_command: str
    tool_status: dict[str, bool]
    stderr: str | None = None


def run_flamegraph(config: FlamegraphConfig) -> FlamegraphSummary:
    out_dir = config.out_dir
    work_dir = out_dir / "work"
    encoded_dir = work_dir / "encoded"
    for directory in (out_dir, work_dir, encoded_dir):
        directory.mkdir(parents=True, exist_ok=True)

    encoder_command = config.cjxl if config.encoder == "libjxl" else config.jxl_encoder
    tool_status = {
        "encoder": tool_path(encoder_command) is not None,
        "flamegraph": tool_path(config.flamegraph) is not None,
    }
    stage_timing_supported = (
        config.instrument_stages
        and config.encoder == "jxl-encoder"
        and tool_status["encoder"]
        and tool_supports_option(config.jxl_encoder, "--stage-timing-json")
    )
    tool_status["jxl_encoder_stage_timing"] = stage_timing_supported

    if not config.dry_run and not tool_status["encoder"]:
        raise FileNotFoundError(f"encoder command not found: {encoder_command}")
    if not config.dry_run and not tool_status["flamegraph"]:
        raise FileNotFoundError(f"flamegraph command not found: {config.flamegraph}")

    image = _first_supported_image(
        discover_images(config.corpus, work_dir, config.max_images)
    )
    case_id = _case_id(image, config)
    encoded_path = encoded_dir / f"{case_id}.jxl"
    svg_path = out_dir / "flamegraph.svg"
    stage_timing_candidate = encoded_dir / f"{case_id}.stage-timing.json"
    stage_timing_path = stage_timing_candidate if stage_timing_supported else None
    _clear_previous_outputs(svg_path, encoded_path, stage_timing_candidate)

    encoder_args = build_encode_args(
        encoder=config.encoder,
        command=encoder_command,
        input_path=_reference_path(image),
        output_path=encoded_path,
        mode=config.mode,
        effort=config.effort,
        distance=config.distance,
        stage_timing_path=stage_timing_path,
    )
    profiler_args = [config.flamegraph, "-o", str(svg_path), "--", *encoder_args]
    _write_command_artifacts(out_dir, encoder_args, profiler_args)

    if config.dry_run:
        summary = _summary(
            config=config,
            image=image,
            encoded_path=encoded_path,
            svg_path=svg_path,
            stage_timing_path=stage_timing_path,
            status="prepared",
            reason="dry run; profiler command was not executed",
            returncode=None,
            elapsed_seconds=None,
            encoder_args=encoder_args,
            profiler_args=profiler_args,
            tool_status=tool_status,
            stderr=None,
        )
        _write_summary(out_dir, summary)
        return summary

    result = run_command(profiler_args)
    summary = _summary(
        config=config,
        image=image,
        encoded_path=encoded_path,
        svg_path=svg_path,
        stage_timing_path=stage_timing_path,
        status="completed" if result.ok else "failed",
        reason="ok" if result.ok else "flamegraph command failed",
        returncode=result.returncode,
        elapsed_seconds=result.elapsed_seconds,
        encoder_args=encoder_args,
        profiler_args=profiler_args,
        tool_status=tool_status,
        stderr=result.stderr.strip()[-4000:] if result.stderr else None,
    )
    _write_summary(out_dir, summary)
    return summary


def _summary(
    *,
    config: FlamegraphConfig,
    image: ImageRecord,
    encoded_path: Path,
    svg_path: Path,
    stage_timing_path: Path | None,
    status: str,
    reason: str,
    returncode: int | None,
    elapsed_seconds: float | None,
    encoder_args: list[str],
    profiler_args: list[str],
    tool_status: dict[str, bool],
    stderr: str | None,
) -> FlamegraphSummary:
    return FlamegraphSummary(
        out_dir=config.out_dir,
        image_id=image.image_id,
        source_path=str(image.source_path),
        reference_path=str(_reference_path(image)),
        encoded_path=str(encoded_path),
        svg_path=str(svg_path),
        stage_timing_path=str(stage_timing_path) if stage_timing_path else None,
        encoder=config.encoder,
        mode=config.mode,
        distance=config.distance,
        effort=config.effort,
        status=status,
        reason=reason,
        returncode=returncode,
        elapsed_seconds=elapsed_seconds,
        encoder_command=_shell_join(encoder_args),
        profiler_command=_shell_join(profiler_args),
        tool_status=tool_status,
        stderr=stderr,
    )


def _write_summary(out_dir: Path, summary: FlamegraphSummary) -> None:
    write_json(out_dir / "flamegraph_summary.json", _json_ready(asdict(summary)))


def _write_command_artifacts(
    out_dir: Path, encoder_args: list[str], profiler_args: list[str]
) -> None:
    encoder_command = _shell_join(encoder_args)
    profiler_command = _shell_join(profiler_args)
    working_directory = _shell_join([Path.cwd()])
    (out_dir / "encoder_command.txt").write_text(
        f"{encoder_command}\n", encoding="utf-8"
    )
    (out_dir / "flamegraph_command.txt").write_text(
        f"{profiler_command}\n", encoding="utf-8"
    )
    script = out_dir / "run_flamegraph.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"cd {working_directory}",
                profiler_command,
                "",
            ]
        ),
        encoding="utf-8",
    )
    current_mode = script.stat().st_mode
    script.chmod(current_mode | 0o111)
    notes = [
        "# Flamegraph Entrypoint",
        "",
        "This directory contains a normalized one-image encoder invocation wrapped by",
        "`flamegraph`. Re-run `run_flamegraph.sh` after rebuilding the target encoder",
        "with debug symbols and frame pointers when you need clearer Rust stacks.",
        "",
        "Artifacts:",
        "",
        "- `flamegraph.svg`: flamegraph output when the profiler command completes.",
        "- `encoder_command.txt`: raw encoder command without profiler wrapping.",
        "- `flamegraph_command.txt`: exact profiler command.",
        "- `flamegraph_summary.json`: selected image, command, status, and tool metadata.",
        "- `work/reference/`: normalized PNG input used by the encoder.",
        "- `work/encoded/`: generated `.jxl` output and optional stage sidecar.",
        "",
        "For Rust binaries, prefer release builds with frame pointers, for example:",
        "",
        "```bash",
        'RUSTFLAGS="-C force-frame-pointers=yes -C debuginfo=2" cargo build --release',
        "```",
        "",
    ]
    (out_dir / "README.md").write_text("\n".join(notes), encoding="utf-8")


def _clear_previous_outputs(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _first_supported_image(images: list[ImageRecord]) -> ImageRecord:
    for image in images:
        if image.unsupported_reason is None:
            return image
    raise FileNotFoundError("no supported image files found for flamegraph profiling")


def _case_id(image: ImageRecord, config: FlamegraphConfig) -> str:
    quality = (
        "lossless"
        if config.distance is None
        else f"d{config.distance:g}".replace(".", "p")
    )
    return (
        f"{image.image_id}-{config.encoder}-{config.mode}-{quality}-e{config.effort}"
        .replace("/", "-")
    )


def _reference_path(image: ImageRecord) -> Path:
    if image.reference_path is None:
        raise ValueError(f"image has no reference path: {image.source_path}")
    return image.reference_path


def _shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
