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

