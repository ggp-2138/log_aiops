import re
import config
import os
import requests
import hmac
import hashlib
import base64
import time
import urllib.parse
import pandas as pd
from root_cause import get_top3_root_cause, build_query_window
from datetime import UTC,datetime, timedelta
from elasticsearch import Elasticsearch      # DSL 查询语句,读取 ES 中的日志
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.file_persistence import FilePersistence

# 正则预处理: 命名分组提取标准时间戳、日志头、正文（可根据实际日志格式微调）
# \s+: 多个空格分隔符
input_format = r"(?P<Time>\S+\s+\S+)\s+(?P<Level>[A-Z]+)\s+-\s+(?P<Content>.*)"

def get_template_miner():
    """ 初始化 Drain3 模板挖掘器，加载历史日志模板 """
    # 初始化 Drain3 parser对象
    miner_config = TemplateMinerConfig()
    miner_config.sim_th = 0.5  # 日志相似度阈值(越大聚类越严格)
    miner_config.depth = 4  # 前缀树最大深度(越大日志拆分粒度越细)

    # 开启持久化：加载历史模板
    persistence = FilePersistence(config.DRAIN_SAVE_PATH)

    # 解析器实例对象
    template_miner = TemplateMiner(config=miner_config)

    # 绑定持久化处理器到实例内部
    template_miner.persistence_handler = persistence
    # 加载历史模板文件
    if os.path.exists(config.DRAIN_SAVE_PATH):
        template_miner.load_state()
        print("已加载历史日志模板")
    return template_miner

def fetch_logs(template_miner):
    """从 ES 拉取最近 LOOKBACK_MINUTES 分钟日志"""
    # 实例化 ES 客户端对象,建立和 ES 服务的 TCP 连接
    es = Elasticsearch(config.ES_HOST)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=config.LOOKBACK_MINUTES)

    # DSL 查询
    query = {
        "size": 1000,
        "query": {
            "range": {  # 范围查询
                "@timestamp": {  # Filebeat 采集日志时产生的时间戳字段
                    "gte": start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "lte": end.strftime("%Y-%m-%dT%H:%M:%S")
                }  # strftime：将 datetime 对象转为字符串( ISO8601 )
            }
        }
    }

    # 调用 ES Search API (res 是字典)
    res = es.search(index=config.INDEX, body=query,request_timeout=10)
    logs = [hit["_source"]["message"] for hit in res["hits"]["hits"]]
    print(f"拉取到 {len(logs)} 条日志")
    return logs
    # print(res)

def parse_logs(template_miner,logs):
    """ 正则切割 → 模板聚类 → 分钟级频次统计，返回 counts( DataFrame ) """
    # 投喂全量日志，生成模板
    for line in logs:
        match_res = re.match(input_format, line)
        if match_res:
            # 匹配日志正文
            log_content = match_res.group("Content")
            # 将日志正文分词、前缀树匹配、相似度计算、聚类合并/新增日志模板簇
            template_miner.add_log_message(log_content)

    # 读取本地历史日志模板簇和新增的日志模板簇(新版 Drain3 底层存储为字典)
    all_clusters = list(template_miner.drain.clusters)  # 将字典转为列表
    if len(all_clusters) == 0:
        print("暂无生成任何日志模板")
    else:
        print("\n日志模板示例：")
        # 打印前5个日志模板
        for i, cluster in enumerate(all_clusters[:5]):
            print(f"模板 {i} | ID:{cluster.cluster_id} | 模板内容: {cluster.get_template()}")

    # 安全持久化保存，判断处理器非空再执行
    if template_miner.persistence_handler is not None:
        try:
            # 传入快照备注
            template_miner.save_state(snapshot_reason="regular_run_save")
            print("日志模板已持久化保存至本地")
        except Exception as e:
            print("暂无日志模板，跳过保存")

