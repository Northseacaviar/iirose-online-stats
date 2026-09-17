/* iirose 在线人数监测 —— 浏览器侧上报(备胎采集器)+ 悬浮窗面板
 *
 * 经站点自带「自定义 JS」功能注入(终端 js -s 开开关,js 弹窗粘贴本文件地址,
 * 或直接粘贴本文件全文)。每次页面加载自动执行。
 *
 * 采集逻辑与终端 stats 指令完全一致(对照解码客户端 tools(1)):
 * 遍历 Objs.mapHolder.Assets.userJson,按记录 [11] 状态字符分桶,
 * 每 60 秒把 5 项人数 POST 到本机采集服务(与 WS 采集共用同一后端与库)。
 *
 * 悬浮窗(全原生 DOM,不嵌 iframe —— 页面更轻,滚动不受站点干扰):
 * - 右下角圆角按钮实时显示 Online 数,点击开/关面板
 * - 面板:5 项指标瓦片 + 轻量自绘走势图(1h/24h/7d 切换)+「完整仪表盘」链接
 * - 按住标题栏拖动移动;右下角 ⤡ 拖拽调大小;位置与尺寸记忆(localStorage)
 * - 滚轮在面板内手动滚动(capture + preventDefault,不受站点任何滚轮逻辑影响)
 */
