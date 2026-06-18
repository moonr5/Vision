"""
Scale Engine — Edge-Cloud Intelligence Bridge.
Edge model management, sync & conflict engine, federated learning coordinator.
"""

from .edge_model_mgr import EdgeModelManager, EdgeModel
from .sync_engine import SyncEngine, SyncConflictResolver
from .federated_learning import FederatedLearningCoordinator

__all__ = [
    "EdgeModelManager", "EdgeModel",
    "SyncEngine", "SyncConflictResolver",
    "FederatedLearningCoordinator",
]
