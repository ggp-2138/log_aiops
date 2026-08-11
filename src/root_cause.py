import requests
import config
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

PROMETHEUS_URL = config.PROMETHEUS_URL  # Prometheus 地址
ROOT_CAUSE_MAP = config.ROOT_CAUSE_MAP  # 异常模板 ID 与关联指标映射
GLOBAL_MAX_SCORE = config.GLOBAL_MAX_SCORE  # 全局得分上限
MIN_CONT_POINTS = config.MIN_CONT_POINTS  # 连续异常点数阈值，过滤单点毛刺


# ========== 时间标准化转换模块(解决 datetime 类型报错) ===============
def normalize_datetime(dt_input):
    """
    时间格式统一兼容转换工具
    支持输入类型：datetime 原生对象(带时区/不带时区) / 秒级时间戳 / 毫秒时间戳 / 标准时间字符串
    统一输出 Asia/Shanghai naive datetime 对象
    返回：标准无时区北京时间 datetime 对象，适配所有窗口计算、Prometheus时间戳转换
    """
    if dt_input is None:
        return None
    # 判断是否是 datetime 类型
    if isinstance(dt_input, datetime):
        dt = dt_input
        # 时区信息 → 先转换为北京时间，再抹掉时区，统一 naive 类型
        if dt.tzinfo is not None:
            dt = dt.astimezone()  # 转为本地时区(Asia/Shanghai)
            dt = dt.replace(tzinfo=None)
        return dt
    # 数字：判定为时间戳，区分秒/毫秒
    if isinstance(dt_input, (int, float)):
        ts_val = dt_input
        # 13位数字判定为毫秒时间戳，除以1000转为秒
        if ts_val > 10**12:
            ts_val = ts_val / 1000
        return datetime.fromtimestamp(ts_val)
    # 字符串：标准 yyyy-MM-dd HH:mm:ss 格式解析
    if isinstance(dt_input, str):
        try:
            return datetime.strptime(dt_input, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError(
                f"时间字符串格式错误：{dt_input}，仅支持 %Y-%m-%d %H:%M:%S"
            )
    # 非法类型抛出异常
    raise TypeError(
        "时间入参仅支持：datetime对象、数字时间戳、'2026-08-01 12:00:00'格式字符串"
    )


def query_prometheus(promql, start_time, end_time, step=None):
    """查询 Prometheus 指标，返回时间序列值列表
    step:
        是 PromQL 查询时的步进间隔,每隔 step 时间戳，就近查找该时间点对应的最近原始采集值，填入结果数组
        例如 step='1m' 代表每分钟返回一个采样点
        step 需要大于等于 Prometheus 的 scrape_interval(采集间隔)，才能拿到数据
    新增：自动根据巡检窗口跨度适配步长，避免历史久远大窗口查询为空
    scrape_interval: Prometheus.yml 中的参数
        原始时序点数量 = 巡检总时长 / 采集间隔 + 1
    """
    # 入参强制标准化，杜绝外部传入带时区时间
    start_time = normalize_datetime(start_time)
    end_time = normalize_datetime(end_time)

    # 巡检总时间(分钟)
    total_min = (end_time - start_time).total_seconds() / 60
    if step is None:
        # 小于60分钟: 15 秒细密采样，MAD计算需要充足样本
        if total_min <= 60:
            step = "15s"
        elif total_min <= 180:
            step = "30s"
        # 超过3小时超大窗口: 2分钟，大幅减少数据点，防止超时
        else:
            step = "1m"

    try:
        params = {  # 请求体
            "query": promql,
            "start": start_time.timestamp(),
            "end": end_time.timestamp(),
            "step": step,
        }
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range", params=params, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "success" or len(data["data"]["result"]) == 0:
            return []
        # 提取所有数值
        values = [float(item[1]) for item in data["data"]["result"][0]["values"]]
        return values
    except Exception as e:
        print(f"[WARN] Prometheus查询异常: {promql} {str(e)}")
        return None


def mad_score_with_baseline(
    detect_series, metric_name, baseline_series=None, min_relative_mad=0.05
):
    """
    分离基线&检测窗口 MAD 打分
    min_relative_mad: 最小相对波动阈值
        相对波动 = 基线 MAD ÷ 基线中位数(基线波动占正常值的比例)
    baseline_series: 故障前平稳正常数据
    detect_series: 待检测时序数组

     σ≈1.4826*MAD
     0.6745*(x−med)     x-med
     ————————————— =  —————————
         MAD          1.4826*MAD
    """
    # 清洗数据
    # np.isfinite : 判断是否为无穷数、NAN
    detect_series = [x for x in detect_series if x is not None and np.isfinite(x)]
    if len(detect_series) < 3:
        return 0.0, 0.0, "-"

    # 存在前置纯净基线：用异常前平稳数据计算基准
    if baseline_series is not None and len(baseline_series) >= 5:
        bs = [x for x in baseline_series if x is not None and np.isfinite(x)]
        bs_ser = pd.Series(bs)
        # 基线中位数(系统日常正常值)
        baseline_median = bs_ser.median()
        # 基线 MAD(系统日常正常波动幅度)
        baseline_mad = np.median(np.abs(bs_ser - baseline_median))
    else:
        # 无可用基线：退化，自身时序算基线
        s = pd.Series(detect_series)
        baseline_median = s.median()
        baseline_mad = np.median(np.abs(s - baseline_median))

    # 完全静止基线直接归零，避免除零导致分数爆炸
    if baseline_mad < 1e-9:
        return 0.0, baseline_median, "-"

    # 基线相对波动校验
    if abs(baseline_median) > 1e-3:
        relative_mad = baseline_mad / abs(baseline_median)
        # 基线波动过小时不强行归零,保证平稳场景正常计分
        if relative_mad < min_relative_mad:
            pass

    # 计算每个点修正Z分数
    detect_arr = np.array(detect_series)
    z_scores = 0.6745 * (detect_arr - baseline_median) / baseline_mad
    abs_z = np.abs(z_scores)

    # 95分位数剔除单点毛刺，避免单个极值拉高整体得分
    raw_score = np.quantile(abs_z, 0.95)
    # 统计连续超标点
    anomaly_cnt = np.sum(abs_z > 3)
    # 不满足门槛则衰减得分,降低误告警权重
    if anomaly_cnt < MIN_CONT_POINTS:
        raw_score *= 0.5

    # 分数封顶，杜绝暴增
    final_score = min(raw_score, GLOBAL_MAX_SCORE)

    # =============== 升降方向判定 ================
    # 取检测窗口最后5个最新点位均值，对比前置基线中位数
    take_last = 5
    if len(detect_arr) >= take_last:
        recent_avg = np.mean(detect_arr[-take_last:])
    else:
        # 检测段点数不足5个，全部取均值兜底
        recent_avg = np.mean(detect_arr)

    if recent_avg > baseline_median:
        direction = "↑"
    elif recent_avg < baseline_median:
        direction = "↓"
    else:
        direction = "-"

    # 调试日志打印
    print(
        f"[DEBUG] {metric_name}: 基线中位数={baseline_median:.4f}, 基线MAD={baseline_mad:.6f}, "
        f"异常点数={anomaly_cnt}, 最终得分={final_score:.1f}, 趋势={direction}"
    )
    #  返回: 最终得分, 基线中位数, 指标升降方向
    return final_score, baseline_median, direction


def build_query_window(anomaly_start=None, anomaly_end=None):
    """根据聚合窗口和传导延迟动态计算查询窗口
    lookback_before: 异常开始前额外拉取多少分钟数据（建立基线）,
        推荐 >= (2~3) * TIME_WINDOW_MINUTES（相邻异常点合并窗口）,回溯窗口能覆盖完整故障链路和基线数据
        确保基线数据包含异常发生前至少一个完整合并窗口的数据，避免基线被前面的异常点污染.

        如果异常点出现在合并窗口的起始位置，告警在合并窗口末尾触发,异常点可能漏掉.
        上下游服务、依赖组件、的告警传导通常有 1~3 分钟延迟
    lookback_after: 异常结束后额外拉取多少分钟数据（观察恢复），
        推荐 >= (2~3) * MIN_ANOMALY_DURATION（最小持续异常分钟数），避免恢复误判
    """

    # 统一转换时间格式，再做类型校验，彻底消除TypeError
    anomaly_start = normalize_datetime(anomaly_start)
    anomaly_end = normalize_datetime(anomaly_end)
    # 因为外部会调用，所以时间标准化重新校验了一次
    # 参数校验
    if anomaly_start and anomaly_end and anomaly_start >= anomaly_end:
        raise ValueError("anomaly_start 必须早于 anomaly_end")

    # 处理空值（异常起止未知时，以当前时间为基准回溯）
    now = datetime.now()
    if anomaly_start is None:
        # 未知起始时间: 回溯 合并窗口 + 传导延迟，保底覆盖完整故障链路
        anomaly_start = now - timedelta(
            minutes=config.TIME_WINDOW_MINUTES + config.PROPAGATION_DELAY_MIN
        )
    if anomaly_end is None:
        anomaly_end = now

    # 计算动态回溯窗口
    # 前置窗口 = 合并窗口 + 传导延迟：完整覆盖告警聚合周期 + 上游故障传导时间
    lookback_before = config.TIME_WINDOW_MINUTES + config.PROPAGATION_DELAY_MIN
    # 后置窗口 = 最小持续异常 + 采集延迟：覆盖恢复验证 + 日志/指标落盘时差
    lookback_after = config.MIN_ANOMALY_DURATION + config.COLLECTION_DELAY_MIN

    # 保底约束：强制不低于推荐倍率，避免配置过小导致截断
    lookback_before = max(lookback_before, config.TIME_WINDOW_MINUTES * 2)
    lookback_after = max(lookback_after, config.MIN_ANOMALY_DURATION * 3)

    # 计算最终查询窗口
    query_start = anomaly_start - timedelta(minutes=lookback_before)
    query_end = anomaly_end + timedelta(minutes=lookback_after)

    return {
        "query_start": query_start,
        "query_end": query_end,
        "lookback_before": lookback_before,
        "lookback_after": lookback_after,
    }


def get_top3_root_cause(anomalous_template_ids, anomaly_start=None, anomaly_end=None):
    """
    根据异常模板 ID 列表，动态查询关联指标，返回 Top3 根因
    最小巡检时间窗口兜底 + 动态回溯窗口，样本充足打分稳定
    压测专用根因函数：基线窗口与检测窗口完全分离
    基线：异常触发前回溯窗口内的平稳空载数据
    检测段：故障发生区间，对比基线判断是否偏离正常范围，适用短压测窗口

    anomalous_template_ids:
        异常模板 ID    list[int]
    anomaly_start/end:
        异常发生的时间范围（datetime 对象）
    query_window:       dict
        拉取 Prometheus 指标数据的时间范围
    anomaly_scores:     list[dict]
        根因列表,每项包含 metric_name, promql, score, direction
    """
    # ========== 1、时间标准化+兜底容错 ==========
    try:
        # 校验传入时间合法性
        s = normalize_datetime(anomaly_start)
        e = normalize_datetime(anomaly_end)
        if s is not None and e is not None and s < e:
            anomaly_start = s
            anomaly_end = e
        else:
            raise ValueError("时间区间不合法")
    except Exception:
        anomaly_end = datetime.now()
        anomaly_start = anomaly_end - timedelta(minutes=5)
        print("[INFO] 未获取有效日志异常时段，自动兜底：最近5分钟实时监控窗口")

    # 构建动态查询窗口（包含前置基线回溯 + 后置恢复观察）
    query_window = build_query_window(anomaly_start, anomaly_end)
    query_start = query_window["query_start"]
    query_end = query_window["query_end"]

    # 分指标阈值：使用率类型指标对波动更敏感，阈值应更高
    metric_tune_cfg = {
        "CPU 使用率": {"min_relative_mad": 0.10},
        "内存使用率": {"min_relative_mad": 0.08},
    }
    seen_metrics = {}

    # ========== 2、遍历异常模板ID + 逐个查询指标时序 ==========
    for tid in anomalous_template_ids:
        # 获取该模板对应的指标集，没有则使用通用指标(id=-1)
        metrics = ROOT_CAUSE_MAP.get(tid, ROOT_CAUSE_MAP.get(-1, {}))
        print(f"[DEBUG] 模板ID={tid}, 待查指标: {list(metrics.keys())}")
        # 依次执行对应模板的 promql
        for metric_name, promql in metrics.items():
            # 单次查询完整动态窗口（前置回溯窗口+后置恢复段）
            full_series = query_prometheus(promql, query_start, query_end)
            # print(f"[TIME-DBG] metric={metric_name} start={query_start} end={query_end}")
            # print(f"[TIME-DBG] start_ts={query_start.timestamp()}, end_ts={query_end.timestamp()}")

            if full_series is None or len(full_series) < 3:
                print(f"[FALLBACK] {metric_name} 原窗口时序不足，切换近20分钟窗口重试")
                fb_e = datetime.now()
                fb_s = fb_e - timedelta(minutes=20)
                full_series = query_prometheus(promql, fb_s, fb_e)
                print(
                    f"[TIME-DBG-FALLBACK] metric={metric_name} fb_start={fb_s} fb_end={fb_e}"
                )
            if full_series is None:
                print(f"[SKIP] {metric_name} ❌ 请求Prometheus异常！")
                continue
            if len(full_series) < 3:
                print(
                    f"[SKIP] {metric_name} ⚠ 时序点数不足({len(full_series)}个)，跳过计算"
                )
                continue

            # 按回溯比例自动拆分基线段与检测段
            # 总巡检时长
            total_span = (query_end - query_start).total_seconds() / 60
            # 基线区间比例
            baseline_ratio = (query_window["lookback_before"] * 0.7) / total_span
            # 基线最高只允许占巡检总窗口70%，强制留存30%作为检测区间
            baseline_ratio = min(baseline_ratio, 0.7)
            # 将基线区间与检测段分割，保证至少 3 个采样点
            split_idx = max(3, int(len(full_series) * baseline_ratio))
            # 基线区间
            baseline_series = full_series[:split_idx]
            # 检测区间
            detect_series = full_series[split_idx:]
            print(
                f"[DEBUG] {metric_name}: baseline_points={len(baseline_series)}, detect_points={len(detect_series)}"
            )

            # 点数校验
            if len(detect_series) < 1 or len(baseline_series) < 2:
                print(f"[SKIP] {metric_name} 检测点不足")
                continue

            # 按指标类型传入不同阈值
            cfg = metric_tune_cfg.get(metric_name, {"min_relative_mad": 0.05})
            # MAD计算异常得分+基线中位数+升降方向
            score, base_med, direction = mad_score_with_baseline(
                detect_series=detect_series,
                metric_name=metric_name,
                baseline_series=baseline_series,
                min_relative_mad=cfg["min_relative_mad"],
            )

            print(
                f"[DEBUG] {metric_name}: score={score}, base_med={base_med}, SCORE_THRESHOLD={config.MIN_SCORE_THRESHOLD}"
            )

            # 低于阈值不纳入结果
            if score <= config.MIN_SCORE_THRESHOLD:
                continue

            item = {
                "metric_name": metric_name,
                "score": round(score, 1),
                "direction": direction,
                "template_id": tid,
            }

            # 同名指标保留最高分
            if (
                metric_name not in seen_metrics
                or score > seen_metrics[metric_name]["score"]
            ):
                seen_metrics[metric_name] = item

    # 按得分降序，取Top3根因返回
    sorted_list = sorted(seen_metrics.values(), key=lambda x: x["score"], reverse=True)
    return sorted_list[:3]


"""
示例输出:
    seen_metrics = {
    "CPU 使用率": {"metric_name": "CPU 使用率", "score": 4.2, "direction": "↑", "template_id": 4},
    "内存可用率": {"metric_name": "内存可用率", "score": 3.1, "direction": "↓", "template_id": 2},
    ...
}

anomaly_scores = [
    {"metric_name": "CPU 使用率", "score": 4.2, "score": 4.2, "direction": "↑", "template_id": 4},
    {"metric_name": "内存可用率", "score": 3.1, "direction": "↓", "template_id": 2},
    ...
]
"""