# ========== 按分钟统计每个模板出现频次 ==========
    events = []
    swm=config.SAMPLING_WINDOW_MINUTES
    for log_line in logs:
        match = re.match(input_format, log_line)
        if match:
            ts = match.group("Time")
            content = match.group("Content")

            # 匹配已有模板ID
            matched_cluster = template_miner.match(content)
            if matched_cluster is not None:
                events.append({
                    "timestamp": ts,
                    "cluster_id": matched_cluster.cluster_id
                })

    # pandas时序统计
    df = pd.DataFrame(events)
    # print(df)
    if not df.empty:
        # 将时间字符串转为 datetime 对象
        # str.strip(): 删除整列字符串首尾空格、制表符、换行符
        # errors="coerce"：解析失败的数据替换为 NaT，不会直接程序崩溃
        df["timestamp"] = pd.to_datetime(df["timestamp"].str.strip(),errors="coerce")
        # 剔除时间解析失败的脏数据
        df = df.dropna(subset=["timestamp"])
        # 将时间列设置为数据表索引( resample 函数必须依赖时间索引)
        # inplace: 是否在原 df 修改
        df.set_index("timestamp", inplace=True)

        # 按1分钟重采样统计频次
        # resample("1T")：时间重采样
        # size()：统计日志总条数
        # unstack(fill_value=0)：将时间行转为表格列，默认填充0
        counts = df.groupby("cluster_id").resample(f"{swm}min").size().unstack(fill_value=0)
        print(f"\n每{swm}分钟日志模板频次统计表：")
        # print(counts)
        return counts
    else:
        print("暂无可时序统计的日志数据")

    print("\n全流程执行完毕：日志拉取->模板聚类->频次统计完成")

# ========== 2. 调用异常检测接口 ==========
def detect_anomalies(counts):
    """将 counts(日志模板频次表)转为接口所需格式，调用 /detect/log 返回异常点列表"""
    if counts.empty:
        return []
    # 将 datetime 对象转为标准的字符串格式
    timestamps = [ts.strftime('%Y-%m-%d %H:%M:%S') for ts in counts.columns]
    # 模板索引ID
    template_ids = counts.index.tolist()
    # 取每行日志模板频次为序列并转为 python 列表
    values = [counts.loc[cid].tolist() for cid in template_ids]
    resp = requests.post(
        config.DETECT_API_URL,
        json={"timestamps": timestamps, "values": values,"template_ids": template_ids},
        headers={"Authorization": f"Bearer {config.API_TOKEN}"},
        timeout=10
    )
    if resp.status_code != 200:
        print(f"[ERROR] 检测接口调用失败: {resp.status_code} {resp.text}")
        return []
    return resp.json().get("anomalies", [])

# ========== 3.告警降噪(聚合) ==========
# 时间连续性分组
def aggregate_anomalies(anomalies):
    """根据起止时间、峰值频次、持续时长,将时间间隔接近的连续异常点合并为一段异常持续区间，
    过滤无效异常,避免短时间内告警刷屏."""
    swm = config.SAMPLING_WINDOW_MINUTES
    twm = config.TIME_WINDOW_MINUTES
    mad = config.MIN_ANOMALY_DURATION
    if not anomalies:
        return {}
    df = pd.DataFrame(anomalies)
    df["ts"] = pd.to_datetime(df["timestamp"])

    result = {}
    for tid, tdf in df.groupby("template_id"):    # 按模板ID分组(tid)
        tdf = tdf.sort_values("ts")            # 按时间升序排序
        # 相邻异常点时间差（分钟）
        diff = tdf["ts"].diff().dt.total_seconds() / 60

        # 大于等于最小合并窗口则标记为新的告警事件(故障周期)
        # cumsum(): 累加列值，区分异常区间
        tdf["anomaly_interval_id"] = (diff >= twm).cumsum()
        merged = []
        for gid, gdf in tdf.groupby("anomaly_interval_id"):      # 按异常区间分组(gid)
            actual_duration = (gdf["ts"].max() - gdf["ts"].min()).total_seconds() / 60 + swm    # 实际异常持续时间
            if actual_duration >= mad:      # 比较异常持续时间
                merged.append({
                    "start": gdf["ts"].min(),
                    "end": gdf["ts"].max(),
                    "max_freq": gdf["frequency"].max(),
                    "actual_duration": actual_duration
                })
        if merged:
            result[tid] = merged
    # print(result)
    return result

