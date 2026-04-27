# List available recipes.
default:
    @just --list

# Run the unit test suite.
test:
    PYTHONPATH=src python3 -m unittest discover -s tests
