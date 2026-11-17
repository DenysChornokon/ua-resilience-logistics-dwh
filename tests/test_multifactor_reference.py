from datetime import datetime,timedelta,timezone

import pytest
from scripts.verify_multifactor_states import reference


BASE=datetime(2026,1,1,tzinfo=timezone.utc)
def t(n): return BASE+timedelta(seconds=n)


def test_nested_overlapping_adjacent_and_simultaneous():
    result=reference(t(0),t(30),[(t(2),t(14)),(t(4),t(6)),(t(14),t(18))],
                     [(t(10),t(22)),(t(12),t(14)),(t(30),t(32))])
    assert result==[(t(0),t(2),False,False),(t(2),t(10),True,False),
                    (t(10),t(18),True,True),(t(18),t(22),False,True),
                    (t(22),t(30),False,False)]


def test_clipping_and_empty_coverage():
    assert reference(t(0),t(10),[(t(-3),t(3)),(t(10),t(12))],[]) == [
        (t(0),t(3),True,False),(t(3),t(10),False,False)]
    assert reference(t(0),t(10),[],[])==[(t(0),t(10),False,False)]


def test_invalid_interval_rejected():
    with pytest.raises(ValueError): reference(t(0),t(10),[(t(3),t(3))],[])
