"""
Scale Engine — Unified Backend Intelligence Platform for SGU Logistics.
Contains 28 engines across 4 groups: data ingestion, smart systems,
AI/ML, and edge-cloud bridge. All engines are API-accessible with
zero frontend changes required.
"""

from . import db
from . import data_ingestion
from . import smart_systems
from . import ai_ml
from . import edge_cloud

__version__ = "1.0.0"
