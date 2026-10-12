"""Test salary extraction and rejection logic."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.processors import ValidationHelper


class TestSalaryExtraction:
    """Salary check should reject < $25/hr and accept >= $25/hr or no salary."""

    def test_reject_low_salary_with_unit(self, salary_page):
        soup = salary_page("Pay: $20.00/hr to $24.00/hr")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision == "REJECT"
        assert "Low salary" in reason

    def test_reject_low_hourly_rate(self, salary_page):
        soup = salary_page("Hourly: $18.00")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision == "REJECT"

    def test_accept_high_salary(self, salary_page):
        soup = salary_page("Compensation: $42.00/hour")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision is None

    def test_accept_range_with_high_end(self, salary_page):
        soup = salary_page("Pay range: $22.00 - $35.00 per hour")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        # High end >= 25, should accept
        assert decision is None

    def test_reject_range_both_low(self, salary_page):
        soup = salary_page("Hourly rate: $18.00 - $22.00/hr")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision == "REJECT"

    def test_no_salary_accepts(self, salary_page):
        soup = salary_page("This is a great internship opportunity at our company.")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision is None

    def test_exact_25_accepts(self, salary_page):
        soup = salary_page("Pay: $25.00/hr")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision is None

    def test_salary_at_bottom_of_page(self, salary_page):
        # Salary with unit identifier after 5000+ chars
        soup = salary_page("Pay rate: $20.00/hr", pad_before=3000)
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision == "REJECT"

    def test_proposed_minimum_salary(self, salary_page):
        soup = salary_page("Proposed Minimum Salary $20.00 hourly")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        assert decision == "REJECT"

    def test_annual_salary_ignored(self, salary_page):
        soup = salary_page("Annual salary: $55,000 - $65,000")
        decision, reason = ValidationHelper.check_salary_requirement(soup)
        # Annual salary numbers > 200, should be filtered out
        assert decision is None
