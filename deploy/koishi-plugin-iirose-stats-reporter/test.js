/**
 * 纯函数测试:node test.js
 *
 * 只测不依赖 Koishi 运行时的部分(帧解码 / 包解析 / 全站判定 / 指标计算)。
 * 样本按站点真实格式构造:段以 '<' 分隔,字段以 '>' 分隔,
 * 第 [4] 位是房间、第 [11] 位是状态字符。
 */
const assert = require('node:assert')
const zlib = require('node:zlib')

const { toText, parseRows, looksLikeFullList, statsOf } = require('./index.js')._internals

let passed = 0
let failed = 0
function check(name, fn) {
  try {
    fn()
    passed++
    console.log('  ✓ ' + name)
  } catch (err) {
    failed++
    console.log('  ✗ ' + name + ' — ' + (err && err.message))
  }
}

/** 造一条用户记录;status 是第 [11] 位状态字符 */
function row(room, status, uid) {
  const f = ['anime/296', '2', 'someone', '241a22', room, 'n', 'a', '', uid, '0', '0', status, '0', '0', '']
  return f.join('>')
}

/** 造一个包:prefix + 若干记录以 '<' 相连 */
function packet(rows, prefix) {
  return (prefix || '%*"') + rows.join('<') + "'"
}

console.log('toText — 帧解码')
check('文本帧原样返回', () => {
  assert.strictEqual(toText('hello'), 'hello')
})
check('首字节 0x01 的帧按 zlib 解压', () => {
  const payload = Buffer.from('用户列表内容')
  const frame = Buffer.concat([Buffer.from([0x01]), zlib.deflateSync(payload)])
  assert.strictEqual(toText(frame), '用户列表内容')
})
check('首字节 0x01 且为 gzip 也能解', () => {
  const frame = Buffer.concat([Buffer.from([0x01]), zlib.gzipSync(Buffer.from('gz'))])
  assert.strictEqual(toText(frame), 'gz')
})
check('普通二进制帧按 UTF-8 读', () => {
  assert.strictEqual(toText(Buffer.from('>1002"23', 'utf-8')), '>1002"23')
})
check('ArrayBuffer 输入可用', () => {
  const buf = Buffer.from('abc')
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
  assert.strictEqual(toText(ab), 'abc')
})
check('坏帧不抛异常,返回空串', () => {
  const frame = Buffer.concat([Buffer.from([0x01]), Buffer.from('not-zlib')])
  assert.strictEqual(toText(frame), '')
})

console.log('parseRows — 包解析')
check('解析出记录并保留状态位与房间位', () => {
  const text = packet([row('r1', '5', 'u1'), row('r2', '', 'u2')])
  const rows = parseRows(text)
  assert.strictEqual(rows.length, 2)
  assert.strictEqual(rows[0][11], '5')
  assert.strictEqual(rows[1][11], '')
  assert.strictEqual(rows[1][4], 'r2')
})
check('丢掉字段不足或首段不含 / 的碎片', () => {
  const text = packet([row('r1', '5', 'u1')]) + '<' + 'garbage>1>2'
  assert.strictEqual(parseRows(text).length, 1)
})
check('空串返回空数组', () => {
  assert.deepStrictEqual(parseRows(''), [])
})

console.log('looksLikeFullList — 全站判定')
check('满 20 行且散落多房间 → 是全站', () => {
  const raw = []
  for (let i = 0; i < 24; i++) raw.push(row('room' + (i % 4), '5', 'u' + i))
  assert.strictEqual(looksLikeFullList(parseRows(packet(raw))), true)
})
check('行数不足 → 不是全站', () => {
  const rows = []
  for (let i = 0; i < 5; i++) rows.push(row('room' + i, '5', 'u' + i))
  assert.strictEqual(looksLikeFullList(rows), false)
})
check('全在同一房间 → 不是全站(房间级列表)', () => {
  const raw = []
  for (let i = 0; i < 30; i++) raw.push(row('same', '5', 'u' + i))
  assert.strictEqual(looksLikeFullList(parseRows(packet(raw))), false)
})

console.log('statsOf — 指标计算')
check('分桶 / 真人 / 热度逐项正确', () => {
  const text = packet([
    row('r1', '5', 'u1'),    // 聊天   12 分
    row('r1', '6', 'u2'),    // 聊天   14 分
    row('r2', '2', 'u3'),    // 活跃    3 分
    row('r2', '', 'u4'),     // 离开    0 分
    row('r3', 'a', 'u5'),    // A.I.   不计
    row('r3', '*', 'u6'),    // 刚进    1 分
  ])
  const s = statsOf(parseRows(text))
  assert.strictEqual(s.online, 6)
  assert.strictEqual(s.real, 5)          // 6 人减 1 个 A.I.
  assert.strictEqual(s.chatting, 2)
  assert.strictEqual(s.active, 1)
  assert.strictEqual(s.away, 1)
  assert.strictEqual(s.heat, 30)         // 12+14+3+0+1,A.I. 不贡献
})
check('空列表得到全 0', () => {
  const s = statsOf([])
  assert.deepStrictEqual(s, { online: 0, real: 0, chatting: 0, active: 0, away: 0, heat: 0 })
})

console.log('')
console.log(passed + ' 通过 / ' + failed + ' 失败')
process.exit(failed === 0 ? 0 : 1)
