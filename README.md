# BiliDown - Bilibili 视频下载器

基于 Python 与 CustomTkinter 的桌面下载工具，支持管理和下载当前账号可访问的 Bilibili 视频、番剧、课程与合集内容。

> 本项目仅用于个人学习和下载本人有权访问的内容。请遵守 Bilibili
> 用户协议及适用的著作权法律。请勿传播、出售或批量分发下载内容，
> 也请勿将登录 Cookie 提交到公开仓库。

## 功能

- 单视频下载（支持分P）
- 番剧/课程选集批量下载
- 大型剧集分页选择、范围选择与当前集定位
- 有限并发下载（1 至 4 个任务）与批量任务进度统计
- 合集下载（series_id）
- 画质选择：240P ~ 8K、HDR、杜比视界
- 音视频自动合并（需 FFmpeg）
- 封面/弹幕/字幕下载
- 断点续传
- **二维码登录**（手机 App 扫码，自动保存完整 Cookie）
- **浏览器 Cookie 导入**（课程已购授权异常时使用）
- **登录状态检查**

## 环境要求

- Python 3.10+
- FFmpeg，且 `ffmpeg` 可在命令行中执行

## 安装与启动

```bash
pip install -r requirements.txt
python run_gui.py
```

## 图形界面使用

1. 启动程序后，点击右上角登录并使用 Bilibili App 扫码。
2. 粘贴视频、课程单集或番剧单集链接，点击“获取信息”。
3. 对课程或番剧，可点击“选择集数”，使用分页、范围选择或快捷按钮选择任务。
4. 需要批量下载时设置并发数；默认同时下载 2 集。
5. 点击“开始下载”，程序会下载音视频流并使用 FFmpeg 合并。

课程或番剧的选择列表从输入的单集 `ep` 链接所属系列自动获取。

## 命令行使用

```bash
# 二维码登录 (推荐)
python bili_downloader.py --login

# 检查登录状态
python bili_downloader.py --check-login

# 下载单视频
python bili_downloader.py BV1xx411c7mD

# 指定画质
python bili_downloader.py "https://www.bilibili.com/video/BV1xx411c7mD" -q 4K

# 下载番剧单集（ep 链接只下载该集）
python bili_downloader.py "https://www.bilibili.com/bangumi/play/ep12345"

# 下载番剧整季（ss 链接下载全部集数，文件名自动加集号前缀）
python bili_downloader.py "https://www.bilibili.com/bangumi/play/ss12345"

# 下载指定分P + 封面 + 弹幕 + 字幕
python bili_downloader.py BV1xx411c7mD -p 1 --cover --danmaku --subtitle

# 直接设置 SESSDATA 登录凭证
python bili_downloader.py BV1xx411c7mD --sessdata "your_sessdata_here"

# 导入浏览器完整 Cookie（网页登录会话鉴权异常时使用）
python bili_downloader.py "https://www.bilibili.com/cheese/play/ep12345" --cookie "SESSDATA=...; DedeUserID=...; bili_jct=..."
```

## 画质等级

8K > 杜比视界 > HDR > 4K > 1080P60 > 1080P+ > 1080P > 720P60 > 720P > 480P > 360P > 240P

> 高清画质需要登录（SESSDATA）和大会员权限。

## 登录会话提示

部分内容接口可能需要比 `SESSDATA` 更多的登录 Cookie。二维码登录会在本地保存登录凭证；如果网页端可播放但程序仍提示无权限，可从浏览器开发者工具的 `Network` 请求头中复制完整 `Cookie`，并在图形界面点击“导入Cookie”。

## 安全说明

- `config.json` 包含账号登录 Cookie，必须保持在本机且不得上传。
- `downloads/` 中的视频或其他下载内容不得随源码发布。
- 仓库已通过 `.gitignore` 排除凭证、下载内容和本地构建产物。

## 测试

```bash
python test_batch_download.py
```

测试覆盖选集范围解析、课程/番剧集合数据、批量下载、分页状态、有限并发调度、停止处理与 Cookie 配置兼容性。

## 打包

```bash
pip install pyinstaller
pyinstaller --noconfirm --clean BiliDownloader.spec
```

构建产物位于 `dist/`，默认不提交到源码仓库。

## 许可

源代码采用 [MIT License](LICENSE) 发布。此许可不授予任何视频、封面、字幕或其他第三方内容的再分发权利。
