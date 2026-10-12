"""Test date/age extraction from job postings."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.utils import DateParser


class TestDateParser:
    """Test extract_days_ago for various formats."""

    def test_posted_today(self):
        assert DateParser.extract_days_ago("posted today") == 0

    def test_posted_yesterday(self):
        assert DateParser.extract_days_ago("posted yesterday") == 1

    def test_posted_3_days_ago(self):
        assert DateParser.extract_days_ago("posted 3 days ago") == 3

    def test_posted_10_days_ago(self):
        assert DateParser.extract_days_ago("posted 10 days ago") == 10

    def test_posted_30_plus_days_ago(self):
        result = DateParser.extract_days_ago("posted 30+ days ago")
        assert result == 31  # 30+ = 31

    def test_posted_hours_ago(self):
        assert DateParser.extract_days_ago("posted 5 hours ago") == 0

    def test_posted_1_month_ago(self):
        result = DateParser.extract_days_ago("posted 1 month ago")
        assert result == 30

    def test_empty_returns_none(self):
        assert DateParser.extract_days_ago("") is None
        assert DateParser.extract_days_ago(None) is None

    def test_no_date_info(self):
        assert DateParser.extract_days_ago("Apply now for this great opportunity!") is None
