(function(){
  // Detect stock code from URL or meta. Skip if data unavailable.
  function getCodeFromPath(){
    const m = location.pathname.match(/\/stock\/([^/]+)\.html?$/);
    if (!m) return null;
    return m[1];
  }
  function isCode(s){ return /^\d{6}$/.test(s); }

  async function init(){
    const seg = getCodeFromPath();
    if (!seg) return;

    let code = isCode(seg) ? seg : null;
    let name = null;

    if (!code) {
      try {
        const mf = await fetch('/stock/_manifest.json').then(r=>r.json());
        const hit = mf.find(x => x.slug === seg);
        if (hit) { code = hit.code; name = hit.name; }
      } catch(e){ return; }
    }
    if (!code) return;

    // Try meta tag for name fallback
    if (!name) {
      const mn = document.querySelector('meta[name="stock-name"]');
      if (mn) name = mn.getAttribute('content');
    }

    let data;
    try {
      data = await fetch('/tools/data/history.json').then(r=>r.json());
    } catch(e){ return; }

    const stock = data.stocks && data.stocks[code];
    if (!stock || !stock.candles || stock.candles.length < 2) return;
    if (!name) name = stock.name;

    render(code, name, stock);
  }

  function fmtKRW(n){ return Math.round(n).toLocaleString('ko-KR'); }
  function fmtPct(n){ return (n>=0?'+':'')+n.toFixed(2)+'%'; }
  function ymd(s){ return s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8); }
  function findCandle(candles, target){
    let lo=0, hi=candles.length-1;
    while (lo < hi) { const mid=(lo+hi)>>1; if (candles[mid].d < target) lo=mid+1; else hi=mid; }
    return candles[lo];
  }

  function compute(stock, years, amt){
    const candles = stock.candles;
    const last = candles[candles.length-1];
    let buy;
    if (years === 0) buy = candles[0];
    else {
      const t = new Date();
      const target = new Date(t.getFullYear()-years, t.getMonth(), t.getDate());
      const ymdStr = target.getFullYear().toString() + String(target.getMonth()+1).padStart(2,'0') + String(target.getDate()).padStart(2,'0');
      buy = findCandle(candles, ymdStr);
    }
    if (!buy || buy.d >= last.d) return null;
    const shares = amt / buy.c;
    const valueNow = shares * last.c;
    const pnl = valueNow - amt;
    const ret = (last.c / buy.c - 1) * 100;
    const days = (Date.parse(ymd(last.d)) - Date.parse(ymd(buy.d))) / 86400000;
    const yrs = days / 365.25;
    const cagr = yrs > 0.1 ? (Math.pow(last.c/buy.c, 1/yrs) - 1) * 100 : null;
    return { buy, last, shares, valueNow, pnl, ret, yrs, cagr };
  }

  function chartSvg(candles, fromYmd, toYmd){
    const slice = candles.filter(c => c.d >= fromYmd && c.d <= toYmd);
    if (slice.length < 2) return '';
    const W=600, H=140, P=8;
    const closes = slice.map(c=>c.c);
    const min = Math.min(...closes), max = Math.max(...closes);
    const range = (max-min) || 1;
    const step = (W - P*2) / (slice.length - 1);
    const points = slice.map((c,i)=>{
      const x = P + i*step;
      const y = H - P - ((c.c-min)/range) * (H - P*2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const startY = H - P - ((closes[0]-min)/range) * (H - P*2);
    const endY = H - P - ((closes[closes.length-1]-min)/range) * (H - P*2);
    const isUp = closes[closes.length-1] >= closes[0];
    const color = isUp ? '#1D9E75' : '#A32D2D';
    const fill = isUp ? 'rgba(29,158,117,0.10)' : 'rgba(163,45,45,0.10)';
    const areaPoints = `${P},${H-P} ${points} ${(W-P).toFixed(1)},${H-P}`;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:auto;display:block">
      <polygon points="${areaPoints}" fill="${fill}"/>
      <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${P}" cy="${startY}" r="3" fill="${color}"/>
      <circle cx="${(W-P).toFixed(1)}" cy="${endY}" r="3" fill="${color}"/>
    </svg>`;
  }

  function render(code, name, stock){
    const css = `
      .__hr-wrap{margin-top:18px}
      .__hr-sec{font-size:13px;font-weight:700;margin:18px 0 8px;padding-bottom:4px;border-bottom:1px solid #d3d1c7;color:#2c2c2a}
      .__hr-card{background:#fff;border:1px solid #d3d1c7;border-radius:10px;padding:14px;margin-bottom:8px}
      .__hr-intro{font-size:12.5px;color:#52514c;line-height:1.6;margin-bottom:10px}
      .__hr-period{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
      .__hr-period button{padding:7px 14px;border:1.5px solid #d3d1c7;background:#fff;border-radius:7px;font-size:12.5px;font-weight:700;color:#52514c;cursor:pointer;font-family:inherit}
      .__hr-period button.active{background:#2A3552;border-color:#2A3552;color:#fff}
      .__hr-amt-row{display:flex;align-items:center;gap:8px;margin-bottom:12px}
      .__hr-amt-row label{font-size:11.5px;color:#6b6a64;font-weight:700;flex-shrink:0}
      .__hr-amt{flex:1;padding:8px 10px;font-size:13px;border:1.5px solid #d3d1c7;border-radius:7px;background:#fff;font-family:'SF Mono',monospace}
      .__hr-amt:focus{outline:none;border-color:#2A3552}
      .__hr-narr{font-size:13.5px;line-height:1.7;color:#2c2c2a;margin-bottom:12px}
      .__hr-narr b{font-weight:700}
      .__hr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-bottom:12px}
      .__hr-stat{background:#f5f5f0;border:1px solid #d3d1c7;border-radius:7px;padding:9px 10px}
      .__hr-stat .l{font-size:10.5px;color:#6b6a64;font-weight:600;margin-bottom:2px}
      .__hr-stat .v{font-size:14px;font-weight:800;font-family:'SF Mono',monospace;color:#2c2c2a}
      .__hr-stat .v.pos{color:#1D9E75}
      .__hr-stat .v.neg{color:#A32D2D}
      .__hr-stat .s{font-size:10.5px;color:#888780;margin-top:2px}
      .__hr-chart{background:#f5f5f0;border:1px solid #d3d1c7;border-radius:7px;padding:10px}
      .__hr-chart-head{display:flex;justify-content:space-between;font-size:11px;color:#6b6a64;margin-bottom:6px;font-weight:600}
      .__hr-foot{margin-top:10px;font-size:11.5px;color:#6b6a64;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
      .__hr-foot a{color:#2A3552;font-weight:700;text-decoration:none}
      .__hr-foot a:hover{text-decoration:underline}
    `;
    const styleTag = document.createElement('style');
    styleTag.textContent = css;
    document.head.appendChild(styleTag);

    const wrap = document.querySelector('.wrap') || document.body;
    const root = document.createElement('div');
    root.className = '__hr-wrap';
    root.innerHTML = `
      <div class="__hr-sec">🕰️ 10년 전 ${name} 샀으면 지금 얼마?</div>
      <div class="__hr-card">
        <div class="__hr-intro">한국투자증권 수정주가 기준. 과거 시점에 ${name}을(를) 샀다면 지금 평가금액은 얼마인지 단순 계산해 봅니다.</div>
        <div class="__hr-period" id="__hr-period">
          <button data-y="1">1년 전</button>
          <button data-y="3">3년 전</button>
          <button data-y="5" class="active">5년 전</button>
          <button data-y="10">10년 전</button>
        </div>
        <div class="__hr-amt-row">
          <label>투자금</label>
          <input type="text" inputmode="numeric" class="__hr-amt" id="__hr-amt" value="1,000,000">
          <span style="font-size:11.5px;color:#888780">원</span>
        </div>
        <div id="__hr-result"></div>
        <div class="__hr-foot">
          <span>※ 배당 재투자 미포함 · 단순 주가 비교</span>
          <a href="/vs/historical.html?code=${code}">다른 종목도 비교 →</a>
        </div>
      </div>
    `;
    wrap.appendChild(root);

    let years = 5;
    const amtEl = document.getElementById('__hr-amt');
    const resEl = document.getElementById('__hr-result');

    amtEl.addEventListener('input', e => {
      const raw = e.target.value.replace(/[^0-9]/g,'');
      e.target.value = raw ? Number(raw).toLocaleString('ko-KR') : '';
      update();
    });
    document.querySelectorAll('#__hr-period button').forEach(b=>{
      b.addEventListener('click', ()=>{
        document.querySelectorAll('#__hr-period button').forEach(x=>x.classList.remove('active'));
        b.classList.add('active');
        years = Number(b.dataset.y);
        update();
      });
    });

    function getAmt(){ return Number((amtEl.value||'').replace(/[^0-9]/g,''))||0; }

    function update(){
      const amt = getAmt();
      if (!amt) { resEl.innerHTML=''; return; }
      const r = compute(stock, years, amt);
      if (!r) {
        resEl.innerHTML = `<div style="padding:14px;background:#f5f5f0;border-radius:7px;font-size:12.5px;color:#6b6a64;text-align:center">${name}은(는) 해당 기간 데이터가 부족해요. 다른 기간을 선택해 보세요.</div>`;
        return;
      }
      const dir = r.ret >= 0 ? '늘었어요' : '줄었어요';
      const cls = r.ret >= 0 ? 'pos' : 'neg';
      const multi = (r.last.c / r.buy.c).toFixed(2);
      resEl.innerHTML = `
        <div class="__hr-narr">
          <b>${name}</b>을(를) <b>${r.yrs.toFixed(1)}년 전</b>(${ymd(r.buy.d)}) 에 <b>${fmtKRW(amt)}원</b>어치 사뒀다면, 오늘(${ymd(r.last.d)}) 기준 <b style="color:${r.ret>=0?'#1D9E75':'#A32D2D'}">${fmtKRW(r.valueNow)}원</b> — ${fmtKRW(Math.abs(r.pnl))}원 ${dir}.
        </div>
        <div class="__hr-grid">
          <div class="__hr-stat"><div class="l">매수가 → 현재가</div><div class="v">${fmtKRW(r.buy.c)} → ${fmtKRW(r.last.c)}</div><div class="s">${multi}배</div></div>
          <div class="__hr-stat"><div class="l">총 수익률</div><div class="v ${cls}">${fmtPct(r.ret)}</div><div class="s">${r.yrs.toFixed(1)}년 누적</div></div>
          <div class="__hr-stat"><div class="l">연환산(CAGR)</div><div class="v ${r.cagr!=null && r.cagr>=0?'pos':'neg'}">${r.cagr==null?'—':fmtPct(r.cagr)}</div><div class="s">매년 평균</div></div>
          <div class="__hr-stat"><div class="l">평가손익</div><div class="v ${cls}">${r.pnl>=0?'+':''}${fmtKRW(r.pnl)}</div><div class="s">원</div></div>
        </div>
        <div class="__hr-chart">
          <div class="__hr-chart-head">
            <span>📊 ${name} 가격 추이</span>
            <span>${ymd(r.buy.d)} ~ ${ymd(r.last.d)}</span>
          </div>
          ${chartSvg(stock.candles, r.buy.d, r.last.d)}
        </div>
      `;
    }
    update();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
