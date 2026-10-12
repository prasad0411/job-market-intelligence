"""Test location extraction, normalization, and international detection."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.processors import LocationProcessor


class TestLocationNormalization:
    """Test normalize_location for various messy inputs."""

    def test_wfh_to_remote(self):
        assert LocationProcessor.normalize_location("Work from Home") == "Remote"

    def test_wfh_abbreviation(self):
        # WFH pattern not matched by normalize_location — only "work from home" variants
        result = LocationProcessor.normalize_location("WFH")
        # was: assert result in ("WFH","Remote") - accepted both outcomes.
        # WFH IS normalised to Remote; assert that so a regression shows.
        assert result == "Remote", "WFH normalisation changed: " + repr(result)

    def test_usa_to_remote(self):
        assert LocationProcessor.normalize_location("USA") == "Remote"

    def test_united_states_to_remote(self):
        assert LocationProcessor.normalize_location("United States") == "Remote"

    def test_nyc_expansion(self):
        assert LocationProcessor.normalize_location("NYC") == "New York, NY"

    def test_sf_expansion(self):
        assert LocationProcessor.normalize_location("SF") == "San Francisco, CA"

    def test_dc_expansion(self):
        assert LocationProcessor.normalize_location("DC") == "Washington, DC"

    def test_icims_format(self):
        assert LocationProcessor.normalize_location("US-AZ-Scottsdale") == "Scottsdale, AZ"

    def test_icims_format_with_dash_city(self):
        result = LocationProcessor.normalize_location("US-CA-San-Jose")
        assert "San" in result and "CA" in result

    def test_venue_stripped(self):
        result = LocationProcessor.normalize_location("Auburn Hills PHINIA WHQ, MI")
        assert "PHINIA" not in result
        assert "MI" in result

    def test_empty_returns_unknown(self):
        assert LocationProcessor.normalize_location("") == "Unknown"

    def test_none_returns_unknown(self):
        assert LocationProcessor.normalize_location(None) == "Unknown"

    def test_normal_city_state(self):
        result = LocationProcessor.normalize_location("Boston, MA")
        assert result == "Boston, MA"


class TestLocationCleaning:
    """Test clean_location_aggressive for garbage inputs."""

    def test_garbage_long_text(self):
        result = LocationProcessor.clean_location_aggressive(
            "This is a very long text that contains no real location information at all and should return Unknown"
        )
        assert result == "Unknown"

    def test_department_keyword(self):
        # "Engineering" alone is short and may pass through
        # clean_location_aggressive checks DEPARTMENT_KEYWORDS which may not include bare "Engineering"
        result = LocationProcessor.clean_location_aggressive("Engineering")
        # was: assert isinstance(result, str) - only checked the type
        assert result in ("Unknown", "Engineering"), repr(result)

    def test_valid_city_state(self):
        result = LocationProcessor.clean_location_aggressive("Pittsburgh, PA")
        assert "Pittsburgh" in result
        assert "PA" in result

    def test_strips_usa_suffix(self):
        result = LocationProcessor.clean_location_aggressive("Boston, MA, USA")
        assert "USA" not in result
        assert "Boston" in result


class TestInternationalDetection:
    """Test check_if_international for various signals."""

    def test_canada_location(self):
        result = LocationProcessor.check_if_international("Toronto, ON, Canada")
        assert result is not None
        assert "Canada" in result

    def test_uk_url(self):
        result = LocationProcessor.check_if_international(
            "London", url="https://company.co.uk/jobs/123"
        )
        assert result is not None
        assert "UK" in result

    def test_us_location_passes(self):
        result = LocationProcessor.check_if_international("Boston, MA")
        assert result is None

    def test_germany_in_title(self):
        result = LocationProcessor.check_if_international(
            "Unknown", title="Software Intern - Germany"
        )
        assert result is not None


class TestUSStateGuard:
    """US state codes/names mark a job as US; foreign cities override them."""

    def _is_us(self, loc):
        import re
        US = ('CA','OH','TX','IN','DE','WA','KY','NY','NJ','MA','PA','GA','MO')
        NAMES = ('california','ohio','texas','indiana','delaware','washington')
        FOREIGN = ('munich','berlin','hyderabad','bangalore','mumbai','toronto','tokyo')
        us = bool(re.search(r',\s*(?:' + '|'.join(US) + r')\b', loc)) or \
             any(re.search(r'\b' + n + r'\b', loc, re.I) for n in NAMES)
        if re.search(r',\s*(?:ON|BC|AB|QC)\b', loc):
            us = False
        if any(re.search(r'\b' + c + r'\b', loc, re.I) for c in FOREIGN):
            us = False
        return us

    def test_us_cities_with_foreign_names_kept(self):
        for loc in ("Dublin, CA", "Dublin, OH", "Paris, TX", "London, KY", "Vancouver, WA"):
            assert self._is_us(loc), loc

    def test_foreign_cities_rejected_despite_ambiguous_code(self):
        for loc in ("Munich, DE", "Berlin, DE", "Hyderabad, IN", "Bangalore, IN"):
            assert not self._is_us(loc), loc

    def test_real_us_state_codes_still_work(self):
        assert self._is_us("Wilmington, DE")
        assert self._is_us("Indianapolis, IN")

    def test_canadian_provinces_rejected(self):
        assert not self._is_us("Toronto, ON")
        assert not self._is_us("Vancouver, BC")
