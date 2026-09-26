# Wafer Defect Counter

A private, local-first tool for counting wafer defects from uploaded or camera-captured images. It provides automatic grid localization, OK/NG statistics, manual correction, and result export through a browser interface.

## Features

- Automatic wafer grid localization with manual four-point fallback
- Batch upload and continuous PDA/phone capture
- OK/NG counting, annotated images, and CSV export
- Manual NG correction and batch history
- Local HTTPS access on the same trusted network

## Installation

For deployment, use the matching package from GitHub Releases instead of cloning the source repository.

### Windows 10/11 64-bit

1. Download the full Windows 10/11 Auto Grid installer ZIP.
2. Extract it to an English-only path such as `C:\WaferCounter`.
3. Run `windows10\setup.bat` while connected to the internet.

### Windows 7 64-bit

1. Download the matching Windows 7 release package.
2. Extract the complete package locally.
3. Run `win7\setup.bat`.

## Run

Double-click `start_app.bat`, or run it from CMD:

```cmd
start_app.bat
```

Computer:

```text
https://127.0.0.1:8765
```

PDA or phone on the same network:

```text
https://<computer-ip>:8765
https://<computer-ip>:8765/live-capture
```

## Data and Safety

- Runtime data is stored in `web_data`; back it up before updating or removing the program.
- The service has no user login. Use it only on a trusted local network and never expose it directly to the public internet.
- Do not commit production images, result files, spreadsheets, certificates, private keys, virtual environments, or release archives.

## Main Source Files

- `wafer_defect_counter.py` — image processing and defect counting
- `web_app.py` — local web server and API
- `web_static/` — browser interface
- `generate_https_cert.py` — local HTTPS certificate generator
- `windows10/` and `win7/` — platform-specific setup files

---

# 晶圆缺陷自动计数

这是一个私有、本地运行的晶圆缺陷计数工具。程序通过浏览器完成图片上传或相机拍摄、自动网格定位、OK/NG 统计、人工修正和结果导出。

## 主要功能

- 自动定位晶圆网格，失败时可使用人工四点校准
- 支持批量上传以及 PDA/手机连续拍摄
- 自动统计 OK/NG，生成标注图和 CSV
- 支持人工修正 NG 和批次历史记录
- 在同一可信局域网内通过 HTTPS 使用

## 安装

部署时请使用 GitHub Releases 中对应的完整安装包，不要只下载源码。

### Windows 10/11 64 位

1. 下载 Windows 10/11 自动网格完整安装 ZIP。
2. 解压到纯英文路径，例如 `C:\WaferCounter`。
3. 保持联网，运行 `windows10\setup.bat`。

### Windows 7 64 位

1. 下载对应的 Windows 7 发布包。
2. 将完整安装包解压到本地磁盘。
3. 运行 `win7\setup.bat`。

## 启动

双击 `start_app.bat`，或在 CMD 中执行：

```cmd
start_app.bat
```

电脑访问地址：

```text
https://127.0.0.1:8765
```

同一网络中的 PDA 或手机访问：

```text
https://<电脑IP>:8765
https://<电脑IP>:8765/live-capture
```

## 数据与安全

- 运行数据保存在 `web_data`，更新或删除程序前必须备份。
- 程序没有用户登录功能，只能在可信局域网中使用，不要直接暴露到公网。
- 不要将生产图片、结果文件、表格、证书、私钥、虚拟环境或发布压缩包提交到 Git。

## 主要源码

- `wafer_defect_counter.py` — 图像处理和缺陷计数
- `web_app.py` — 本地网站和 API
- `web_static/` — 浏览器界面
- `generate_https_cert.py` — 本地 HTTPS 证书生成工具
- `windows10/` 和 `win7/` — 不同系统的安装脚本
