"""FYP1 KPI cards: definitions (cards.py), execution (engine.py), CLI (__main__.py)."""
from .cards import CARDS
from .engine import run_card, run_cards

__all__ = ["CARDS", "run_card", "run_cards"]
