#!/bin/bash
set -euo pipefail

# ====================== 全局默认参数 ======================
CPU=1
MEM="1G"
VM_COUNT=1
DURATION=60
LOG_FILE="../data/logs/stress.log"
LOG_MODE="append"
DRY_RUN=false

# 彩色输出兼容判断
if [ -t 1 ]; then
    RED='\033[31m'
    YELLOW='\033[33m'
    GREEN='\033[32m'
    BLUE='\033[36m'
    NC='\033[0m'
else
    RED=''
    YELLOW=''
    GREEN=''
    BLUE=''
    NC=''
fi

# ====================== 使用帮助 ======================
usage() {
cat << EOF
压力测试脚本 | CPU/内存压力测试（前台运行，Ctrl+C 终止）
用法: $0 [-c CPU] [-m 内存大小] [-p 内存进程数] [-t 秒] [-l 日志] [-o 日志模式] [-n] [-h]
参数说明：
  -c  CPU压力进程数量，正整数，默认1
  -m  单进程内存，支持 512M / 1G（大小写均可），默认1G
  -p  内存进程个数，正整数，默认1
  -t  压测持续秒数，正整数，默认60
  -l  自定义日志文件路径，默认 ./stress.log
  -o  日志模式：append(追加)/overwrite(覆盖旧日志会重命名为 .bak)，默认append
  -n  干运行模式，仅打印配置不执行压测
  -h  打印本帮助并退出

示例：
  # 1核CPU，2个内存进程各1G，运行2分钟
  $0 -c 1 -m 1G -p 2 -t 120
  # 仅打印配置不执行
  $0 -c 1 -m 512m -p 1 -t 180 -n
EOF
    exit 1
}

# 纯数字校验工具
check_pos_int() {
    if ! [[ "$1" =~ ^[1-9][0-9]*$ ]]; then
        echo -e "${RED}[ERROR] 参数 $2 必须为正整数，输入：$1${NC}"
        exit 1
    fi
}

# 内存单位解析：统一转为MB
parse_mem_mb() {
    local val="$1"
    local num="${val//[gGmM]/}"
    local unit="${val: -1}"
    unit="${unit^^}"
    check_pos_int "$num" "内存数值"
    case "$unit" in
        G) echo $(( num * 1024 )) ;;
        M) echo "$num" ;;
        *) echo -e "${RED}[ERROR] 内存仅支持 M/G 单位，示例：512M、1G${NC}"; exit 1 ;;
    esac
}

# ====================== 入参解析 ======================
while getopts "c:m:p:t:l:o:nh" opt; do
    case "$opt" in
        c) CPU="$OPTARG" ;;
        m) MEM="$OPTARG" ;;
        p) VM_COUNT="$OPTARG" ;;
        t) DURATION="$OPTARG" ;;
        l) LOG_FILE="$OPTARG" ;;
        o) LOG_MODE="$OPTARG" ;;
        n) DRY_RUN=true ;;
        h) usage ;;
        *) usage ;;
    esac
done

# 参数校验
check_pos_int "$CPU" "CPU进程数"
check_pos_int "$VM_COUNT" "内存进程数"
check_pos_int "$DURATION" "持续时间"
if [[ "$LOG_MODE" != "append" && "$LOG_MODE" != "overwrite" ]]; then
    echo -e "${RED}[ERROR] -o 仅支持 append / overwrite${NC}"
    exit 1
fi

# ====================== 依赖自动安装 ======================
if ! command -v stress &> /dev/null; then
    echo -e "${YELLOW}[INFO] 未检测stress，自动安装中...${NC}"
    sudo apt update -qq || { echo -e "${RED}[ERROR] apt更新失败，请检查网络${NC}"; exit 1; }
    sudo apt install stress -y -qq || { echo -e "${RED}[ERROR] stress安装失败${NC}"; exit 1; }
    echo -e "${GREEN}[INFO] stress 安装完成${NC}"
fi

# ====================== 内存占用风险校验 ======================
MEM_PER_MB=$(parse_mem_mb "$MEM")
TOTAL_VM_MB=$(( MEM_PER_MB * VM_COUNT ))
TOTAL_SYS_MB=$(free -m | awk '/^Mem:/{print $2}')

echo -e "${BLUE}[CALC] 系统总内存：${TOTAL_SYS_MB}MB，申请总内存：${TOTAL_VM_MB}MB${NC}"
if [[ $TOTAL_VM_MB -ge $TOTAL_SYS_MB ]]; then
    echo -e "${RED}[CRITICAL WARNING] 申请内存 ≥ 整机内存，执行会触发OOM宕机！${NC}"
    if [ -t 0 ]; then
        read -p "是否强行执行(y/N)：" confirm
    else
        confirm="n"
    fi
    if [[ "${confirm,,}" != "y" ]]; then
        echo -e "${BLUE}[INFO] 用户取消压测，脚本退出${NC}"
        exit 0
    fi
elif [[ $TOTAL_VM_MB -ge $(( TOTAL_SYS_MB - 1024 )) ]]; then
    echo -e "${YELLOW}[WARNING] 申请内存距离整机内存不足1G，存在OOM风险${NC}"
fi

# ====================== 日志初始化 ======================
LOG_DIR=$(dirname "$LOG_FILE")
mkdir -p "$LOG_DIR"
if [[ "$LOG_MODE" == "overwrite" && -f "$LOG_FILE" ]]; then
    mv "$LOG_FILE" "${LOG_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    echo -e "${BLUE}[INFO] 历史日志已备份为 .bak$(date +%Y%m%d%H%M%S)${NC}"
fi

# ====================== 干运行模式 ======================
if $DRY_RUN; then
    echo -e "${YELLOW}[DRY RUN] 配置检查通过，未实际执行压力测试。${NC}"
    echo "命令预览: stress --cpu $CPU --vm $VM_COUNT --vm-bytes $MEM --timeout $DURATION > $LOG_FILE"
    exit 0
fi

# ====================== 打印压测配置 ======================
echo "============================================="
echo "          CPU+内存压力测试配置"
echo "CPU进程数量：      $CPU 个"
echo "单进程内存规格：   $MEM"
echo "内存进程数量：     $VM_COUNT 个"
echo "预估总占用内存：   ${TOTAL_VM_MB}MB"
echo "压测持续时长：     ${DURATION}s"
echo "日志文件：         $LOG_FILE ($LOG_MODE)"
echo "============================================="

# ====================== 前台执行 stress ======================
echo -e "${BLUE}[INFO] 开始前台压测，按 Ctrl+C 可立即终止${NC}"
stress \
    --cpu "$CPU" \
    --vm "$VM_COUNT" --vm-bytes "$MEM" \
    --timeout "$DURATION" \
    > "$LOG_FILE" 2>&1

echo -e "${GREEN}[SUCCESS] 压力测试执行完毕！日志路径：$LOG_FILE${NC}"