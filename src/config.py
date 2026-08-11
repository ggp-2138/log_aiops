from dotenv import load_dotenv
import os

load_dotenv()  # 加载 .env 文件
# 索引名称通配符：默认按天生成索引(filebeat-2026.07.27)
INDEX = "filebeat-*"
# 巡检时间窗口
LOOKBACK_MINUTES = 60
# 日志模板树存储地址(持久化)
DRAIN_SAVE_PATH = "../data/drain_state.bin"

# 降噪参数
TIME_WINDOW_MINUTES = 5  # 相邻异常点合并窗口（分钟）
MIN_ANOMALY_DURATION = 1  # 最小持续异常分钟数，过滤毛刺

# 采样窗口大小，单位：分钟
SAMPLING_WINDOW_MINUTES = 1
# 钉钉机器人配置
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")

# FastAPI 配置
DETECT_API_URL = os.getenv("DETECT_API_URL")
API_TOKEN = os.getenv("API_TOKEN", "my-secret-token")  # 第二个参数是默认值

# Prometheus 容器服务
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL")

# 日志解析配置
ES_HOST = os.getenv("ES_HOST")

# ========== 根因分析时间窗口相关延迟 ==========
PROPAGATION_DELAY_MIN = 1  # 故障传导延迟（分钟）：上游故障传导到下游产生告警的耗时
COLLECTION_DELAY_MIN = (
    1  # 采集延迟（分钟）：指标/日志从产生到被 Prometheus/ES 拉取的耗时
)

# 全局阈值配置（方便后续根据测试或生产环境微调）
GLOBAL_MAX_SCORE = 50  # 得分封顶上限（测试环境建议25，生产环境可调至30）
MIN_SCORE_THRESHOLD = 1.0  # 入围最低异常分数（过滤噪声）
MIN_CONT_POINTS = 4  # 至少连续3个点超标才算有效异常（减少毛刺）

# 异常模板 ID 与关联指标映射
ROOT_CAUSE_MAP = {
    4: {  # Connection timeout to database mysql-primary
        "MySQL 连接数": "mysql_global_status_threads_connected",
        "MySQL 慢查询数": "increase(mysql_global_status_slow_queries[5m])",
        # "网络 IO 增长率": "rate(node_network_receive_bytes_total[1m])"
    },
    2: {  # Slow query detected on table orders
        "MySQL 慢查询数": "increase(mysql_global_status_slow_queries[5m])",
        "CPU 使用率": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        "内存使用率": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
    },
    3: {  # Out of memory: process java killed
        "内存使用率": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
        "CPU 使用率": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        #         "网络 IO 增长率": "rate(node_network_receive_bytes_total[1m])"
    },
    # 其他模板可扩展
    # 通用模板 (适用于所有异常，优先级低于特定模板)
    -1: {  # 默认通用指标
        "CPU 使用率": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        "内存使用率": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
        #         "网络 IO 增长率": "rate(node_network_receive_bytes_total[1m])"
    },
}
