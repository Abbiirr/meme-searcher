import math
import logging

logger = logging.getLogger(__name__)


def dcg_at_k(scores: list[float], k: int) -> float:
    dcg = 0.0
    for i, score in enumerate(scores[:k]):
        dcg += score / math.log2(i + 2)
    return dcg


def ndcg_at_k(graded_relevances: list[float], k: int = 10) -> float:
    dcg = dcg_at_k(graded_relevances, k)
    ideal = sorted(graded_relevances, reverse=True)
    idcg = dcg_at_k(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg / idcg


def recall_at_k(graded_relevances: list[float], k: int = 10, min_grade: int = 1) -> float:
    relevant_total = sum(1 for g in graded_relevances if g >= min_grade)
    if relevant_total == 0:
        return 0.0
    relevant_in_k = sum(1 for g in graded_relevances[:k] if g >= min_grade)
    return relevant_in_k / relevant_total


def mrr(graded_relevances_list: list[list[float]], min_grade: int = 1) -> float:
    rr_sum = 0.0
    for grades in graded_relevances_list:
        for i, g in enumerate(grades):
            if g >= min_grade:
                rr_sum += 1.0 / (i + 1)
                break
    return rr_sum / len(graded_relevances_list) if graded_relevances_list else 0.0


def top1_hit_rate(graded_relevances_list: list[list[float]], min_grade: int = 1) -> float:
    hits = 0
    for grades in graded_relevances_list:
        if grades and grades[0] >= min_grade:
            hits += 1
    return hits / len(graded_relevances_list) if graded_relevances_list else 0.0


def reranker_uplift_ndcg10(ndcg_before: float, ndcg_after: float) -> float:
    return ndcg_after - ndcg_before


def compute_all_metrics(
    query_results: list[dict],
) -> dict[str, float]:
    all_ndcg10 = []
    all_recall10 = []
    all_recall50 = []
    all_grades_for_mrr = []

    for q in query_results:
        grades = q.get("grades", [])
        all_ndcg10.append(ndcg_at_k(grades, 10))
        all_recall10.append(recall_at_k(grades, 10))
        all_recall50.append(recall_at_k(grades, 50))
        all_grades_for_mrr.append(grades)

    metrics = {
        "nDCG@10": sum(all_ndcg10) / len(all_ndcg10) if all_ndcg10 else 0.0,
        "Recall@10": sum(all_recall10) / len(all_recall10) if all_recall10 else 0.0,
        "Recall@50": sum(all_recall50) / len(all_recall50) if all_recall50 else 0.0,
        "MRR": mrr(all_grades_for_mrr),
        "top_1_hit_rate": top1_hit_rate(all_grades_for_mrr),
    }

    return metrics
