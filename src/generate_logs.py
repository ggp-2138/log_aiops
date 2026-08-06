import time
import random
import os
from datetime import datetime

log_lines = [
    "INFO - User {num} logged in from IP {ip}",
    "INFO - Order {num} placed successfully",
    "WARN - Slow query detected on table orders, duration {num}ms",
    "ERROR - Connection timeout to database mysql-primary",
    "ERROR - User {num} login failed: password incorrect",
    "CRITICAL - Out of memory: process java killed",
]
# 生成当天日期文件夹
today_str = datetime.now().strftime("%Y-%m-%d")
output_path = f"../data/logs/{today_str}/app.log"

# 自动递归创建 logs 以及日期文件夹
os.makedirs(os.path.dirname(output_path), exist_ok=True)

print(f"Writing logs to {output_path}... (Press Ctrl+C to stop)")
try:
    with open(output_path, "w") as f:
        for i in range(1000):
            rand_num = random.randint(1000, 9999)
            rand_ip = f"192.168.1.{random.randint(1, 255)}"
            line = random.choice(log_lines).format(num=rand_num, ip=rand_ip)
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")       #strftime：获取当前系统本地时间字符串
            f.flush()   # 立即写入磁盘，Filebeat 实时采集
            time.sleep(0.1)
except KeyboardInterrupt:
    print(f"\n[INFO] 日志生成被中断，已写入 {i} 条日志。")
else:
    print(f"[INFO] 日志生成完成，共 1000 条。")
