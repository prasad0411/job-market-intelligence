"""Test URL → company name mappings."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggregator.processors import CompanyExtractor


class TestURLMappings:
    """Every URL pattern in URL_TO_COMPANY_MAPPING should resolve correctly."""

    @pytest.mark.parametrize("url,expected", [
        # Workday patterns
        ("https://quickenloans.wd1.myworkdayjobs.com/job/123", "Rocket Companies"),
        ("https://geico.wd5.myworkdayjobs.com/ext/job/456", "GEICO"),
        ("https://zoll.wd5.myworkdayjobs.com/en-US/ZOLLMedicalCorp/job/Pittsburgh", "ZOLL Medical"),
        ("https://thermofisher.wd5.myworkdayjobs.com/ThermoFisherCareers/job/CA", "Thermo Fisher Scientific"),
        ("https://genpt.wd1.myworkdayjobs.com/careers/job/Birmingham", "Genuine Parts Company"),
        ("https://jeffersonhealth.wd5.myworkdayjobs.com/job/PA", "Jefferson Health"),
        ("https://icf.wd5.myworkdayjobs.com/icfexternal/job/Reston", "ICF"),
        ("https://njm.wd1.myworkdayjobs.com/njm_confidential/job/Trenton", "NJM Insurance"),
        ("https://fiserv.wd5.myworkdayjobs.com/ext/job/Alpharetta", "Fiserv"),
        ("https://chamberlain.wd1.myworkdayjobs.com/Chamberlain_Group/job/IL", "Chamberlain Group"),
        ("https://thales.wd3.myworkdayjobs.com/en-US/Careers/job/Cincinnati", "Thales"),
        ("https://kiongroup.wd3.myworkdayjobs.com/en-US/KION_SCS/job/Holland", "KION Group"),
        ("https://statestreet.wd1.myworkdayjobs.com/en-US/global/job/Burlington", "State Street"),
        ("https://hntb.wd5.myworkdayjobs.com/hntb_university_careers/job/FL", "HNTB"),
        ("https://tamus.wd1.myworkdayjobs.com/System-wide_External/job/TX", "Texas A\u0026M University System"),
        ("https://enovis.wd5.myworkdayjobs.com/en-US/enovis/job/Atlanta", "Enovis"),
        ("https://firstam.wd1.myworkdayjobs.com/firstamericancareers/job/CA", "First American Financial"),
        ("https://realtyincome.wd108.myworkdayjobs.com/realty_income_careers/job/CA", "Realty Income"),
        ("https://moog.wd5.myworkdayjobs.com/moog_external/job/Buffalo", "Moog"),
        # Greenhouse patterns
        ("https://job-boards.greenhouse.io/billiontoone/jobs/4687612005", "BillionToOne"),
        ("https://job-boards.greenhouse.io/spacekinetic/jobs/4885803008", "Space Kinetic"),
        ("https://job-boards.greenhouse.io/avride/jobs/4230411009", "Avride"),
        ("https://job-boards.greenhouse.io/rocketlawyer/jobs/5195521008", "Rocket Lawyer"),
        ("https://job-boards.greenhouse.io/newtonresearch/jobs/5195476008", "Newton Research"),
        ("https://job-boards.greenhouse.io/armada/jobs/5195689008", "Armada"),
        ("https://job-boards.greenhouse.io/industrialelectricmanufacturing/jobs/42", "Industrial Electric Manufacturing"),
        ("https://job-boards.greenhouse.io/northspyre/jobs/7704668003", "Northspyre"),
        # iCIMS patterns
        ("https://careers-ice.icims.com/jobs/12830/job", "Intercontinental Exchange"),
        ("https://careers-axway.icims.com/jobs/8659/job", "Axway"),
        ("https://careers-sig.icims.com/jobs/10821/job", "Susquehanna International Group"),
        ("https://careers-stifel.icims.com/jobs/9137/job", "Stifel"),
        ("https://careers-magaero.icims.com/jobs/8179/job", "MAG Aerospace"),
        # Lever patterns
        ("https://jobs.lever.co/tri/000be2f3-b77c", "Toyota Research Institute"),
        ("https://jobs.lever.co/brightmachines/03006bd3", "Bright Machines"),
        # Oracle Cloud
        ("https://edel.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/22741", "Fortinet"),
        ("https://fa-exty-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/job/117344", "Howmet Aerospace"),
        # Other
        ("https://careers.hellofresh.com/global/en/job/7848068", "HelloFresh"),
        ("https://wacom.applytojob.com/apply/Byv4JKtMaR", "Wacom"),
        ("https://www.skillz.com/careers/list/?gh_jid=7813108", "Skillz"),
        ("https://sponsorunited.breezy.hr/p/371371bba8a8", "SponsorUnited"),
    ])
    def test_url_to_company(self, url, expected):
        result = CompanyExtractor.extract_from_url_mapping(url)
        assert result.value == expected, f"URL {url[:50]}... expected {expected}, got {result.value}"

    def test_unknown_url_returns_none(self):
        result = CompanyExtractor.extract_from_url_mapping("https://unknown-company.com/jobs/123")
        assert result.value is None

    def test_empty_url(self):
        result = CompanyExtractor.extract_from_url_mapping("")
        assert result.value is None

    def test_none_url(self):
        result = CompanyExtractor.extract_from_url_mapping(None)
        assert result.value is None
