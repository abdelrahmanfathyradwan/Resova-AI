"""OpenAI-based opportunity analysis (Phase 4).

Scores a project 0–100 against the client profile, identifies matching/missing skills,
assesses risk, and provides a clear rationale. Never fabricates data.
"""

from resova.ai.analyzer import AIAnalyzer, AnalysisResult

__all__ = ["AIAnalyzer", "AnalysisResult"]
