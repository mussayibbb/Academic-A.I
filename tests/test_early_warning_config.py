from backend.app.early_warning_config import (
    normalise_positive_shap,
    priority_for_probability,
)


def test_priority_thresholds() -> None:
    assert priority_for_probability(0.75) == 'High'
    assert priority_for_probability(0.74) == 'Medium'
    assert priority_for_probability(0.40) == 'Medium'
    assert priority_for_probability(0.399) == 'Low'


def test_positive_shap_shares_sum_to_one() -> None:
    shares = normalise_positive_shap([
        ('attendance', 0.2),
        ('assessment', -0.1),
        ('missing_submissions', 0.3),
    ])
    assert abs(sum(shares.values()) - 1.0) < 1e-9
    assert shares == {'attendance': 0.4, 'missing_submissions': 0.6}
