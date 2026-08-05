import importlib.util
import sys
from pathlib import Path


def load_scanner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_private_context.py"
    spec = importlib.util.spec_from_file_location("check_private_context", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_secret_assignment_regex_allows_blank_values():
    scanner = load_scanner()
    text = "SAM_GOV_API_KEY=\nAPP_BASE_URL=https://sam-radar.example.test\nAPP_WRITE_TOKEN=\n"

    assert scanner.SECRET_ASSIGNMENT.findall(text) == []


def test_secret_assignment_regex_flags_filled_values():
    scanner = load_scanner()
    text = "SAM_GOV_API_KEY=abc123\n"

    assert scanner.SECRET_ASSIGNMENT.findall(text) == ["SAM_GOV_API_KEY=abc123"]
