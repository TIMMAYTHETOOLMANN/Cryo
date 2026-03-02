"""
Contract Discovery Crawler
Unearthing unlisted alpha through funding intelligence, social mindshare, and wallet tracking

This module discovers protocols before they're widely known by tracking:
- Funding rounds and investor activity
- Social sentiment and KOL mentions
- Smart money wallet movements
- New contract deployments
"""

from .funding_intelligence import FundingIntelligence, CoincarpAPI, FundraisingAPI
from .social_mindshare import SocialMindshare, KaitoAI, DexuAI
from .kol_wallet_tracker import KOLWalletTracker, ZeroXPPL, DeBankAPI
from .deployer_monitor import DeployerMonitor
from .clone_detector import CloneDetector
from .protocol_classifier import ProtocolClassifier, ProtocolType

__all__ = [
    # Main classes
    'FundingIntelligence',
    'SocialMindshare',
    'KOLWalletTracker',
    'DeployerMonitor',
    'CloneDetector',
    'ProtocolClassifier',
    
    # API integrations
    'CoincarpAPI',
    'FundraisingAPI',
    'KaitoAI',
    'DexuAI',
    'ZeroXPPL',
    'DeBankAPI',
    
    # Enums
    'ProtocolType',
]
