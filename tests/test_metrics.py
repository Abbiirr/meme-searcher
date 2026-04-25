from vidsearch.eval.metrics import (
    ndcg_at_k, recall_at_k, mrr, top1_hit_rate, compute_all_metrics,
)


def test_ndcg_perfect():
    grades = [3, 2, 1, 0]
    assert ndcg_at_k(grades, 4) == 1.0


def test_ndcg_worst():
    grades = [0, 0, 0, 0]
    assert ndcg_at_k(grades, 4) == 0.0


def test_recall_at_k():
    grades = [3, 0, 2, 0, 1]
    assert recall_at_k(grades, k=5) == 1.0
    assert recall_at_k(grades, k=3) == 2 / 3


def test_mrr():
    lists = [[0, 3, 1], [2, 0, 0], [0, 0, 1]]
    result = mrr(lists)
    expected = (1/2 + 1/1 + 1/3) / 3
    assert abs(result - expected) < 1e-6


def test_top1_hit_rate():
    lists = [[3, 0], [0, 2], [1, 0]]
    assert top1_hit_rate(lists) == 2/3


def test_compute_all_metrics_structure():
    query_results = [
        {"grades": [3, 2, 0]},
        {"grades": [0, 1, 2]},
    ]
    metrics = compute_all_metrics(query_results)
    assert "nDCG@10" in metrics
    assert "Recall@10" in metrics
    assert "MRR" in metrics
    assert "top_1_hit_rate" in metrics
