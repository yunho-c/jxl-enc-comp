from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_html(path: Path, summary: object, rows: list[dict[str, object]]) -> None:
    summary_dict = asdict(summary)
    headers = list(rows[0].keys()) if rows else []
    table_rows = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>"
        for row in rows
    )
    header_cells = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    summary_items = "\n".join(
        f"<li><strong>{html.escape(key)}</strong>: {html.escape(str(value))}</li>"
        for key, value in summary_dict.items()
    )
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>JPEG XL parity report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; }}
    th {{ background: #f3f3f3; position: sticky; top: 0; }}
    .failed {{ color: #9f1239; }}
  </style>
</head>
<body>
  <h1>JPEG XL parity report</h1>
  <ul>{summary_items}</ul>
  <table>
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_feature_coverage(path: Path, rows: list[dict[str, object]], tool_status: dict[str, bool]) -> None:
    def has_case(encoder: str | None = None, **criteria: object) -> bool:
        if encoder is not None:
            criteria["encoder"] = encoder
        return any(all(row.get(key) == value for key, value in criteria.items()) for row in rows)

    def status_for(encoder: str, **criteria: object) -> str:
        criteria["encoder"] = encoder
        matches = [row for row in rows if all(row.get(key) == value for key, value in criteria.items())]
        if not matches:
            return "not tested"
        if any(row.get("status") == "passed" for row in matches):
            return "pass"
        if any(row.get("status") == "failed" for row in matches):
            return "fail"
        return "skipped"

    def corpus_status(key: str, value: object) -> tuple[str, str]:
        libjxl = "tested" if has_case("libjxl", **{key: value}) else "not present"
        jxl_encoder = "tested" if has_case("jxl-encoder", **{key: value}) else "not present"
        return libjxl, jxl_encoder

    alpha = corpus_status("has_alpha", True)
    high_bit = corpus_status("bit_depth", 16)
    jpeg = corpus_status("source_format", "JPEG")

    features = [
        ("lossless modular", status_for("libjxl", mode="lossless"), status_for("jxl-encoder", mode="lossless"), "Pixel-exact round-trip required."),
        ("lossy VarDCT", status_for("libjxl", mode="vardct"), status_for("jxl-encoder", mode="vardct"), "Decode plus size/quality/timing comparison."),
        ("alpha inputs", alpha[0], alpha[1], "Covered when corpus includes alpha."),
        ("16-bit inputs", high_bit[0], high_bit[1], "Covered when corpus includes 16-bit PNG/TIFF."),
        ("JPEG pixel encode", jpeg[0], jpeg[1], "JPEG transcoding is intentionally not compared."),
        ("SSIMULACRA2 metric", "available" if tool_status.get("ssimulacra2") else "missing", "same", "Optional external metric."),
        ("Butteraugli metric", "available" if tool_status.get("butteraugli") else "missing", "same", "Optional external metric."),
        ("animation", "not implemented", "not implemented", "Single-frame parity suite scope."),
        ("ICC/Exif/XMP metadata", "not validated", "not validated", "Inputs are normalized to pixel references for encoder parity."),
        ("stage instrumentation", "external profiler", "external profiler", "Use emitted timing data with profiler/flamegraphs."),
    ]

    lines = [
        "# Feature Coverage",
        "",
        "| Feature | libjxl | jxl-encoder | Notes |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {feature} | {libjxl} | {jxl_encoder} | {notes} |"
        for feature, libjxl, jxl_encoder, notes in features
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_csv(path: Path, rows: list[dict[str, object]]) -> None:
    grouped: dict[tuple[object, object, object, object], dict[str, object]] = {}
    for row in rows:
        key = (row["encoder"], row["mode"], row["distance"], row["effort"])
        group = grouped.setdefault(
            key,
            {
                "encoder": row["encoder"],
                "mode": row["mode"],
                "distance": row["distance"],
                "effort": row["effort"],
                "cases": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "avg_bpp": "",
                "avg_encode_seconds_per_mp": "",
                "avg_psnr": "",
                "avg_ssimulacra2": "",
            },
        )
        group["cases"] = int(group["cases"]) + 1
        group[str(row["status"])] = int(group.get(str(row["status"]), 0)) + 1

    for group in grouped.values():
        matches = [
            row
            for row in rows
            if row["encoder"] == group["encoder"]
            and row["mode"] == group["mode"]
            and row["distance"] == group["distance"]
            and row["effort"] == group["effort"]
        ]
        group["avg_bpp"] = _average(row.get("bits_per_pixel") for row in matches)
        group["avg_encode_seconds_per_mp"] = _average(
            (float(row["encode_seconds"]) / float(row["megapixels"]))
            for row in matches
            if row.get("encode_seconds") not in {"", None} and float(row["megapixels"]) > 0
        )
        group["avg_psnr"] = _average(row.get("psnr") for row in matches)
        group["avg_ssimulacra2"] = _average(row.get("ssimulacra2") for row in matches)

    write_csv(path, grouped.values())


def write_corpus_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    seen: dict[object, dict[str, object]] = {}
    for row in rows:
        seen.setdefault(
            row["image_id"],
            {
                "image_id": row["image_id"],
                "source_path": row["source_path"],
                "width": row["width"],
                "height": row["height"],
                "megapixels": row["megapixels"],
                "source_format": row["source_format"],
                "image_mode": row["image_mode"],
                "has_alpha": row["has_alpha"],
                "bit_depth": row["bit_depth"],
            },
        )
    write_csv(path, seen.values())


def _average(values: Iterable[object]) -> float | str:
    numbers: list[float] = []
    for value in values:
        if value in {"", None}:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math_is_finite(number):
            numbers.append(number)
    return sum(numbers) / len(numbers) if numbers else ""


def math_is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}
