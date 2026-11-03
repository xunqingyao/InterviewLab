from types import SimpleNamespace

from domain.thread import parent_chain


def test_parent_chain_root_first():
    root = SimpleNamespace(id="t1", parent_turn_id=None, followup_depth=0)
    mid = SimpleNamespace(id="t2", parent_turn_id="t1", followup_depth=1)
    leaf = SimpleNamespace(id="t3", parent_turn_id="t2", followup_depth=2)
    other = SimpleNamespace(id="t9", parent_turn_id=None, followup_depth=0)
    chain = parent_chain([other, root, mid, leaf], leaf)
    assert [item.id for item in chain] == ["t1", "t2", "t3"]
