"""The 50dma direction rules are the part the strategy doc warns most loudly
about blurring, so every boundary gets pinned."""
import pytest

from pcs.screener import ABOVE, BROKEN, NEAR_TIGHT, NOT_BEATEN, PRIMARY, STRETCHED, classify


@pytest.mark.parametrize("from50,off_high,expected", [
    (-0.055, 0.20, PRIMARY),        # the intended setup
    (-0.030, 0.20, PRIMARY),        # inclusive upper edge of the band
    (-0.080, 0.20, PRIMARY),        # inclusive lower edge
    (-0.029, 0.20, NEAR_TIGHT),     # just inside 3% -> its own bucket
    (0.000, 0.20, NEAR_TIGHT),      # exactly at the average
    (-0.081, 0.20, STRETCHED),      # just past the band
    (-0.120, 0.20, STRETCHED),      # inclusive edge of stretched
    (-0.121, 0.20, BROKEN),         # broken down, excluded
    (-0.500, 0.20, BROKEN),
    (0.001, 0.20, ABOVE),           # above -> different thesis
    (0.250, 0.20, ABOVE),
])
def test_dma_buckets_never_blend(from50, off_high, expected):
    assert classify(from50, off_high) == expected


@pytest.mark.parametrize("off_high", [0.0, 0.149])
def test_beaten_down_floor_is_15pct(off_high):
    assert classify(-0.055, off_high) == NOT_BEATEN


def test_no_ceiling_on_how_beaten_down(): # NKE at 50% off still qualifies
    assert classify(-0.066, 0.493) == PRIMARY


def test_off_high_is_checked_before_the_average():
    # a name that is near its 50dma but not beaten down is not a candidate
    assert classify(-0.05, 0.10) == NOT_BEATEN
