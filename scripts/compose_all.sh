#!/bin/bash

# COMPOSE 文件位置
LOG_COMPOSE="../docker/compose_log/compose_log.yml"
PROME_COMPOSE="../docker/compose_prome/compose_prome.yml"
# 项目名称
PROJ1="test_1"
PROJ2="test_2"

case "$1" in
up)
     echo "==================== 构建并后台启动 ${PROJ1}、${PROJ2} ===================="
    docker compose -f ${LOG_COMPOSE} -p ${PROJ1} up -d 
    docker compose -f ${PROME_COMPOSE} -p ${PROJ2} up -d 
    wait
    echo "✅ 所有容器建立完毕"
    docker ps -a
    ;;
down)
     echo "==================== 删除 ${PROJ1}、${PROJ2} ===================="
    docker compose -f ${LOG_COMPOSE} -p ${PROJ1} down
    docker compose -f ${PROME_COMPOSE} -p ${PROJ2} down
    wait
    echo "✅ 所有容器删除完毕"
    docker ps -a
    ;;
start)
    echo "==================== 启动项目 ${PROJ1}、${PROJ2} ===================="
    docker compose -p ${PROJ1} start &
    docker compose -p ${PROJ2} start &
    wait
    echo "✅ 所有容器服务启动完毕"
    docker ps -a
    ;;
stop)
     echo "==================== 停止容器 ${PROJ1}、${PROJ2} ===================="
    docker compose -p ${PROJ1} stop &
    docker compose -p ${PROJ2} stop &
    wait
    echo "✅ 所有容器已关闭"
    docker ps -a
    ;;
*)
    echo "❌ 参数错误！"
    echo "用法: $0 up|down|start|stop"
    exit 1
    ;;
esac
