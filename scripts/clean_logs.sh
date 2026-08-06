#!/bin/bash

# ==========配置项==========
LOG_DIR="../data/logs"
RETENTION_DAYS=7
# =========================

# 计算截止日期
CUTOFF_DATE=$(date -d "-${RETENTION_DAYS} days" +%Y-%m-%d)

# 遍历日志目录下所有子目录
for dir in "${LOG_DIR}"/*/; do
    [ -d "$dir" ] || continue

    # 提取目录名（纯8位日期）
    dir_name=$(basename "$dir")
    # 只匹配 8 位数字的日期目录
    echo "$dir_name" | grep -qE '^\d{4}-\d{2}-\d{2}$' || continue

    # 日期早于截止日期则删除整个目录
    if [ "$dir_name" -lt "$CUTOFF_DATE" ]; then
        rm -rf "$dir"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已删除过期目录: ${dir_name}"
    fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日志目录清理任务执行完成"