# ================ 4.根因分析 =================
def analyze_root_causes(aggregation):
    # 统计有效异常条目数量
    total_anomaly_count = 0
    print("===== 开始遍历aggregation字典 =====")
    for tid, group_list in aggregation.items():
        if len(group_list) > 0:
            total_anomaly_count += len(group_list)
        print(f"模板ID:{tid} 条目数量: {len(group_list)}")
    print(f"✅ 全局有效异常总条数：{total_anomaly_count}")

    # 读取日志降噪聚合出来的真实异常起止时间
    anomalous_tids = list(aggregation.keys())
    times = []
    for groups in aggregation.values():
        for g in groups:
            times.extend([g["start"], g["end"]])
    if times:
        detect_start = min(times)
        detect_end = max(times)
        # 偏移30秒，规避整点边界查询空洞
        detect_start = detect_start + timedelta(seconds=30)
        # 关键：end不能超过当前时间，防止查询未来空白区间
        now = datetime.now()
        if detect_end > now:
            detect_end = now
            print(f"[WARN] 窗口结束时间超出当前时间，自动截断至 {now}")

        # 安全校验：防止起始大于结束
        if detect_start >= detect_end:
            print("[WARN] 时间窗口异常，降级使用最近15分钟")
            detect_end = now
            detect_start = detect_end - timedelta(minutes=15)
        print(f"[INFO] 检测到日志异常，异常时段：{detect_start} ~ {detect_end}")
    else:
        detect_end = datetime.now()
        detect_start = detect_end - timedelta(minutes=5)
        print("[INFO] ✅ 无日志异常，使用近5分钟监控窗口兜底")

    # 动态根因查询窗口
    window = build_query_window(detect_start, detect_end)
    query_start = window["query_start"].strftime('%m-%d %H:%M')
    query_end = window["query_end"].strftime('%m-%d %H:%M')
    # 固定异常时间为最近 twm 分钟
    twm = config.TIME_WINDOW_MINUTES
    recently_detect_end = datetime.now()
    recently_detect_start = recently_detect_end - timedelta(minutes=twm)
    # 固定根因查询窗口
    recently_window = build_query_window(recently_detect_start, recently_detect_end)
    recently_query_start = recently_window["query_start"].strftime('%m-%d %H:%M')
    recently_query_end = recently_window["query_end"].strftime('%m-%d %H:%M')
    # 动态根因巡检 (巡检起止时间 + 模板ID列表)
    print("🚀 开始动态根因巡检")
    root_causes = get_top3_root_cause(anomalous_tids, detect_start, detect_end)
    print("🚀 调用结束，结果数量:", len(root_causes))
    # 固定根因巡检
    print("🚀 开始固定根因巡检")
    recently_root_causes = get_top3_root_cause(anomalous_tids, recently_detect_start, recently_detect_end)
    print("🚀 调用结束，结果数量:", len(recently_root_causes))
    query_info = {
        "query_start": query_start,
        "query_end": query_end,
        "recently_query_start": recently_query_start,
        "recently_query_end": recently_query_end,
    }
    return root_causes, recently_root_causes, query_info

