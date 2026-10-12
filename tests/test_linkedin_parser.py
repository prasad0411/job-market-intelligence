"""Tests for LinkedInEmailParser."""
import pytest
from aggregator.extractors import LinkedInEmailParser


def _build_job_card(company, title, location, job_id, flavor=""):
    """Build a realistic LinkedIn email job card HTML."""
    flavor_html = ""
    if flavor:
        flavor_html = f'''
        <table class="inline-block"><tr>
        <td class="job-card-flavor__container">
        <table><tbody><tr>
        <td class="pr-1 w-3"><img src="icon.png" alt="radar icon" width="24" height="24"></td>
        <td><p class="job-card-flavor__detail">{flavor}</p></td>
        </tr></tbody></table>
        </td></tr></table>'''

    return f'''
    <tr><td class="pt-3" data-test-id="job-card">
    <table><tbody><tr><td>
    <table><tbody><tr><td>
    <table><tbody><tr>
    <td class="pr-2 w-6" valign="top">
      <a href="https://www.linkedin.com/comm/jobs/view/{job_id}?alertAction=markasviewed&amp;trk=eml-company_logo" target="_blank">
        <img alt="{company}" src="https://media.licdn.com/logo.png" width="64" height="64">
      </a>
    </td>
    <td valign="top">
      <a href="https://www.linkedin.com/comm/jobs/view/{job_id}?alertAction=markasviewed&amp;trk=eml-job_posting" target="_blank">
        <table><tbody>
        <tr><td class="pb-0.25">
          <a href="https://www.linkedin.com/comm/jobs/view/{job_id}?trk=eml-jobcard_body_{job_id}" target="_blank"
             class="text-md leading-regular text-color-brand">{title}</a>
        </td></tr>
        <tr><td class="pb-0.5">
          <p class="text-system-gray-100 text-sm leading-[20px]">{company} \u00b7 {location}, United States</p>
        </td></tr>
        <tr><td>{flavor_html}</td></tr>
        </tbody></table>
      </a>
    </td>
    </tr></tbody></table>
    </td></tr></tbody></table>
    </td></tr></tbody></table>
    </td></tr>'''


def _build_email(cards_html):
    """Wrap job cards in a minimal LinkedIn email structure."""
    return f'''<html><head></head><body>
    <table><tbody><tr><td>
    <table><tbody><tr><td>
    <h2>Your job alert has been created: <strong>SWE Intern</strong> in <strong>United States</strong></h2>
    </td></tr>
    {cards_html}
    </tbody></table>
    </td></tr></tbody></table>
    </body></html>'''


class TestLinkedInEmailParser:
    """Core parsing tests."""

    def test_parse_single_job(self):
        card = _build_job_card("Neuralink", "ML Engineer Intern", "San Francisco, California", "4416712707", "Actively recruiting")
        html = _build_email(card)
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        assert len(jobs) == 1
        url = "https://www.linkedin.com/jobs/view/4416712707"
        assert url in jobs
        assert jobs[url]["company"] == "Neuralink"
        assert jobs[url]["title"] == "ML Engineer Intern"
        assert jobs[url]["location"] == "San Francisco, California"
        assert jobs[url]["linkedin_job_id"] == "4416712707"

    def test_parse_multiple_jobs(self):
        cards = (
            _build_job_card("Jacobs", "Data Science Internship", "Boston, Massachusetts", "4425925246", "Actively recruiting")
            + _build_job_card("EV Realty", "Intern, Data Analytics", "San Francisco, California", "4428109102")
            + _build_job_card("Neuralink", "ML Engineer Intern", "San Francisco, California", "4416712707", "16 school alumni")
        )
        html = _build_email(cards)
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        assert len(jobs) == 3

    def test_company_from_img_alt_fallback(self):
        """When info line has no middot, fall back to img alt."""
        html = _build_email('''
        <tr><td data-test-id="job-card">
          <img alt="Tesla" src="logo.png" width="64" height="64">
          <a href="https://www.linkedin.com/comm/jobs/view/99999?trk=eml-jobcard_body_99999"
             class="text-md text-color-brand">Software Intern</a>
          <p class="text-system-gray-100">No middot here</p>
        </td></tr>''')
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        assert len(jobs) == 1
        data = list(jobs.values())[0]
        assert data["company"] == "Tesla"
        assert data["title"] == "Software Intern"

    def test_location_cleaning_strips_united_states(self):
        card = _build_job_card("Acme", "SWE Intern", "Denver, Colorado", "1111111")
        html = _build_email(card)
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        data = list(jobs.values())[0]
        assert data["location"] == "Denver, Colorado"
        assert "United States" not in data["location"]

    def test_location_only_united_states(self):
        """If location is just 'United States' with no city, keep it."""
        html = _build_email('''
        <tr><td data-test-id="job-card">
          <a href="https://www.linkedin.com/comm/jobs/view/22222?trk=eml-jobcard_body_22222"
             class="text-md">Remote SWE Intern</a>
          <p class="text-system-gray-100">RemoteCo \u00b7 United States</p>
        </td></tr>''')
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        data = list(jobs.values())[0]
        assert data["location"] == "United States"

    def test_dedup_same_job_id(self):
        """Same job ID appearing twice should only produce one entry."""
        cards = (
            _build_job_card("Acme", "SWE Intern", "NYC, New York", "5555555")
            + _build_job_card("Acme", "SWE Intern", "NYC, New York", "5555555")
        )
        html = _build_email(cards)
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        assert len(jobs) == 1

    def test_empty_html(self):
        assert LinkedInEmailParser.parse_email_jobs("") == {}
        assert LinkedInEmailParser.parse_email_jobs(None) == {}

    def test_no_job_cards(self):
        html = "<html><body><p>No jobs here</p></body></html>"
        assert LinkedInEmailParser.parse_email_jobs(html) == {}

    def test_skip_alts_not_used_as_company(self):
        """LinkedIn logo, radar icon, user name should not become company."""
        html = _build_email('''
        <tr><td data-test-id="job-card">
          <img alt="LinkedIn" src="logo.png">
          <a href="https://www.linkedin.com/comm/jobs/view/33333?trk=eml-jobcard_body_33333"
             class="text-md">Backend Intern</a>
          <p class="text-system-gray-100">RealCo \u00b7 Austin, Texas, United States</p>
        </td></tr>''')
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        data = list(jobs.values())[0]
        assert data["company"] == "RealCo"

    def test_real_email_sample(self):
        """Test against real LinkedIn email if available."""
        try:
            with open("/tmp/linkedin_email_sample.html", "r") as f:
                html = f.read()
        except FileNotFoundError:
            pytest.skip("No real email sample at /tmp/linkedin_email_sample.html")
        jobs = LinkedInEmailParser.parse_email_jobs(html)
        assert len(jobs) >= 5
        companies = {d["company"] for d in jobs.values()}
        assert "Neuralink" in companies
        assert "Ekimetrics" in companies
