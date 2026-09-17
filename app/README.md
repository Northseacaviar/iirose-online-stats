# 本地端(采集 + 存储 + 仪表盘)

周期采集 [iirose](https://www.iirose.com) 全站在线人数五类数据(online / chatting / active / away / entering),存入本地 SQLite,并用本地 Web 仪表盘展示走势图。

- 与网页终端 `stats` 指令**同源的全站统计**(同一套分组公式,本地计算)
- **默认每 60 秒采样一次**,间隔可配置
- 采集器为轻量后台进程(无浏览器),仪表盘为本地网页(localhost)
- 入库前做异常检测,剔除偏离自身近 5 分钟均值过大的样本(见 config.yaml `anomaly`)

## 运行

在**项目根目录**(本文件夹的上一级)执行:

```bash
python -m venv .venv
.venv\Scripts\pip install -r app\requirements.txt
.venv\Scripts\python app\run.py        # 或双击 app\start.bat
```

浏览器打开 <http://127.0.0.1:8080> 查看走势图。

- 图表:5 条曲线 + 图例 + 十字线提示 + 最新值端标签;时间范围 1 小时 / 24 小时 / 7 天 / 全部;深浅主题跟随系统,右上角可手动切换;页面底部有数据明细表
- 数据文件:`data/iirose_stats.db`(SQLite,`samples` 表)
- 日志:`logs/collector.log`(控制台同步输出)

## 配置(config.yaml)

| 项 | 说明 |
|---|---|
| `interval_seconds` | 采样间隔秒数,默认 60 |
| `http.host` / `http.port` | 仪表盘监听地址与端口 |
| `ws.hosts` / `ws.port` | WebSocket 主机列表与端口(线上 = wss 443) |
| `account.username` / `password` | **登录账号**(必填,仅存本机;统计是全站的,任意账号/房间均可) |
| `account.room` | 登录后进入的房间(空间站 `5ce6a4b520a90` 恒可用) |
| `database` | SQLite 文件路径 |
| `anomaly` | 异常检测:见下 |

**异常检测**:入库前剔除偏离自身近 `window_seconds`(默认 300 秒)内均值过大的样本。每项指标 `|偏差| > max(abs, rel × 均值)` 记一次违规,**≥2 项指标同时违规才剔除整条**(单指标偶发抖动如 entering 尖峰属正常)。基线窗口内样本不足 `min_samples`(默认 3)条时放行(冷启动/断连恢复后首条不被误杀)。应对场景:浏览器脚本更新瞬间用户列表只加载了一部分、站点断连恢复等造成的瞬时异常数据。剔除的样本记入日志(WARNING),不落库。

## 浏览器侧上报(可选备胎采集)

利用站点自带的「自定义 JS」功能,把你平时开着的 iirose 网页也变成一个采集器:页面里的脚本每 60 秒算一次全站人数 POST 到本机。WS 采集被踢下线/协议变动时,数据仍不断。与 WS 采样同秒冲突时保留 WS 值(不覆盖)。

脚本本体在 `../browser-js/collector.js`,本服务提供两个等价地址(推荐前者):

- `http://127.0.0.1:8080/js/collector.js`
- `http://127.0.0.1:8080/static/collector.js`(旧地址,兼容保留)

**开启步骤**(本程序需保持运行):

1. iirose 网页内 `Ctrl+S` 打开内置终端
2. 输入 `js -s` 回车 —— 打开「自定义 JS」开关(输出 `Custom JS : 1` 即成功)
3. 输入 `js` 回车 —— 弹窗中粘贴脚本地址,确定
4. 页面自动刷新后脚本生效;之后每次刷新页面会自动加载脚本,并弹提示框列出已加载的自定义脚本(可在此移除)

**停用**:终端 `js -s` 关闭开关,或刷新后弹窗点移除。

悬浮窗、面板交互与脚本配置见 [../browser-js/README.md](../browser-js/README.md)。

## 工作原理

1. 以配置的账号登录 `wss://m.iirose.com:443`(md5 密码,与网页登录一致),服务器下发 `%` 全站快照(所有房间的所有用户)
2. 客户端维护用户列表(快照 + u11/u10 增量事件),按网页终端 `stats` 指令的同一套公式本地计算五项指标(状态字符 `5~9`=Chatting、`0~4`=Active、空=Away、`*`=Entering,总人数=Online)
3. 采样器按间隔入库(断连期间留空);仪表盘通过 API 读取绘制

协议细节与调研结论见 [docs/产品文档.md](docs/产品文档.md)。

## 测试

```bash
# 项目根目录:
.venv\Scripts\python -m pytest app\tests -v
node browser-js\collector_js_test.js   # 网页 JS 测试(需 Node)
```

- 单元:协议分帧/快照解析/登录错误识别、stats 公式
- 集成:本地 mock WS 服务器回放握手→快照→增量事件→采样入库全链路
- 上报:ingest API 写入/校验、CORS+PNA 预检头、同秒不覆盖、异常样本剔除
- 异常检测:基线不足放行/正常通过/系统性崩塌剔除/单指标尖峰放行/自定义阈值

## 已知事项

- 游客(未注册)账号无法通过纯 WS 连接创建(需走网页"新的开始"流程,且网页有机器人网关);用任意注册账号即可,已实测可用
- 账号与浏览器里的网页登录可共存(与 iirosebot 机器人框架同款用法)
- 自定义 JS 是站点客户端功能,站点随时可能收紧(现仅过滤 `.imoe.xyz/` 地址);这正是把它定位为备胎而非主采集的原因
- **分享注意**:本文件夹含 config.yaml(账号凭据)与 data/(你的数据),不要直接外传
