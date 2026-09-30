# 本地端(采集 + 存储 + 仪表盘)

采集 iirose 全站在线六项数据存入本地 SQLite,并用本地网页展示走势图。

- 与网页终端 `stats` 指令同一套分组公式,本地计算;热度 = Σ 各用户状态贡献分(机器人 `a` 不贡献),真人 = 总人数 − A.I. 数
- 默认 600 秒采样一次
- 停摆期间不补数:缺失时段图表留断口
- 点图例可隐藏/显示任意一条线(选择记在浏览器本地,刷新仍是隐藏的)

## 运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r app\requirements.txt
.venv\Scripts\python app\run.py        # 或双击 app\start.bat
```

浏览器打开 <http://127.0.0.1:8080>。数据在 `data/iirose_stats.db`(`samples` 表),日志在 `logs/collector.log`。

## 配置(app/config.yaml)

| 项 | 说明 |
| --- | --- |
| `interval_seconds` | 采样间隔秒数,默认 600 |
| `http.host` / `http.port` | 仪表盘监听地址与端口 |
| `ws.enabled` | `true` = 本机 WS 采集;`false` = 只接收上报(不登录 WS) |
| `ws.hosts` / `ws.port` | WebSocket 主机与端口 |
| `account.username` / `password` | 登录账号(必填,仅存本机;统计是全站的,任意账号房间均可) |
| `account.room` | 登录后进入的房间 |
| `database` | SQLite 文件路径 |
| `anomaly` | 入库前剔除偏离近 1 小时均值过大的样本 |

密码可用环境变量 `IIROSE_PASSWORD` 覆盖,config.yaml 里不必存明文。

## 测试

```bash
.venv\Scripts\python -m pytest app\tests
```

## 注意

本目录含账号凭据(config.yaml)与数据(data/),不要直接外传。
