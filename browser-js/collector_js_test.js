/* collector.js 验证(node collector_js_test.js):
 * 分桶公式 + 上报载荷 + 原生悬浮面板(开关/拖动/调大小/滚轮/图表/范围切换/记忆)。
 * 需要 Node.js(项目开发机已装 v24)。
 */
'use strict';
const fs = require('fs');
const path = require('path');

let failed = 0;
function assert(cond, msg) {
    if (cond) { console.log('PASS ' + msg); }
    else { failed++; console.error('FAIL ' + msg); }
}

/* ---- 最小 DOM shim ---- */
let ctxCalls = 0;
const ctxMock = new Proxy({}, {
    get(t, p) {
        if (p === 'measureText') return () => ({ width: 10 });
        ctxCalls++;
        return () => {};
    },
    set() { return true; },
});
function makeEl(tag) {
    const node = {
        tagName: tag,
        style: {},
        children: [],
        parentNode: null,
        className: '',
        dataset: {},
        scrollTop: 0,
        appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
        removeChild(c) {
            const i = this.children.indexOf(c);
            if (i >= 0) this.children.splice(i, 1);
            c.parentNode = null;
        },
        addEventListener(ev, fn) { (this._listeners ||= {})[ev] = fn; },
        querySelectorAll(sel) {
            const cls = sel.trim().split(/\s+/).pop().slice(1);
            const out = [];
            const walk = (n) => {
                if ((n.className || '').split(/\s+/).includes(cls)) out.push(n);
                (n.children || []).forEach(walk);
            };
            walk(this);
            return out;
        },
    };
    Object.defineProperty(node, 'offsetWidth', { get() { return parseInt(this.style.width) || 0; } });
    Object.defineProperty(node, 'offsetHeight', { get() { return parseInt(this.style.height) || 0; } });
    Object.defineProperty(node, 'offsetLeft', { get() { return parseInt(this.style.left) || 0; } });
    Object.defineProperty(node, 'offsetTop', { get() { return parseInt(this.style.top) || 0; } });
    Object.defineProperty(node, 'clientWidth', { get() { return this.offsetWidth; } });
    Object.defineProperty(node, 'clientHeight', { get() { return this.offsetHeight; } });
    if (tag === 'canvas') node.getContext = () => ctxMock;
    return node;
}
const body = makeEl('body');
const docListeners = {};
global.document = {
    createElement: makeEl,
    body,
    readyState: 'complete',
    addEventListener(ev, fn) { docListeners[ev] = fn; },
    removeEventListener(ev) { delete docListeners[ev]; },
};
const storage = {};
global.localStorage = {
    getItem: (k) => (k in storage ? storage[k] : null),
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
};
global.ResizeObserver = class { constructor(fn) { this.fn = fn; } observe() {} };
global.setInterval = () => 0;

/* ---- 站点数据 mock ---- */
function makeRec(status) {
    const rec = ['cartoon/1', '0', 'user', 'user', 'room', '', '', '', 'uid', '', '0'];
    rec[11] = status;
    return rec;
}
const userJson = {
    a: makeRec('9'), b: makeRec('5'), c: makeRec('4'), d: makeRec('0'),
    e: makeRec(''), f: makeRec('*'), g: makeRec('a'), h: makeRec('8'),
};
let opened = false;
global.window = {
    Objs: { mapHolder: { Assets: { userJson } } },
    innerWidth: 1920,
    innerHeight: 1080,
    devicePixelRatio: 1,
    open: () => { opened = true; },
};
const posted = [];
const seriesUrls = [];
global.fetch = (url, opts) => {
    if (String(url).includes('/api/series')) {
        seriesUrls.push(String(url));
        return Promise.resolve({
            json: () => Promise.resolve({
                samples: [
                    { ts: '2026-09-17T10:00:00', online: 6, chatting: 2, active: 2, away: 1, entering: 1 },
                    { ts: '2026-09-17T10:01:00', online: 8, chatting: 3, active: 2, away: 1, entering: 1 },
                ],
            }),
        });
    }
    posted.push(JSON.parse(opts.body));
    return Promise.resolve({ ok: true });
};
global.console.warn = (m) => { throw new Error('unexpected warn: ' + m); };
const flush = () => new Promise((r) => setImmediate(r));