# ============ 5.调用本地大模型 =============
def ask_llm(alert_text: str) -> str:
    """ 
    调用 Ollama 生成排障建议 
    入参：alert_text 字符串，告警完整文本
    返回值：字符串，大模型输出的运维排查建议

    """
    try:
        # 请求 Ollama 本地服务接口
        resp = requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={
                "model": "qwen2.5:1.5b",        # 模型名称
                "prompt": f"""你是一个运维专家。收到以下告警信息：

{alert_text}

请给出：
1. 可能根因（一句话）
2. 排查步骤（3步以内）
3. 紧急程度（高/中/低）

用简洁中文回答，不要多余内容。""",
                # 关闭流式返回；一次性接收完整回答
                "stream": False,        
                # num_predict：限制模型最多输出 256 个 token，避免大模型输出过长文本拖慢告警推送
                # temperature：温度参数，数值越接近 0，回答越固定客观；越高越富有创造性，运维场景适合低温度
                "options": {"num_predict": 256, "temperature": 0.1}
            },
            timeout=60      # 防止 Ollama 模型加载缓慢卡住巡检流程
        )

        if resp.status_code == 200:
            return resp.json().get("response", "")
    except Exception as e:
        print(f"LLM 调用失败: {e}")
    # 接口调用出错时返回空字符串，保证程序不会崩溃中断巡检流程
    return ""

# ========== 6.钉钉加签计算 ==========
def build_signed_url():
    """ 加签计算生成带签名的钉钉 Webhook URL """
    # 生成毫秒时间戳
    # 签名和时间戳绑定，过期的签名会失效，避免被重复利用
    timestamp = str(round(time.time() * 1000))

    # 构造待签名字符串(按照钉钉官方规范)
    sign_str = f"{timestamp}\n{config.DINGTALK_SECRET}"

    # HMAC-SHA256 签名计算
    # 以 config.DINGTALK_SECRET 为密钥，对 sign_str 做哈希运算
    # 对计算后的二进制结果做 Base64 编码
    sign = base64.b64encode(
        hmac.new(config.DINGTALK_SECRET.encode(), sign_str.encode(), hashlib.sha256
                 ).digest())

    # 对签名结果做 URL 编码
    sign_encoded = urllib.parse.quote_plus(sign)
    Webhook_url=f"{config.DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign_encoded}"
    return Webhook_url

# ========== 7.钉钉推送 ==========
def send_dingtalk(aggregation, miner, root_causes, recently_root_causes,
                  query_info):
    """发送钉钉 Markdown 告警消息"""
    # 测试可用成员
    # 适配clusters是字典结构
    # clusters = list(miner.drain.clusters)
    # if clusters:
    #     obj = clusters[0]
    #     print("LogCluster全部属性：\n", dir(obj))
    # else:
    #     print("暂无聚类数据")
    twm = config.TIME_WINDOW_MINUTES
    swm = config.SAMPLING_WINDOW_MINUTES
    # 动态根因查询窗口
    query_start = query_info["query_start"]
    query_end = query_info["query_end"]
    # 固定根因查询窗口
    recently_query_start = query_info["recently_query_start"]
    recently_query_end = query_info["recently_query_end"]
    # 异常日志模板ID
    if not aggregation:
        print("[INFO] 无有效告警，跳过推送")
        return
    # 构建消息内容
    lines = ["### ⚠️ 日志异常告警\n"]
    lines.append(f"**检测时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**统计窗口**：最近 {config.LOOKBACK_MINUTES} 分钟\n")

    # 按日志模板逐个输出异常
    for tid, groups in aggregation.items():
        # 默认模板文本
        template_text = "系统杂项日志（无固定模板）"
        try:
            all_clusters = list(miner.drain.clusters)
            for cluster in all_clusters:
                if cluster.cluster_id == tid:       #匹配模板ID
                    # 获取完整日志模板字符串
                    template_text = cluster.get_template()
        except Exception as err:
            print(f"[WARN] 读取模板ID{tid}失败: {err}")

        # 异常日志模板详情
        lines.append(f"\n**模板 {tid}**：\n")
        lines.append(f"`{template_text}`\n")
        for g in groups:
            start_str = g["start"].strftime("%m-%d-%H:%M")
            end_str = g["end"].strftime("%m-%d-%H:%M")
            max_freq_1m=round(g["max_freq"]/swm)
            lines.append(
                f"- 异常时段：{start_str} ~ {end_str}，峰值 {max_freq_1m} 次/每分钟，持续 {g['actual_duration']} 分钟"
            )

    # 动态根因查询消息模板
    for rc in root_causes:
        print(rc)
    if root_causes:
        lines.append(f"\n**🔍 (动态巡检)可疑根因 Top3**（查询窗口: {query_start} ~ {query_end}）:")
        for i, rc in enumerate(root_causes, 1):
            lines.append(
                f"{i}. {rc['metric_name']}：异常得分 {rc['score']}σ {rc['direction']}"
            )
    else:
        lines.append(f"\n**🔍 动态巡检未发现明显关联指标异常**（查询窗口: {query_start} ~ {query_end}）")

    # 固定根因查询消息模板
    for rrc in recently_root_causes:
        print(rrc)
    if recently_root_causes:
        lines.append(f"\n**🔍 (固定巡检)可疑根因 Top3**（数据时段: {recently_query_start} ~ {recently_query_end}）:")
        for j, rrc in enumerate(recently_root_causes, 1):
            lines.append(
                f"{j}. {rrc['metric_name']}：异常得分 {rrc['score']}σ {rrc['direction']}"
            )
    else:
        lines.append(f"\n**🔍 最近 {twm} 分钟未发现明显关联指标异常**（数据时段: {recently_query_start} ~ {recently_query_end}）")

