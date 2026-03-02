#!/usr/bin/env python3
"""
Model Trainer
Train and update ML models for scoring and routing

Features:
- Historical data analysis
- Model parameter tuning
- A/B testing support
- Continuous learning
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math


@dataclass
class TrainingResult:
    """Result of model training"""
    model_name: str
    training_samples: int
    validation_samples: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    training_duration_ms: int
    completed_at: int
    model_version: str = ""
    notes: str = ""


@dataclass
class ModelMetrics:
    """Model performance metrics"""
    model_name: str
    version: str
    total_predictions: int
    correct_predictions: int
    accuracy: float
    avg_confidence: float
    win_rate: float
    roi: float  # Return on investment
    last_updated: int


class ModelType(Enum):
    """Types of ML models"""
    QUALITY_SCORER = "quality_scorer"
    COMPETITION_ESTIMATOR = "competition_estimator"
    ROUTING_DECISION = "routing_decision"
    SUCCESS_PREDICTOR = "success_predictor"


class ModelTrainer:
    """
    Train and update ML models
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Model storage
        self._models: Dict[ModelType, Dict] = {}
        self._metrics: Dict[str, ModelMetrics] = {}

        # Training data buffer
        self._training_data: List[Dict] = []
        self._max_training_data = self.config.get('max_training_data', 10000)

        # Auto-training configuration
        self.auto_train_enabled = self.config.get('auto_train_enabled', True)
        self.auto_train_interval_s = self.config.get('auto_train_interval_s', 3600)

        # Model hyperparameters
        self._hyperparameters: Dict[ModelType, Dict] = {
            ModelType.QUALITY_SCORER: {
                'ev_weight': 0.35,
                'confidence_weight': 0.25,
                'complexity_weight': 0.15,
                'cost_weight': 0.15,
                'competition_weight': 0.10,
            },
            ModelType.COMPETITION_ESTIMATOR: {
                'visibility_factor': 0.3,
                'gas_factor': 0.2,
                'time_factor': 0.2,
                'historical_factor': 0.3,
            },
            ModelType.ROUTING_DECISION: {
                'score_threshold_excellent': 0.8,
                'score_threshold_good': 0.6,
                'value_threshold_high': 10000,
                'value_threshold_medium': 1000,
            },
        }

        # Statistics
        self.training_sessions = 0
        self.total_samples_processed = 0

        print("🤖 Model Trainer initialized")

    async def start(self):
        """Start trainer"""
        print("\n🤖 Starting Model Trainer...")
        self.is_running = True

        # Initialize default models
        self._initialize_models()

        # Start auto-training if enabled
        if self.auto_train_enabled:
            asyncio.create_task(self._auto_train_loop())

        print("   ✅ Model Trainer started")

    async def stop(self):
        """Stop trainer"""
        self.is_running = False
        print("   🤖 Model Trainer stopped")

    def _initialize_models(self):
        """Initialize default models"""
        for model_type in ModelType:
            self._models[model_type] = {
                'version': '1.0.0',
                'parameters': self._hyperparameters.get(model_type, {}),
                'trained_at': int(time.time()),
                'samples_used': 0,
            }

        print(f"   🤖 Initialized {len(ModelType)} models")

    async def _auto_train_loop(self):
        """Background auto-training loop"""
        while self.is_running:
            try:
                await asyncio.sleep(self.auto_train_interval_s)

                if len(self._training_data) > 100:
                    await self.train_all_models()

            except Exception as e:
                print(f"   ⚠️  Auto-train error: {e}")
                await asyncio.sleep(60)

    def record_outcome(self, signal_id: str, signal_type: str,
                       predicted_score: float, actual_outcome: Dict):
        """Record outcome for training"""
        data_point = {
            'signal_id': signal_id,
            'signal_type': signal_type,
            'predicted_score': predicted_score,
            'actual_outcome': actual_outcome,
            'timestamp': int(time.time()),
        }

        self._training_data.append(data_point)
        self.total_samples_processed += 1

        # Trim if too large
        if len(self._training_data) > self._max_training_data:
            self._training_data = self._training_data[-self._max_training_data:]

    async def train_all_models(self) -> Dict[ModelType, TrainingResult]:
        """Train all models"""
        results = {}

        for model_type in ModelType:
            result = await self.train_model(model_type)
            results[model_type] = result

        return results

    async def train_model(self, model_type: ModelType) -> TrainingResult:
        """Train specific model"""
        start_time = time.time()

        # Get training data
        training_data = self._get_training_data_for_model(model_type)

        if len(training_data) < 50:
            return TrainingResult(
                model_name=model_type.value,
                training_samples=0,
                validation_samples=0,
                accuracy=0,
                precision=0,
                recall=0,
                f1_score=0,
                training_duration_ms=0,
                completed_at=int(time.time()),
                notes="Insufficient training data"
            )

        # Split data
        split_idx = int(len(training_data) * 0.8)
        train_data = training_data[:split_idx]
        val_data = training_data[split_idx:]

        # Train model (simplified - would use actual ML library)
        new_params = await self._optimize_parameters(model_type, train_data)

        # Evaluate on validation set
        metrics = await self._evaluate_model(model_type, new_params, val_data)

        # Update model if improved
        old_model = self._models[model_type]
        old_metrics = self._metrics.get(model_type.value)

        if old_metrics is None or metrics['accuracy'] > old_metrics.accuracy:
            # Update model
            version = f"2.{self.training_sessions}.0"
            self._models[model_type] = {
                'version': version,
                'parameters': new_params,
                'trained_at': int(time.time()),
                'samples_used': len(train_data),
            }

            # Update metrics
            self._metrics[model_type.value] = ModelMetrics(
                model_name=model_type.value,
                version=version,
                total_predictions=0,
                correct_predictions=0,
                accuracy=metrics['accuracy'],
                avg_confidence=0,
                win_rate=metrics.get('win_rate', 0),
                roi=metrics.get('roi', 0),
                last_updated=int(time.time())
            )

            self.training_sessions += 1

            print(f"   🤖 Trained {model_type.value} v{version} (accuracy: {metrics['accuracy']:.2%})")

        training_duration_ms = int((time.time() - start_time) * 1000)

        return TrainingResult(
            model_name=model_type.value,
            training_samples=len(train_data),
            validation_samples=len(val_data),
            accuracy=metrics['accuracy'],
            precision=metrics.get('precision', 0),
            recall=metrics.get('recall', 0),
            f1_score=metrics.get('f1', 0),
            training_duration_ms=training_duration_ms,
            completed_at=int(time.time()),
            model_version=self._models[model_type]['version'],
            notes="Model updated" if metrics['accuracy'] > 0.5 else "No improvement"
        )

    def _get_training_data_for_model(self, model_type: ModelType) -> List[Dict]:
        """Get relevant training data for model"""
        # Filter and prepare data for specific model
        return self._training_data[-1000:]  # Last 1000 samples

    async def _optimize_parameters(self, model_type: ModelType,
                                    training_data: List[Dict]) -> Dict:
        """Optimize model parameters (simplified grid search)"""
        current_params = self._hyperparameters.get(model_type, {})

        # In production, would use proper optimization
        # For now, return current params with minor adjustments
        return current_params.copy()

    async def _evaluate_model(self, model_type: ModelType,
                               params: Dict,
                               validation_data: List[Dict]) -> Dict:
        """Evaluate model on validation data"""
        if not validation_data:
            return {'accuracy': 0, 'precision': 0, 'recall': 0, 'f1': 0}

        # Simulate evaluation
        correct = 0
        total = len(validation_data)

        for sample in validation_data:
            # Simplified prediction
            predicted = sample.get('predicted_score', 0.5) > 0.5
            actual = sample.get('actual_outcome', {}).get('success', False)

            if predicted == actual:
                correct += 1

        accuracy = correct / total if total > 0 else 0

        return {
            'accuracy': accuracy,
            'precision': accuracy,
            'recall': accuracy,
            'f1': accuracy,
            'win_rate': accuracy,
            'roi': 0.1,  # Would calculate from actual outcomes
        }

    def get_model(self, model_type: ModelType) -> Optional[Dict]:
        """Get current model"""
        return self._models.get(model_type)

    def get_metrics(self, model_name: str) -> Optional[ModelMetrics]:
        """Get model metrics"""
        return self._metrics.get(model_name)

    def get_hyperparameters(self, model_type: ModelType) -> Dict:
        """Get current hyperparameters"""
        return self._hyperparameters.get(model_type, {}).copy()

    def update_hyperparameters(self, model_type: ModelType, params: Dict):
        """Update hyperparameters"""
        if model_type in self._hyperparameters:
            self._hyperparameters[model_type].update(params)
            print(f"   🤖 Updated {model_type.value} hyperparameters")

    def export_model(self, model_type: ModelType) -> Dict:
        """Export model for deployment"""
        model = self._models.get(model_type)
        if not model:
            return {}

        return {
            'type': model_type.value,
            'version': model['version'],
            'parameters': model['parameters'],
            'trained_at': model['trained_at'],
            'samples_used': model['samples_used'],
        }

    def import_model(self, model_data: Dict):
        """Import model from external source"""
        model_type = ModelType(model_data.get('type', ''))
        if model_type not in ModelType:
            print(f"   ⚠️  Unknown model type: {model_data.get('type')}")
            return

        self._models[model_type] = {
            'version': model_data.get('version', '1.0.0'),
            'parameters': model_data.get('parameters', {}),
            'trained_at': model_data.get('trained_at', int(time.time())),
            'samples_used': model_data.get('samples_used', 0),
        }

        print(f"   🤖 Imported {model_type.value} v{model_data.get('version')}")

    def get_stats(self) -> Dict:
        """Get trainer statistics"""
        return {
            'training_sessions': self.training_sessions,
            'total_samples_processed': self.total_samples_processed,
            'training_data_size': len(self._training_data),
            'models': {
                m.value: {
                    'version': self._models[m]['version'],
                    'trained_at': self._models[m]['trained_at'],
                }
                for m in ModelType
            },
            'metrics': {
                name: {
                    'accuracy': m.accuracy,
                    'win_rate': m.win_rate,
                    'roi': m.roi,
                }
                for name, m in self._metrics.items()
            },
        }


# Pre-configured hyperparameter sets
HYPERPARAMETER_PRESETS = {
    'aggressive': {
        ModelType.QUALITY_SCORER: {
            'ev_weight': 0.45,
            'confidence_weight': 0.20,
            'complexity_weight': 0.10,
            'cost_weight': 0.10,
            'competition_weight': 0.15,
        },
    },
    'balanced': {
        ModelType.QUALITY_SCORER: {
            'ev_weight': 0.35,
            'confidence_weight': 0.25,
            'complexity_weight': 0.15,
            'cost_weight': 0.15,
            'competition_weight': 0.10,
        },
    },
    'conservative': {
        ModelType.QUALITY_SCORER: {
            'ev_weight': 0.25,
            'confidence_weight': 0.35,
            'complexity_weight': 0.20,
            'cost_weight': 0.15,
            'competition_weight': 0.05,
        },
    },
}
