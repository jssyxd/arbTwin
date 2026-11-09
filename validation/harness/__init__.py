"""Validation harness for arbTwin — deterministic replay, fill modelling, metrics."""

from .manifest import DataManifest, manifest_from_dict
from .fill_model import (
    FillModel,
    conservative_fill,
    get_fill_model,
    queue_reactive_fill,
    set_fill_model,
)
from .loaders import (
    load_fills,
    load_funding,
    load_l2,
    load_manifest_data,
    load_orders,
    load_quotes,
    load_trades,
)
from .metrics import (
    MetricResult,
    compute_abort_cost,
    compute_capture_bps,
    compute_escalation_rate,
    compute_markout,
    compute_p_fail,
    compute_p_fill,
    compute_p_fill_by_level,
    compute_time_to_pair,
    compute_all_metrics,
)
from .replay import (
    ReplayConfig,
    ReplayOutcome,
    ReplayResult,
    SlicedReplayResult,
    run_replay,
    run_sliced_replay,
)

__all__ = [
    "DataManifest",
    "manifest_from_dict",
    "FillModel",
    "conservative_fill",
    "get_fill_model",
    "queue_reactive_fill",
    "set_fill_model",
    "load_quotes",
    "load_l2",
    "load_trades",
    "load_funding",
    "load_orders",
    "load_fills",
    "load_manifest_data",
    "ReplayConfig",
    "ReplayOutcome",
    "ReplayResult",
    "SlicedReplayResult",
    "run_replay",
    "run_sliced_replay",
    "MetricResult",
    "compute_p_fill",
    "compute_p_fill_by_level",
    "compute_time_to_pair",
    "compute_p_fail",
    "compute_markout",
    "compute_capture_bps",
    "compute_abort_cost",
    "compute_escalation_rate",
    "compute_all_metrics",
]