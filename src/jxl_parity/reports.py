from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable


PAIRED_COMPARISON_FIELDS = [
    "image_id",
    "source_path",
    "mode",
    "distance",
    "effort",
    "libjxl_status",
    "jxl_encoder_status",
    "libjxl_bpp",
    "jxl_encoder_bpp",
    "bpp_ratio_jxl_encoder_to_libjxl",
    "libjxl_psnr",
    "jxl_encoder_psnr",
    "psnr_delta_jxl_encoder_minus_libjxl",
    "libjxl_ssimulacra2",
    "jxl_encoder_ssimulacra2",
    "ssimulacra2_delta_jxl_encoder_minus_libjxl",
    "libjxl_butteraugli",
    "jxl_encoder_butteraugli",
    "butteraugli_delta_jxl_encoder_minus_libjxl",
    "libjxl_encode_seconds_per_mp",
    "jxl_encoder_encode_seconds_per_mp",
    "encode_time_ratio_jxl_encoder_to_libjxl",
]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    rows = list(rows)
    if not rows:
        if fieldnames is None:
            path.write_text("", encoding="utf-8")
            return
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=fieldnames).writeheader()
        return
    fieldnames = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_html(path: Path, summary: object, rows: list[dict[str, object]]) -> None:
    summary_dict = asdict(summary)
    headers = list(rows[0].keys()) if rows else []
    failures = [row for row in rows if row.get("status") == "failed"]
    decode_failures = [row for row in failures if row.get("reason") == "decode failed"]
    lossless_failures = [
        row for row in failures if row.get("mode") == "lossless" and row.get("reason") != "decode failed"
    ]
    table_rows = "\n".join(
        f"<tr class=\"{html.escape(str(row.get('status', '')))}\">"
        + "".join(f"<td>{_format_cell(row.get(header, ''))}</td>" for header in headers)
        + "</tr>"
        for row in rows
    )
    header_cells = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    summary_items = "\n".join(
        f"<li><strong>{html.escape(key)}</strong>: {html.escape(str(value))}</li>"
        for key, value in summary_dict.items()
    )
    quality_chart = _scatter_svg(rows, "bits_per_pixel", "ssimulacra2", "Lossy size vs SSIMULACRA2")
    if quality_chart == "":
        quality_chart = _scatter_svg(rows, "bits_per_pixel", "psnr", "Lossy size vs PSNR")
    paired_time_chart = _paired_time_svg(rows)
    time_chart = _time_svg(rows)
    paired_rows = _paired_comparison_rows(rows)
    paired_section = _section_table("Paired Encoder Comparison", paired_rows)
    failure_section = _section_table("Failures", failures)
    decode_section = _section_table("Decode Failures", decode_failures)
    lossless_section = _section_table("Lossless Round-Trip Failures", lossless_failures)
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>JPEG XL parity report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
    section {{ margin: 2rem 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; }}
    th {{ background: #f3f3f3; position: sticky; top: 0; }}
    tr.failed {{ background: #fff1f2; }}
    tr.skipped {{ color: #71717a; }}
    svg {{ max-width: 100%; height: auto; border: 1px solid #ddd; background: #fff; }}
  </style>
</head>
<body>
  <h1>JPEG XL parity report</h1>
  <ul>{summary_items}</ul>
  {failure_section}
  {decode_section}
  {lossless_section}
  {paired_section}
  <section>
    <h2>Size and Quality</h2>
    {quality_chart or "<p>No lossy quality data was available.</p>"}
  </section>
  <section>
    <h2>Encode Time vs Image Size</h2>
    {paired_time_chart or "<p>No paired timing data was available.</p>"}
  </section>
  <section>
    <h2>Slowest Encode Cases</h2>
    {time_chart or "<p>No timing data was available.</p>"}
  </section>
  <section>
    <h2>All Results</h2>
    <table>
      <thead><tr>{header_cells}</tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </section>
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
        ("stage instrumentation", "profile command", "profile command", "Use `jxl-parity profile` totals with profiler/flamegraphs."),
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


def write_paired_comparisons(path: Path, rows: list[dict[str, object]]) -> None:
    write_csv(path, _paired_comparison_rows(rows), PAIRED_COMPARISON_FIELDS)


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


def _format_cell(value: object) -> str:
    if isinstance(value, str) and value.endswith(".png") and ("/visual_diffs/" in value or value.startswith("visual_diffs/")):
        escaped = html.escape(value)
        return f'<a href="{escaped}">{escaped}</a>'
    return html.escape(str(value))


def _section_table(title: str, rows: list[dict[str, object]]) -> str:
    if not rows:
        return f"<section><h2>{html.escape(title)}</h2><p>None.</p></section>"
    preferred_headers = ["image_id", "encoder", "mode", "effort", "distance", "reason", "visual_diff_path"]
    headers = [header for header in preferred_headers if header in rows[0]]
    headers.extend(header for header in rows[0] if header not in headers)
    header_cells = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{_format_cell(row.get(header, ''))}</td>" for header in headers) + "</tr>"
        for row in rows
    )
    return f"<section><h2>{html.escape(title)}</h2><table><thead><tr>{header_cells}</tr></thead><tbody>{body}</tbody></table></section>"


def _paired_comparison_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[object, object, object, object], dict[str, dict[str, object]]] = {}
    for row in rows:
        key = (row["image_id"], row["mode"], row["distance"], row["effort"])
        grouped.setdefault(key, {})[str(row["encoder"])] = row

    comparisons: list[dict[str, object]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
        encoders = grouped[key]
        libjxl = encoders.get("libjxl")
        jxl_encoder = encoders.get("jxl-encoder")
        if libjxl is None or jxl_encoder is None:
            continue
        libjxl_time_per_mp = _seconds_per_mp(libjxl)
        jxl_encoder_time_per_mp = _seconds_per_mp(jxl_encoder)
        comparisons.append(
            {
                "image_id": key[0],
                "source_path": libjxl.get("source_path", ""),
                "mode": key[1],
                "distance": key[2],
                "effort": key[3],
                "libjxl_status": libjxl.get("status", ""),
                "jxl_encoder_status": jxl_encoder.get("status", ""),
                "libjxl_bpp": _number_or_blank(libjxl.get("bits_per_pixel")),
                "jxl_encoder_bpp": _number_or_blank(jxl_encoder.get("bits_per_pixel")),
                "bpp_ratio_jxl_encoder_to_libjxl": _ratio(
                    jxl_encoder.get("bits_per_pixel"), libjxl.get("bits_per_pixel")
                ),
                "libjxl_psnr": _number_or_blank(libjxl.get("psnr")),
                "jxl_encoder_psnr": _number_or_blank(jxl_encoder.get("psnr")),
                "psnr_delta_jxl_encoder_minus_libjxl": _delta(jxl_encoder.get("psnr"), libjxl.get("psnr")),
                "libjxl_ssimulacra2": _number_or_blank(libjxl.get("ssimulacra2")),
                "jxl_encoder_ssimulacra2": _number_or_blank(jxl_encoder.get("ssimulacra2")),
                "ssimulacra2_delta_jxl_encoder_minus_libjxl": _delta(
                    jxl_encoder.get("ssimulacra2"), libjxl.get("ssimulacra2")
                ),
                "libjxl_butteraugli": _number_or_blank(libjxl.get("butteraugli")),
                "jxl_encoder_butteraugli": _number_or_blank(jxl_encoder.get("butteraugli")),
                "butteraugli_delta_jxl_encoder_minus_libjxl": _delta(
                    jxl_encoder.get("butteraugli"), libjxl.get("butteraugli")
                ),
                "libjxl_encode_seconds_per_mp": _number_or_blank(libjxl_time_per_mp),
                "jxl_encoder_encode_seconds_per_mp": _number_or_blank(jxl_encoder_time_per_mp),
                "encode_time_ratio_jxl_encoder_to_libjxl": _ratio(
                    jxl_encoder_time_per_mp, libjxl_time_per_mp
                ),
            }
        )
    return comparisons


def _seconds_per_mp(row: dict[str, object]) -> float | None:
    seconds = _to_float(row.get("encode_seconds"))
    megapixels = _to_float(row.get("megapixels"))
    if seconds is None or megapixels in {None, 0.0}:
        return None
    return seconds / (megapixels or 1.0)


def _number_or_blank(value: object) -> float | str:
    number = _to_float(value)
    return number if number is not None else ""


def _ratio(numerator: object, denominator: object) -> float | str:
    numerator_float = _to_float(numerator)
    denominator_float = _to_float(denominator)
    if numerator_float is None or denominator_float in {None, 0.0}:
        return ""
    return numerator_float / (denominator_float or 1.0)


def _delta(left: object, right: object) -> float | str:
    left_float = _to_float(left)
    right_float = _to_float(right)
    if left_float is None or right_float is None:
        return ""
    return left_float - right_float


def _scatter_svg(rows: list[dict[str, object]], x_key: str, y_key: str, title: str) -> str:
    points = [
        row
        for row in rows
        if row.get("mode") == "vardct"
        and row.get("status") == "passed"
        and _to_float(row.get(x_key)) is not None
        and _to_float(row.get(y_key)) is not None
    ]
    if not points:
        return ""

    width, height = 760, 360
    pad = 48
    xs = [_to_float(row.get(x_key)) or 0.0 for row in points]
    ys = [_to_float(row.get(y_key)) or 0.0 for row in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    def scale(value: float, low: float, high: float, out_low: float, out_high: float) -> float:
        if high == low:
            return (out_low + out_high) / 2
        return out_low + ((value - low) / (high - low)) * (out_high - out_low)

    circles = []
    labels = []
    colors = {"libjxl": "#2563eb", "jxl-encoder": "#dc2626"}
    for row in points:
        x = scale(_to_float(row.get(x_key)) or 0.0, min_x, max_x, pad, width - pad)
        y = scale(_to_float(row.get(y_key)) or 0.0, min_y, max_y, height - pad, pad)
        color = colors.get(str(row.get("encoder")), "#52525b")
        label = html.escape(f"{row.get('encoder')} {row.get('image_id')}")
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"><title>{label}</title></circle>')
    labels.extend(
        [
            f'<text x="{pad}" y="24" font-size="16" font-weight="600">{html.escape(title)}</text>',
            f'<text x="{pad}" y="{height - 10}" font-size="12">{html.escape(x_key)}: {min_x:.3g} - {max_x:.3g}</text>',
            f'<text x="{width - pad - 160}" y="{height - 10}" font-size="12">{html.escape(y_key)}: {min_y:.3g} - {max_y:.3g}</text>',
            f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#71717a" />',
            f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#71717a" />',
        ]
    )
    return f'<svg viewBox="0 0 {width} {height}" role="img">{"".join(labels + circles)}</svg>'


def _time_svg(rows: list[dict[str, object]]) -> str:
    values = []
    for row in rows:
        seconds = _to_float(row.get("encode_seconds"))
        megapixels = _to_float(row.get("megapixels"))
        if seconds is None or megapixels in {None, 0.0} or row.get("status") != "passed":
            continue
        values.append((seconds / (megapixels or 1.0), row))
    if not values:
        return ""

    values = sorted(values, key=lambda item: item[0], reverse=True)[:20]
    width = 760
    row_height = 22
    pad = 170
    height = 40 + row_height * len(values)
    max_value = max(value for value, _ in values)
    bars = [f'<text x="16" y="24" font-size="16" font-weight="600">Slowest encode cases</text>']
    for index, (value, row) in enumerate(values):
        y = 42 + index * row_height
        bar_width = 1 if max_value == 0 else (value / max_value) * (width - pad - 32)
        label = html.escape(f"{row.get('encoder')} {row.get('mode')} e{row.get('effort')}")
        bars.append(f'<text x="16" y="{y + 13}" font-size="12">{label}</text>')
        bars.append(f'<rect x="{pad}" y="{y}" width="{bar_width:.1f}" height="14" fill="#0f766e" />')
        bars.append(f'<text x="{pad + bar_width + 6:.1f}" y="{y + 12}" font-size="12">{value:.3f}s/MP</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img">{"".join(bars)}</svg>'


def _paired_time_svg(rows: list[dict[str, object]]) -> str:
    grouped: dict[tuple[object, object, object, object], dict[str, dict[str, object]]] = {}
    for row in rows:
        key = (row["image_id"], row["mode"], row["distance"], row["effort"])
        grouped.setdefault(key, {})[str(row["encoder"])] = row

    pairs = []
    for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
        encoders = grouped[key]
        libjxl = encoders.get("libjxl")
        jxl_encoder = encoders.get("jxl-encoder")
        if libjxl is None or jxl_encoder is None:
            continue
        if libjxl.get("status") != "passed" or jxl_encoder.get("status") != "passed":
            continue
        megapixels = _to_float(libjxl.get("megapixels"))
        libjxl_seconds = _to_float(libjxl.get("encode_seconds"))
        jxl_encoder_seconds = _to_float(jxl_encoder.get("encode_seconds"))
        if megapixels in {None, 0.0} or libjxl_seconds is None or jxl_encoder_seconds is None:
            continue
        pairs.append((megapixels or 0.0, libjxl_seconds, jxl_encoder_seconds, libjxl, jxl_encoder))

    if not pairs:
        return ""

    width, height = 760, 420
    left, right, top, bottom = 70, 140, 48, 60
    plot_width = width - left - right
    plot_height = height - top - bottom
    max_mp = max(megapixels for megapixels, *_ in pairs)
    max_seconds = max(max(libjxl_seconds, jxl_encoder_seconds) for _, libjxl_seconds, jxl_encoder_seconds, *_ in pairs)
    x_high = max_mp if max_mp > 0 else 1.0
    y_high = max_seconds if max_seconds > 0 else 1.0

    def scale_x(value: float) -> float:
        return left + (value / x_high) * plot_width

    def scale_y(value: float) -> float:
        return top + plot_height - (value / y_high) * plot_height

    pieces = [
        '<text x="16" y="24" font-size="16" font-weight="600">Paired encode time by image size</text>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#71717a" />',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#71717a" />',
        f'<text x="{left}" y="{height - 18}" font-size="12">Image size: 0 - {x_high:.3g} MP</text>',
        f'<text x="{width - right - 60}" y="{height - 18}" font-size="12">seconds</text>',
        f'<text x="{left + plot_width + 20}" y="{top + 18}" font-size="12" fill="#2563eb">libjxl</text>',
        f'<circle cx="{left + plot_width + 12}" cy="{top + 14}" r="5" fill="#2563eb" />',
        f'<text x="{left + plot_width + 20}" y="{top + 38}" font-size="12" fill="#dc2626">jxl-encoder</text>',
        f'<circle cx="{left + plot_width + 12}" cy="{top + 34}" r="5" fill="#dc2626" />',
        f'<text x="16" y="{top + 8}" font-size="12">0 - {y_high:.3g}s</text>',
    ]

    for megapixels, libjxl_seconds, jxl_encoder_seconds, libjxl, jxl_encoder in pairs:
        x = scale_x(megapixels)
        libjxl_y = scale_y(libjxl_seconds)
        jxl_encoder_y = scale_y(jxl_encoder_seconds)
        distance = libjxl.get("distance")
        quality = "lossless" if distance in {"", None} else f"d{distance}"
        label_bits = f"{libjxl.get('image_id')} {libjxl.get('mode')} {quality} e{libjxl.get('effort')}"
        label = html.escape(label_bits)
        pieces.append(
            f'<line x1="{x:.1f}" y1="{libjxl_y:.1f}" x2="{x:.1f}" y2="{jxl_encoder_y:.1f}" '
            'stroke="#a1a1aa" stroke-width="1" opacity="0.7" />'
        )
        pieces.append(
            f'<circle cx="{x:.1f}" cy="{libjxl_y:.1f}" r="4.5" fill="#2563eb" opacity="0.9">'
            f"<title>{label} libjxl {libjxl_seconds:.3f}s</title></circle>"
        )
        pieces.append(
            f'<circle cx="{x:.1f}" cy="{jxl_encoder_y:.1f}" r="4.5" fill="#dc2626" opacity="0.9">'
            f"<title>{label} jxl-encoder {jxl_encoder_seconds:.3f}s</title></circle>"
        )

    return f'<svg viewBox="0 0 {width} {height}" role="img">{"".join(pieces)}</svg>'


def _to_float(value: object) -> float | None:
    if value in {"", None}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math_is_finite(number) else None
