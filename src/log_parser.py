import re
import os
from datetime import UTC
from elasticsearch import Elasticsearch  # DSL 查询语句,读取 ES 中的日志
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.file_persistence import FilePersistence
import pandas as pd
from datetime import datetime, timedelta

# ========== 配置 ==========
ES_HOST = "http://xxx1:9200"
# 索引名称通配符：默认按天生成索引(filebeat-2026.07.27)
INDEX = "filebeat-*"
LOOKBACK_MINUTES = 60 * 24
DRAIN_SAVE_PATH = "../data/drain_state.bin"  # 日志模板树持久化
INDIR = "../data/drain_data/"  # 输入缓存目录
OUTDIR = "../data/drain_result/"  # 解析结果输出目录
# 正则预处理: 命名分组提取标准时间戳、日志头、正文（可根据实际日志格式微调）
# \s+: 多个空格分隔符
input_format = r"(?P<Time>\S+\s+\S+)\s+(?P<Level>[A-Z]+)\s+-\s+(?P<Content>.*)"

# 创建目录
os.makedirs(INDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

# 初始化 Drain3 parser对象
config = TemplateMinerConfig()
config.sim_th = 0.5  # 日志相似度阈值(越大聚类越严格)
config.depth = 4  # 前缀树最大深度(越大日志拆分粒度越细)

# 开启持久化：加载历史模板
persistence = FilePersistence(DRAIN_SAVE_PATH)
# 解析器实例对象
template_miner = TemplateMiner(config=config)
# 绑定持久化处理器到实例内部
template_miner.persistence_handler = persistence

# 加载历史模板文件
if os.path.exists(DRAIN_SAVE_PATH):
    template_miner.load_state()
    print("已加载历史日志模板")

# ========== 1. 从 ES 拉取日志 ==========
# 实例化 ES 客户端对象,建立和 ES 服务的 TCP 连接
es = Elasticsearch(ES_HOST)
end = datetime.now(UTC)
start = end - timedelta(minutes=LOOKBACK_MINUTES)

# DSL 查询
query = {
    "size": 1000,
    "query": {
        "range": {  # 范围查询
            "@timestamp": {  # Filebeat 采集日志时产生的时间戳字段
                "gte": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "lte": end.strftime("%Y-%m-%dT%H:%M:%S"),
            }  # strftime：将 datetime 对象转为字符串( ISO8601 )
        }
    },
}

# 调用 ES Search API(res 是字典)
res = es.search(index=INDEX, body=query)
# print(res)
logs = [hit["_source"]["message"] for hit in res["hits"]["hits"]]
print(f"拉取到 {len(logs)} 条日志")

# ========== 2. 日志正则切割 + Drain3 聚类生成模板==========
# 投喂全量日志，生成模板
for line in logs:
    match_res = re.match(input_format, line)
    if match_res:
        # 匹配日志正文
        log_content = match_res.group("Content")
        # 将日志正文分词、前缀树匹配、相似度计算、聚类合并/新增日志模板簇
        template_miner.add_log_message(log_content)

# 打印前5个日志模板
# 读取存储的(内存)日志模板簇(新版 Drain3 底层存储为字典)
all_clusters = list(template_miner.drain.clusters)  # 将字典转为列表
if len(all_clusters) == 0:
    print("暂无生成任何日志模板")
else:
    print("\n日志模板示例：")
    for i, cluster in enumerate(all_clusters[:5]):
        print(
            f"模板 {i} | ID:{cluster.cluster_id} | 模板内容: {cluster.get_template()}"
        )

# 安全持久化保存，判断处理器非空再执行
if template_miner.persistence_handler is not None:
    try:
        # 传入快照备注
        template_miner.save_state(snapshot_reason="regular_run_save")
        print("日志模板已持久化保存至本地")
    except Exception as e:
        print("暂无日志模板，跳过保存")

# ========== 3. 按分钟统计每个模板出现频次 ==========
events = []
for log_line in logs:
    match = re.match(input_format, log_line)
    if match:
        ts = match.group("Time")
        content = match.group("Content")
        # 匹配已有模板ID
        matched_cluster = template_miner.match(content)
        if matched_cluster is not None:
            events.append({"timestamp": ts, "cluster_id": matched_cluster.cluster_id})

# pandas时序统计
df = pd.DataFrame(events)
if not df.empty:
    # 将时间字符串转为 datetime 对象
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    # 将时间列设置为数据表索引( resample 函数必须依赖时间索引)
    df.set_index("timestamp", inplace=True)

    # 按1分钟重采样统计频次
    # resample("1T")：时间重采样
    # size()：统计日志总条数
    # unstack(fill_value=0)：将时间行转为表格列，默认填充0
    counts = df.groupby("cluster_id").resample("1min").size().unstack(fill_value=0)
    print("\n每分钟日志模板频次统计表：")
    print(counts)
else:
    print("暂无可时序统计的日志数据")

print("\n全流程执行完毕：日志拉取->模板聚类->频次统计完成")

# ==================  测试 ===============
# import requests, os
#
# timestamps = [ts.strftime('%Y-%m-%d %H:%M:%S') for ts in counts.columns]
# values = [counts.loc[cluster_id].tolist() for cluster_id in counts.index]
#
# resp = requests.post(
#     "http://localhost:8001/detect/log",
#     json={"timestamps": timestamps, "values": values},
#     headers={"Authorization": f"Bearer {os.getenv('API_TOKEN', 'my-secret-token')}"}
# )
# print(f"Status code: {resp.status_code}")
# print(f"Response text: {resp.text}")   # 看服务返回了什么
# # 如果状态码是 200，再尝试 resp.json()
# if resp.status_code == 200:
#     print(resp.json())
