# BiliDown Studio 2.0 — Bilibili 媒体下载工作台

全新重构的现代化 Bilibili 媒体下载与管理工作台，采用模块化 Python 核心 (`bilidown/`) 与 **BiliDown Studio** 现代化交互界面，同时支持 **Windows 独立桌面应用模式**、**Linux / Cloudreve 网盘服务端模式** 与 **命令行模式 (CLI)**。

> 本项目仅用于个人学习和下载本人有权访问的内容。请遵守 Bilibili 用户协议及适用的著作权法律。请勿传播、出售或批量分发下载内容，也请勿将登录 Cookie 提交到公开仓库。

## 2.0 全新特性与重构亮点

### 界面与体验全面升级
- **现代 Studio 工作台界面**：深色午夜 / 浅色瓷白双主题切换、响应式侧边栏导航、实时速率折线图（Sparkline）、Toast 通知与拖拽链接解析。
- **独立桌面应用窗口**：运行 `python run_gui.py` 自动启动本地服务并以 Edge/Chrome 原生独立应用窗口（`--app`）打开，支持系统原生文件夹选择框、一键定位文件夹与本地播放。
- **内联选集与分P筛选器**：不再受限于狭小弹窗；支持关键词实时搜索、列表/网格视图切换、`Shift+点击` 连续范围勾选、范围表达式（`1-10, 15, 20-25`）以及百集课程分页渲染。
- **真正的多任务下载队列**：支持连续提交多个任务排队执行、批量多行粘贴链接一键入队、分集实时进度展开、实时下载速率/剩余时间/CDN 节点显示、以及**一键「重试失败项」**。
- **我的 B 站直连片库**：登录后直接在界面中浏览、搜索并批量下载你的**「稍后再看」**与**「我的收藏夹」**，无需逐条复制链接。
- **持久化下载历史**：基于 SQLite 自动归档任务记录、统计总下载体积，支持搜索、重新解析与一键打开目录。

### 下载内核增强与问题修复
- **全能链接解析**：支持 `BV` / `av` 单视频与多 P、**`b23.tv` 移动端短链与分享文案**、番剧 `ep` / `ss`、课程 `cheese` (`ep` / `ss`)、UP 主合集 (`collectiondetail` / `lists`)、系列合集 (`series_id`)、收藏夹 (`fid` / `ml`) 与稍后再看 (`toview`)。
- **WBI 签名支持**：自动获取并缓存 WBI `img_key/sub_key` 对高画质接口与 `x/player/wbi/v2` 字幕接口签名，显著提升 4K/HDR 与 AI 自动字幕获取成功率。
- **编码偏好与仅音频提取**：
  - 视频编码自由选择：`自动最佳` / `H.265 (HEVC)` / `H.264 (AVC)` / `AV1`
  - **仅音频模式**：支持 `M4A`（无损原流直出）、`MP3 (320kbps)`、`FLAC` 提取，并自动写入封面图与 ID3 元数据
  - **仅附加内容模式**：单独拉取封面、弹幕与字幕，不消耗视频流量
- **弹幕美学定制 & 色彩修复**：
  - 修复旧版 ASS 弹幕 RGB → BGR 通道顺序颠倒导致红蓝弹幕变色的问题
  - 修复旧版 `download_bangumi_episode` 未透传目标画质的问题
  - 修复新版二维码扫码状态码 `86090` 识别
  - 设置页内置**弹幕渲染实时预览画布**，可自由调节显示区域、不透明度、字号缩放与滚动速度
- **自定义文件命名模板**：支持 `{title}` `{index}` `{owner}` `{bvid}` `{quality}` 等占位符并实时预览。

## 项目结构

```text
bilibili-downloader/
├── bilidown/                  # 模块化核心包
│   ├── constants.py           # 画质/编码/音质映射与默认配置
│   ├── config.py              # 凭证(config.json)、用户偏好与 Cloudreve 配置
│   ├── wbi.py                 # Bilibili WBI 签名与缓存
│   ├── parser.py              # 链接/短链/范围/文件名模板解析
│   ├── api.py                 # B站 API 客户端（视频/番剧/课程/合集/收藏夹/稍后再看/登录）
│   ├── media.py               # 流选择（画质/编码/音质）与 FFmpeg 合并/音频提取
│   ├── danmaku.py             # 封面、XML 弹幕 -> ASS 转换、SRT 字幕与软封装
│   ├── engine.py              # 断点续传、智能 CDN 测速排序与下载/合并流水线
│   ├── cloudreve.py           # Cloudreve WebDAV 上传与连通性检测
│   ├── store.py               # SQLite 历史记录存储
│   ├── manager.py             # 多任务队列调度、遥测数据与失败重试
│   ├── launcher.py            # 桌面应用窗口启动器
│   ├── cli.py                 # 命令行入口
│   └── web/                   # 统一 Web Studio (Flask + 原生前端资源)
│       ├── app.py
│       ├── templates/index.html
│       └── static/{css/studio.css, js/studio.js}
├── run_gui.py / gui.py        # 桌面应用启动入口
├── bili_web.py                # 服务端 / Cloudreve Web 启动入口
├── bili_downloader.py         # 命令行入口 & 向后兼容核心门面
├── cloudreve_upload.py        # 向后兼容 WebDAV 导出
├── test_batch_download.py     # 原有 55 项兼容性测试
└── tests/test_bilidown_v2.py  # 2.0 新特性与 API 自动化测试
```

## 环境要求

- Python 3.10+
- FFmpeg（`ffmpeg` 与 `ffprobe` 在系统 `PATH` 中）

## 安装与启动

```bash
pip install -r requirements.txt

# 启动桌面应用（默认以独立应用窗口打开）
python run_gui.py

# 或在默认浏览器标签页中打开
python run_gui.py --browser

# 或直接作为 Web 服务运行（默认 127.0.0.1:8787）
python bili_web.py
```

## 命令行使用

```bash
# 二维码登录 / 检查登录状态
python bili_downloader.py --login
python bili_downloader.py --check-login

# 下载单视频（指定 4K + H.265 编码 + 封面/弹幕/字幕）
python bili_downloader.py BV1xx411c7mD -q 4K --codec hevc --cover --danmaku --subtitle

# 解析 b23.tv 短链并仅提取 MP3 音频
python bili_downloader.py "https://b23.tv/xxxxxx" --audio-only --audio-format mp3

# 批量下载课程/番剧/多P指定范围并开启 3 并发
python bili_downloader.py "https://www.bilibili.com/cheese/play/ep12345" --episodes "1-10,15" -w 3 --subtitle --danmaku

# 下载收藏夹或稍后再看
python bili_downloader.py "https://space.bilibili.com/12345/favlist?fid=67890" -q 1080P
python bili_downloader.py toview -q 1080P

# 直接从命令行拉起桌面 Studio
python bili_downloader.py --gui
```

## 测试与打包

```bash
# 运行全部兼容性与 2.0 自动化测试
python test_batch_download.py
pytest tests/

# PyInstaller 打包桌面可执行文件
pip install pyinstaller
pyinstaller --noconfirm --clean BiliDownloader.spec
```

## 许可

源代码采用 [MIT License](LICENSE) 发布。
