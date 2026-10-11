"""api：HTTP 路由、请求校验与错误响应（实现 contracts/openapi.yaml）。"""

from api.server import ApiServer, ApiService, serve

__all__ = ["ApiServer", "ApiService", "serve"]