(function () {
    'use strict';
    if (window.__IIROSE_STATS_COLLECTOR__) return;
    window.__IIROSE_STATS_COLLECTOR__ = true;

    var ENDPOINT = 'http://127.0.0.1:8080/api/ingest';
    var SERIES_URL = 'http://127.0.0.1:8080/api/series?range=';
    var DASHBOARD = 'http://127.0.0.1:8080/';
    var INTERVAL_MS = 60 * 1000;

    /* ===== 外观/行为配置 ===== */
    var BTN_RIGHT = '12px';
    var BTN_BOTTOM_PX = 140;
    var Z = '2147483000';
    var PANEL_KEY = 'iirose_stats_panel';      // {x,y,w,h}
    var KEYS = ['online', 'chatting', 'active', 'away', 'entering'];
    var LABELS = ['Online', 'Chatting', 'Active', 'Away', 'Entering'];
    var COLORS = ['#3987e5', '#68b26d', '#f89323', '#f04747', '#a0a0a0'];  // 与仪表盘深色调色板一致

    /* ===== 与 stats tools(1) 相同的分桶:d[""]=10 d["*"]=11 d["a"]=12,数字状态直接作下标 ===== */
    function compute() {
        var A = window.Objs && window.Objs.mapHolder && window.Objs.mapHolder.Assets;
        if (!A || !A.userJson) return null;
        var d = [];
        var online = 0;
        for (var k in A.userJson) {
            var rec = A.userJson[k];
            if (!Array.isArray(rec)) continue;
            online++;
            var st = rec[11] == null ? '' : rec[11];
            var idx = st === '' ? 10 : st === '*' ? 11 : st === 'a' ? 12 : st;
            d[idx] = (d[idx] || 0) + 1;
        }
        var sum = function (from, to) {
            var s = 0;
            for (var i = from; i <= to; i++) s += d[i] || 0;
            return s;
        };
        return {
            online: online,
            chatting: sum(5, 9),
            active: sum(0, 4),
            away: d[10] || 0,
            entering: d[11] || 0,
        };
    }

    /* ===== 悬浮按钮 ===== */
    var btn = null;
    var panel = null;
    var chartCanvas = null;
    var chartData = null;
    var chartRange = '24h';
    var legendVals = [];

    function buildButton() {
        btn = document.createElement('div');
        btn.style.cssText =
            'position:fixed;right:' + BTN_RIGHT + ';bottom:' + BTN_BOTTOM_PX + 'px;z-index:' + Z + ';' +
            'display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;' +
            'background:rgba(18,18,22,.88);color:#eee;font:600 13px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif;' +
            'box-shadow:0 2px 12px rgba(0,0,0,.5);cursor:pointer;user-select:none;' +
            'border:1px solid rgba(255,255,255,.16);';
        btn.textContent = '📊 —';
        btn.title = '点击打开/关闭监测面板';
        btn.addEventListener('click', togglePanel);
        document.body.appendChild(btn);
    }

    /* ===== 面板:位置/尺寸记忆与钳制 ===== */
    function loadPanelState() {
        var st = { w: 720, h: 560, x: null, y: null };
        try {
            var saved = JSON.parse(localStorage.getItem(PANEL_KEY) || 'null');
            if (saved && saved.w >= 360 && saved.h >= 280) {
                st.w = Math.min(saved.w, window.innerWidth - 24);
                st.h = Math.min(saved.h, window.innerHeight - 220);
                if (typeof saved.x === 'number' && typeof saved.y === 'number') {
                    st.x = Math.max(0, Math.min(saved.x, window.innerWidth - 200));
                    st.y = Math.max(0, Math.min(saved.y, window.innerHeight - 100));
                }
            }
        } catch (e) {}
        if (st.x === null) st.x = window.innerWidth - st.w - 12;
        if (st.y === null) st.y = window.innerHeight - st.h - BTN_BOTTOM_PX - 56;
        if (st.y < 0) st.y = 0;
        return st;
    }

    function savePanelState() {
        if (!panel) return;
        try {
            localStorage.setItem(PANEL_KEY, JSON.stringify({
                x: panel.offsetLeft, y: panel.offsetTop,
                w: panel.offsetWidth, h: panel.offsetHeight,
            }));
        } catch (e) {}
    }

    /* ===== 面板构建 ===== */
    function openPanel() {
        if (panel) return;
        var st = loadPanelState();
        panel = document.createElement('div');
        panel.style.cssText =
            'position:fixed;z-index:' + Z + ';background:#1a1a19;' +
            'border:1px solid rgba(255,255,255,.12);border-radius:12px;' +
            'box-shadow:0 8px 40px rgba(0,0,0,.6);overflow-y:auto;overflow-x:hidden;' +
            'display:flex;flex-direction:column;' +
            'font:13px system-ui,-apple-system,"Segoe UI",sans-serif;color:#eee;';
        panel.style.left = st.x + 'px';
        panel.style.top = st.y + 'px';
        panel.style.width = st.w + 'px';
        panel.style.height = st.h + 'px';

        /* 标题栏:空白区可拖动移动 */
        var bar = document.createElement('div');
        bar.style.cssText =
            'flex:none;display:flex;align-items:center;gap:10px;' +
            'padding:7px 12px;background:rgba(255,255,255,.05);cursor:move;user-select:none;';
        var title = document.createElement('span');
        title.textContent = '📊 iirose 在线状态监测';
        title.style.cssText = 'font:12px system-ui,sans-serif;color:#c3c2b7;';
        var full = document.createElement('span');
        full.textContent = '🖥 完整仪表盘';
        full.title = '在新标签页打开完整仪表盘';
        full.style.cssText = 'cursor:pointer;color:#8ab4f8;font:12px system-ui,sans-serif;';
        full.addEventListener('click', function (e) {
            e.stopPropagation();
            window.open(DASHBOARD, '_blank');
        });
        full.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
        var close = document.createElement('span');
        close.textContent = '✕';
        close.title = '关闭面板';
        close.style.cssText =
            'margin-left:auto;cursor:pointer;color:#c3c2b7;font:13px system-ui,sans-serif;' +
            'padding:2px 8px;border-radius:5px;';
        close.addEventListener('mouseenter', function () { close.style.background = 'rgba(255,255,255,.12)'; });
        close.addEventListener('mouseleave', function () { close.style.background = ''; });
        close.addEventListener('click', closePanel);
        close.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
        bar.appendChild(title);
        bar.appendChild(full);
        bar.appendChild(close);
        bar.addEventListener('pointerdown', startDrag);

        /* 指标瓦片 */
        var tiles = document.createElement('div');
        tiles.style.cssText =
            'flex:none;display:grid;grid-template-columns:repeat(5,1fr);gap:6px;padding:8px 10px 0;';
        for (var i = 0; i < KEYS.length; i++) {
            var tile = document.createElement('div');
            tile.style.cssText =
                'background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);' +
                'border-radius:8px;padding:6px 8px;';
            var tRow = document.createElement('div');
            tRow.style.cssText = 'display:flex;align-items:center;gap:5px;font-size:11px;color:#c3c2b7;';
            var tDot = document.createElement('span');
            tDot.style.cssText = 'width:8px;height:8px;border-radius:50%;background:' + COLORS[i] + ';flex:none;';
            var tLab = document.createElement('span');
            tLab.textContent = LABELS[i];
            var tVal = document.createElement('div');
            tVal.className = 'pv';
            tVal.style.cssText = 'font-size:18px;font-weight:600;margin-top:3px;';
            tVal.textContent = '—';
            tRow.appendChild(tDot);
            tRow.appendChild(tLab);
            tile.appendChild(tRow);
            tile.appendChild(tVal);
            tiles.appendChild(tile);
        }

        /* 范围切换 */
        var ranges = document.createElement('div');
        ranges.style.cssText = 'flex:none;display:flex;gap:4px;padding:8px 10px 0;';
        ['1h', '24h', '7d'].forEach(function (r) {
            var b = document.createElement('span');
            b.textContent = r;
            b.dataset.range = r;
            b.style.cssText =
                'cursor:pointer;padding:2px 10px;border-radius:6px;font-size:11px;color:#c3c2b7;' +
                'border:1px solid rgba(255,255,255,.1);' +
                (r === chartRange ? 'background:rgba(255,255,255,.14);color:#fff;' : '');
            b.addEventListener('click', function () {
                chartRange = r;
                ranges.querySelectorAll('span').forEach(function (x) {
                    x.style.background = x === b ? 'rgba(255,255,255,.14)' : '';
                    x.style.color = x === b ? '#fff' : '#c3c2b7';
                });
                fetchSeries();
            });
            ranges.appendChild(b);
        });

        /* 图例(含最新值) */
        var legend = document.createElement('div');
        legend.className = 'iirose-stats-legend';
        legend.style.cssText = 'flex:none;display:flex;flex-wrap:wrap;gap:8px;padding:6px 10px 0;font-size:11px;color:#c3c2b7;';
        legendVals.length = 0;
        for (var j = 0; j < KEYS.length; j++) {
            var item = document.createElement('span');
            item.style.cssText = 'display:flex;align-items:center;gap:4px;';
            var lDot = document.createElement('span');
            lDot.style.cssText = 'width:8px;height:3px;border-radius:2px;background:' + COLORS[j] + ';';
            var lLab = document.createElement('span');
            lLab.textContent = LABELS[j];
            var lVal = document.createElement('b');
            lVal.className = 'pv';
            lVal.style.cssText = 'color:#fff;font-weight:600;';
            lVal.textContent = '—';
            item.appendChild(lDot);
            item.appendChild(lLab);
            item.appendChild(lVal);
            legend.appendChild(item);
            legendVals.push(lVal);
        }

        /* 走势图 */
        chartCanvas = document.createElement('canvas');
        chartCanvas.style.cssText = 'display:block;margin:4px 10px 8px;';

        /* 页脚 */
        var foot = document.createElement('div');
        foot.style.cssText =
            'flex:none;padding:4px 12px 8px;font-size:11px;color:#777;border-top:1px solid rgba(255,255,255,.06);';
        foot.textContent = '数据:本站终端 stats 同源 · 每 ' + (INTERVAL_MS / 1000) + ' 秒上报本机';

        /* 右下角拖拽调大小 */
        var grip = document.createElement('div');
        grip.textContent = '⤡';
        grip.title = '拖动调整大小';
        grip.style.cssText =
            'position:absolute;right:2px;bottom:2px;width:18px;height:18px;line-height:16px;' +
            'text-align:center;cursor:nwse-resize;color:#777;font-size:13px;user-select:none;';
        grip.addEventListener('pointerdown', startResize);

        /* 面板内滚轮手动滚动:capture + preventDefault,站点任何滚轮逻辑都拦不住 */
        panel.addEventListener('wheel', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
            panel.scrollTop += d;
        }, { capture: true, passive: false });

        panel.appendChild(bar);
        panel.appendChild(tiles);
        panel.appendChild(ranges);
        panel.appendChild(legend);
        panel.appendChild(chartCanvas);
        panel.appendChild(foot);
        panel.appendChild(grip);
        document.body.appendChild(panel);

        if (window.ResizeObserver) {
            new ResizeObserver(function () { drawChart(); }).observe(panel);
        }
        fetchSeries();
        if (btn) btn.textContent = '✕ 收起';
        if (window.__iirose_stats_sample) updateUI(window.__iirose_stats_sample);
    }

    /* ===== 拖动移动(标题栏空白区;按钮自身已 stopPropagation) ===== */
    function startDrag(e) {
        if (!panel) return;
        e.preventDefault();
        var sx = e.clientX, sy = e.clientY;
        var ox = panel.offsetLeft, oy = panel.offsetTop;
        var move = function (ev) {
            panel.style.left = Math.max(-panel.offsetWidth + 120,
                Math.min(window.innerWidth - 120, ox + (ev.clientX - sx))) + 'px';
            panel.style.top = Math.max(0,
                Math.min(window.innerHeight - 60, oy + (ev.clientY - sy))) + 'px';
        };
        var up = function () {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
            savePanelState();
        };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
    }

    /* ===== 右下角拖拽调大小 ===== */
    function startResize(e) {
        if (!panel) return;
        e.preventDefault();
        var sx = e.clientX, sy = e.clientY;
        var sw = panel.offsetWidth, sh = panel.offsetHeight;
        var move = function (ev) {
            panel.style.width = Math.max(360,
                Math.min(window.innerWidth - panel.offsetLeft - 12, sw + (ev.clientX - sx))) + 'px';
            panel.style.height = Math.max(280,
                Math.min(window.innerHeight - panel.offsetTop - 60, sh + (ev.clientY - sy))) + 'px';
        };
        var up = function () {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
            savePanelState();
        };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
    }

    /* ===== 走势图(轻量 canvas,无依赖) ===== */
    function drawChart() {
        if (!panel || !chartCanvas) return;
        var c = chartCanvas;
        var dpr = window.devicePixelRatio || 1;
        var cssW = Math.max(120, panel.clientWidth - 20);
        var cssH = Math.max(160, panel.clientHeight - 250);
        c.style.width = cssW + 'px';
        c.style.height = cssH + 'px';
        c.width = cssW * dpr;
        c.height = cssH * dpr;
        var ctx = c.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, cssW, cssH);
        var data = chartData;
        if (!data || data.length < 2) {
            ctx.fillStyle = '#777';
            ctx.font = '12px sans-serif';
            ctx.fillText('暂无数据 —— 等待本机采集服务', 10, 20);
            return;
        }

        var padL = 34, padR = 8, padT = 6, padB = 18;
        var plotW = cssW - padL - padR, plotH = cssH - padT - padB;
        var n = data.length;

        var max = 0;
        data.forEach(function (r) { KEYS.forEach(function (k) { max = Math.max(max, r[k] || 0); }); });
        max = Math.max(50, Math.ceil(max / 50) * 50);

        /* 网格 + y 轴 */
        ctx.font = '10px sans-serif';
        ctx.textBaseline = 'middle';
        for (var gy = 0; gy <= 4; gy++) {
            var val = max * gy / 4;
            var py = padT + plotH - plotH * gy / 4;
            ctx.strokeStyle = 'rgba(255,255,255,.08)';
            ctx.beginPath();
            ctx.moveTo(padL, py);
            ctx.lineTo(cssW - padR, py);
            ctx.stroke();
            ctx.fillStyle = '#888';
            ctx.textAlign = 'right';
            ctx.fillText(String(Math.round(val)), padL - 5, py);
        }

        /* 五条折线 */
        ctx.lineWidth = 1.6;
        ctx.lineJoin = 'round';
        for (var s = 0; s < KEYS.length; s++) {
            ctx.strokeStyle = COLORS[s];
            ctx.beginPath();
            for (var i = 0; i < n; i++) {
                var x = padL + plotW * i / (n - 1);
                var y = padT + plotH - plotH * (data[i][KEYS[s]] || 0) / max;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }

        /* x 轴时间标签 */
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = '#888';
        for (var t = 0; t < 4; t++) {
            var idx = Math.round((n - 1) * t / 3);
            var ts = data[idx].ts;
            var label = ts ? (chartRange === '7d'
                ? ts.slice(5, 16).replace('T', ' ')
                : ts.slice(11, 16)) : '';
            ctx.fillText(label, padL + plotW * t / 3, cssH - padB + 4);
        }
    }

    function updateLegend() {
        var latest = chartData && chartData.length ? chartData[chartData.length - 1] : null;
        for (var i = 0; i < legendVals.length; i++) {
            legendVals[i].textContent = latest ? String(latest[KEYS[i]]) : '—';
        }
    }

    function fetchSeries() {
        fetch(SERIES_URL + chartRange)
            .then(function (r) { return r.json(); })
            .then(function (j) {
                chartData = j.samples || [];
                drawChart();
                updateLegend();
            })
            .catch(function () { /* 本机服务未启动,下轮重试 */ });
    }

    /* ===== UI 刷新(前 5 个 .pv 是瓦片值,后 5 个是图例值) ===== */
    function updateUI(sample) {
        if (!panel) return;
        var pvs = panel.querySelectorAll('.pv');
        for (var i = 0; i < KEYS.length && i < pvs.length; i++) {
            pvs[i].textContent = String(sample[KEYS[i]]);
        }
    }

    /* ===== 上报 ===== */
    function post(sample) {
        fetch(ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sample),
        }).then(function (resp) {
            if (!resp.ok) console.warn('[iirose-stats] 上报失败 HTTP ' + resp.status);
        }).catch(function () { /* 本机服务未启动/页面后台节流,静默重试下一轮 */ });
    }

    function tick() {
        var sample = compute();
        if (!sample || sample.online <= 0) return;
        window.__iirose_stats_sample = sample;
        updateButton(sample);
        updateUI(sample);
        post(sample);
        fetchSeries();
    }

    function updateButton(sample) {
        if (!btn || panel) return; // 展开时按钮显示「收起」
        btn.textContent = '📊 ' + sample.online;
        btn.title = '点击打开/关闭监测面板\nOnline ' + sample.online +
            ' · Chatting ' + sample.chatting + ' · Active ' + sample.active +
            ' · Away ' + sample.away + ' · Entering ' + sample.entering;
    }

    function closePanel() {
        if (!panel) return;
        if (panel.parentNode) panel.parentNode.removeChild(panel);
        panel = null;
        chartCanvas = null;
        if (btn) {
            btn.textContent = '📊 —';
            if (window.__iirose_stats_sample) updateButton(window.__iirose_stats_sample);
        }
    }

    function togglePanel() {
        if (panel) closePanel();
        else openPanel();
    }

    /* ===== 启动 ===== */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }

    function start() {
        if (btn) return;
        buildButton();
        tick();
        setInterval(tick, INTERVAL_MS);
    }

    /* 调试入口:控制台 iiroseStats.now() 立即算一次并上报 */
    window.iiroseStats = { compute: compute, now: function () { post(compute()); } };
})();
