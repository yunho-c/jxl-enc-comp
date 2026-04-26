> Do you think it would be possible to build a comprehensive parity test suite to compare between libjxl and jxl-encoder—to see if jxl-encoder is mature enough to be used as a surrogate for profiling?

---

# Building a Parity Test Suite for `libjxl` vs. `jxl-encoder`

## Executive summary

Yes, it is possible and worth doing. But you need to define **parity** carefully.

For encoders, **bit-exact parity with `libjxl` is the wrong goal**. Two valid JPEG XL encoders can produce different `.jxl` byte streams for the same input and settings, while both decode correctly and both are standards-compliant.

The right goal is:

> Can `jxl-encoder` produce valid JPEG XL files across the cases I care about, with comparable decoded pixels, quality/size tradeoffs, and stage behavior good enough to use as a profiling surrogate?

That is testable.

---

## Current baseline

`jxl-encoder` presents itself as a pure Rust JPEG XL encoder supporting both **lossless Modular** and **lossy VarDCT** modes. Its docs also expose separate `modular`, `vardct`, `color`, and `entropy_coding`-style module boundaries, which is exactly why it is attractive as a profiling sandbox.

However, that does **not** automatically mean it is mature enough to stand in for `libjxl`. `libjxl` remains the practical reference implementation and broad compatibility baseline. Its C API exposes the encoder mostly as an opaque object rather than a conveniently stage-inspectable pipeline.

So the test suite should answer a narrower question:

> Is `jxl-encoder` representative enough for the stages I want to profile, not “is it a drop-in `libjxl` replacement?”

---

## What your parity suite should compare

I would build the suite in layers.

---

## 1. Validity and decodability

For every encoded file from both encoders:

```text
input image
→ encode with libjxl
→ encode with jxl-encoder
→ decode both with libjxl decoder
→ optionally decode both with jxl-oxide or another decoder
→ verify dimensions, channels, bit depth, alpha, and color metadata
```

This catches basic correctness and compatibility problems.

Where possible, include official JPEG XL conformance materials in addition to your private corpus.

The main questions:

- Does the file decode successfully?
- Does it decode with `djxl` / `libjxl`?
- Does it decode with at least one independent decoder?
- Are dimensions and channel counts correct?
- Are alpha, bit depth, color metadata, and orientation handled correctly?

---

## 2. Lossless pixel equality

For lossless mode, parity is strict:

```text
decode(jxl_encoder_output) == original_pixels
decode(libjxl_output) == original_pixels
```

For lossless encoding, `jxl-encoder` either round-trips or it does not.

Test cases should include:

```text
RGB
RGBA
grayscale
gray + alpha
1-bit-like images
8-bit images
16-bit images
transparent edges
large flat regions
screenshots
pixel art
gradients
odd image dimensions
large images
```

The report should clearly separate:

```text
lossless success
lossless mismatch
decode failure
metadata mismatch
unsupported input format
```

---

## 3. Lossy quality and size comparison

For lossy mode, do **not** expect decoded pixels to match `libjxl`. Instead compare:

```text
file size
bits per pixel
Butteraugli / SSIMULACRA2 / DSSIM / PSNR
encode time
decode time
visual diffs
artifact patterns
```

Use matched target settings as closely as possible, but expect imperfect mapping. A `libjxl --distance=1.0` encode and a `jxl-encoder` encode that claims similar quality may not produce the same perceptual target.

For each input, generate a table like:

```text
image | mode | target | libjxl size | jxl-encoder size | libjxl metric | jxl-encoder metric | time ratio
```

The important question is not:

> Do they produce the same output?

The important question is:

> At similar visual quality, is the compute distribution similar enough to trust profiling?

---

## 4. Feature coverage parity

This is where I would expect differences.

Build a feature matrix:

```text
Feature                         libjxl    jxl-encoder
------------------------------------------------------
lossless modular                yes       test
lossy VarDCT                    yes       test
alpha in lossy                  yes       test
ICC profiles                    yes       test
Exif/XMP/JUMBF metadata          yes       test
animation                       yes?      likely weak/none
progressive encoding            yes       test / likely different
JPEG lossless transcoding        yes       likely no
HDR / wide gamut                 yes       test
16-bit PNG input                 yes       test
CMYK / unusual color             yes       likely weak
large image groups               yes       test
odd dimensions                   yes       test
```

This matters because a profiling surrogate only needs to match the feature subset you care about.

For GPU work, you probably care most about:

```text
VarDCT lossy photo path
Modular lossless screenshot path
alpha handling
16-bit / high-bit-depth behavior, if relevant
```

You probably do **not** need JPEG lossless transcoding as part of the first GPU prototype.

---

## 5. Stage-level structural comparison

This is the key part for your use case.

You want to know whether `jxl-encoder` has roughly analogous stages to `libjxl`:

```text
color transform
block strategy
VarDCT transform
quantization
entropy coding
modular prediction
context modeling
histogram building
bitstream assembly
```

Because `jxl-encoder` exposes more of this structure in Rust, it should be much easier to instrument than `libjxl`.

But be careful:

> Same stage names do not guarantee same algorithms, same heuristics, same bottlenecks, or same optimization maturity.

A pure Rust encoder may spend time in places `libjxl` does not, simply because `libjxl` has years of CPU, SIMD, and multithreading optimization.

So your suite should report both:

```text
A. Output parity:
   validity, size, quality, round-trip correctness

B. Profiling parity:
   stage proportions, scaling behavior, and bottleneck similarity
```

