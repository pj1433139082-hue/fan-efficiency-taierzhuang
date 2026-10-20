/* ==========================================================================
   风机效率公式口径讲解 — 证据地图册（定稿版）
   内容契约见同目录 design-brief.md 第 5 节。
   数据来源：Excel《风机效率核算工具》逐字公式链 + 台儿庄自控信号台账实时值。
   ========================================================================== */
(function () {
  "use strict";

  /* ------------------------------------------------------------ 常量与模型 */

  var K = 0.2857;                 // (k-1)/k, k=1.4 空气比热比
  var P0 = 101.325;               // kPa，标准大气压（表 L4）
  var T0 = 293.15;                // K，标准状态温度（表 L6）
  var C18 = 354635.537746665;     // 表 C18 = P0'/K，隐含 P0' = 101,319.4 Pa
  var FIX = { Pin: 72.4, dp: 47.2, H: 38.5 };   // 示例读数（表中黄底格）
  var DEF = { t1: 24.8, Q1: 93 };

  function entryPressure() {
    return 101.325 - FIX.H / 12 * 0.133;        // 表 C9
  }

  function compute(t1, Q1, dp, Pin) {
    dp = dp === undefined ? FIX.dp : dp;
    Pin = Pin === undefined ? FIX.Pin : Pin;
    var P1 = entryPressure();
    var P2 = P1 + dp;
    var T1 = t1 + 273.15;
    var Q1s = Q1 / 60;
    var Q0 = Q1 * (P1 / P0) * (T0 / T1);        // Nm³/min，表 L5
    var Q0s = Q0 / 60;
    var Pi = P2 / P1;
    var br = Math.pow(Pi, K) - 1;               // 方括号
    var Wc = (P1 / K) * Q1s * br;               // 正确：前导项用入口状态
    var We = (C18 * Q0s * br) / 1000;           // Excel：逐字复现 C18*C6/60*br/1000
    return {
      P1: P1, P2: P2, T1: T1, Q0: Q0, Q0s: Q0s, Q1s: Q1s,
      Pi: Pi, PiK: Math.pow(Pi, K), br: br,
      Wc: Wc, We: We,
      etaC: Wc / Pin, etaE: We / Pin,
      dev: We / Wc - 1,                          // 负 = Excel 低估
      lead_correct: P1 * Q1s,
      lead_excel: P0 * Q0s
    };
  }

  var D = compute(DEF.t1, DEF.Q1);               // 默认状态（表里那天）

  /* ------------------------------------------------------------ 格式化 */

  function f(x, d) { return Number(x).toFixed(d === undefined ? 3 : d); }
  function pct(x, d) { return (x * 100).toFixed(d === undefined ? 2 : d) + "%"; }
  // 偏差统一按 0.1 个百分点显示；不足 0.01 个百分点视为同温。
  // 精确值：按 62.406/63.431 实算为 1.62%，按纯温度比 T0/T1 为 1.61%，
  // 差 0.01 个百分点来自表里 C18 的 ρ 只保留三位小数。
  function devFmt(x) {
    if (Math.abs(x) < 1e-4) { return "≈0%"; }
    return (x > 0 ? "+" : "−") + Math.abs(x * 100).toFixed(1) + "%";
  }

  /* ------------------------------------------------------------ 片段 */

  function calcBlock() {
    return '<div class="calc" data-calc>' +
      '<div class="calc-head">' +
        '<span class="calc-title">现场试算台</span>' +
        '<span class="tag plain">拖动即可复算</span>' +
      '</div>' +
      '<div class="calc-body">' +
        '<div class="field">' +
          '<div class="field-top"><label class="field-label" for="t1in">进口温度 t₁</label>' +
          '<span class="field-val"><span data-vt>' + f(DEF.t1, 1) + '</span> <span class="u">℃</span></span></div>' +
          '<input id="t1in" type="range" min="0" max="40" step="0.1" value="' + DEF.t1 + '"' +
            ' aria-label="进口温度，单位摄氏度" data-tinput>' +
        '</div>' +
        '<div class="field">' +
          '<div class="field-top"><label class="field-label" for="q1in">进口风量 Q₁</label>' +
          '<span class="field-val"><span data-vq>' + f(DEF.Q1, 1) + '</span> <span class="u">m³/min</span></span></div>' +
          '<input id="q1in" type="range" min="20" max="140" step="0.5" value="' + DEF.Q1 + '"' +
            ' aria-label="进口风量，单位立方米每分钟" data-qinput>' +
        '</div>' +
        '<div class="calc-out" aria-live="polite">' +
          '<div class="calc-row cv-wrong"><span class="cl">EXCEL 口径</span>' +
            '<span class="cv"><span data-we>' + f(D.We) + '</span> kW · <span data-ete>' + pct(D.etaE) + '</span></span></div>' +
          '<div class="calc-row cv-ok"><span class="cl">修正口径</span>' +
            '<span class="cv"><span data-wc>' + f(D.Wc) + '</span> kW · <span data-etc>' + pct(D.etaC) + '</span></span></div>' +
          '<div class="dev"><div class="dev-track">' +
            '<div class="dev-zero" data-zero></div><div class="dev-mark" data-mark></div></div>' +
            '<div class="dev-caption"><span>← 高估</span><span data-dev>' + devFmt(D.dev) + '</span><span>低估 →</span></div>' +
          '</div>' +
          '<p data-tempnote style="margin-top:10px;font-size:12.5px;line-height:1.6;color:var(--ink-3)"></p>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function readingsTable() {
    var rows = [
      ["P<sub>in</sub>", "电机输入功率", "72.4", "kW", "效率的分母，后面是分子"],
      ["Δp", "背压（出口－进口）", "47.2", "kPa", "压升多少，只进压比项"],
      ["H", "竣工图设计海拔", "38.5", "m", "只用来算当地大气压"],
      ["Q<sub>1</sub>", "进口风量（面板显示）", "93", "m³/min", "不是标况流量，是进口状态"],
      ["t<sub>1</sub>", "进口温度", "24.8", "℃", "压缩功的起点温度"]
    ];
    return '<table class="tbl"><caption>表 1 · 五个读数（示例数据）</caption>' +
      '<thead><tr><th>符号</th><th>是什么</th><th>值</th><th>单位</th><th>在公式里的位置</th></tr></thead><tbody>' +
      rows.map(function (r) {
        return '<tr><td class="n">' + r[0] + '</td><td>' + r[1] + '</td>' +
          '<td class="n">' + r[2] + '</td><td class="n">' + r[3] + '</td><td>' + r[4] + '</td></tr>';
      }).join("") + '</tbody></table>';
  }

  function combosTable() {
    var P1 = D.P1, T1 = D.T1, base = P1 * D.Q1s;
    var rows = [
      { p: "P标准 101.325", pv: P0, q: "Q标准", qv: D.Q0s, note: "Excel 走的" },
      { p: "P入口 " + f(P1), pv: P1, q: "Q标准", qv: D.Q0s, note: "混搭，更糟" },
      { p: "P入口 " + f(P1), pv: P1, q: "Q入口", qv: D.Q1s, note: "正确" },
      { p: "P标准 101.325", pv: P0, q: "Q入口", qv: D.Q1s, note: "混搭" }
    ];
    return '<table class="tbl"><caption>表 2 · 四种搭配，只有一种对</caption>' +
      '<thead><tr><th>前导项用的 P</th><th>用的 Q</th><th>P × Q<br><span style="font-weight:400">kPa·m³/s</span></th>' +
      '<th>相对正确值</th><th>相当于假设<br>进气温度</th><th>判定</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var prod = r.pv * r.qv;
        var ratio = prod / base;
        var Teq = ratio * T1 - 273.15;
        var cls = r.note === "正确" ? "row-ok" : (r.note === "Excel 走的" ? "row-bad" : "");
        var tag = r.note === "正确" ? '<span class="tag ok">✓ 正确</span>'
                : r.note === "Excel 走的" ? '<span class="tag wrong">✗ Excel</span>'
                : '<span class="tag plain">✗ ' + r.note + '</span>';
        return '<tr class="' + cls + '"><td class="n">' + r.p + '</td><td class="n">' + r.q + '</td>' +
          '<td class="n">' + f(prod, 2) + '</td><td class="n">' + f(ratio, 4) + '</td>' +
          '<td class="n">' + f(Teq, 1) + ' ℃</td><td>' + tag + '</td></tr>';
      }).join("") + '</tbody></table>';
  }

  function tempTable() {
    var temps = [5, 20, 24.8, 30, 40];
    var notes = { 5: "冬季", 20: "恰好等于标准状态，看不出错", 24.8: "示例那天", 30: "夏季", 40: "极端" };
    return '<table class="tbl"><caption>表 3 · 偏差随进气温度变号（这是它最难被发现的原因）</caption>' +
      '<thead><tr><th>进气温度 t₁</th><th>Excel 相对正确值</th><th>看起来</th></tr></thead><tbody>' +
      temps.map(function (t) {
        var dev = T0 / (t + 273.15) - 1;
        var word = Math.abs(dev) < 1e-9 ? '<span class="tag plain">看不出来</span>'
          : dev > 0 ? '<span class="tag wrong">高估</span>' : '<span class="tag ok">低估</span>';
        return '<tr><td class="n">' + f(t, 1) + ' ℃</td><td class="n">' + devFmt(dev) + '</td>' +
          '<td>' + word + ' <span style="color:var(--ink-3);font-family:var(--font-mono);font-size:12.5px">' + (notes[t] || "") + '</span></td></tr>';
      }).join("") +
      '</tbody></table><p style="margin-top:10px;font-size:13.5px;color:var(--ink-3)">' +
      '所以靠历史对账是发现不了的：冬天它高估、夏天它低估，平均下来像是“大体对得上”。</p>';
  }

  var LIVE = [
    { name: "一污 4#", Q: 46.1, dp: 46.2, Pin: 42.2, t: 25.5 },
    { name: "一污 5#", Q: 48.6, dp: 46.1, Pin: 41.9, t: 25.8 },
    { name: "二污 2#", Q: 48.2, dp: 62.1, Pin: 61.3, t: 26.0 }
  ];

  function liveTable() {
    return '<table class="tbl"><caption>表 4 · 台儿庄台账实时值（真实数据，海拔按 38.5 m）</caption>' +
      '<thead><tr><th>风机</th><th>进口风量<br>m³/min</th><th>出口压力<br>kPa</th><th>功率<br>kW</th>' +
      '<th>进口温度<br>℃</th><th>修正 η</th><th>Excel 口径 η</th></tr></thead><tbody>' +
      LIVE.map(function (r) {
        var c = compute(r.t, r.Q, r.dp, r.Pin);
        return '<tr><td>' + r.name + '</td><td class="n">' + f(r.Q, 1) + '</td><td class="n">' + f(r.dp, 1) + '</td>' +
          '<td class="n">' + f(r.Pin, 1) + '</td><td class="n">' + f(r.t, 1) + '</td>' +
          '<td class="n"><b>' + pct(c.etaC) + '</b></td><td class="n" style="color:var(--wrong)">' + pct(c.etaE) + '</td></tr>';
      }).join("") + '</tbody></table>' +
      '<p style="margin-top:10px;font-size:13.5px;color:var(--ink-3)">' +
      '6#、二污 1#、二污 3# 停机（功率 0），不参与计算。三台在跑的风机效率 68%–78%，落在离心鼓风机正常区间。</p>';
  }

  function fixBlock() {
    return '<div class="formula">' +
      '<b>原式（表里 C20）</b><br>' +
      '=C18*C6/60*(C19^C14-1)/1000<br><br>' +
      '<b>改成</b><br>' +
      '<span class="fx-ok">=(C9/C14)*(L8/60)*(C19^C14-1)</span>' +
    '</div>' +
    '<p>两处同时改，缺一不可：<b>C18 → C9</b>（前导压力从“标准”换成“入口”）、<b>C6 → L8</b>（风量从“标况”换成“进口”）。' +
    '只改一个字母会怎样？看表 2 —— 第 2、4 行就是两种“只改一个”，误差比现在更大。</p>' +
    '<p style="color:var(--ink-3);font-size:13.5px">另：C18 还被 <b>5、空气系统</b> 的 T13:T18（实际效率列，混搭，同样偏低约 1.6%）和 ' +
    'I13:I18（额定效率列，全程用海平面 101.325，自洽，属铭牌口径）共用 —— 改的时候要三处一起看。</p>';
  }

  function evidenceBlock() {
    return '<table class="tbl"><caption>表 5 · 证据等级</caption>' +
      '<thead><tr><th>等级</th><th>内容</th></tr></thead><tbody>' +
      '<tr><td><span class="tag ok">读到的</span></td><td>所有单元格公式与数值、B22 注、右侧 ρ–T 对照表、' +
        '<b>5、空气系统</b> 六台铭牌与实测列、台儿庄台账 17 列信号与实时值（openpyxl 逐字核对）</td></tr>' +
      '<tr><td><span class="tag plain">推算的</span></td><td>C18 隐含压力 101,319.4 Pa（= J15 × R）；' +
        '出口温度 332.5 K、出口风量 70.70 m³/min；台儿庄海拔按 38.5 m 假设（0–50 m 对效率影响 &lt; 0.1 个百分点）</td></tr>' +
      '</tbody></table>';
  }

  /* ------------------------------------------------------------ 偏差曲线 */

  function sparkSvg() {
    var W = 320, Hh = 132, padL = 26, padR = 8, padT = 10, padB = 20;
    var iw = W - padL - padR, ih = Hh - padT - padB;
    var yMax = 8, yMin = -7;
    function x(t) { return padL + (t / 40) * iw; }
    function y(devPct) { return padT + (yMax - devPct) / (yMax - yMin) * ih; }
    var pts = [];
    for (var t = 0; t <= 40; t += 0.5) {
      var dev = (T0 / (t + 273.15) - 1) * 100;
      pts.push(x(t).toFixed(2) + "," + y(dev).toFixed(2));
    }
    var zeroY = y(0);
    var curDev = (T0 / (DEF.t1 + 273.15) - 1) * 100;
    var curY = y(curDev);
    return '<svg class="spark" viewBox="0 0 ' + W + ' ' + Hh + '" role="img" ' +
      'aria-label="Excel 相对偏差随进口温度变化的曲线：低温区高估，20 摄氏度为零，高温区低估">' +
      '<rect x="' + padL + '" y="' + padT + '" width="' + iw + '" height="' + (zeroY - padT) + '" fill="rgba(168,58,32,0.06)"/>' +
      '<rect x="' + padL + '" y="' + zeroY + '" width="' + iw + '" height="' + (padT + ih - zeroY) + '" fill="rgba(23,98,74,0.06)"/>' +
      '<line class="zero" x1="' + padL + '" y1="' + zeroY + '" x2="' + (padL + iw) + '" y2="' + zeroY + '"/>' +
      '<polyline class="curve" points="' + pts.join(" ") + '"/>' +
      '<line class="marker-line" x1="' + x(DEF.t1) + '" y1="' + padT + '" x2="' + x(DEF.t1) + '" y2="' + (padT + ih) + '"/>' +
      '<circle class="marker-dot ' + (curDev < 0 ? 'is-low' : 'is-high') + '" cx="' + x(DEF.t1) + '" cy="' + curY + '" r="3"/>' +
      '<text x="' + padL + '" y="' + (padT + ih + 14) + '">0 ℃</text>' +
      '<text x="' + x(20) + '" y="' + (padT + ih + 14) + '" text-anchor="middle">20 ℃</text>' +
      '<text x="' + (padL + iw) + '" y="' + (padT + ih + 14) + '" text-anchor="end">40 ℃</text>' +
      '<text x="2" y="' + (zeroY - 3) + '">0</text>' +
      '<text x="2" y="' + (y(yMax) + 9) + '">+' + yMax + '%</text>' +
      '<text x="2" y="' + (y(yMin) - 1) + '">' + yMin + '%</text>' +
      '</svg>' +
      '<p class="t-tag-note">横轴：进口温度。纵轴：Excel 相对正确值的偏差。曲线穿过 0 的位置正好是 20 ℃ —— 标准状态的温度。</p>';
  }

  /* ------------------------------------------------------------ 页面 */

  function renderPage() {
    return '<div class="v-c"><div class="atlas">' +
      '<header class="c-head">' +
        '<div><p class="kicker">证据地图册 · 一眼看完</p>' +
        '<h1>Excel 前导项配错了状态</h1></div>' +
        '<div class="c-meta">示例读数：P<sub>in</sub> 72.4 kW / Δp 47.2 kPa<br>H 38.5 m / Q₁ 93 m³/min / t₁ 24.8 ℃<br>' +
        '结论只由公式决定，示例数据不参与判断</div>' +
      '</header>' +

      '<div class="bento">' +
        '<div class="tile s4 reveal"><span class="tile-kicker">结论</span>' +
          '<div class="c-verdict-num wrong">' + f(D.We) + ' kW</div>' +
          '<div class="c-verdict-sub">Excel 口径 · η ' + pct(D.etaE) + '</div>' +
          '<div style="height:10px"></div>' +
          '<div class="c-verdict-num ok">' + f(D.Wc) + ' kW</div>' +
          '<div class="c-verdict-sub">修正口径 · η ' + pct(D.etaC) + '</div>' +
          '<p class="t-tag-note">相对偏差 <b>' + devFmt(D.dev) + '</b>（Excel 偏低约 1.6%）。精确：kW 实算 1.62% / 纯温度比 1.61%（汇报稿）/ 反向 1.64%，同一个差、三个分母。</p></div>' +

        '<div class="tile s4 reveal"><span class="tile-kicker">三个压力</span>' +
          '<h3>别混成两个</h3>' +
          '<div class="pres">' +
            '<div><span class="pl">P入口</span><span class="pv">' + f(D.P1) + '</span><span class="pd">kPa · 真实</span></div>' +
            '<div><span class="pl">P出口</span><span class="pv">' + f(D.P2) + '</span><span class="pd">kPa · 真实</span></div>' +
            '<div><span class="pl">P标准</span><span class="pv">101.325</span><span class="pd">kPa · 记账基准</span></div>' +
          '</div>' +
          '<p style="font-size:13px;color:var(--ink-3)">P入口 = 101.325 − 38.5/12 × 0.133；P出口 = P入口 + 47.2。P标准 不对应任何真实位置。</p>' +
          '<p class="t-tag-note">前导项必须用左边两个真实值，Excel 用了最右边的基准。</p></div>' +

        '<div class="tile s4 reveal"><span class="tile-kicker">压比项</span>' +
          '<h3>这一项是对的</h3>' +
          '<div class="pres">' +
            '<div><span class="pl">Π = P出口/P入口</span><span class="pv">' + f(D.Pi, 5) + '</span><span class="pd">—</span></div>' +
            '<div><span class="pl">K</span><span class="pv">0.2857</span><span class="pd">(k−1)/k</span></div>' +
            '<div><span class="pl">Π^K − 1</span><span class="pv">' + f(D.br, 5) + '</span><span class="pd">方括号</span></div>' +
          '</div>' +
          '<p class="t-tag-note">两种口径共用同样两个真实压力，所以错误只能出在前导项。</p></div>' +

        '<div class="tile s7 reveal"><span class="tile-kicker">偏差曲线的形状</span>' +
          '<h3>为什么对账发现不了</h3>' + sparkSvg() + '</div>' +

        '<div class="tile s5 reveal"><span class="tile-kicker">现场试算</span>' +
          '<h3>自己拖一下</h3>' + calcBlock() + '</div>' +

        '<div class="tile s12 reveal"><span class="tile-kicker">四种搭配</span>' +
          '<h3>只有一种对</h3>' + combosTable() + '</div>' +

        '<div class="tile s6 reveal"><span class="tile-kicker">台儿庄实测</span>' +
          '<h3>三台在跑的风机</h3>' + liveTable() + '</div>' +

        '<div class="tile s6 reveal"><span class="tile-kicker">修法</span>' +
          '<h3>一行公式，两处替换</h3>' + fixBlock() + '</div>' +
      '</div>' +

      '<div class="c-folds">' +
        '<details class="fold"><summary>展开：五步推导（公式全链）</summary><div class="fold-body">' +
          '<div class="step-inner"><span class="fold-no">01</span><div>' +
            '<h3>五个读数</h3>' + readingsTable() + '</div></div>' +
          '<div class="step-inner"><span class="fold-no">02</span><div>' +
            '<h3>三个压力</h3><div class="formula">P入口 = 101.325 − 38.5/12 × 0.133 = <b>' + f(D.P1) + '</b> kPa<br>' +
            'P出口 = ' + f(D.P1) + ' + 47.2 = <b>' + f(D.P2) + '</b> kPa<br>P标准 = <b>101.325</b> kPa</div></div></div>' +
          '<div class="step-inner"><span class="fold-no">03</span><div>' +
            '<h3>压比项</h3><div class="formula">Π = ' + f(D.Pi, 5) + '　Π<sup>K</sup> = ' + f(D.PiK, 6) + '　方括号 = ' + f(D.br, 6) + '</div></div></div>' +
          '<div class="step-inner"><span class="fold-no">04</span><div>' +
            '<h3>前导项</h3><div class="formula"><span class="fx-ok">正确 ' + f(D.lead_correct, 2) + ' kPa·m³/s</span><br>' +
            '<span class="fx-wrong">Excel ' + f(D.lead_excel, 2) + ' kPa·m³/s</span></div></div></div>' +
          '<div class="step-inner"><span class="fold-no">05</span><div>' +
            '<h3>合成</h3><div class="formula">W = (1/K) × 前导项 × 方括号　→　' +
            '<span class="fx-ok">' + f(D.Wc) + ' kW</span> / <span class="fx-wrong">' + f(D.We) + ' kW</span></div></div></div>' +
        '</div></details>' +
        '<details class="fold"><summary>展开：偏差随温度对照表 + 证据等级</summary><div class="fold-body">' +
          '<div class="step-inner"><span class="fold-no">T</span><div>' + tempTable() + '</div></div>' +
          '<div class="step-inner"><span class="fold-no">E</span><div>' + evidenceBlock() + '</div></div>' +
        '</div></details>' +
      '</div>' +
    '</div></div>';
  }

  /* ------------------------------------------------------------ 来源指纹 */

  function renderSrc() {
    document.getElementById("srcBody").innerHTML =
      '<h3>证据地图册</h3>' +
      '<p class="kicker" style="margin-top:8px">五维来源指纹</p>' +
      '<table>' +
      '<tr><th>字体</th><td>宋体标题 + 黑体正文 + 等宽数字（系统字体栈，离线可用）；' +
        '图注统一 11px 等宽 + 0.12em 字距，作图纸标注</td></tr>' +
      '<tr><th>组件</th><td>展示库 <code>section-bento-evidence</code>（非等宽证据拼版）→ 6 格数据拼版（4/4/4、7/5、12、6/6）</td></tr>' +
      '<tr><th>动效</th><td>展示库 <code>progressive-unmask</code>：章节逐层揭示，每格错峰进入</td></tr>' +
      '<tr><th>设计参考</th><td>展示库 <code>direction-field-atlas</code>（整页方向：数据地图册）+ ' +
        '<code>background-contour-paper</code> 的网格做法</td></tr>' +
      '<tr><th>排版</th><td>12 列网格 + 1px 网格缝（显式图纸骨骼），非等宽格位</td></tr>' +
      '</table>' +
      '<p class="kicker" style="margin-top:18px">写清的代价</p>' +
      '<p style="font-size:13.5px;color:var(--ink-2)">现场一步步讲会跳，第一次看需要自己找路径；' +
      '≤760px 塌成单列后备忘感全失；折叠块容易被跳过，等于把推导藏起来。</p>' +
      '<p class="kicker" style="margin-top:18px">字体落地说明</p>' +
      '<p style="font-size:13.5px;color:var(--ink-2)">不引 CDN，用系统字体栈：' +
      '标题 <code>Songti SC / Noto Serif SC / SimSun</code>，正文 <code>PingFang SC / Microsoft YaHei / Noto Sans SC</code>。' +
      '理由：现场投影可能无网，离线必须能打开；代价是不同机器字重略有差异。</p>' +
      '<p class="kicker" style="margin-top:18px">证据来源</p>' +
      '<p style="font-size:13.5px;color:var(--ink-2)">' +
      '<code>docs/风机效率核算工具-公式口径问题汇总.md</code>、' +
      '<code>docs/风机效率核算公式-参数厂家核对清单.md</code>、' +
      '《枣庄台儿庄自控信号.xlsx》openpyxl 逐字核对、.venv-311 实算。</p>';
  }

  /* ------------------------------------------------------------ 运行时 */

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function updateCalc() {
    var box = document.querySelector("[data-calc]");
    if (!box) { return; }
    var ti = box.querySelector("[data-tinput]");
    var qi = box.querySelector("[data-qinput]");
    var t1 = parseFloat(ti.value);
    var Q1 = parseFloat(qi.value);
    var c = compute(t1, Q1);

    box.querySelector("[data-vt]").textContent = t1.toFixed(1);
    box.querySelector("[data-vq]").textContent = Q1.toFixed(1);
    box.querySelector("[data-we]").textContent = f(c.We);
    box.querySelector("[data-wc]").textContent = f(c.Wc);
    box.querySelector("[data-ete]").textContent = pct(c.etaE);
    box.querySelector("[data-etc]").textContent = pct(c.etaC);
    box.querySelector("[data-dev]").textContent = devFmt(c.dev);

    var dMax = (T0 / 273.15 - 1);            // t=0 时的偏差（高估最大）
    var dMin = (T0 / 313.15 - 1);            // t=40 时的偏差（低估最大）
    var pos = (dMax - c.dev) / (dMax - dMin);   // 0 = 最左（高估）
    pos = Math.max(0, Math.min(1, pos));
    box.querySelector("[data-mark]").style.left = (pos * 100).toFixed(2) + "%";
    box.querySelector("[data-zero]").style.left = ((dMax / (dMax - dMin)) * 100).toFixed(2) + "%";

    var delta = t1 - 20;
    var word = Math.abs(c.dev) < 0.0005 ? "与标准状态同温，两种口径结果一致"
      : c.dev > 0 ? "Excel <b>高估</b>" : "Excel <b>低估</b>";
    box.querySelector("[data-tempnote]").innerHTML =
      "当前 " + t1.toFixed(1) + " ℃，比标准状态（20 ℃）" + (delta >= 0 ? "高 " : "低 ") +
      Math.abs(delta).toFixed(1) + " K → " + word + " " + Math.abs(c.dev * 100).toFixed(1) + "%。";
  }

  function wireMotion() {
    var list = Array.prototype.slice.call(document.querySelectorAll(".reveal, .stagger"));
    function revealAll() {
      list.forEach(function (n) { n.classList.add("in"); });
    }
    if (reduce || !("IntersectionObserver" in window)) { revealAll(); return; }

    var vh = window.innerHeight || 800;
    var rest = [];
    list.forEach(function (n) {
      // 首屏（含略微越过折线的部分）直接显示，避免“打开是空白”和整页截图/打印缺失
      if (n.getBoundingClientRect().top < vh * 1.15) { n.classList.add("in"); }
      else { rest.push(n); }
    });

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
    rest.forEach(function (n) { io.observe(n); });

    // 兜底：打印前与 2.5 秒后必须全部可见，不能让内容卡在 opacity:0
    window.addEventListener("beforeprint", revealAll);
    setTimeout(revealAll, 2500);
  }

  function markReady() {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        var apply = function () {
          document.documentElement.setAttribute("data-ready", "true");
          window.__PROTOTYPE_READY__ = true;
          window.__PROTOTYPE_STATE__ = {
            fontsLoaded: document.fonts ? document.fonts.status : "unsupported",
            mounted: true,
            motion: reduce ? "reduced" : "full"
          };
        };
        if (document.fonts && document.fonts.ready) {
          document.fonts.ready.then(apply, apply);
        } else { apply(); }
      });
    });
  }

  function mount() {
    document.documentElement.classList.remove("js-motion");
    if (!reduce) { document.documentElement.classList.add("js-motion"); }
    document.getElementById("app").innerHTML = renderPage();
    updateCalc();
    wireMotion();
    renderSrc();
    markReady();
  }

  /* -------- 事件 -------- */

  document.addEventListener("input", function (e) {
    if (e.target.matches("[data-tinput], [data-qinput]")) { updateCalc(); }
  });

  var panel = document.getElementById("srcPanel");
  var toggle = document.getElementById("srcToggle");
  var closeBtn = document.getElementById("srcClose");

  function openSrc() {
    panel.setAttribute("data-open", "true");
    panel.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");
  }
  function closeSrc() {
    panel.removeAttribute("data-open");
    panel.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");
  }
  toggle.addEventListener("click", function () {
    if (panel.getAttribute("data-open") === "true") { closeSrc(); } else { openSrc(); }
  });
  closeBtn.addEventListener("click", closeSrc);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeSrc(); }
  });

  /* -------- 启动 -------- */

  mount();
})();
