# iirose 在线人数监测 —— 浏览器侧采集脚本

注入 [iirose](https://www.iirose.com) 网页的小脚本,利用站点自带的「自定义 JS」功能运行,周期采集**全站**在线人数五类数据(online / chatting / active / away / entering),上报到本机采集服务,并附一个页面内悬浮监测面板。

- **采集上报**:与终端 `stats` 指令同一套公式,每 60 秒计算全站五项人数,POST 到本机采集服务(`http://127.0.0.1:8080/api/ingest`)
- **悬浮面板**:页面右下角按钮实时显示 Online 数;点击展开面板 —— 五项指标瓦片 + 轻量走势图(1h / 24h / 7d)+「完整仪表盘」链接;按住标题栏可拖动,右下角 ⤡ 可调大小,位置尺寸自动记忆;面板内滚轮手动滚动,不受站点滚轮逻辑干扰;历史曲线在**数据缺口处断开留白**,不跨缺口连线

脚本为**纯明文、无隐藏行为、不含任何账号信息**,可直接阅读审计。

## 安装(3 步)

1. iirose 网页内 `Ctrl+S` 打开内置终端
2. 输入 `js -s` 回车 —— 打开「自定义 JS」开关(输出 `Custom JS : 1` 即成功)
3. 输入 `js` 回车 —— 弹窗中粘贴脚本地址,确定

**托管地址**(二选一,都指向 `collector.js`):

```
https://cdn.jsdelivr.net/gh/Northseacaviar/iirose-online-stats@main/collector.js
https://raw.githubusercontent.com/Northseacaviar/iirose-online-stats/main/collector.js
```

> 也可以由你自己的本地采集服务托管:把 `collector.js` 放到该服务的静态目录下,粘贴 `http://127.0.0.1:8080/js/collector.js`。仓库作者的本地端程序即如此提供。

页面自动刷新后生效;以后每次刷新页面都会自动加载脚本,并弹提示框列出已加载的自定义脚本(站点自身的安全提示,可在此移除)。

**停用**:终端 `js -s` 关闭开关,或刷新后弹窗点移除。

## 依赖:本地采集服务

脚本把数据发到**你自己电脑**的 `127.0.0.1:8080`。没有该服务时:

- 悬浮按钮/五项瓦片**仍能实时显示**(数据来自页面自身),但历史曲线显示"暂无数据",数据不保存
- 用他人电脑运行本脚本 = 数据发到**他人电脑**的本机服务,与你的库无关

本仓库只含脚本。要获得"入库 + 历史曲线"的完整功能,需自建一个监听 `127.0.0.1:8080` 的服务,接口约定如下(作者完整本地端也遵循此契约):

| 接口 | 说明 |
|---|---|
| `POST /api/ingest` | 请求体 `{"online":n,"chatting":n,"active":n,"away":n,"entering":n}`(非负整数,时间戳由服务端补);返回 `{"ok":true,...}` |
| `GET /api/series?range=1h\|24h\|7d\|all` | 返回 `{"range":"…","samples":[{"ts","online","chatting","active","away","entering"},…],"interval_seconds":n}`(ts 升序;`interval_seconds` 为采样节拍,面板据此识别数据缺口) |

服务需对 POST 预检放行 CORS 与 Private Network Access(`Access-Control-Allow-Origin: *`、`Access-Control-Allow-Private-Network: true`),HTTPS 页面才能上报到本机 http 服务。

## 脚本内可调常量(改完刷新 iirose 页面即生效)

| 常量 | 默认 | 说明 |
|---|---|---|
| `INTERVAL_MS` | `60000` | 采集/上报间隔(毫秒) |
| `BTN_RIGHT` / `BTN_BOTTOM_PX` | `12px` / `140` | 悬浮按钮位置 |
| `ENDPOINT` / `SERIES_URL` / `DASHBOARD` | `http://127.0.0.1:8080/…` | 本机服务地址(一般不用改) |

控制台调试:`iiroseStats.now()` 立即算一次并上报;`iiroseStats.barCovered()` 诊断面板是否被站点元素遮挡;`iiroseStats.chartInfo()` 查看图表点数与缺口点数。

## 测试

```bash
node collector_js_test.js   # 分桶公式/上报载荷/悬浮面板(拖动/调大小/滚轮/图表/记忆),需 Node
```

## 技术说明

- 运行机制:站点终端 `js` 弹窗把地址存入 localStorage `extJs`,每次页面加载结束时站点客户端以 `<script>` 注入执行(站点仅过滤含 `.imoe.xyz/` 的地址);脚本直读页面上下文 `Objs.mapHolder.Assets.userJson`(与 `stats` 同源)
- 统计公式:用户记录字段 `[11]` 状态字符 —— `"5"~"9"` = chatting、`"0"~"4"` = active、`""` = away、`"*"` = entering;总数 = online
- 悬浮面板为原生 DOM(无 iframe),避免与站点自身的滚动/拖动逻辑冲突;针对站点可能的置顶遮挡层,面板在 window 捕获阶段按坐标接管点击/拖动/调大小/滚轮(pointer + mouse 双通道兜底)
- 自定义 JS 是站点客户端功能,站点随时可能收紧(现仅过滤 `.imoe.xyz/` 地址);脚本定位是"页面开着时顺手上报"的辅助采集
- 协议细节与实现思路见作者项目文档

## 隐私与安全

- 脚本**不含任何凭据**,数据只发往本机 `127.0.0.1:8080`,不出网
- 依赖站点自带的自定义 JS 机制运行,与浏览器扩展不同,无额外权限

## 许可证

暂未指定。如需使用,请先联系作者或关注后续更新。
