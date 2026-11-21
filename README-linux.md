# BiliDown Studio 2.0 — Linux / Cloudreve 部署说明

## 网盘内入口

Cloudreve 侧边栏底部「B站视频下载」按钮指向：

- 地址：`https://<your-domain>/api/bili/`
- 服务监听 `127.0.0.1:8787`，由 Caddy 反向代理并强制 HTTPS
- 前端所有静态资源（`static/...`）与接口（`api/...`）均使用相对路径，原生兼容 `/api/bili/` 子路径部署。

当服务端目录存在 `cloudreve_config.json` 时，`bili_web.py` 会自动进入 **Cloudreve 服务器模式**：
- 自动禁用本地文件夹弹窗与 Explorer 打开接口（防止安全风险）；
- 下载前自动检测服务器磁盘剩余空间（低于 2GB 自动拦截提示）；
- 支持多任务排队下载、实时速率折线图、分集进度与一键重试失败集数；
- 下载完成后自动将视频及同名 `.srt` 外挂字幕上传至 Cloudreve（供 Cloudreve 网页播放器自动加载），并默认清理服务器临时缓存目录 `work/<任务ID>/`。

## 关键文件

| 文件 / 目录 | 作用 |
| --- | --- |
| `bilidown/` | 核心下载包与 Studio Web 前端/后端 |
| `bili_web.py` | Waitress / Flask Web 入口 |
| `bili_downloader.py` | 命令行与兼容门面 |
| `cloudreve_upload.py` | Cloudreve WebDAV 兼容导出 |
| `config.json` | B站登录 Cookie（权限 600） |
| `cloudreve_config.json` | Cloudreve WebDAV 地址/账号/默认目录（权限 600） |
| `bilidown_data/` | 偏好设置（`settings.json`）与历史库（`history.db`） |
| `work/` | 下载临时目录，成功上传后自动清理 |

## 服务管理

```bash
sudo systemctl status bili-web
sudo systemctl restart bili-web
sudo journalctl -u bili-web -f
```

## 更新部署到服务器

```bash
scp -r -i ~/.ssh/id_rsa bilidown bili_downloader.py bili_web.py cloudreve_upload.py \
    ubuntu@<your-server-ip>:/home/ubuntu/bili-downloader/
ssh -i ~/.ssh/id_rsa ubuntu@<your-server-ip> "sudo systemctl restart bili-web"
```
