#!/usr/bin/env python3
"""
Job relevance scorer -- ranks valid jobs by fit against a resume.

Uses keyword matching with rarity weighting. No API calls, runs offline.
Plugs into the aggregator pipeline after validation and before outreach.

Usage:
    from aggregator.scorer import ResumeScorer

    scorer = ResumeScorer("path/to/resume.txt")
    score = scorer.score_job(title="ML Engineer Intern", company="Google", location="Remote")
"""

import re
from collections import Counter
from pathlib import Path


# Skills grouped by rarity. Rarer skills get higher weight because
# they're stronger signals of fit. Tailored for CS/ML internship
# postings that F-1 visa MS students typically apply to.
SKILL_WEIGHTS = {
    # Tier 1 -- every intern posting mentions these (weight 1)
    "python": 1,
    "java": 1,
    "javascript": 1,
    "c++": 1,
    "c#": 1,
    "sql": 1,
    "git": 1,
    "linux": 1,
    "html": 1,
    "css": 1,
    "agile": 1,
    "scrum": 1,
    "excel": 1,

    # Tier 2 -- common in SWE intern postings (weight 2)
    "aws": 2,
    "gcp": 2,
    "azure": 2,
    "docker": 2,
    "kubernetes": 2,
    "react": 2,
    "node": 2,
    "typescript": 2,
    "django": 2,
    "flask": 2,
    "fastapi": 2,
    "spring": 2,
    "postgresql": 2,
    "mysql": 2,
    "mongodb": 2,
    "redis": 2,
    "rest api": 2,
    "rest": 2,
    "graphql": 2,
    "microservices": 2,
    "ci/cd": 2,
    "terraform": 2,
    "go": 2,
    "rust": 2,
    "scala": 2,

    # Tier 3 -- ML/data skills, shows specialization (weight 3)
    "pytorch": 3,
    "tensorflow": 3,
    "scikit-learn": 3,
    "sklearn": 3,
    "pandas": 3,
    "numpy": 3,
    "keras": 3,
    "spark": 3,
    "hadoop": 3,
    "kafka": 3,
    "airflow": 3,
    "mlflow": 3,
    "deep learning": 3,
    "machine learning": 3,
    "neural network": 3,
    "computer vision": 3,
    "nlp": 3,
    "natural language processing": 3,
    "data pipeline": 3,
    "etl": 3,
    "data engineering": 3,
    "statistical modeling": 3,
    "regression": 3,
    "classification": 3,
    "a/b testing": 3,
    "recommendation": 3,

    # Tier 4 -- hot skills for 2025-2026 intern market (weight 4)
    "generative ai": 4,
    "llm": 4,
    "large language model": 4,
    "rag": 4,
    "fine-tuning": 4,
    "embeddings": 4,
    "vector database": 4,
    "langchain": 4,
    "huggingface": 4,
    "transformers": 4,
    "openai": 4,
    "anthropic": 4,
    "mlops": 4,
    "reinforcement learning": 4,
    "distributed systems": 4,
    "system design": 4,
    "cuda": 4,
    "triton": 4,
    "onnx": 4,
    "tensorrt": 4,
    "vllm": 4,
    "ray": 4,
    "dspy": 4,
    "distributed training": 4,
    "model optimization": 4,
    "quantization": 4,
    "inference optimization": 4,
    "causal inference": 4,
    "recommendation systems": 4,
    "feature store": 4,
}

# Bonus keywords in job titles that signal higher relevance
TITLE_BONUS = {
    "machine learning": 5,
    "ml engineer": 5,
    "ml intern": 5,
    "ai engineer": 5,
    "deep learning": 5,
    "data scientist": 4,
    "applied scientist": 4,
    "research engineer": 4,
    "mlops": 4,
    "software engineer": 2,
    "swe intern": 2,
    "backend": 2,
    "full stack": 1,
    "frontend": 1,
}

# Company tier -- known companies get a small boost
COMPANY_TIER = {
    # Tier A -- top AI/ML companies
    "google": 5, "deepmind": 5, "openai": 5, "anthropic": 5,
    "meta": 5, "apple": 5, "microsoft": 5, "nvidia": 5,
    "amazon": 4, "tesla": 4, "netflix": 4,

    # Tier B -- strong tech companies
    "stripe": 3, "databricks": 3, "snowflake": 3, "palantir": 3,
    "uber": 3, "lyft": 3, "airbnb": 3, "doordash": 3,
    "salesforce": 3, "adobe": 3, "linkedin": 3, "bytedance": 3,

    # Tier C -- solid companies
    "walmart": 2, "jpmorgan": 2, "goldman sachs": 2, "bloomberg": 2,
    "cisco": 2, "ibm": 2, "intel": 2, "qualcomm": 2, "samsung": 2,
}