/* ---- 执行脚本 ---- */
const code = fs.readFileSync(path.join(__dirname, 'collector.js'), 'utf-8');
eval(code);

(async () => {
    /* 分桶与上报 */
    const got = window.iiroseStats.compute();
    assert(got.online === 8, 'online=8(含 AI 在内)');
    assert(got.chatting === 3, 'chatting=3(9/5/8)');
    assert(got.active === 2, 'active=2(4/0)');
    assert(got.away === 1, 'away=1(空状态)');
    assert(got.entering === 1, 'entering=1(*)');
    assert(posted.length === 1 && posted[0].online === 8, '首轮 tick 上报完整五桶');

    /* 按钮 */
    const btn = body.children[0];
    assert(!!btn && btn.textContent === '📊 8', '按钮创建并显示实时 Online 数');

    /* 打开面板:原生 DOM,无 iframe */
    btn._listeners.click();
    assert(body.children.length === 2, '点击出现面板');
    const panel = body.children[1];
    assert(!panel.children.some((c) => c.tagName === 'iframe'), '面板不再内嵌 iframe(原生 DOM)');
    assert(panel.style.left === '1188px' && panel.style.top === '324px', '默认位置右下角');
    assert(panel.style.width === '720px' && panel.style.height === '560px', '默认尺寸 720×560');
    assert(btn.textContent === '✕ 收起', '展开时按钮变为收起');
    await flush();
    const canvas = panel.children[4];
    assert(canvas.tagName === 'canvas' && canvas.width > 0 && ctxCalls > 10, '走势图已用 canvas 绘制');
    const pvs = panel.querySelectorAll('.pv');
    assert(pvs[0].textContent === '8', '瓦片值已更新(online=8)');
    assert(panel.children[3].children[0].children[2].textContent === '8', '图例显示最新值');

    /* 滚轮:capture + 手动滚动 */
    panel._listeners.wheel({ deltaY: 100, deltaMode: 0, preventDefault() { this.pd = true; }, stopPropagation() {} });
    assert(panel.scrollTop === 100, '滚轮手动滚动面板(deltaY 生效)');

    /* 拖动标题栏移动 */
    const bar = panel.children[0];
    bar._listeners.pointerdown({ clientX: 100, clientY: 100, preventDefault() {} });
    assert(!!docListeners.pointermove, '拖拽开始监听 pointermove');
    docListeners.pointermove({ clientX: 250, clientY: 150 });
    assert(panel.style.left === '1338px' && panel.style.top === '374px', '面板被拖动到新位置');
    docListeners.pointerup();
    assert(storage['iirose_stats_panel'] === '{"x":1338,"y":374,"w":720,"h":560}', '位置写入 localStorage');

    /* 右下角拖拽调大小(按当前位置钳制) */
    const grip = panel.children[6];
    grip._listeners.pointerdown({ clientX: 0, clientY: 0, preventDefault() {} });
    docListeners.pointermove({ clientX: 200, clientY: 150 });
    assert(panel.style.width === '570px' && panel.style.height === '646px', '拖拽调整大小(右缘不超视口)');
    docListeners.pointerup();

    /* 范围切换 */
    panel.children[2].children[0]._listeners.click();
    await flush();
    assert(seriesUrls.some((u) => u.includes('range=1h')), '点击 1h 切换图表范围');
    assert(seriesUrls.some((u) => u.includes('range=24h')), '打开面板默认拉取 24h');

    /* 完整仪表盘链接 */
    panel.children[0].children[1]._listeners.click({ stopPropagation() {} });
    assert(opened, '「完整仪表盘」新开标签页');

    /* 关闭 + 重开恢复位置尺寸 */
    panel.children[0].children[2]._listeners.click();
    assert(body.children.length === 1, '✕ 关闭面板');
    btn._listeners.click();
    const panel2 = body.children[1];
    assert(panel2.style.left === '1338px' && panel2.style.width === '570px', '重开面板恢复记忆的位置与尺寸');

    if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
    console.log('ALL PASS');
})();
