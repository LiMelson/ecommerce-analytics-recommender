"""Frozen Popular-led ranking rules used only by the final offline study."""

import json
from pathlib import Path

from data_algo.data_algo.schema import CORE_BEHAVIORS

from ..benchmark import HistoryBands
from ..itemcf import ITEMCF, ItemCFConfig, ItemCFModel
from ..popular import PopularModel, local_dataset_id
from ..sample import sha256_file

CONFIG_PATH = Path(__file__).with_name("protocol.json")
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
POPULAR = "popular"
CATEGORY = "popular_category_rank_move"


class HybridStudy:
    def __init__(self, events, start, cutoff, digest):
        if set(CONFIG["positive"]) != set(CORE_BEHAVIORS):
            raise ValueError("Unexpected positive behavior contract")
        self.popular = PopularModel.fit(
            events,
            cutoff,
            fit_start=start,
            dataset_id=local_dataset_id(digest),
            model_version="popular_rec07_fit",
            sample_sha256=digest,
            code_sha256=sha256_file(Path(__file__)),
            protocol_version="purchase_global_utc_v1",
        )
        self.itemcf, _ = ItemCFModel.fit(
            events, self.popular, ItemCFConfig(**CONFIG["itemcf"]), model_version="itemcf_rec07_fit"
        )
        self.bands = HistoryBands.fit(events)
        positive = events.loc[events.behavior_type.isin(CONFIG["positive"])]
        self.eligible = set(
            positive.groupby("user_id").item_id.nunique().loc[lambda x: x >= CONFIG["min_distinct"]].index
        )
        known = positive.loc[positive.category_id.notna() & ~positive.category_id.isin(("", "unknown"))]
        counts = known.groupby(["user_id", "category_id"], observed=True).item_id.nunique()
        normalized = counts / counts.groupby(level=0).transform("max")
        self.affinities = normalized.to_dict()

    def rank(self, user, arm):
        if arm not in CONFIG["arms"]:
            raise ValueError("Unknown study arm")
        active = user in self.eligible and arm != "H0"
        depth = CONFIG["popular_pool"] if active and arm == "H3" else CONFIG["k"]
        candidates = self.popular.recommend(user, depth).items
        base = [(item.item_id, POPULAR) for item in candidates[: CONFIG["k"]]]
        if not active:
            return base
        if arm == "H3":

            def key(entry):
                position, item = entry
                affinity = self.affinities.get((user, item.category_id), 0)
                return position - CONFIG["category_bonus"] * affinity, position, item.item_id

            ordered = sorted(enumerate(candidates, 1), key=key)
            return [
                (item.item_id, CATEGORY if old != new else POPULAR)
                for new, (old, item) in enumerate(ordered[: CONFIG["k"]], 1)
            ]
        slot = CONFIG["replacement_rank"][arm] - 1
        if slot < len(base):
            blocked = self.popular.history.blocked(user)
            used = {item for item, _ in base}
            for pick in self.itemcf.recommend(user, CONFIG["itemcf_depth"]).items:
                if pick.source == ITEMCF and pick.item_id not in used and pick.item_id not in blocked:
                    base[slot] = (pick.item_id, ITEMCF)
                    break
        return base
