# koishi-plugin-iirose-stats-reporter

在 Koishi 的 iirose 适配器进程内旁听全站用户列表包,算出六项在线指标,
每 10 分钟 POST 一次给同机的采集器 `http://127.0.0.1:8080/api/ingest`。

## 什么时候需要它

采集器本身能用独立 WS 连接取数(见 `app/collector`)。但同一账号已由 Koishi
登录时,站方对第二个连接不下发业务数据 —— 此时改由本插件借适配器现有连接取数,
采集器侧设 `ws.enabled: false` 只收上报。

## 安装

把本目录整个复制到 Koishi 的 `node_modules/` 下:

    cp -r koishi-plugin-iirose-stats-reporter /srv/koishi/node_modules/

在 Koishi 配置里启用(或用控制台安装本插件):

    plugins:
      koishi-plugin-iirose-stats-reporter: {}

重启 Koishi 生效。日志前缀为 `iirose-stats`。

## 可调常量(index.js 顶部)

| 常量 | 默认 | 含义 |
| --- | --- | --- |
| `ENDPOINT` | `http://127.0.0.1:8080/api/ingest` | 采集器上报地址 |
| `INTERVAL_MS` | 10 分钟 | 上报间隔 |
| `STALE_MS` | 15 分钟 | 列表超过这么久未更新则不上报 |
| `MIN_ROOM_SPREAD` | 3 | 用户需散落这么多个房间才算全站列表 |

## 依赖的事实(改代码前先看)

- 帧格式:首字节 `0x01` 表示其后为 zlib 压缩数据,其余按 UTF-8 文本处理
  (同适配器 `utils/ws/message.ts`)。
- 全站列表有两种形态:登录大包(`%*"anime/…`)与运行期增量包(经济数据前缀 + `anime/…`);
  判据取「用户散落房间数」,以免把房间级列表当成全站。
- 用户记录以 `<` 分段、`>` 分字段:第 `[11]` 位是状态字符,第 `[4]` 位是房间。
- 不用适配器的 `bot.getUserListFile()`:它落盘的 `userlist.json` 只保留
  avatar/username/color/room/uid,状态字符被丢弃。
