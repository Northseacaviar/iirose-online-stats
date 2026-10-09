# iirose 在线状态监测器

周期采集 [iirose](https://www.iirose.com) 全站在线六项数据(online 总人数 / real 真人(去 A.I.) / chatting / active / away / heat 热度),存入本地 SQLite,并用本地 Web 仪表盘展示走势图(时间范围可选预设或自定义日期区间)。统计口径与网页终端 `stats` 指令同源。

## 目录结构

```
app/         本地端:采集 + SQLite + 仪表盘(自包含)
browser-js/  网页 JS:注入 iirose 页面上报 + 悬浮面板
deploy/      服务器用:Koishi 上报插件
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\pip install -r app\requirements.txt
# 在 app\config.yaml 填入 iirose 注册账号,然后:
.venv\Scripts\python app\run.py
```

浏览器打开 <http://127.0.0.1:8080> 查看走势图。详见 [app/README.md](app/README.md)。

## 两种取数方式

- 默认:采集器自己登录 WS 取数(`ws.enabled: true`)。
- 同一账号已由 Koishi 登录时:采集器设 `ws.enabled: false` 只收上报,数据由
  [deploy/](deploy/koishi-plugin-iirose-stats-reporter) 的 Koishi 插件取出后 POST 过来
  —— 同账号的第二个 WS 连接取不到站方数据。

## 测试

```bash
.venv\Scripts\python -m pytest app\tests
node browser-js\collector_js_test.js
node deploy\koishi-plugin-iirose-stats-reporter\test.js
```

## 作者

[@Northseacaviar](https://github.com/Northseacaviar)

