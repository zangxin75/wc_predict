"""pytest 配置:把 wc_predict/ 根目录加入 sys.path,让测试能 import 各模块。"""

import os
import sys

# conftest.py 在 tests/,往上一级是 wc_predict/ 根(各 .py 模块所在)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def pytest_configure(config):
    """注册自定义 marker,避免 PytestUnknownMarkWarning。"""
    config.addinivalue_line(
        "markers",
        "slow: 真实数据慢测试(全量拟合/回测),用 `pytest -m 'not slow'` 跳过",
    )


def pytest_collection_modifyitems(config, items):
    """默认跳过 @slow 测试,除非用 -m slow 显式要求。"""
    if config.getoption("-m") == "":
        skip_slow = pytest.mark.skip(reason="慢测试,用 -m slow 单独跑")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)


import pytest  # noqa: E402 (pytest_collection_modifyitems 需要)

