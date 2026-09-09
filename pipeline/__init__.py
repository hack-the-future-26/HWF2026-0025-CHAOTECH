"""
pipeline — AI/NLP Intake Pipeline (P1)

Public API for P2 to import:
    from pipeline import process_report
"""

from .main import process_report  # noqa: F401

__all__ = ["process_report"]
