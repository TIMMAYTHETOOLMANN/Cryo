"""
ML Aggregator & Ranker
The Brain of the Operation

This module aggregates all signals and applies ML-based scoring:
- Quality scoring based on EV, confidence, complexity
- Competition estimation
- Dynamic routing to execution modules
- Model training and continuous improvement
"""

from .quality_scorer import QualityScorer, ScoredSignal
from .competition_estimator import CompetitionEstimator
from .complexity_analyzer import ComplexityAnalyzer
from .dynamic_router import DynamicRouter, RoutingDecision
from .model_trainer import ModelTrainer

__all__ = [
    # Main classes
    'QualityScorer',
    'ScoredSignal',
    'CompetitionEstimator',
    'ComplexityAnalyzer',
    'DynamicRouter',
    'RoutingDecision',
    'ModelTrainer',
]
