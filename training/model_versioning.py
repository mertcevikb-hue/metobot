"""
Model Versioning & Deployment System

Every trained model gets a version with:
- Unique ID and timestamp
- Training/validation metrics
- Status (CANDIDATE → VALIDATED → ACTIVE → RETIRED)
- Rollback capability
- A/B testing support

Lifecycle:
  CANDIDATE → (walk-forward test) → VALIDATED → (approval) → ACTIVE → (degradation) → RETIRED
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from loguru import logger
import json


class ModelStatus(Enum):
    """Model lifecycle status."""
    CANDIDATE = "CANDIDATE"  # Newly trained, awaiting validation
    VALIDATING = "VALIDATING"  # Running walk-forward tests
    VALIDATED = "VALIDATED"  # Passed validation
    APPROVED = "APPROVED"  # Ready for deployment
    ACTIVE = "ACTIVE"  # Currently live
    DEGRADING = "DEGRADING"  # Performance declining
    RETIRED = "RETIRED"  # Replaced or deprecated


@dataclass
class ModelMetrics:
    """Training and validation metrics for a model."""
    # Training metrics
    train_sharpe: float
    train_profit: float
    train_win_rate: float

    # Validation metrics (out-of-sample)
    test_sharpe: float
    test_profit: float
    test_win_rate: float

    # Risk metrics
    max_drawdown: float
    sortino_ratio: float

    # Degradation from training to testing
    degradation: float

    def is_valid(self) -> bool:
        """Check if model meets minimum quality gates."""
        return (
            self.train_sharpe > 0.5  # Min train Sharpe
            and self.test_sharpe > 0.3  # Min test Sharpe (allows some degradation)
            and self.test_win_rate > 0.45  # Min win rate
            and self.degradation < 0.3  # Max degradation (30%)
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "train_sharpe": round(self.train_sharpe, 2),
            "train_profit": round(self.train_profit, 2),
            "train_win_rate": round(self.train_win_rate * 100, 2),
            "test_sharpe": round(self.test_sharpe, 2),
            "test_profit": round(self.test_profit, 2),
            "test_win_rate": round(self.test_win_rate * 100, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "degradation": round(self.degradation * 100, 2)
        }


@dataclass
class ModelVersion:
    """A specific version of a trained model."""
    model_id: str
    version_number: int
    status: ModelStatus
    created_at: datetime
    metrics: ModelMetrics
    symbols: list  # ["BTC/USDT", "ETH/USDT", ...]
    timeframes: list  # ["1m", "5m", "15m"]
    parameters: Dict[str, Any]  # Strategy parameters
    notes: str = ""
    deployed_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "model_id": self.model_id,
            "version_number": self.version_number,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "deployed_at": self.deployed_at.isoformat() if self.deployed_at else None,
            "retired_at": self.retired_at.isoformat() if self.retired_at else None,
            "metrics": self.metrics.to_dict(),
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "parameters": self.parameters,
            "notes": self.notes
        }

    def meets_approval_gates(self) -> Tuple[bool, str]:
        """Check if model meets deployment approval criteria."""
        if not self.metrics.is_valid():
            return False, "Metrics do not meet minimum quality gates"

        if self.status != ModelStatus.VALIDATED:
            return False, f"Status is {self.status.value}, must be VALIDATED"

        return True, "Model meets all approval criteria"

    def get_summary(self) -> str:
        """Get human-readable summary."""
        return (
            f"Model {self.model_id} v{self.version_number}\n"
            f"Status: {self.status.value}\n"
            f"Created: {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Test Sharpe: {self.metrics.test_sharpe:.2f}\n"
            f"Test Profit: ${self.metrics.test_profit:.2f}\n"
            f"Win Rate: {self.metrics.test_win_rate*100:.1f}%\n"
            f"Degradation: {self.metrics.degradation*100:.1f}%\n"
        )


class ModelRegistry:
    """
    Registry and management of all model versions.

    Handles:
    - Model versioning
    - Status transitions
    - Deployment tracking
    - Rollback capability
    """

    def __init__(self):
        self.models: Dict[str, List[ModelVersion]] = {}  # model_id -> [versions]
        self.active_models: Dict[str, ModelVersion] = {}  # symbol -> active model

    def register_model(self, model: ModelVersion):
        """Register a new model version."""
        if model.model_id not in self.models:
            self.models[model.model_id] = []

        self.models[model.model_id].append(model)
        logger.info(f"✓ Registered {model.model_id} v{model.version_number}")

    def get_latest_version(self, model_id: str) -> Optional[ModelVersion]:
        """Get latest version of a model."""
        if model_id not in self.models:
            return None
        return self.models[model_id][-1]

    def approve_model(self, model_id: str, version: int) -> bool:
        """Approve a model for deployment."""
        model = self._get_version(model_id, version)
        if not model:
            return False

        approved, reason = model.meets_approval_gates()
        if not approved:
            logger.error(f"Cannot approve: {reason}")
            return False

        model.status = ModelStatus.APPROVED
        logger.info(f"✓ Approved {model_id} v{version} for deployment")
        return True

    def deploy_model(self, model_id: str, version: int) -> bool:
        """Deploy a model to production."""
        model = self._get_version(model_id, version)
        if not model:
            return False

        if model.status != ModelStatus.APPROVED:
            logger.error(f"Cannot deploy: Status is {model.status.value}")
            return False

        # Deactivate previous versions for same symbols
        for symbol in model.symbols:
            if symbol in self.active_models:
                old_model = self.active_models[symbol]
                old_model.status = ModelStatus.RETIRED
                old_model.retired_at = datetime.now(timezone.utc)
                logger.info(f"Retired {old_model.model_id} v{old_model.version_number}")

        # Activate new model
        model.status = ModelStatus.ACTIVE
        model.deployed_at = datetime.now(timezone.utc)

        for symbol in model.symbols:
            self.active_models[symbol] = model

        logger.success(f"✓ Deployed {model_id} v{version} to production")
        return True

    def rollback_model(self, symbol: str) -> bool:
        """Rollback to previous model version for a symbol."""
        if symbol not in self.active_models:
            logger.error(f"No active model for {symbol}")
            return False

        current = self.active_models[symbol]

        # Find previous version
        model_id = current.model_id
        versions = self.models.get(model_id, [])
        prev_version = None

        for v in reversed(versions[:-1]):  # All except current
            if v.status == ModelStatus.VALIDATED:
                prev_version = v
                break

        if not prev_version:
            logger.error("No previous validated version available for rollback")
            return False

        # Rollback
        current.status = ModelStatus.RETIRED
        self.deploy_model(prev_version.model_id, prev_version.version_number)
        logger.warning(f"⚠️  Rolled back to {prev_version.model_id} v{prev_version.version_number}")
        return True

    def get_active_model(self, symbol: str) -> Optional[ModelVersion]:
        """Get currently active model for a symbol."""
        return self.active_models.get(symbol)

    def list_all_models(self) -> Dict[str, list]:
        """List all models and versions."""
        return {
            model_id: [
                {
                    "version": v.version_number,
                    "status": v.status.value,
                    "created": v.created_at.isoformat(),
                    "sharpe": v.metrics.test_sharpe
                }
                for v in versions
            ]
            for model_id, versions in self.models.items()
        }

    def _get_version(self, model_id: str, version: int) -> Optional[ModelVersion]:
        """Get specific model version."""
        if model_id not in self.models:
            return None

        for v in self.models[model_id]:
            if v.version_number == version:
                return v

        return None


# Global registry instance
_registry = None


def get_registry() -> ModelRegistry:
    """Get or create global model registry."""
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
