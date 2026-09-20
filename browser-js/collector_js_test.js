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
        click() { const fn = this._listeners && this._listeners.click; if (fn) fn({ stopPropagation() {} }); },
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
        getBoundingClientRect() {
            const w = parseInt(this.style.width) || 0;
            const h = parseInt(this.style.height) || 0;
            let l = 0, t = 0;
            if (this.style.left) l = parseInt(this.style.left);
            else if (this.style.right) l = 1920 - parseInt(this.style.right) - w;
            if (this.style.top) t = parseInt(this.style.top);
            else if (this.style.bottom) t = 1080 - parseInt(this.style.bottom) - h;
            return { left: l, top: t, right: l + w, bottom: t + h, width: w, height: h };
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
    topEl: null, // 测试:elementFromPoint 返回的最顶层元素(模拟站点遮挡层)
    elementFromPoint() { return this.topEl; },
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
/* window 捕获/冒泡监听表(键 = 事件名 + ':c' 表示捕获) */
const winListeners = {};
global.window.addEventListener = (ev, fn, capOrOpts) => {
    const cap = typeof capOrOpts === 'object' ? !!capOrOpts.capture : !!capOrOpts;
    (winListeners[ev + (cap ? ':c' : '')] ||= []).push(fn);
};
global.window.removeEventListener = (ev, fn, capOrOpts) => {
    const cap = typeof capOrOpts === 'object' ? !!capOrOpts.capture : !!capOrOpts;
    const k = ev + (cap ? ':c' : '');
    if (winListeners[k]) winListeners[k] = winListeners[k].filter((f) => f !== fn);
};
const fireWin = (ev, props = {}) => {
    (winListeners[ev + ':c'] || []).slice().forEach((f) => f({ type: ev, preventDefault() {}, stopPropagation() {}, ...props }));
};
const hasWin = (ev) => (winListeners[ev + ':c'] || []).length > 0;
const posted = [];
const seriesUrls = [];
let mockSeries = {
    interval_seconds: 60,
    samples: [
        { ts: '2026-09-17T10:00:00', online: 6, chatting: 2, active: 2, away: 1, entering: 1 },
        { ts: '2026-09-17T10:01:00', online: 8, chatting: 3, active: 2, away: 1, entering: 1 },
    ],
};
global.fetch = (url, opts) => {
    if (String(url).includes('/api/series')) {
        seriesUrls.push(String(url));
        return Promise.resolve({ json: () => Promise.resolve(mockSeries) });
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

    /* 拖动标题栏移动(移动/抬起监听在 window 捕获阶段,站点拦截不住) */
    const bar = panel.children[0];
    bar._listeners.pointerdown({ clientX: 100, clientY: 100, preventDefault() {} });
    assert(hasWin('pointermove'), '拖拽开始监听 window 捕获 pointermove');
    fireWin('pointermove', { clientX: 250, clientY: 150 });
    assert(panel.style.left === '1338px' && panel.style.top === '374px', '面板被拖动到新位置');
    fireWin('pointerup');
    assert(storage['iirose_stats_panel'] === '{"x":1338,"y":374,"w":720,"h":560}', '位置写入 localStorage');

    /* 右下角拖拽调大小(按当前位置钳制) */
    const grip = panel.children[6];
    grip._listeners.pointerdown({ clientX: 0, clientY: 0, preventDefault() {} });
    fireWin('pointermove', { clientX: 200, clientY: 150 });
    assert(panel.style.width === '570px' && panel.style.height === '646px', '拖拽调整大小(右缘不超视口)');
    fireWin('pointerup');

    /* 鼠标通道兜底:站点拦截 pointer 事件时,仅 mousedown/mousemove 也能拖动 */
    bar._listeners.mousedown({ clientX: 100, clientY: 100, preventDefault() {} });
    assert(hasWin('mousemove'), '鼠标通道:mousedown 启动拖动');
    fireWin('mousemove', { clientX: 300, clientY: 200 });
    assert(panel.style.left === '1538px' && panel.style.top === '474px', '鼠标通道:面板被拖动到新位置');
    fireWin('mouseup');
    assert(JSON.parse(storage['iirose_stats_panel']).x === 1538, '鼠标通道:位置写入 localStorage');

    /* data-nodrag 守卫:按下关闭钮/链接不启动拖动 */
    bar._listeners.pointerdown({ target: panel.children[0].children[2], clientX: 0, clientY: 0, preventDefault() {} });
    assert(!hasWin('pointermove'), '关闭钮按下不启动拖动');

    /* 遮挡防护:站点置顶层盖在面板上时,window 捕获阶段按坐标接管 */
    const overlay = makeEl('div');
    overlay.className = 'site-mask';
    document.topEl = overlay;
    // shim 无布局引擎:手动给标题栏/关闭钮摆放面板内坐标(面板当前 1538,474 / 570×646)
    bar.style.left = '1538px'; bar.style.top = '474px'; bar.style.width = '200px'; bar.style.height = '34px';
    const closeBtn = bar.children[2];
    closeBtn.style.left = '1700px'; closeBtn.style.top = '478px'; closeBtn.style.width = '20px'; closeBtn.style.height = '20px';

    fireWin('pointerdown', { clientX: 1550, clientY: 485 }); // 标题栏空白区
    assert(hasWin('pointermove'), '遮挡下按下标题栏仍启动拖动');
    fireWin('pointermove', { clientX: 1600, clientY: 535 });
    assert(panel.style.left === '1588px' && panel.style.top === '524px', '遮挡下拖动面板生效');
    fireWin('pointerup', { clientX: 1600, clientY: 535 });
    // 拖动后同步模拟布局坐标
    bar.style.left = '1588px'; bar.style.top = '524px';
    closeBtn.style.left = '1750px'; closeBtn.style.top = '528px';

    fireWin('pointerdown', { clientX: 1760, clientY: 538 }); // 关闭钮
    fireWin('pointerup', { clientX: 1760, clientY: 538 });
    assert(body.children.length === 1, '遮挡下点击关闭钮仍能关闭面板');

    // 遮挡下点击悬浮按钮开关面板(按钮位置模拟在面板上方空白处)
    btn._listeners.click(); // 常规路径重开
    btn.style.left = '1800px'; btn.style.top = '400px'; btn.style.width = '60px'; btn.style.height = '30px';
    fireWin('pointerdown', { clientX: 1830, clientY: 415 });
    fireWin('pointerup', { clientX: 1830, clientY: 415 });
    assert(body.children.length === 1, '遮挡下点击悬浮按钮仍能开关面板');

    // 遮挡下滚轮仍能滚动面板
    btn._listeners.click(); // 重开
    const panel3 = body.children[1];
    fireWin('wheel', { clientX: 1650, clientY: 600, deltaY: 100, deltaMode: 0 });
    assert(panel3.scrollTop === 100, '遮挡下滚轮仍能滚动面板');
    document.topEl = null;

    /* 范围切换 */
    panel.children[2].children[0]._listeners.click();
    await flush();
    assert(seriesUrls.some((u) => u.includes('range=1h')), '点击 1h 切换图表范围');
    assert(seriesUrls.some((u) => u.includes('range=24h')), '打开面板默认拉取 24h');
    panel.children[2].children[5]._listeners.click();
    await flush();
    assert(seriesUrls.some((u) => u.includes('range=all')), '点击全部切换图表范围');

    /* 图表缺口:固定时间窗口内,前导/中间/尾部缺失时段都补空点(曲线断开、时间轴不压缩) */
    const realNow = Date.now;
    const NOW_MS = 1800000000000; // 固定时钟:断言可精确计算补点数量
    Date.now = () => NOW_MS;
    const pad2 = (n) => (n < 10 ? '0' : '') + n;
    const fmtMs = (ms) => {
        const d = new Date(ms);
        return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
            'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    };
    mockSeries = {
        interval_seconds: 60,
        samples: [
            { ts: fmtMs(NOW_MS - 6 * 60000), online: 100, chatting: 10, active: 20, away: 50, entering: 2 },
            { ts: fmtMs(NOW_MS), online: 110, chatting: 12, active: 22, away: 55, entering: 2 },
        ],
    };
    panel.children[2].children[3]._listeners.click(); // 点 24h 重新拉取
    await flush();
    const info = window.iiroseStats.chartInfo();
    const lead = (24 * 3600 - 6 * 60) / 60; // 24h 窗口内首条样本前的空点 = 1434
    assert(info.points === lead + 2 + 5, `6 分钟缺口补 5 空点,窗口前导补 ${lead} 空点(2 实)`);
    assert(info.gaps === lead + 5, '空点标记为 null,曲线在缺口处断开');
    mockSeries = {
        interval_seconds: 60,
        samples: [
            { ts: fmtMs(NOW_MS - 60000), online: 100, chatting: 10, active: 20, away: 50, entering: 2 },
            { ts: fmtMs(NOW_MS), online: 110, chatting: 12, active: 22, away: 55, entering: 2 },
        ],
    };
    panel.children[2].children[0]._listeners.click(); // 点 1h 重新拉取
    await flush();
    const info1h = window.iiroseStats.chartInfo();
    const lead1h = (3600 - 60) / 60; // 1h 窗口前导 = 59
    assert(info1h.gaps === lead1h, `连续数据只补窗口前导空点(${lead1h}),不补中间空点`);
    Date.now = realNow;

    /* 完整仪表盘链接 */
    panel.children[0].children[1]._listeners.click({ stopPropagation() {} });
    assert(opened, '「完整仪表盘」新开标签页');

    /* 关闭 + 重开恢复位置尺寸 */
    panel.children[0].children[2]._listeners.click();
    assert(body.children.length === 1, '✕ 关闭面板');
    btn._listeners.click();
    const panel2 = body.children[1];
    assert(panel2.style.left === '1588px' && panel2.style.width === '570px', '重开面板恢复记忆的位置与尺寸');

    if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
    console.log('ALL PASS');
})();
