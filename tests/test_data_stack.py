from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from tests.test_hello_world import _require_wasm_environment



def test_numpy_pandas_matplotlib_stack() -> None:
    _require_wasm_environment()

    client = TestClient(create_app())
    code = """
import io
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

data = np.arange(6, dtype=np.float64).reshape(3, 2)
df = pd.DataFrame(data, columns=['x', 'y'])

summary = {
    'shape': list(df.shape),
    'x_mean': float(df['x'].mean()),
    'y_total': float(df['y'].sum()),
}

fig, ax = plt.subplots()
ax.plot(df['x'], df['y'])
buf = io.BytesIO()
fig.savefig(buf, format='png')
plt.close(fig)

print(json.dumps({'summary': summary, 'png_bytes': len(buf.getvalue())}))
"""

    response = client.post(
        "/v1/execute",
        json={
            "code": code,
            "stdin": None,
            "timeout_ms": 2000,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stderr"] == ""
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False

    stdout = payload["stdout"].strip()
    result = json.loads(stdout)

    assert result["summary"]["shape"] == [3, 2]
    assert result["summary"]["x_mean"] == pytest.approx(2.0, rel=1e-9)
    assert result["summary"]["y_total"] == pytest.approx(9.0, rel=1e-9)
    assert result["png_bytes"] > 0
