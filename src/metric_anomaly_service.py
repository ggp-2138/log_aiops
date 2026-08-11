from fastapi import FastAPI, Header, HTTPException  # Web框架
from pydantic import BaseModel, Field  # 数据验证和设置管理
import os
import pandas as pd
import numpy as np

# Web 服务的核心入口类,用于创建应用实例、注册路由、处理请求
app = FastAPI()


# 定义请求数据模型
# 继承 BaseModel 类
class LatencyData(BaseModel):
    # 类型声明(输入长度限制)
    # ...  是 Ellipsis（省略号）对象,代表该字段没有默认值、是必填项
    values: list[float] = Field(..., min_length=5, max_length=2000)


# 注册路由
@app.post("/detect")
# LatencyData(请求体): 接收调用方传入的时延数据
# authorization: 从请求头中自动提取 Authorization 字段,默认值为 None
def detect(data: LatencyData, authorization: str = Header(None)):
    series = pd.Series(data.values)

    # Token 校验
    # 从环境变量 API_TOKEN 中读取预设令牌（默认兜底值为 my-secret-token）
    # 校验请求头中的令牌格式是否为 Bearer 令牌值，和预设值是否一致
    # 校验不通过则抛出 HTTPException，返回 HTTP 403 状态码和「无效令牌」提示，拒绝接口访问
    if authorization != f"Bearer {os.getenv('API_TOKEN', 'my-secret-token')}":
        raise HTTPException(status_code=403, detail="Invalid token")

    # 保证样本量足够（减少MAD失真)
    if len(series) < 5:
        return {"anomalies": [], "message": "Need at least 5 data points"}

    # 计算序列中位数
    median = series.median()

    # MAD算法相较于 3-sigma算法 不受极端大延迟干扰(中位数对极值不敏感)
    # 绝对偏差的中位数
    # 相比标准差，MAD 不受极端异常值影响
    mad = np.median(np.abs(series - median))

    # σ≈1.4826*MAD
    # 0.6745*(x−med)     x-med
    # ————————————— =  —————————
    #     MAD          1.4826*MAD
    # 避免延迟相同 mad 为 0
    if mad == 0:
        mad = 1e-6
    modified_z_scores = 0.6745 * (series - median) / mad
    is_anomaly = modified_z_scores > 3  # 只检测 > 3σ 的延迟

    anomalies = [
        {"index": i, "value": v}
        for i, (v, flag) in enumerate(zip(data.values, is_anomaly))
        if flag
    ]
    return {"anomalies": anomalies}
