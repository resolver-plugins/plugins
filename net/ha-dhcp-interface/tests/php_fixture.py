"""Execute controller fixtures and retain PHP diagnostics on failure."""
import json
import subprocess


def run_php_fixture(fixture, *args):
    result = subprocess.run(['php', str(fixture), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"PHP fixture failed: {result.stderr or result.stdout}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid PHP fixture response: {result.stdout}\n{result.stderr}") from exc
