from __future__ import annotations

import html
import shlex
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .codecs import (
    CommandResult,
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


@dataclass(frozen=True)
class _ProfilerOutcome:
    status: str
    reason: str
    returncode: int | None
    elapsed_seconds: float | None
    profiler_args: list[str]
    stderr: str | None


@dataclass
class _FlameNode:
    name: str
    count: int = 0
    children: dict[str, "_FlameNode"] | None = None


def run_flamegraph(config: FlamegraphConfig) -> FlamegraphSummary:
    out_dir = config.out_dir
    work_dir = out_dir / "work"
    run_dir = out_dir / "flamegraph-run"
    encoded_dir = work_dir / "encoded"
    for directory in (out_dir, work_dir, run_dir, encoded_dir):
        directory.mkdir(parents=True, exist_ok=True)

    encoder_command = config.cjxl if config.encoder == "libjxl" else config.jxl_encoder
    encoder_path = tool_path(encoder_command)
    flamegraph_path = tool_path(config.flamegraph)
    tool_status = {
        "encoder": encoder_path is not None,
        "flamegraph": flamegraph_path is not None,
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
    trace_path = run_dir / "cargo-flamegraph.trace"
    xctrace_trace_path = run_dir / "xctrace-fallback.trace"
    xctrace_xml_path = run_dir / "xctrace-time-profile.xml"
    _clear_previous_outputs(
        svg_path,
        encoded_path,
        stage_timing_candidate,
        trace_path,
        xctrace_trace_path,
        xctrace_xml_path,
        out_dir / "folded_stacks.txt",
        out_dir / "xctrace_record_command.txt",
        out_dir / "xctrace_export_command.txt",
    )

    encoder_args = build_encode_args(
        encoder=config.encoder,
        command=encoder_path or encoder_command,
        input_path=_absolute_path(_reference_path(image)),
        output_path=_absolute_path(encoded_path),
        mode=config.mode,
        effort=config.effort,
        distance=config.distance,
        stage_timing_path=_absolute_path(stage_timing_path)
        if stage_timing_path is not None
        else None,
    )
    profiler_args = [
        config.flamegraph,
        "-o",
        str(_absolute_path(svg_path)),
        "--",
        *encoder_args,
    ]
    _write_command_artifacts(out_dir, run_dir, encoder_args, profiler_args)

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

    outcome = _run_profiler_with_fallback(
        out_dir=out_dir,
        run_dir=run_dir,
        svg_path=svg_path,
        profiler_args=profiler_args,
        encoder_args=encoder_args,
    )
    summary = _summary(
        config=config,
        image=image,
        encoded_path=encoded_path,
        svg_path=svg_path,
        stage_timing_path=stage_timing_path,
        status=outcome.status,
        reason=outcome.reason,
        returncode=outcome.returncode,
        elapsed_seconds=outcome.elapsed_seconds,
        encoder_args=encoder_args,
        profiler_args=outcome.profiler_args,
        tool_status=tool_status,
        stderr=outcome.stderr,
    )
    _write_summary(out_dir, summary)
    return summary


def _run_profiler_with_fallback(
    *,
    out_dir: Path,
    run_dir: Path,
    svg_path: Path,
    profiler_args: list[str],
    encoder_args: list[str],
) -> _ProfilerOutcome:
    result = run_command(profiler_args, cwd=run_dir)
    if result.ok:
        return _outcome_from_result(result, "completed", "ok", profiler_args)
    if _is_macos_xctrace_collapse_failure(result.stderr):
        fallback = _run_macos_xctrace_fallback(
            out_dir=out_dir,
            run_dir=run_dir,
            svg_path=svg_path,
            encoder_args=encoder_args,
            previous_stderr=result.stderr,
        )
        if fallback is not None:
            return fallback
    return _outcome_from_result(
        result, "failed", "flamegraph command failed", profiler_args
    )


def _outcome_from_result(
    result: CommandResult, status: str, reason: str, profiler_args: list[str]
) -> _ProfilerOutcome:
    return _ProfilerOutcome(
        status=status,
        reason=reason,
        returncode=result.returncode,
        elapsed_seconds=result.elapsed_seconds,
        profiler_args=profiler_args,
        stderr=result.stderr.strip()[-4000:] if result.stderr else None,
    )


def _is_macos_xctrace_collapse_failure(stderr: str) -> bool:
    return (
        sys.platform == "darwin"
        and "unable to collapse generated profile data" in stderr
        and "Read xml event failed" in stderr
        and "MismatchedEndTag" in stderr
    )


def _run_macos_xctrace_fallback(
    *,
    out_dir: Path,
    run_dir: Path,
    svg_path: Path,
    encoder_args: list[str],
    previous_stderr: str,
) -> _ProfilerOutcome | None:
    xctrace_path = tool_path("xctrace")
    if xctrace_path is None:
        return None

    trace_path = run_dir / "xctrace-fallback.trace"
    xml_path = run_dir / "xctrace-time-profile.xml"
    folded_path = out_dir / "folded_stacks.txt"
    _clear_previous_outputs(trace_path, xml_path, folded_path)

    record_args = [
        xctrace_path,
        "record",
        "--template",
        "Time Profiler",
        "--output",
        trace_path.name,
        "--target-stdout",
        "-",
        "--launch",
        "--",
        *encoder_args,
    ]
    export_args = [
        xctrace_path,
        "export",
        "--input",
        trace_path.name,
        "--xpath",
        r'/trace-toc/*/data/table[@schema="time-profile"]',
    ]
    (out_dir / "xctrace_record_command.txt").write_text(
        f"{_shell_join(record_args)}\n", encoding="utf-8"
    )
    (out_dir / "xctrace_export_command.txt").write_text(
        f"{_shell_join(export_args)}\n", encoding="utf-8"
    )

    record_result = run_command(record_args, cwd=run_dir)
    elapsed_seconds = record_result.elapsed_seconds
    if not record_result.ok:
        return _fallback_failed(
            record_result,
            record_args,
            previous_stderr,
            "xctrace fallback record failed",
        )

    export_result = run_command(export_args, cwd=run_dir)
    elapsed_seconds += export_result.elapsed_seconds
    if not export_result.ok:
        return _fallback_failed(
            export_result,
            export_args,
            previous_stderr,
            "xctrace fallback export failed",
        )

    xml_path.write_text(export_result.stdout, encoding="utf-8")
    try:
        stacks = _collapse_xctrace_time_profile(export_result.stdout)
        if not stacks:
            raise ValueError("no stack samples found in xctrace export")
        _write_folded_stacks(folded_path, stacks)
        _write_basic_flamegraph_svg(svg_path, stacks)
    except (ET.ParseError, ValueError) as error:
        return _ProfilerOutcome(
            status="failed",
            reason="xctrace fallback render failed",
            returncode=1,
            elapsed_seconds=elapsed_seconds,
            profiler_args=export_args,
            stderr=_combined_stderr(previous_stderr, str(error)),
        )
    finally:
        shutil.rmtree(trace_path, ignore_errors=True)

    return _ProfilerOutcome(
        status="completed",
        reason="ok (macOS xctrace fallback after flamegraph XML collapse failure)",
        returncode=0,
        elapsed_seconds=elapsed_seconds,
        profiler_args=record_args,
        stderr=None,
    )


def _fallback_failed(
    result: CommandResult,
    profiler_args: list[str],
    previous_stderr: str,
    reason: str,
) -> _ProfilerOutcome:
    return _ProfilerOutcome(
        status="failed",
        reason=reason,
        returncode=result.returncode,
        elapsed_seconds=result.elapsed_seconds,
        profiler_args=profiler_args,
        stderr=_combined_stderr(previous_stderr, result.stderr),
    )


def _combined_stderr(primary: str, secondary: str) -> str:
    detail = "\n\nmacOS xctrace fallback:\n".join(
        part.strip() for part in (primary, secondary) if part.strip()
    )
    return detail[-4000:] if detail else ""


def _collapse_xctrace_time_profile(xml_text: str) -> Counter[tuple[str, ...]]:
    root = ET.fromstring(xml_text)
    frames: dict[str, str] = {}
    backtraces: dict[str, tuple[str, ...]] = {}
    stacks: Counter[tuple[str, ...]] = Counter()

    for row in root.iter():
        if _xml_tag(row.tag) != "row":
            continue
        backtrace = next(
            (child for child in row if _xml_tag(child.tag) == "backtrace"), None
        )
        if backtrace is None:
            continue

        frame_ids: tuple[str, ...] | None
        if "ref" in backtrace.attrib:
            frame_ids = backtraces.get(backtrace.attrib["ref"])
        else:
            parsed_frame_ids: list[str] = []
            for frame in backtrace:
                if _xml_tag(frame.tag) != "frame":
                    continue
                frame_id = frame.attrib.get("ref")
                if frame_id is None:
                    frame_id = frame.attrib.get("id")
                    if frame_id is None:
                        continue
                    frames[frame_id] = _frame_name(frame)
                parsed_frame_ids.append(frame_id)
            frame_ids = tuple(parsed_frame_ids)
            backtrace_id = backtrace.attrib.get("id")
            if backtrace_id is not None:
                backtraces[backtrace_id] = frame_ids

        if not frame_ids:
            continue
        stack = tuple(
            _clean_frame_name(frames[frame_id])
            for frame_id in reversed(frame_ids)
            if frame_id in frames
        )
        if stack:
            stacks[stack] += 1
    return stacks


def _xml_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _frame_name(frame: ET.Element) -> str:
    return frame.attrib.get("name") or frame.attrib.get("addr") or "(unknown)"


def _clean_frame_name(name: str) -> str:
    cleaned = " ".join(name.split())
    return cleaned or "(unknown)"


def _write_folded_stacks(
    folded_path: Path, stacks: Counter[tuple[str, ...]]
) -> None:
    lines = []
    for stack, count in sorted(stacks.items(), key=lambda item: (-item[1], item[0])):
        folded_stack = ";".join(frame.replace(";", ":") for frame in stack)
        lines.append(f"{folded_stack} {count}")
    folded_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_basic_flamegraph_svg(
    svg_path: Path, stacks: Counter[tuple[str, ...]]
) -> None:
    root = _build_flame_tree(stacks)
    max_depth = _max_depth(root)
    width = 1200
    frame_height = 18
    top_padding = 36
    bottom_padding = 24
    graph_width = width - 20
    height = top_padding + bottom_padding + (max_depth + 1) * frame_height
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        "<style>",
        (
            "text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,"
            "sans-serif;font-size:12px;fill:#111827}"
        ),
        ".frame rect{stroke:#ffffff;stroke-width:.5}",
        ".subtitle{font-size:12px;fill:#4b5563}",
        "</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="10" y="20" font-size="16" font-weight="600">macOS xctrace flamegraph</text>',
        (
            f'<text class="subtitle" x="10" y="34">{root.count} samples; '
            "generated from xctrace time-profile XML</text>"
        ),
    ]
    _append_svg_node(
        lines,
        root,
        x=10.0,
        y_base=height - bottom_padding,
        width=graph_width,
        depth=0,
        frame_height=frame_height,
    )
    lines.append("</svg>")
    svg_path.write_text("\n".join(lines), encoding="utf-8")


def _build_flame_tree(stacks: Counter[tuple[str, ...]]) -> _FlameNode:
    root = _FlameNode("(all)", children={})
    for stack, count in stacks.items():
        root.count += count
        node = root
        for frame in stack:
            if node.children is None:
                node.children = {}
            node = node.children.setdefault(frame, _FlameNode(frame, children={}))
            node.count += count
    return root


def _max_depth(node: _FlameNode) -> int:
    if not node.children:
        return 0
    return 1 + max(_max_depth(child) for child in node.children.values())


def _append_svg_node(
    lines: list[str],
    node: _FlameNode,
    *,
    x: float,
    y_base: int,
    width: float,
    depth: int,
    frame_height: int,
) -> None:
    if width < 0.5 or node.count <= 0:
        return
    y = y_base - ((depth + 1) * frame_height)
    title = html.escape(f"{node.name} ({node.count} samples)")
    label = html.escape(_fit_label(node.name, width))
    color = _frame_color(node.name)
    lines.extend(
        [
            f'<g class="frame"><title>{title}</title>',
            (
                f'<rect x="{x:.3f}" y="{y}" width="{width:.3f}" '
                f'height="{frame_height - 1}" fill="{color}" rx="2" ry="2"/>'
            ),
        ]
    )
    if label:
        lines.append(f'<text x="{x + 4:.3f}" y="{y + 13}">{label}</text>')
    lines.append("</g>")

    if not node.children:
        return
    child_x = x
    for child in sorted(
        node.children.values(), key=lambda item: (-item.count, item.name)
    ):
        child_width = width * (child.count / node.count)
        _append_svg_node(
            lines,
            child,
            x=child_x,
            y_base=y_base,
            width=child_width,
            depth=depth + 1,
            frame_height=frame_height,
        )
        child_x += child_width


def _fit_label(label: str, width: float) -> str:
    max_chars = int((width - 8) / 7)
    if max_chars < 4:
        return ""
    if len(label) <= max_chars:
        return label
    return f"{label[: max_chars - 3]}..."


def _frame_color(name: str) -> str:
    value = sum((index + 1) * ord(char) for index, char in enumerate(name))
    red = 180 + (value % 55)
    green = 80 + ((value // 7) % 95)
    blue = 55 + ((value // 13) % 65)
    return f"rgb({red},{green},{blue})"


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
    out_dir: Path, run_dir: Path, encoder_args: list[str], profiler_args: list[str]
) -> None:
    encoder_command = _shell_join(encoder_args)
    profiler_command = _shell_join(profiler_args)
    working_directory = _shell_join([_absolute_path(run_dir)])
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
        "- `flamegraph-run/`: isolated working directory for profiler trace files.",
        "- `folded_stacks.txt`: folded stacks when the macOS xctrace fallback is used.",
        "- `xctrace_*_command.txt`: fallback profiler commands when generated.",
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
        if path.is_dir():
            shutil.rmtree(path)
        else:
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


def _absolute_path(path: Path) -> Path:
    return path.expanduser().resolve()


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
