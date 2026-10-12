"""Relevance scorer: ranking, bounds, case handling, short skill boundaries and empty input."""
from aggregator.scorer import ResumeScorer

ML_RESUME = "Python, PyTorch, machine learning, deep learning, pandas, SQL, React, Go"


def scorer() -> ResumeScorer:
    return ResumeScorer(resume_text=ML_RESUME)


def test_ml_posting_outranks_hardware_posting():
    s = scorer()
    ml = s.score_job(title="Machine Learning Engineer Intern", company="Acme", description="PyTorch and Python for deep learning")
    hw = s.score_job(title="Hardware Technician", company="Acme", description="soldering and circuit boards")
    assert ml > hw


def test_scores_stay_between_0_and_100():
    s = scorer()
    for title, desc in [("ML Engineer Intern", "pytorch python machine learning deep learning"), ("Barista", ""), ("", "")]:
        assert 0 <= s.score_job(title=title, company="Google", description=desc) <= 100


def test_matching_ignores_case():
    s = scorer()
    lower = s.score_job(title="ml engineer intern", company="acme", description="pytorch python")
    upper = s.score_job(title="ML ENGINEER INTERN", company="ACME", description="PYTORCH PYTHON")
    assert lower == upper


def test_short_skill_does_not_match_inside_other_words():
    s = scorer()
    real = s.score_job(title="Software Engineer Intern", company="Acme", description="Backend services in Go")
    fake = s.score_job(title="Software Engineer Intern", company="Acme", description="Storage arrays at Google scale")
    assert real > fake


def test_empty_resume_never_crashes():
    s = ResumeScorer(resume_text="")
    assert 0 <= s.score_job(title="ML Engineer Intern", company="Google", description="pytorch") <= 100
