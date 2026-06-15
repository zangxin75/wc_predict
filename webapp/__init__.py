"""webapp — 世界杯预测 FastAPI 前后端系统。

包初始化时把项目根目录(wc_predict/)加入 sys.path,使 webapp 内部模块能直接
import 根目录的引擎模块(compute_elo / wc_predict / tournament_sim 等),
无论 uvicorn 从哪个 cwd 启动都稳定。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)   # wc_predict/
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
