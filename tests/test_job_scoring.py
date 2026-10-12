"""Relevance scoring hook: off by default, ranks and saves when on, and can never break a run."""
import json

from aggregator import config
from aggregator import run_aggregator as ra

JOBS = [
    {"title": "Hardware Technician", "company": "Acme", "location": "Boston, MA", "url": "https://a/1"},
    {"title": "Machine Learning Engineer Intern", "company": "Beta", "location": "Remote",
     "description": "PyTorch and Python", "url": "https://b/2"},
]


def test_off_by_default_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCORE_JOBS", False, raising=False)
    monkeypatch.setattr(config, "SCORE_OUTPUT_PATH", str(tmp_path / "scores.json"), raising=False)
    assert ra._score_valid_jobs(JOBS) is None
    assert not (tmp_path / "scores.json").exists()


def test_on_ranks_jobs_and_saves_them(tmp_path, monkeypatch):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python, PyTorch, machine learning, deep learning")
    out = tmp_path / "scores.json"
    monkeypatch.setattr(config, "SCORE_JOBS", True, raising=False)
    monkeypatch.setattr(config, "SCORE_RESUME_PATH", str(resume), raising=False)
    monkeypatch.setattr(config, "SCORE_OUTPUT_PATH", str(out), raising=False)
    ranked = ra._score_valid_jobs(JOBS)
    assert [r["company"] for r in ranked] == ["Beta", "Acme"]
    assert json.loads(out.read_text())[0]["company"] == "Beta"


def test_missing_resume_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCORE_JOBS", True, raising=False)
    monkeypatch.setattr(config, "SCORE_RESUME_PATH", str(tmp_path / "none.txt"), raising=False)
    assert ra._score_valid_jobs(JOBS) is None


def test_a_scorer_error_never_breaks_the_run(tmp_path, monkeypatch):
    resume = tmp_path / "resume.txt"
    resume.write_text("python")
    monkeypatch.setattr(config, "SCORE_JOBS", True, raising=False)
    monkeypatch.setattr(config, "SCORE_RESUME_PATH", str(resume), raising=False)
    import aggregator.scorer as scorer

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("bad resume")
    monkeypatch.setattr(scorer, "ResumeScorer", Boom)
    assert ra._score_valid_jobs(JOBS) is None
