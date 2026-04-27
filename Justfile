# List available recipes.
default:
    @just --list

# Run the unit test suite.
test:
    PYTHONPATH=src python3 -m unittest discover -s tests

# Run the parity test suite. Pass jxl-parity flags after the recipe name.
parity *args:
    PYTHONPATH=src python3 -m jxl_parity.cli run {{args}}

# Run a profiling sweep. Pass jxl-parity profile flags after the recipe name.
profile *args:
    PYTHONPATH=src python3 -m jxl_parity.cli profile {{args}}

# Run a small parity smoke suite.
parity-smoke:
    PYTHONPATH=src python3 -m jxl_parity.cli run --max-images 3 --modes lossless --efforts 1 --out reports/smoke

# Run a small profiling smoke suite.
profile-smoke:
    PYTHONPATH=src python3 -m jxl_parity.cli profile --encoder jxl-encoder --instrument-stages --samples 2 --warmups 1 --max-images 1 --modes vardct --distances 1.0 --efforts 7 --out reports/profile-smoke
