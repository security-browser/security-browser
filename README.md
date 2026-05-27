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

## 项目结构

```
.
├── start.sh          # 启动脚本
├── .venv/            # Python 虚拟环境（不含在仓库中）
└── gui/
    ├── main_window.py         # 主程序
    ├── camoufox_manager.ui    # UI 布局
    ├── dark.qss               # 主题样式
    └── profiles/              # 各账号数据（运行后自动创建）
```

## 依赖项目

- [daijro/camoufox](https://github.com/daijro/camoufox) — 反指纹 Firefox 引擎
- [TechQaiser/camoufox-profile-manager](https://github.com/TechQaiser/camoufox-profile-manager) — GUI 基础（已修改适配 Linux）
