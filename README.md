# iirose 在线状态监测器

周期采集 [iirose](https://www.iirose.com) 全站在线人数五类数据(online / chatting / active / away / entering),存入本地 SQLite,并用本地 Web 仪表盘展示走势图。统计口径与网页终端 `stats` 指令**同源**。

## 目录结构

```
iirose-stats/
├── browser-js/            ← 网页 JS(可单独分享)
│   ├── collector.js         浏览器侧上报脚本 + 悬浮窗面板
│   ├── collector_js_test.js node 测试
│   └── README.md            安装/分享说明
├── app/                   ← 本地端(采集 + 存储 + 仪表盘,自包含)
│   ├── run.py               入口
│   ├── start.bat            双击启动
│   ├── config.yaml          配置(含你的账号,勿外传)
│   ├── collector/           WS 客户端 / 用户列表 / stats 公式 / 采样 / 异常检测
│   ├── storage/             SQLite
│   ├── web/                 本地仪表盘(ECharts)+ API + 静态服务
│   ├── tests/               Python 测试
│   ├── docs/产品文档.md     调研与设计文档
│   ├── data/                数据文件(运行时)
│   └── logs/                日志(运行时)
└── .venv/                  Python 虚拟环境(不入分享)
```

- **本地端**(app/)是核心:无头 WS 采集器 7×24 运行,数据入库,仪表盘在 <http://127.0.0.1:8080>
- **网页 JS**(browser-js/)是备胎:注入 iirose 网页,页面开着时顺手上报 + 页面内悬浮面板;由本地端在 `http://127.0.0.1:8080/js/collector.js` 提供

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\pip install -r app\requirements.txt
# 在 app\config.yaml 填入 iirose 注册账号,然后:
.venv\Scripts\python app\run.py          # 或双击 app\start.bat
```

浏览器打开 <http://127.0.0.1:8080> 查看走势图。详见 [app/README.md](app/README.md)。

网页 JS 的注入与分享说明见 [browser-js/README.md](browser-js/README.md)。

## 测试

```bash
.venv\Scripts\python -m pytest app\tests -v   # Python 测试
node browser-js\collector_js_test.js           # 网页 JS 测试(需 Node)
```
