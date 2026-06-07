# Security Browser

基于 [Camoufox](https://github.com/daijro/camoufox) 的开源多账号指纹浏览器管理器，支持同时运行多个隔离的浏览器 Profile，每个 Profile 拥有独立的 Cookie、指纹和代理 IP。

## 功能

- 多账号同时运行，互相隔离
- 每个 Profile 独立代理配置（HTTP / SOCKS5）
- GeoIP 自动匹配时区、语言
- 指纹伪造：Canvas、WebGL、User-Agent、屏幕分辨率等
- Profile 数据持久化存储

## 环境要求

- Ubuntu（带桌面）
- Python 3.9+

## 安装

```bash
# 1. 安装系统依赖
sudo apt-get install -y libxcb-xinerama0

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install 'camoufox[geoip]' PyQt5

# 3. 下载 Camoufox 浏览器
python -m camoufox fetch
```

## 启动

```bash
./start.sh
```

## 使用

1. 点击 **New Profile** 创建账号
2. 填写代理（Host / Port / 用户名 / 密码）
3. 勾选 **Use GeoIP** 自动匹配时区
4. 点击 **Save** 保存
5. 点击 **Launch** 启动该账号的浏览器窗口
6. 重复以上步骤可同时开启多个账号

运行中的账号在列表显示绿色 `▶` 标记。

## Gemini 自动化引擎

应用启动时会附带启动一个 **Gemini 自动化引擎**（后台线程），通过驱动已登录的 Camoufox
Profile 中真实的 Gemini 网页界面来完成文生图 / 图生图 / 文生视频 / 视频编辑。`gemini-genmedia`
服务把生成请求转发到本引擎的作业 HTTP API。

- **账号池**：`gui/gemini_pool.json`（`[{profile, slot, enabled}]`）。缺省时自动取
  `profiles.json` 中所有邮箱（`@`）Profile，按 `slot=0`（`/u/0`）启用。请求按 round-robin
  分发；空闲窗口被复用，需要的 Profile 会自动启动（可见窗口）。
- **作业 API**（缺省 `http://127.0.0.1:8090`，`GEMINI_ENGINE_PORT` 可改）：
  - `POST /v1/jobs` `{type:"image"|"video"|"dump", prompt, input_media:[{data,media_type}], account?}` → `{job_id, status}`
  - `GET  /v1/jobs/{job_id}` → 作业状态：`pending | running | needs_verification | completed | failed`
  - `GET  /v1/pool` · `GET /health`
- **人机验证**：出现验证挑战时作业进入 `needs_verification`，在可见窗口中人工完成后自动继续。
- **媒体目录**：下载的媒体保存到 `GEMINI_MEDIA_DIR`（缺省 `/tmp/gemini_media`），与
  `gemini-genmedia` 共享。
- **选择器**：Gemini 的 DOM 选择器集中在 `gui/gemini_selectors.py`，是唯一易随官方改版失效的
  部分。失效时发一个 `{"type":"dump"}` 作业，引擎会把页面 HTML + 截图保存到媒体目录，据此更新选择器。

## 项目结构

```
.
├── start.sh          # 启动脚本
├── .venv/            # Python 虚拟环境（不含在仓库中）
└── gui/
    ├── main_window.py         # 主程序（含 CamoufoxWorker 作业队列）
    ├── camoufox_manager.ui    # UI 布局
    ├── dark.qss               # 主题样式
    ├── gemini_engine.py       # 账号池 + round-robin 调度
    ├── gemini_api.py          # 作业 HTTP API（stdlib，独立线程）
    ├── automation.py          # Gemini 网页 UI 自动化流程
    ├── gemini_selectors.py    # Gemini DOM 选择器（易失效，集中维护）
    ├── gemini_job.py          # Job 模型 + 作业存储
    ├── gemini_pool.json       # 账号池配置
    └── profiles/              # 各账号数据（运行后自动创建）
```

## 依赖项目

- [daijro/camoufox](https://github.com/daijro/camoufox) — 反指纹 Firefox 引擎
- [TechQaiser/camoufox-profile-manager](https://github.com/TechQaiser/camoufox-profile-manager) — GUI 基础（已修改适配 Linux）
