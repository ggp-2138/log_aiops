#!/bin/bash
export PATH=$PATH:/usr/bin

########################## 配置区 ##########################
CONTAINER_NAME="mysql-container"   # 修改为你的mysql容器名称
MYSQL_USER="root"
MYSQL_PWD="你的mysql密码"
DATABASES="aiops_log_db"           # 需要备份库，多个空格分隔
BACKUP_DIR="../data/mysql_backup"
RETENTION_DAYS=7
DATE=$(date +%Y%m%d_%H%M%S)
###########################################################

mkdir -p ${BACKUP_DIR}
echo "========== MySQL容器备份开始 ${DATE} =========="

for DB in ${DATABASES};
do
    BACKUP_FILE="${BACKUP_DIR}/${DB}_${DATE}.sql.gz"
    
    docker exec ${CONTAINER_NAME} mysqldump -u${MYSQL_USER} -p${MYSQL_PWD} --single-transaction ${DB} | gzip > ${BACKUP_FILE}

    if [ $? -eq 0 ];then
        echo "[OK] ${DB} 备份成功：${BACKUP_FILE}"
    else
        echo "[ERROR] ${DB} 备份失败！"
    fi
done

# 清理过期备份
find ${BACKUP_DIR} -name "*.sql.gz" -mtime +${RETENTION_DAYS} -delete
echo "已清理${RETENTION_DAYS}天前备份文件"
echo "========== 备份任务结束 ==========
"