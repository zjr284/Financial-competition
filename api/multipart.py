"""multipart/form-data 解析（标准库实现，无第三方 Web 框架依赖）。

POST /documents 需要接收 file、source_url、disclosure_date 三个 part。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MultipartForm:
    fields: dict[str, str] = field(default_factory=dict)
    files: dict[str, tuple[str, str, bytes]] = field(default_factory=dict)  # name -> (filename, content_type, payload)

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)


_BOUNDARY = re.compile(rb'boundary="?([^";]+)"?')
_DISPOSITION = re.compile(rb'name="([^"]*)"(?:;\s*filename="([^"]*)")?', re.IGNORECASE)


class MultipartError(ValueError):
    pass


def parse_boundary(content_type: str) -> bytes:
    if "multipart/form-data" not in content_type.lower():
        raise MultipartError("Content-Type 必须是 multipart/form-data")
    match = _BOUNDARY.search(content_type.encode("latin-1"))
    if not match:
        raise MultipartError("缺少 multipart boundary")
    return match.group(1)


def parse_multipart(payload: bytes, content_type: str) -> MultipartForm:
    boundary = parse_boundary(content_type)
    delimiter = b"--" + boundary
    form = MultipartForm()

    if delimiter not in payload:
        raise MultipartError("请求体与 boundary 不匹配")
    body = payload.split(delimiter)[1:]
    for raw_part in body:
        if raw_part.startswith(b"--"):
            break  # 结束标记
        part = raw_part.lstrip(b"\r\n")
        split = part.find(b"\r\n\r\n")
        if split == -1:
            continue
        headers = part[:split]
        content = part[split + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]

        disposition = _DISPOSITION.search(headers)
        if not disposition:
            continue
        name = (disposition.group(1) or b"").decode("utf-8", errors="replace")
        filename = disposition.group(2)
        content_type_match = re.search(rb"content-type:\s*([^\r\n]+)", headers, re.IGNORECASE)
        media_type = content_type_match.group(1).decode("latin-1").strip() if content_type_match else "application/octet-stream"

        if filename is None:
            form.fields[name] = content.decode("utf-8", errors="replace").strip()
        else:
            form.files[name] = (filename.decode("utf-8", errors="replace"), media_type, content)
    return form
