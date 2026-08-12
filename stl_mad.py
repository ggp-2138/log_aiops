import numpy as np
from statsmodels.tsa.seasonal import STL


def calc_mad(arr: np.ndarray) -> float:
    """计算中位数绝对偏差MAD"""
    arr = np.asarray(arr)
    if arr.size == 0:
        raise ValueError("不能为空")
    median_val = np.median(arr)
    return np.median(np.abs(arr - median_val))


def robust_z_score(arr: np.ndarray) -> np.ndarray:
    """基于MAD的稳健Z‑score"""
    arr = np.asarray(arr)
    median_val = np.median(arr)
    mad = calc_mad(arr)
    # MAD等于0时全部得分置零
    if mad == 0:
        return np.zeros_like(arr, dtype=np.float64)
    z = 0.6745 * (arr - median_val) / mad
    return z


def mad_detect(data, threshold: float = 3):
    """基础MAD异常检测
    :return: mask布尔异常掩码, scores稳健z分数
    """
    arr = np.asarray(data, dtype=np.float64)
    scores = robust_z_score(arr)
    mask = np.abs(scores) > threshold
    return mask, scores


class StlMadDetector:
    def __init__(self, period: int, threshold: float = 3):
        if period <= 0:
            raise ValueError("必须大于0")
        self.period = period
        self.threshold = threshold

    def detect(self, time_series):
        """STL时序分解 + 残差MAD异常检测"""
        data = np.asarray(time_series, dtype=np.float64)
        min_length = 2 * self.period + 1
        if len(data) < min_length:
            raise ValueError("过小")

        # STL时间序列分解
        stl = STL(data, period=self.period)
        res = stl.fit()
        residual = res.resid

        mask, z_score = mad_detect(residual, self.threshold)
        return mask, z_score, res
