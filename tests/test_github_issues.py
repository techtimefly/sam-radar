import importlib.util
import sys
from pathlib import Path


def load_issue_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "create_github_issues.py"
    spec = importlib.util.spec_from_file_location("create_github_issues", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_backlog_creates_milestones_and_issues():
    module = load_issue_script()
    backlog = (Path(__file__).resolve().parents[1] / "docs" / "github-backlog.md").read_text()

    milestones, issues = module.parse_backlog(backlog)

    assert "v0.1 - Public MVP" in milestones
    assert "v1.0 - Stable Self-Hosted Release" in milestones
    assert any(issue.title == "Repo foundation and CI" for issue in issues)
    assert all(issue.body for issue in issues)
