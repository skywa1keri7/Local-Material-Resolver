import importlib.util
from pathlib import Path

import numpy as np


root = Path(__file__).resolve().parent.parent
module_path = root / "blender" / "pbr_resolver.py"
spec = importlib.util.spec_from_file_location("lmr_worker", module_path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

size = 256
x_gradient = np.linspace(0.40, 0.68, size, dtype=np.float32)
luminance = np.repeat(x_gradient[None, :], size, axis=0)
coverage = np.zeros((size, size), dtype=bool)
coverage[8:-8, 8:-8] = True

# Local dark seam should become a groove; bright paint and broad lighting should not.
luminance[24:-24, 78:82] -= 0.22
luminance[24:-24, 168:172] += 0.22
height, safe = worker.extract_dark_grooves(luminance, coverage, feature_size_at_2k=32)

dark_response = float(np.mean(np.abs(height[30:-30, 76:84])))
bright_response = float(np.mean(np.abs(height[30:-30, 166:174])))
gradient_response = float(np.mean(np.abs(height[30:-30, 110:145])))

assert float(np.max(height)) <= 1e-7, "Only negative groove heights are allowed"
assert dark_response > 0.10, dark_response
assert bright_response < dark_response * 0.25, (dark_response, bright_response)
assert gradient_response < dark_response * 0.15, (dark_response, gradient_response)
assert np.all(height[~safe] == 0.0), "UV boundary guard must remain flat"

good_coverage = np.ones((64, 64), dtype=bool)
sparse_coverage = np.zeros((64, 64), dtype=bool)
sparse_coverage[::4, :] = True
dense_edges = sparse_coverage.copy()
assert worker.structure_reliability(good_coverage, np.zeros_like(good_coverage)) == 1.0
assert worker.structure_reliability(sparse_coverage, dense_edges) <= 0.16
print(
    {
        "dark_response": round(dark_response, 5),
        "bright_response": round(bright_response, 5),
        "broad_gradient_response": round(gradient_response, 5),
    }
)
