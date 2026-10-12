"""Test company name normalization and cleaning."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.utils import CompanyNormalizer
from aggregator.processors import CompanyExtractor


class TestCompanyNormalizer:
    """Test ATS prefix stripping and slug mapping."""

    def test_strip_ats_code(self):
        result = CompanyNormalizer.normalize("F1138 Acme Corp")
        assert result is not None
        assert "F1138" not in result

    def test_strip_numeric_prefix(self):
        result = CompanyNormalizer.normalize("1600 NIO")
        assert result is not None
        assert "1600" not in result

    def test_strip_company_prefix(self):
        result = CompanyNormalizer.normalize("Company 601 LivaNova")
        assert result is not None
        assert "Company" not in result
        assert "601" not in result

    def test_strip_workable_prefix(self):
        result = CompanyNormalizer.normalize("Workable SomeCompany")
        assert result is not None
        assert "Workable" not in result

    def test_usc0_prefix(self):
        result = CompanyNormalizer.normalize("USC0 Revvity")
        assert result is not None
        assert "USC0" not in result

    def test_empty_returns_none(self):
        assert CompanyNormalizer.normalize("") is None
        assert CompanyNormalizer.normalize(None) is None

    def test_strip_legal_suffix(self):
        result = CompanyNormalizer.normalize("Acme Corp, Inc.")
        assert result is not None
        assert "Inc" not in result


class TestCompanyClean:
    """Test CompanyExtractor.clean_company_name."""

    def test_html_unescape(self):
        result = CompanyExtractor.clean_company_name("AT&amp;T")
        assert "&amp;" not in result

    def test_strip_leading_at(self):
        result = CompanyExtractor.clean_company_name("at Atlantic Health System")
        assert result.startswith("Atlantic")

    def test_strip_country_parenthetical(self):
        result = CompanyExtractor.clean_company_name("Siemens (United Kingdom)")
        assert "United Kingdom" not in result

    def test_workday_url_returns_unknown(self):
        result = CompanyExtractor.clean_company_name("myworkdayjobs.com")
        assert result == "Unknown"

    def test_normalizations_applied(self):
        result = CompanyExtractor.clean_company_name("The Charles Stark Draper Laboratory")
        assert result == "Draper"

    def test_unknown_passthrough(self):
        assert CompanyExtractor.clean_company_name("Unknown") == "Unknown"
        assert CompanyExtractor.clean_company_name(None) is None
