from __future__ import annotations

from types import SimpleNamespace

from vidsearch.eval import smoke_phase0


def test_vector_norm_dense():
    assert smoke_phase0._vector_norm([3.0, 4.0]) == 5.0


def test_vector_norm_sparse_like():
    sparse = SimpleNamespace(values=[6.0, 8.0])
    assert smoke_phase0._vector_norm(sparse) == 10.0
