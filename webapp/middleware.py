"""webapp/middleware.py — 安全响应头中间件 (F-09 修复)

为所有响应附加 CSP / X-Content-Type-Options / X-Frame-Options /
Referrer-Policy / Permissions-Policy。
"""
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """在最外层注入安全响应头。

    CSP 允许:
      - self(同源脚本/样式/图片/连接)
      - js.stripe.com(Stripe Elements,未来兼容)
      - hooks.stripe.com / api.stripe.com(Stripe 网络调用)
      - data: / https: 图片(允许 ESM CDN 图标)
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://js.stripe.com https://static.cloudflareinsights.com 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "frame-src https://js.stripe.com https://hooks.stripe.com; "
            "connect-src 'self' https://api.stripe.com https://ipapi.co https://cloudflareinsights.com; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CF 边缘缓存:回源链路慢(海外节点→国内源站 10s+),公开页面/联赛数据
        # 让边缘扛 2-10 分钟,命中后访客不再等回源。whoami/登录/refresh 不在
        # 这些路径里,不会被缓存。
        path = request.url.path
        if request.method == "GET":
            if path.startswith("/static/"):
                # 带版本号 query 的静态资源可长缓存;HTML 页面本身不缓存
                response.headers["Cache-Control"] = "public, max-age=604800"
            elif path in ("/", "/wc", "/leagues"):
                response.headers.setdefault("Cache-Control", "public, max-age=60, s-maxage=600")
            elif path.startswith("/api/leagues"):
                response.headers.setdefault("Cache-Control", "public, s-maxage=120")
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=(), payment=()"
        )
        # HSTS: 1 年 + 子域。HTTPS-only 部署时 nginx 也应加,这里是双保险。
        # dev(http)下浏览器会忽略,但不影响功能。
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response