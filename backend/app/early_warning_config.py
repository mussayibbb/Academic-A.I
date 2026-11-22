from typing import Iterable

HIGH_PRIORITY_THRESHOLD = 0.75
MEDIUM_PRIORITY_THRESHOLD = 0.40
MINIMUM_USABLE_SIGNALS = 3
WARNING_CACHE_TTL_SECONDS = 900


def priority_for_probability(probability: float) -> str:
    if probability >= HIGH_PRIORITY_THRESHOLD:
        return 'High'
    if probability >= MEDIUM_PRIORITY_THRESHOLD:
        return 'Medium'
    return 'Low'


def normalise_positive_shap(contributions: Iterable[tuple[str, float]]) -> dict[str, float]:
    positive = {name: max(0.0, float(value)) for name, value in contributions if float(value) > 0}
    total = sum(positive.values())
    if total <= 0:
        return {}
    return {name: value / total for name, value in positive.items()}