---

## Recommended test corpus

Use a deliberately annoying corpus, not just pretty photos.

### Photos

```text
portraits
landscapes
night shots
high ISO / noisy images
grass, leaves, water
sky gradients
sharp architecture
skin tones
low-light indoor images
```

### Screenshots

```text
macOS screenshots
Windows screenshots
text-heavy UI
terminal windows
browser pages
diagrams
IDE screenshots
dark-mode UI
```

### Graphics

```text
flat colors
icons
line art
pixel art
synthetic gradients
transparency stress tests
alpha edges
logos
charts
```

### Edge cases

```text
tiny images
very large images
odd dimensions
16-bit PNG/TIFF
alpha premultiplication traps
grayscale
gray + alpha
embedded ICC profiles
Exif orientation
wide-gamut images
HDR-like images, if your pipeline supports them
```

### Existing JPEGs

Include existing JPEGs, but separate them:

```text
JPEG input:
  libjxl can do special JPEG transcoding
  jxl-encoder should probably be tested as pixel encode only
```

Do not let JPEG transcoding results pollute the general comparison.

---

## Pass/fail criteria

I would define levels.

---

## Level 1: Basic surrogate

`jxl-encoder` can be used for early profiling experiments if:

```text
lossless round-trips correctly for normal RGB/RGBA/grayscale
lossy VarDCT files decode reliably with libjxl
file sizes are within maybe 20–50% of libjxl at comparable quality
stage boundaries are clear enough to instrument
runtime is not dominated by obvious unoptimized glue
```

This is enough to prototype `wgpu` kernels.

---

## Level 2: Credible profiling surrogate

It becomes a credible surrogate if:

```text
quality/size curves have similar shape to libjxl
the expensive stages are algorithmically comparable
stage timings scale similarly with megapixels, effort, and image class
profiles show real codec work, not incidental Rust implementation overhead
```

This is the threshold I would want before drawing conclusions like:

> DCT is worth GPU acceleration.

or:

> Histogram/context work is the real bottleneck.

---

## Level 3: Production replacement

This is a much higher bar:

```text
wide feature coverage
excellent conformance
metadata correctness
color-management correctness
robust fuzzing
competitive compression
competitive speed
stable API
security posture
maintenance confidence
```

Do not assume this unless your tests prove it.

---

## Suggested CLI design

A Rust CLI could look like this:

```bash
jxl-parity run \
  --corpus ./corpus \
  --libjxl /usr/local/bin/cjxl \
  --jxl-encoder \
  --modes lossless,vardct \
  --distances 0.5,1.0,2.0,3.0 \
  --efforts 5,7,9 \
  --metrics butteraugli,ssimulacra2,psnr \
  --out ./reports
```

Pipeline:

```text
1. Normalize input metadata into a manifest.
2. Encode with libjxl.
3. Encode with jxl-encoder.
4. Decode both with djxl / libjxl.
5. Compare decoded pixels for lossless.
6. Compute visual metrics for lossy.
7. Record file size and timing.
8. Generate HTML/CSV reports.
9. Save visual diff images for failures and outliers.
```

For profiling:

```bash
jxl-parity profile \
  --encoder jxl-encoder \
  --instrument-stages \
  --corpus ./corpus
```

And separately:

```bash
perf record --call-graph dwarf target/release/jxl-parity ...
```

---

## Suggested report outputs

Generate these artifacts:

```text
summary.csv
per_image_results.csv
feature_coverage.md
lossless_failures/
lossy_outliers/
visual_diffs/
flamegraphs/
stage_timing.json
report.html
```

The HTML report should include:

```text
overall pass/fail summary
lossless round-trip failures
decode failures
size/quality scatter plots
time/megapixel charts
stage timing breakdowns
image thumbnails for worst outliers
links to visual diffs
```

---

## What to avoid

Avoid using byte equality between `.jxl` outputs as a correctness test.

This is usually the wrong test:

```text
libjxl_output.jxl == jxl_encoder_output.jxl
```

A better test is:

```text
decode(libjxl_output.jxl) is correct
decode(jxl_encoder_output.jxl) is correct
quality/size/runtime behavior is comparable
stage-level bottlenecks are similar enough for the intended experiment
```

Also avoid testing only one mode. A Rust encoder could be decent in Modular lossless and weak in VarDCT lossy, or vice versa. Keep the modes separate in your reports.

---

## My recommendation

Yes, build the parity suite. It is the right move.

But frame `jxl-encoder` as a **profiling sandbox**, not a `libjxl` substitute, until it passes your own corpus tests.

The productive path is:

```text
1. Use libjxl as oracle and baseline.
2. Use jxl-encoder as an inspectable experimental encoder.
3. Build parity tests to define where the sandbox is trustworthy.
4. Prototype wgpu kernels only for stages that both:
   - matter in jxl-encoder profiles
   - also appear hot in libjxl flamegraphs
```

That last condition is the guardrail.

If a stage is hot only in `jxl-encoder` but not in `libjxl`, accelerating it teaches you less about real-world JPEG XL encoding.

---

## Source links

- `jxl-encoder` crate documentation: https://docs.rs/jxl-encoder
- `libjxl` project: https://github.com/libjxl/libjxl
- JPEG XL overview: https://jpeg.org/jpegxl/
- JPEG XL format overview in libjxl repository: https://github.com/libjxl/libjxl/blob/main/doc/format_overview.md
