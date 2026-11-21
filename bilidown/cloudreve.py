"""Cloudreve WebDAV uploader with progress tracking, retries, and connection test."""

import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import requests


class ProgressReader:
    """Wrap a file object so requests can report streaming upload progress."""

    def __init__(
        self,
        fileobj,
        total: int,
        callback: Optional[Callable[[int, int], None]] = None,
        chunk_size: int = 1024 * 1024,
    ):
        self.fileobj = fileobj
        self.total = total
        self.callback = callback
        self.sent = 0
        self.chunk_size = chunk_size

    def read(self, size: int = -1):
        if size is None or size < 0:
            size = self.chunk_size
        data = self.fileobj.read(size)
        if data:
            self.sent += len(data)
            if self.callback:
                self.callback(self.sent, self.total)
        return data

    def __len__(self) -> int:
        return self.total


class CloudreveWebDAV:
    """Minimal, resilient WebDAV client for Cloudreve."""

    def __init__(self, base_url: str, username: str, password: str, timeout=(20, 900)):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.timeout = timeout

    def _url(self, remote_path: str, directory: bool = False) -> str:
        parts = [quote(part) for part in remote_path.strip("/").split("/") if part]
        url = self.base + "/" + "/".join(parts)
        if directory and not url.endswith("/"):
            url += "/"
        return url

    def test_connection(self) -> tuple[bool, str]:
        """Verify credentials & WebDAV endpoint accessibility."""
        try:
            resp = self.session.request(
                "PROPFIND",
                self.base + "/",
                headers={"Depth": "0"},
                timeout=(10, 15),
            )
            if resp.status_code in (200, 207):
                return True, "连接成功"
            if resp.status_code == 401:
                return False, "认证失败 (HTTP 401)，请检查用户名或密码"
            return False, f"服务返回 HTTP {resp.status_code}"
        except Exception as exc:
            return False, f"无法连接: {exc}"

    def ensure_dir(self, remote_dir: str) -> None:
        current = ""
        for part in [p for p in remote_dir.strip("/").split("/") if p]:
            current += "/" + part
            resp = self.session.request(
                "MKCOL",
                self._url(current, directory=True),
                timeout=self.timeout,
            )
            if resp.status_code not in (200, 201, 204, 405):
                raise RuntimeError(
                    f"创建网盘目录失败 {current}: HTTP {resp.status_code} {resp.text[:160]}"
                )

    def upload(
        self,
        local_path: Path,
        remote_dir: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        max_retries: int = 2,
    ) -> str:
        local_path = Path(local_path)
        self.ensure_dir(remote_dir)
        remote_path = remote_dir.rstrip("/") + "/" + local_path.name
        url = self._url(remote_path)
        size = local_path.stat().st_size

        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                with open(local_path, "rb") as fileobj:
                    reader = ProgressReader(fileobj, size, progress_callback)
                    resp = self.session.put(
                        url,
                        data=reader,
                        headers={"Content-Length": str(size)},
                        timeout=self.timeout,
                    )
                if resp.status_code in (200, 201, 204):
                    return remote_path
                last_err = RuntimeError(
                    f"上传失败 {local_path.name}: HTTP {resp.status_code} {resp.text[:200]}"
                )
            except requests.RequestException as exc:
                last_err = exc
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))

        raise last_err or RuntimeError(f"上传失败 {local_path.name}")

    def exists(self, remote_path: str) -> bool:
        resp = self.session.request("HEAD", self._url(remote_path), timeout=self.timeout)
        if resp.status_code == 200:
            return True
        resp = self.session.request(
            "PROPFIND",
            self._url(remote_path, directory=True),
            headers={"Depth": "0"},
            timeout=self.timeout,
        )
        return resp.status_code in (200, 207)

    def list_dir(self, remote_dir: str) -> str:
        resp = self.session.request(
            "PROPFIND",
            self._url(remote_dir, directory=True),
            headers={"Depth": "1"},
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 207):
            raise RuntimeError(f"列目录失败: HTTP {resp.status_code} {resp.text[:160]}")
        return resp.text
