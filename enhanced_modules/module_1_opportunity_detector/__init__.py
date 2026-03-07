"""
Module 1 — Enhanced Opportunity Detector
Predictive ML scoring, cross-protocol detection, real-time streaming.
"""
from enhanced_modules.module_1_opportunity_detector.ml_probability_scorer import (
    MLProbabilityScorer,
)
from enhanced_modules.module_1_opportunity_detector.cross_protocol_detector import (
    CrossProtocolDetector,
)
from enhanced_modules.module_1_opportunity_detector.realtime_stream import (
    RealtimeStreamIngester,
)

__all__ = ["MLProbabilityScorer", "CrossProtocolDetector", "RealtimeStreamIngester"]
