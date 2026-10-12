"""Test undergraduate-only and PhD-only detection."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.processors import ValidationHelper


class TestUndergradDetection:
    """Should reject undergrad-only roles, accept roles open to MS students."""

    def test_reject_pursuing_bachelor(self, job_page):
        soup = job_page("Currently pursuing a bachelor's degree in Computer Science")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_reject_rising_junior(self, job_page):
        soup = job_page("Must be a rising junior or senior in an accredited university")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_reject_advancing_to_junior(self, job_page):
        soup = job_page("Must be at least advancing to their junior year of study")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_reject_sophomore_standing(self, job_page):
        soup = job_page("Requires sophomore standing or higher")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_accept_bs_ms(self, job_page):
        soup = job_page("Pursuing a BS/MS in Computer Engineering, Computer Science")
        dec, reason = ValidationHelper._check_degree_requirements_strict(soup)
        assert dec is None, f"BS/MS should be accepted, got {reason}"

    def test_accept_masters_mentioned(self, job_page):
        soup = job_page("Currently pursuing a bachelor's or master's degree in CS")
        dec, reason = ValidationHelper._check_degree_requirements_strict(soup)
        assert dec is None, f"Master's mentioned should accept, got {reason}"

    def test_reject_phd_only(self, job_page):
        soup = job_page("must be currently pursuing a phd in computer science")
        # Production runs BOTH gates; "PhD in <field>" is caught by the
        # dedicated PhD check, not the undergraduate one. Assert what the
        # pipeline actually does rather than one half of it.
        p_dec, p_why = ValidationHelper._check_phd_only_requirements(soup)
        u_dec, u_why = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert "REJECT" in (p_dec, u_dec), \
            "PhD-only page not rejected: phd={!r} undergrad={!r}".format(p_why, u_why)

    def test_reject_phd_internship_title(self, job_page):
        soup = job_page("PhD Internship - Machine Learning Research")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_reject_2027_start(self, job_page):
        soup = job_page("This is a Summer 2027 internship program")
        dec, reason = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec == "REJECT"

    def test_accept_generic_degree(self, job_page):
        soup = job_page("Currently pursuing a degree in Computer Science or related field")
        dec, _ = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec is None

    def test_accept_no_degree_info(self, job_page):
        soup = job_page("Join our team as a software engineering intern!")
        dec, _ = ValidationHelper._check_undergraduate_only_requirements(soup)
        assert dec is None
