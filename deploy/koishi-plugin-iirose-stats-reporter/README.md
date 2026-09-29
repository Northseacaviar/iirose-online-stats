# koishi-plugin-iirose-stats-reporter

在 Koishi 的 iirose 适配器进程内旁听全站用户列表,算出六项在线指标,每 10 分钟 POST 给采集器 `http://127.0.0.1:8080/api/ingest`。

同一账号已由 Koishi 登录时用这个插件取数(此时第二个 WS 连接取不到数据),采集器设 `ws.enabled: false`。

## 安装

复制本目录到 Koishi 的 `node_modules/`,在配置里启用:

    plugins:
      koishi-plugin-iirose-stats-reporter: {}

重启 Koishi 生效,日志前缀 `iirose-stats`。上报地址、间隔等常量在 `index.js` 顶部。

## 测试

```bash
node test.js
```
