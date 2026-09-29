/**
 * iirose 全站在线指标上报(每 10 分钟一次)。
 *
 * 挂在 iirose 适配器的 bot.socket 上旁听原始消息(适配器自己也用
 * bot.socket.addEventListener('message', …) 收包,旁听不干扰它),识别出全站用户
 * 列表包后解析每段的第 [11] 位状态字符,算出六项指标 POST 给采集器。
 *
 * 帧格式:首字节 0x01 表示「其后为 zlib 压缩数据」,其余为 UTF-8 文本
 * (与适配器 utils/ws/message.ts 的处理一致)。
 *
 * 全站列表有两种形态:登录时的大包(%*"anime/…)与运行期的增量包
 * (经济数据前缀 + anime/…)。判据取「用户是否散落在 3 个以上房间」,
 * 以免把房间级列表当成全站。
 *
 * 未走 adapter 的 bot.getUserListFile():其落盘的 userlist.json 只保留
 * avatar/username/color/room/uid 五个字段,状态字符被丢弃,算不出
 * chatting/active/away/热度。
 *
 * 指标与 app/collector 同一套:online / real(总人数−A.I.) /
 * chatting / active / away / heat(Σ 状态分,机器人不贡献)。
 * 列表超过 STALE_MS 未更新则不上报 —— 宁可缺数,不报旧数。
 */
const zlib = require('node:zlib')

/** 适配器导出的底层发包函数(用于定期请求刷新;拿不到则跳过) */
let wsSend = null
try {
  wsSend = require('koishi-plugin-adapter-iirose').IIROSE_WSsend
} catch (err) {
  wsSend = null
}

const ENDPOINT = 'http://127.0.0.1:8080/api/ingest'
const INTERVAL_MS = 10 * 60 * 1000       // 上报间隔
const STALE_MS = 15 * 60 * 1000          // 列表超过这么久未更新则不上报
const MIN_LIST_ROWS = 20                 // 低于此行数的不当作全站列表
const MIN_ROOM_SPREAD = 3                // 用户需散落在这么多个房间才算全站
const HOOK_POLL_MS = 1000                // 等待 socket 出现
const FALLBACK_CHECK_MS = 2 * 60 * 1000  // 兜底检查周期
const REFRESH_MS = 10 * 60 * 1000        // 定期请求刷新
const FRAME_LOG_LIMIT = 40               // 前 N 帧打摘要,便于诊断

/** 状态字符 → 房间热度贡献分(与 app/collector/heat.py 同一份) */
const SCORE = {
  '9': 20, '8': 18, '7': 16, '6': 14, '5': 12,
  '4': 4, '3': 3.5, '2': 3, '1': 2.5, '0': 2,
  '': 0, '*': 1, 'a': 0,
}

/** 二进制/文本帧 → 字符串(首字节 0x01 的帧先解 zlib) */
function toText(data) {
  if (typeof data === 'string') return data
  if (data == null) return ''
  let buf
  try {
    if (Buffer.isBuffer(data)) buf = data
    else if (data instanceof ArrayBuffer) buf = Buffer.from(new Uint8Array(data))
    else if (ArrayBuffer.isView(data)) buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength)
    else buf = Buffer.from(data)
  } catch (err) {
    return ''
  }
  if (buf.length > 1 && buf[0] === 1) {
    try {
      return zlib.unzipSync(buf.subarray(1)).toString('utf-8')
    } catch (err) {
      return ''
    }
  }
  return buf.toString('utf-8')
}

