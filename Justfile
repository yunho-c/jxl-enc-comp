# List available recipes.
default:
    @just --list

# Run the unit test suite.
test:
    PYTHONPATH=src python3 -m unittest discover -s tests

# Run the parity test suite. Pass jxl-parity flags after the recipe name.
parity *args:
    PYTHONPATH=src python3 -m jxl_parity.cli run {{args}}

# Run a small parity smoke suite.
parity-smoke:
    PYTHONPATH=src python3 -m jxl_parity.cli run --max-images 3 --modes lossless --efforts 1 --out reports/smoke
