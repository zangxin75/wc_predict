"""webapp — 世界杯预测 FastAPI 前后端系统。

包初始化时把项目根目录(wc_predict/)加入 sys.path,使 webapp 内部模块能直接
import 根目录的引擎模块(compute_elo / wc_predict / tournament_sim 等),
无论 uvicorn 从哪个 cwd 启动都稳定。

自动从项目根目录加载 .env(如有),使 os.environ 中可用密钥/配置。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)   # wc_predict/
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── 加载 .env ────────────────────────────────────────────
_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass
