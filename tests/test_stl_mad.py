import numpy as np
import pytest
import pandas as pd
from stl_mad import calc_mad, robust_z_score, mad_detect, StlMadDetector

# ================== 全局常量配置 ==================
np.random.seed(42)
TEST_PERIOD = 24
FALSE_POSITIVE_LIMIT = 10

# ================== 公共测试夹具 ==================
@pytest.fixture
def normal_odd_series():
    return np.array([10, 12, 9, 11, 8, 10, 13])

@pytest.fixture
def normal_even_series():
    return np.array([10, 12, 9, 11, 8, 10])

@pytest.fixture
def flat_series():
    return np.full(30, 7.0)

# ================== calc_mad 基础函数测试 ==================
def test_calc_mad_odd_length(normal_odd_series):
    res = calc_mad(normal_odd_series)
    med = np.median(normal_odd_series)
    expect = np.median(np.abs(normal_odd_series - med))
    assert np.isclose(res, expect)

def test_calc_mad_even_length(normal_even_series):
    res = calc_mad(normal_even_series)
    med = np.median(normal_even_series)
    expect = np.median(np.abs(normal_even_series - med))
    assert np.isclose(res, expect)

def test_calc_mad_all_same(flat_series):
    assert np.isclose(calc_mad(flat_series), 0)

def test_calc_mad_single_element():
    arr = np.array([5])
    assert np.isclose(calc_mad(arr), 0)

def test_calc_mad_empty_array():
    arr = np.array([])
    with pytest.raises(ValueError, match="不能为空"):
        calc_mad(arr)

# ================== robust_z_score 测试 ==================
def test_zscore_flat_data(flat_series):
    z = robust_z_score(flat_series)
    assert np.all(z == 0)

def test_zscore_with_extreme():
    arr = np.array([10, 10, 10, 100])
    z = robust_z_score(arr)
    assert z[-1] > 2.0

def test_zscore_input_unchanged():
    arr = np.array([1, 2, 3, 4, 5])
    original = arr.copy()
    robust_z_score(arr)
    assert np.array_equal(arr, original)

def test_zscore_negative_sequence():
    arr = np.array([-5, -4, -6, -3, -80])
    z = robust_z_score(arr)
    assert np.abs(z[-1]) > 3

# ================== mad_detect 检测器 ==================
def test_plain_mad_detect_single_anomaly():
    data = np.array([1, 2, 3, 4, 100])
    mask, scores = mad_detect(data, threshold=3)
    assert mask[-1]
    assert not np.any(mask[:-1])
    assert len(scores) == len(data)
    assert scores[-1] > 3

def test_plain_mad_detect_no_anomaly():
    data = np.arange(10, dtype=float)
    mask, scores = mad_detect(data, threshold=3)
    assert not np.any(mask)
    assert all(s < 3 for s in scores)

def test_plain_mad_detect_threshold_zero():
    data = np.array([1, 2, 3])
    mask, _ = mad_detect(data, threshold=0)
    assert np.all(mask)

# ================== StlMadDetector 参数校验 ==================
def test_stl_length_exception():
    detector = StlMadDetector(period=TEST_PERIOD)
    short_seq = np.zeros(10)
    with pytest.raises(ValueError, match="过小"):
        detector.detect(short_seq)

def test_stl_period_zero():
    with pytest.raises(ValueError, match="必须大于0"):
        StlMadDetector(period=0)

def test_stl_negative_period():
    with pytest.raises(ValueError):
        StlMadDetector(period=-12)

# ================== STL‑MAD 时序异常检测功能 ==================
def test_stl_spike_single_anomaly():
    t = np.arange(200)
    seq = 10 + 3 * np.sin(2 * np.pi * t / TEST_PERIOD) + np.random.normal(0, 0.3, 200)
    seq[60] += 15
    det = StlMadDetector(period=TEST_PERIOD, threshold=3)
    mask, z_score, stl_result = det.detect(seq)

    assert mask[60]
    normal_mask = np.ones_like(seq, dtype=bool)
    normal_mask[60] = False
    false_positives = np.sum(mask[normal_mask])
    assert false_positives < FALSE_POSITIVE_LIMIT

    assert isinstance(z_score, np.ndarray)
    assert len(z_score) == len(seq)
    assert z_score[60] > 3
    assert stl_result is not None
    # 校验三大分解字段存在
    assert hasattr(stl_result, "trend")
    assert hasattr(stl_result, "seasonal")
    assert hasattr(stl_result, "resid")


def test_stl_dropdown_anomaly():
    """测试指标断崖下跌异常"""
    t = np.arange(200)
    seq = 10 + 3 * np.sin(2 * np.pi * t / TEST_PERIOD) + np.random.normal(0, 0.3, 200)
    seq[70] -= 15
    det = StlMadDetector(period=TEST_PERIOD, threshold=3)
    mask, _, _ = det.detect(seq)
    assert mask[70]


def test_stl_multiple_spike_anomaly():
    t = np.arange(200)
    seq = 10 + 3 * np.sin(2 * np.pi * t / TEST_PERIOD) + np.random.normal(0, 0.3, 200)
    anomaly_idx = [30, 80, 130]
    for idx in anomaly_idx:
        seq[idx] += 16
    det = StlMadDetector(period=TEST_PERIOD)
    mask, _, _ = det.detect(seq)
    for idx in anomaly_idx:
        assert mask[idx]


def test_stl_residual_std_lower():
    t = np.arange(100)
    seq = 20 + 5 * np.sin(2 * np.pi * t / 12) + np.random.normal(0, 1, 100)
    det = StlMadDetector(period=12)
    _, _, result = det.detect(seq)
    residual = result.resid
    valid_res = residual[~np.isnan(residual)]
    assert np.std(valid_res) < np.std(seq)


def test_stl_boundary_min_length():
    period = 12
    min_len = 2 * period + 1
    seq = np.zeros(min_len)
    det = StlMadDetector(period=period)
    mask, _, _ = det.detect(seq)
    assert len(mask) == min_len


def test_input_type_flexibility():
    det = StlMadDetector(period=12)
    data_list = [1, 2, 3] * 20
    mask_list, _, _ = det.detect(data_list)
    assert len(mask_list) == len(data_list)

    data_series = pd.Series(data_list)
    mask_series, _, _ = det.detect(data_series)
    assert len(mask_series) == len(data_series)


def test_flat_time_series_no_anomaly(flat_series):
    det = StlMadDetector(period=10)
    mask, _, _ = det.detect(flat_series)
    assert np.sum(mask) == 0