# ================ AI 智能故障根因分析 ====================
    # 拼接为单行字符串
    alert_summary = ''.join(lines)
    # 调用本地 Ollama 大模型自动根因分析
    ai_suggest = ask_llm(alert_summary)
    # 将 AI 诊断结果追加进告警消息
    lines.append(f"\n🤖 AI智能故障根因分析:\n{ai_suggest}")

    # Kibana 链接(容器宿主机IP地址)
    lines.append(f"\n> 🔗 [查看 Kibana 日志详细](http://ubt1:5601)")
    lines.append(f"\n> 🔗 [查看 Grafana 实时指标](http://ubt1:3000)")
    # 钉钉消息体格式
    message = {
        "msgtype": "markdown",              #消息类型
        "markdown": {
            "title": "⚠️ 日志异常告警",       #标题
            "text": "\n".join(lines)        #正文
        }
    }

    # 生成带签名的完整 Webhook 地址
    signed_url = build_signed_url()
    try:
        resp = requests.post(signed_url, json=message, timeout=10)
        if resp.status_code == 200:
            print("[INFO] 钉钉告警推送成功")
        else:
            print(f"[ERROR] 钉钉推送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[ERROR] 钉钉推送异常: {e}")

# ========== 主流程 ==========
def main():
    print(f"[{datetime.now()}] 开始日志智能巡检...")

    # 初始化 Drain3 模板挖掘器，加载历史日志模板
    template_miner = get_template_miner()

    # 从 ES 拉取最近 (LOOKBACK_MINUTES) 分钟日志
    logs= fetch_logs(template_miner)

    # 获取日志模板频次表
    counts = parse_logs(template_miner,logs)
    if counts is None:
        print("[INFO] 暂无日志数据")
        return

    # 调用异常检测API
    anomalies = detect_anomalies(counts)
    # print(anomalies)
    if not anomalies:
        print("[INFO] 未检测到异常")
        return

    # 异常持续区间分组(告警降噪)
    aggregation = aggregate_anomalies(anomalies)

# =============== 调用根因分析模块 ================
    root_causes, recently_root_causes, query_info=analyze_root_causes(aggregation)

# ========== 钉钉告警推送 ===============
    send_dingtalk(aggregation, template_miner , root_causes, recently_root_causes, query_info)

    # 保存最新的模板状态
    if template_miner.persistence_handler is not None:
        template_miner.save_state(snapshot_reason="scheduler_run")
    print(f"[{datetime.now()}] 巡检完成")

if __name__ == "__main__":
    main()



