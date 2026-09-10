evaluation_file := env_var_or_default("EVALUATION_FILE", "evaluation/questions.local.json")
benchmark_output := env_var_or_default("BENCHMARK_OUTPUT", "_tmp/benchmark.json")
benchmark_iterations := env_var_or_default("BENCHMARK_ITERATIONS", "5")

default: check

setup:
    uv sync --all-groups

format:
    uv run ruff format .

lint:
    uv run ruff check .

typecheck:
    uv run ty check

test:
    uv run pytest

check:
    uv run ruff format --check .
    uv run ruff check .
    uv run ty check
    uv run pytest

# Everything below needs a configured Drive — see README.md.

census:
    uv run python scripts/census.py

test-live:
    uv run pytest -m live -q

evaluate:
    uv run python scripts/evaluate_retrieval.py "{{ evaluation_file }}"

benchmark:
    uv run python -m gdrive_scoped.bench.benchmark_cli "{{ evaluation_file }}" --output "{{ benchmark_output }}" --iterations "{{ benchmark_iterations }}"

# Build the package and refuse a source distribution carrying anything outside the allowlist.
package:
    rm -rf dist
    uv build
    uv run python scripts/check_sdist.py dist/*.tar.gz

# The example adapter, for trying the library by hand.
example-stdio:
    uv run python examples/mcp_server.py

example-http:
    uv run python examples/mcp_server.py --transport streamable-http
