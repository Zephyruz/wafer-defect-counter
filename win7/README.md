# Windows 7 64位离线安装说明

本说明适用于公司 Windows 7 64位电脑，使用 Python 3.8.10 和 `win7_offline_bundle/offline_packages` 中的离线依赖。

## 一、复制文件

将整个“自动计数”项目文件夹复制到公司电脑本地磁盘，不要直接在U盘中运行。项目中应包含：

```text
win7\requirements.txt
start_app.bat
win7_offline_bundle/
web_app.py
wafer_defect_counter.py
web_static/
```

## 二、安装 Python

运行：

```text
win7_offline_bundle\python-3.8.10-amd64.exe
```

勾选 `Add Python 3.8 to PATH`，然后点击 `Install Now`。

安装完成后打开 CMD，检查：

```cmd
python --version
```

应显示 `Python 3.8.10`。

## 三、进入项目文件夹

在资源管理器中打开“自动计数”文件夹，点击顶部地址栏，输入 `cmd` 并按回车。新窗口会自动进入正确目录。

## 四、推荐的一键配置方式

安装好 Python 后，直接双击：

```text
win7\setup.bat
```

它会自动创建独立的 `.venv_win7`、离线安装依赖、检查依赖一致性、实际加载图像组件并生成 HTTPS 证书。看到 `Setup completed successfully` 即配置完成，然后跳到“八、启动程序”。

以下“五、六”是需要手动配置或排查问题时使用的命令。

## 五、手动创建虚拟环境

```cmd
python -m venv .venv_win7
```

## 六、手动离线安装依赖

```cmd
.venv_win7\Scripts\python.exe -m pip install --no-index --find-links=win7_offline_bundle\offline_packages -r win7\requirements.txt
```

安装完成后检查全部主要依赖：

```cmd
.venv_win7\Scripts\python.exe -m pip check
.venv_win7\Scripts\python.exe -c "import cv2,numpy,matplotlib,PIL,cryptography; print('OK')"
```

如果显示 `OK`，说明依赖安装成功。

## 七、手动生成本机 HTTPS 证书

先让电脑连接现场实际使用的局域网，再执行：

```cmd
.venv_win7\Scripts\python.exe generate_https_cert.py
```

项目目录中会生成 `cert.pem` 和 `key.pem`。如果电脑 IP 或使用网络发生变化，可重新生成。

## 八、启动程序

以后直接双击：

```text
start_app.bat
```

也可以在 CMD 中运行：

```cmd
.venv_win7\Scripts\python.exe -u web_app.py
```

电脑浏览器访问：

```text
https://127.0.0.1:8765
```

PDA访问时，在电脑执行 `ipconfig`，找到当前网卡的 IPv4 地址，然后访问：

```text
https://<电脑IP>:8765
https://<电脑IP>:8765/live-capture
```

首次访问可能出现自签名证书警告，选择高级并继续访问。

## 常见问题

- Python 安装程序无法启动：确认系统为 Windows 7 SP1，并已安装必要的 Windows 系统更新。
- 依赖安装提示找不到包：确认使用的是 `win7/requirements.txt`，且 `win7_offline_bundle/offline_packages` 完整存在。
- 8765端口被占用：程序会自动改用8766端口，按窗口显示的网址访问。
- PDA无法访问：确认电脑和PDA在同一局域网，并允许 Python 通过 Windows 防火墙的专用网络。
- 网站使用期间必须保持服务窗口运行。
