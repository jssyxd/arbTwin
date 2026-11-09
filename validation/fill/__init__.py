"""validation.fill -- fill-probability / markout / p_fail measurement stack (T2).

Importable as a package so the modules can use relative imports::

    from validation.fill import fill_model, metrics, harness

Nothing here imports credentials and nothing here trades; the recorder is public
and read-only, the models run offline on a recorded or synthetic capture.
"""

from . import fill_model, metrics            # noqa: F401
from .fill_model import Capture, load_capture, synthetic_capture      # noqa: F401
from .harness import simulate_two_leg        # noqa: F401

__all__ = ["fill_model", "metrics", "Capture", "load_capture", "synthetic_capture",
           "simulate_two_leg"]