class ResumeScorer:
    """Scores jobs against a resume using keyword matching with rarity weights."""

    def __init__(self, resume_path=None, resume_text=None, custom_skills=None):
        """
        Args:
            resume_path: path to a plain text resume file
            resume_text: raw resume text (alternative to file path)
            custom_skills: dict of {skill: weight} to add or override defaults
        """
        if resume_path:
            self.resume_text = Path(resume_path).read_text(encoding="utf-8").lower()
        elif resume_text:
            self.resume_text = resume_text.lower()
        else:
            self.resume_text = ""

        self.skills = dict(SKILL_WEIGHTS)
        if custom_skills:
            self.skills.update(custom_skills)

        # Extract which skills the resume actually contains
        self.resume_skills = self._extract_skills(self.resume_text)

    def _extract_skills(self, text):
        """Find all known skills in a block of text. Returns {skill: weight}."""
        text = text.lower()
        found = {}
        for skill, weight in self.skills.items():
            # Word boundary matching to avoid partial matches
            # "sql" shouldn't match "postgresql" separately
            pattern = r'\b' + re.escape(skill) + r'\b'
            if re.search(pattern, text):
                found[skill] = weight
        return found

    def score_job(self, title="", company="", location="", description="", job_type=""):
        """
        Score a single job. Returns 0-100.

        Higher scores mean better fit. The score is a weighted combination of:
        - skill overlap between resume and job (60% of score)
        - title relevance (25% of score)
        - company tier (15% of score)
        """
        combined_text = f"{title} {description} {job_type}".lower()

        # 1. Skill match score (0-60)
        job_skills = self._extract_skills(combined_text)
        overlap = set(self.resume_skills.keys()) & set(job_skills.keys())

        if not overlap:
            skill_score = 0
        else:
            # Sum of weights for matching skills
            match_weight = sum(self.skills[s] for s in overlap)
            # Normalize against the job's skill demands, not the resume total.
            # A job asking for 3 skills where you match all 3 is a perfect fit,
            # even if your resume lists 20 other skills.
            job_weight = sum(job_skills.values()) or 1
            coverage = match_weight / job_weight  # what fraction of the job you cover
            # Also factor in raw match count -- more matches = better
            depth = min(1.0, len(overlap) / 4)  # caps at 4 matching skills
            skill_score = min(60, int(60 * (0.6 * coverage + 0.4 * depth)))

        # 2. Title relevance (0-25)
        title_lower = title.lower()
        title_score = 0
        for keyword, bonus in TITLE_BONUS.items():
            if keyword in title_lower:
                title_score = max(title_score, bonus)
        title_score = min(25, title_score * 5)

        # 3. Company tier (0-15)
        company_lower = company.lower().strip()
        company_score = 0
        for name, tier in COMPANY_TIER.items():
            if name in company_lower:
                company_score = max(company_score, tier)
                break
        company_score = min(15, company_score * 3)

        total = skill_score + title_score + company_score
        return min(100, total)

    def score_jobs(self, jobs):
        """
        Score a list of jobs and return them sorted by score (highest first).

        Args:
            jobs: list of dicts with keys like 'title', 'company', 'location', etc.

        Returns:
            list of (score, job) tuples, sorted descending by score
        """
        scored = []
        for job in jobs:
            score = self.score_job(
                title=job.get("title", job.get("Title", "")),
                company=job.get("company", job.get("Company", "")),
                location=job.get("location", job.get("Location", "")),
                description=job.get("description", job.get("Description", "")),
                job_type=job.get("job_type", job.get("Job Type", "")),
            )
            scored.append((score, job))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def explain_score(self, title="", company="", description=""):
        """Show why a job got its score. Useful for debugging."""
        combined_text = f"{title} {description}".lower()
        job_skills = self._extract_skills(combined_text)
        overlap = set(self.resume_skills.keys()) & set(job_skills.keys())

        print(f"Job: {title} @ {company}")
        print(f"  Resume skills found in job: {sorted(overlap)}")
        print(f"  Job skills not in resume: {sorted(set(job_skills.keys()) - set(self.resume_skills.keys()))}")
        print(f"  Score: {self.score_job(title=title, company=company, description=description)}/100")


if __name__ == "__main__":
    # Quick test with a sample resume
    sample_resume = """
    Saivedant Hava
    MS Computer Science, Northeastern University

    Skills: Python, PyTorch, TensorFlow, scikit-learn, pandas, numpy,
    AWS, Docker, Kubernetes, FastAPI, PostgreSQL, Redis,
    Machine Learning, Deep Learning, NLP, Computer Vision,
    LLM, RAG, LangChain, MLflow, Airflow, Git, Linux, SQL
    """

    scorer = ResumeScorer(resume_text=sample_resume)
    print(f"Resume skills detected: {sorted(scorer.resume_skills.keys())}\n")

    # Test against some sample jobs
    test_jobs = [
        {"title": "ML Engineer Intern", "company": "Google", "description": "pytorch tensorflow distributed training"},
        {"title": "Software Engineer Intern", "company": "Walmart", "description": "java spring boot microservices"},
        {"title": "AI Research Intern", "company": "Anthropic", "description": "llm rag langchain python deep learning"},
        {"title": "Frontend Intern", "company": "Startup Inc", "description": "react typescript css html"},
        {"title": "MLOps Engineer Intern", "company": "Databricks", "description": "mlflow kubernetes docker airflow python"},
        {"title": "Data Scientist Intern", "company": "Netflix", "description": "python pandas scikit-learn a/b testing sql"},
    ]

    scored = scorer.score_jobs(test_jobs)
    print("Ranked jobs:")
    for i, (score, job) in enumerate(scored, 1):
        print(f"  {i}. [{score}/100] {job['title']} @ {job['company']}")

    print("\nDetailed breakdown for top job:")
    top = scored[0][1]
    scorer.explain_score(title=top["title"], company=top["company"], description=top.get("description", ""))
