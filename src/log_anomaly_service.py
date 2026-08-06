import os
import config
import pandas as pd
import numpy as np
from fastapi import FastAPI, Header, HTTPException
from typing_extensions import Self
from pydantic import BaseModel, Field, model_validator

app = FastAPI()

# 请求体
class LogFrequencyData(BaseModel):
    # ...  是 Ellipsis 对象,代表该字段没有默认值、是必填项
    # timestamps: 时间戳列表
    timestamps: list[str] = Field(..., min_length=5, max_length=2000)
    # values: 对应时间点的日志模板频次
    values: list[list[int]] = Field(..., min_length=1, max_length=2000)
    # 模板索引ID
    template_ids: list[int] = Field(..., min_length=1)
    # after: 先执行 Field 校验，再执行该校验
    # 检验日志模板的频次数组长度和时间戳列表长度是否相等
    @model_validator(mode='after')
    def check_lengths(self) -> Self:
        ts = self.timestamps
        vals = self.values
        n = len(ts)
        for i, seq in enumerate(vals):
            if len(seq) != n:
                raise ValueError(f'模板序列长度不一致: 期望 {n}，实际第 {i} 个序列长度为 {len(seq)}')
        return self

@app.post("/detect/log")
# authorization: 从 data 请求头中提取 Authorization 字段,默认值为 None
# data: LogFrequencyData：将前端 JSON 请求体校验、解析为 Python 对象
def detect_log_anomaly(data: LogFrequencyData, authorization: str = Header(None)):
    # Token 鉴权
    # 从环境变量 API_TOKEN 中读取预设令牌（默认值为 my-secret-token）
    # 校验请求头中的令牌格式是否为 Bearer 令牌值，和预设值是否一致
    # 校验不通过则抛出 HTTPException，返回 HTTP 403 状态码和「无效令牌」提示，拒绝接口访问
    if authorization != f"Bearer {config.API_TOKEN}":
        raise HTTPException(status_code=403, detail="Invalid token")
    # 保证样本量足够（减少MAD失真)
    if len(data.timestamps) < 5:
        return {"anomalies": [], "message": "Need at least 5 data points"}

    timestamps = data.timestamps
    freq_matrix = data.values
    anomalies = []

    if not freq_matrix:
        return {"anomalies": [], "message": "Empty data"}
    # σ≈1.4826*MAD
    # 0.6745*(x−med)     x-med
    # ————————————— =  —————————
    #     MAD          1.4826*MAD
    # MAD 异常检测
    for idx, freq_list in enumerate(freq_matrix):
        template_id=data.template_ids[idx]      # 使用正确的模板索引ID(避免首个模板丢失)
        series = pd.Series(freq_list)
        median = series.median()
        mad = np.median(np.abs(series - median))
        # 避免同一日志模板不同时间频次相同而报错
        if mad == 0:
            mad = 1e-6
        modified_z_scores = 0.6745 * (series - median) / mad
        # 只检测频次突增
        is_anomaly = modified_z_scores > 3
        for i, flag in enumerate(is_anomaly):

            if flag:
                anomalies.append({
                    "timestamp": timestamps[i],
                    "template_id": template_id,
                    "frequency": freq_list[i]
                })
    # print(is_anomaly)
    return {"anomalies": anomalies}
