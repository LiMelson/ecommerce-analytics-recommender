from types import SimpleNamespace

import pandas as pd
import pytest
from test_bpr import CUTOFF, END, START, frame

from recommendation.itemcf import FILL, ITEMCF
from recommendation.research07.experiment import Window, selection_user
from recommendation.research07.ranker import CATEGORY, POPULAR, HybridStudy


@pytest.fixture
def model():
    rows = [(f"buyer-{i:02}", f"{i:02}", "buy") for i in range(1, 17)]
    rows += [("active", item, "pv") for item in ("13", "14", "15")]
    rows += [("low", "13", "pv"), ("low", "13", "cart"), ("active", "01", "buy")]
    events = frame(*rows)
    events["category_id"] = events.item_id.map(lambda i: "liked" if i in ("05", "13", "14", "15") else "other")
    return HybridStudy(events, START, CUTOFF, "a" * 64)


def test_control_and_category_match_hand_calculation(model):
    original = model.popular.recommend("active", 50).items
    assert [i.item_id for i in original[:10]] == [f"{i:02}" for i in range(2, 12)]
    assert model.rank("active", "H0") == [(i.item_id, POPULAR) for i in original[:10]]
    ranked = model.rank("active", "H3")
    # Affinity liked=1, other=1/3 from the purchase. Original rank 4 ties rank 2 after bonus.
    assert [item for item, _ in ranked] == ["02", "03", "05", "04", "06", "07", "08", "09", "10", "11"]
    assert ranked[2][1] == CATEGORY
    assert model.rank("active", "H3") == ranked
    assert model.popular.recommend("active", 50).items == original


@pytest.mark.parametrize("user", ["new", "low"])
def test_threshold_counts_distinct_products(model, user):
    for arm in ("H1", "H2", "H3"):
        assert model.rank(user, arm) == model.rank(user, "H0")


@pytest.mark.parametrize(("arm", "slot"), [("H1", 9), ("H2", 4)])
def test_only_true_unseen_cosine_replaces_requested_slot(model, monkeypatch, arm, slot):
    cf = model.itemcf
    underlying = cf.recommend("active", 11)
    picks = [
        SimpleNamespace(item_id=i, source=s)
        for i, s in [("12", FILL), ("01", ITEMCF), ("02", ITEMCF), ("14", ITEMCF), ("15", ITEMCF)]
    ]
    with monkeypatch.context() as patch:
        patch.setattr(type(cf), "recommend", lambda self, user, k: SimpleNamespace(items=picks))
        base = model.rank("active", "H0")
        result = model.rank("active", arm)
        assert result == [("14", ITEMCF) if i == slot else pick for i, pick in enumerate(base)]
        assert len({i for i, _ in result}) == len(result)
        picks[:] = [SimpleNamespace(item_id="12", source=FILL)]
        assert model.rank("active", arm) == base
    assert cf.recommend("active", 11) == underlying


def test_no_reliable_categories_and_short_catalog():
    events = frame(("u", "A", "pv"), ("u", "B", "pv"), ("u", "C", "cart"))
    model = HybridStudy(events, START, CUTOFF, "a" * 64)
    assert all(model.rank("u", arm) == model.rank("u", "H0") for arm in ("H1", "H2", "H3"))


def test_window_excludes_boundary_and_future_purchases():
    history = frame(("u", "A", "pv"), ("u", "B", "cart"), ("u", "C", "buy"))
    future = frame(("u", "X", "buy"), ("u", "A", "buy"), time=CUTOFF)
    window = Window(pd.concat([history, future]), START, CUTOFF, END, "a" * 64)
    assert window.labels("u") == {"A"}
    for arm in ("H0", "H1", "H2", "H3"):
        assert not (set(window.prediction("u", arm).ids) & {"X", "C"})
    with pytest.raises(ValueError):
        HybridStudy(pd.concat([history, future]), START, CUTOFF, "a" * 64)
    assert selection_user("abc") is False  # SHA-256 starts with ba