/** 原始包 → 用户字段数组(段以 '<' 分隔,字段以 '>' 分隔,第 [11] 位是状态字符,[4] 是房间) */
function parseRows(raw) {
  const text = String(raw).trim().replace(/^[%*"']+/, '').replace(/["']+$/, '')
  const rows = []
  for (const segment of text.split('<')) {
    const fields = segment.split('>')
    if (fields.length < 12 || !fields[0].includes('/')) continue
    rows.push(fields)
  }
  return rows
}

/** 是否全站列表:行数够多,且用户散落在多个房间 */
function looksLikeFullList(rows) {
  if (rows.length < MIN_LIST_ROWS) return false
  const rooms = new Set()
  for (const fields of rows) rooms.add(String(fields[4] || ''))
  return rooms.size >= MIN_ROOM_SPREAD
}

/** 字段数组 → 六项指标 */
function statsOf(rows) {
  let ai = 0
  let chatting = 0
  let active = 0
  let away = 0
  let heat = 0
  for (const fields of rows) {
    const status = fields[11] == null ? '' : String(fields[11])
    if (status === 'a') { ai++; continue }        // 人工智能账户:不算真人,也不贡献热度
    if (status === '') away++
    else if ('56789'.includes(status)) chatting++
    else if ('01234'.includes(status)) active++
    const score = SCORE[status]
    if (typeof score === 'number') heat += score
  }
  return {
    online: rows.length,
    real: rows.length - ai,
    chatting,
    active,
    away,
    heat: Math.round(heat * 10) / 10,
  }
}

module.exports = {
  name: 'iirose-stats-reporter',
  // 纯函数单独导出,便于 test.js 直接测(不需要 Koishi 运行时)
  _internals: { toText, parseRows, looksLikeFullList, statsOf },
  apply(ctx) {
    const logger = ctx.logger('iirose-stats')
    let latestRows = null
    let latestAt = 0
    let lastReportedAt = 0
    let hookedSocket = null
    let stopPoll = null
    let framesLogged = 0
    let reporting = false

    /** 上报最近一次列表(列表过旧则跳过) */
    const report = async (reason) => {
      if (reporting) return
      if (!latestRows) {
        logger.warn('尚未旁听到用户列表,跳过上报(%s)', reason)
        return
      }
      const ageMin = Math.round((Date.now() - latestAt) / 60000)
      if (Date.now() - latestAt > STALE_MS) {
        logger.warn('最近一次用户列表已过 %d 分钟,不用它上报(宁可缺数不报旧数)', ageMin)
        return
      }
      reporting = true
      try {
        const stats = statsOf(latestRows)
        const resp = await fetch(ENDPOINT, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(stats),
        })
        const body = await resp.json().catch(() => ({}))
        lastReportedAt = Date.now()
        logger.info('上报(%s,列表 %d 分钟前) %j → HTTP %d %j',
          reason, ageMin, stats, resp.status, body)
      } catch (err) {
        logger.warn('上报失败: %s', (err && err.message) || err)
      } finally {
        reporting = false
      }
    }

    /** 到达间隔则上报一次 */
    const maybeReport = () => {
      if (Date.now() - lastReportedAt >= INTERVAL_MS - 60000) report('新列表')
    }

    // ---- 旁听:识别全站列表并解析(不抛出,避免影响适配器) ----
    const onMessage = (event) => {
      try {
        const text = toText(event && event.data)
        if (framesLogged < FRAME_LOG_LIMIT) {
          framesLogged++
          logger.info('帧#%d len=%d head=%s', framesLogged, text.length,
            text.slice(0, 60).replace(/[^\x20-\x7e\u4e00-\u9fff]/g, '.'))
        }
        if (typeof text !== 'string' || text.length < 500 || !text.includes('<')) return
        const rows = parseRows(text)
        if (framesLogged <= FRAME_LOG_LIMIT) logger.info('  ↳ 该帧解析出 %d 行用户', rows.length)
        if (!looksLikeFullList(rows)) return
        latestRows = rows
        latestAt = Date.now()
        logger.info('旁听到全站用户列表:%d 人(len=%d)', rows.length, text.length)
        maybeReport()
      } catch (err) {
        logger.debug('旁听解析失败(忽略): %s', (err && err.message) || err)
      }
    }

    /** 确保旁听器挂在当前 socket 上;返回可用的 bot(没有则 null) */
    const attach = () => {
      const bot = ctx.bots.find((b) => b.platform === 'iirose')
      const socket = bot && bot.socket
      if (!bot || !socket || typeof socket.addEventListener !== 'function') return null
      if (socket !== hookedSocket) {
        if (hookedSocket && typeof hookedSocket.removeEventListener === 'function') {
          hookedSocket.removeEventListener('message', onMessage)
        }
        socket.addEventListener('message', onMessage)
        hookedSocket = socket
        logger.info('已在 bot.socket 上挂好旁听器')
      }
      return bot
    }

    // 尽早挂上:站点在登录成功那一刻就下发全站大包
    stopPoll = ctx.setInterval(() => {
      if (attach() && stopPoll) {
        stopPoll()
        stopPoll = null
      }
    }, HOOK_POLL_MS)

    // 定期请求一次刷新
    ctx.setInterval(() => {
      try {
        const bot = attach()
        if (!bot) return
        if (typeof bot.internal?.requestUserList === 'function') bot.internal.requestUserList()
        else if (typeof wsSend === 'function') wsSend(bot, 'r2')
      } catch (err) {
        logger.debug('刷新请求失败(忽略): %s', (err && err.message) || err)
      }
    }, REFRESH_MS)

    // 兜底:避免错过一次列表导致整轮不上报
    ctx.setInterval(() => {
      if (Date.now() - lastReportedAt >= INTERVAL_MS + 60000) report('兜底')
    }, FALLBACK_CHECK_MS)

    logger.info('已装载:旁听全站用户列表并每 %d 分钟上报到 %s', INTERVAL_MS / 60000, ENDPOINT)
  },
}
