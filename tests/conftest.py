"""Shared fixtures for all test modules."""
import pytest
import sys
import os
from bs4 import BeautifulSoup

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def make_soup():
    """Factory fixture: build a BeautifulSoup from raw HTML text."""
    def _make(html_text):
        return BeautifulSoup(html_text, "html.parser")
    return _make


@pytest.fixture
def salary_page(make_soup):
    """Factory: build a page with salary text at a given position."""
    def _page(salary_text, pad_before=0):
        padding = "x " * pad_before
        html = f"<html><body><p>{padding}</p><p>{salary_text}</p></body></html>"
        return make_soup(html)
    return _page


@pytest.fixture
def job_page(make_soup):
    """Factory: build a realistic job posting page."""
    def _page(body_text):
        html = f"""<html><body>
        <div class="job-description">{body_text}</div>
        </body></html>"""
        return make_soup(html)
    return _page
