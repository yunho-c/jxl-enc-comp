# jxl-enc-comp

`jxl-enc-comp` is a parity test harness for comparing `libjxl` (`cjxl`/`djxl`)
with the Rust `jxl-encoder` CLI (`cjxl-rs`).

The suite is intentionally output-oriented: it does not compare encoded `.jxl`
bytes. It encodes inputs with each encoder, decodes the outputs through
`djxl`, checks metadata and lossless round trips, records size/timing, computes
optional quality metrics, and emits CSV, JSON, Markdown, and HTML reports.

## Quick Start

```bash
python3 -m pip install -e .
jxl-parity run --out reports/parity
```

By default the runner looks for local sample corpora at:

- `~/GitHub/Kodak-Lossless-True-Color-Image-Suite`
- `~/GitHub/test_images`

It also auto-detects `cjxl`, `djxl`, `cjxl-rs`, `ssimulacra2`, and `butteraugli`
on `PATH`. Missing optional tools are reported in the output instead of causing
the whole run to fail.

