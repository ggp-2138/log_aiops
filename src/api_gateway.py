from fastapi import FastAPI
import requests

app = FastAPI()

@app.post("/diagnose")
def unified_diagnosis(alert: dict):
    # 调用项目一 MAD 检测接口
    metric_result = requests.post("http://localhost:8000/detect", json=alert).json()
    # 调用项目二日志检测接口
    log_result = requests.post("http://localhost:8001/detect/log", json=alert).json()
    # 合并结果返回
    return {"metric_anomaly": metric_result, "log_anomaly": log_result}