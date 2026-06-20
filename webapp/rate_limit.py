"""webapp/rate_limit.py — slowapi 速率限制单例 (F-06 修复)

统一配置:IP 维度 + (部分路由) email 维度。
中间件层:SlowAPIMiddleware 在 Starlette 中间件栈内,作为最外层之一。
装饰器:在路由函数上加 @limiter.limit("N/period"),需要函数接受 request: Request。
"""
from slowapi import Limiter
from slowapi.util import get_remote_address


def _key_func(request):
    """IP 维度 key。生产可改为 X-Forwarded-For (需 nginx trust)。"""
    return get_remote_address(request)


limiter = Limiter(
    key_func=_key_func,
    default_limits=["120/minute"],  # 全局兜底,装饰器叠加更严
    storage_uri="memory://",        # in-memory;多进程部署需换 redis
)