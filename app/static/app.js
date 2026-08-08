let selected = 'SOXL';

function clsBias(b){return b==='LONG'?'long':b==='SHORT'?'short':'neutral'}
function clsStatus(s){return s==='TRIGGER'?'trigger':s==='SETUP'?'setup':s==='WATCH'?'watch':'wait'}
function money(v){return '$'+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}

async function loadMarket(){
  const data = await fetch('/api/market').then(r=>r.json());
  const bull=data.market.nasdaq_bull, bear=data.market.nasdaq_bear;
  const pill=document.getElementById('marketPill');
  pill.textContent=`NASDAQ · BULL ${bull} / BEAR ${bear}`;
  pill.className='market-pill '+(bull>=58?'positive':bear>=58?'negative':'');
  document.getElementById('updated').textContent=new Date().toLocaleTimeString()+' · DEMO';
  const box=document.getElementById('top10'); box.innerHTML='';
  data.top10.forEach(r=>{
    const el=document.createElement('div');
    el.className='rank-row '+(r.symbol===selected?'active':'');
    el.innerHTML=`<div class="rank-num">${r.rank}</div><div><div class="ticker">${r.symbol}</div><div class="company">${r.name}</div></div><div class="score">${r.score}</div><div><span class="tag ${clsBias(r.bias)}">${r.bias}</span></div><div><span class="tag ${clsStatus(r.status)}">${r.status}</span></div>`;
    el.onclick=()=>{selected=r.symbol; loadMarket(); loadSymbol();};
    box.appendChild(el);
  });
}

async function loadSymbol(){
  const d=await fetch('/api/symbol/'+selected).then(r=>r.json());
  document.getElementById('symbolTitle').textContent=d.symbol;
  document.getElementById('price').textContent=money(d.price);
  const ch=document.getElementById('change'); ch.textContent=(d.change_pct>=0?'+':'')+d.change_pct.toFixed(2)+'%'; ch.className='change '+(d.change_pct>=0?'positive':'negative');
  const bias=document.getElementById('biasBadge'); bias.textContent=`${d.bias} ${d.bias==='LONG'?d.long_bias:d.bias==='SHORT'?d.short_bias:50}%`; bias.className='bias '+(d.bias==='LONG'?'positive':d.bias==='SHORT'?'negative':'');
  document.getElementById('status').textContent=d.status;
  document.getElementById('trigger').textContent=money(d.trigger);
  document.getElementById('stop').textContent=money(d.technical_stop);
  const items=[['1분봉',d.one_min],['5분봉',d.five_min],['VWAP',money(d.vwap)],['EMA 9',money(d.ema9)],['EMA 20',money(d.ema20)],['RVOL',d.rvol+'x'],['RSI',d.rsi],['ATR',d.atr_pct+'%']];
  document.getElementById('metrics').innerHTML=items.map(x=>`<div class="metric"><div class="m-label">${x[0]}</div><div class="m-value">${x[1]}</div></div>`).join('');
  const pi=document.getElementById('positionInfo');
  if(d.position){
    const pnl=d.pnl_pct; pi.innerHTML=`${d.position.side} · 진입 ${money(d.position.entry)} · ${(d.position.amount_krw/10000).toLocaleString()}만원 · <b class="${pnl>=0?'positive':'negative'}">${pnl>=0?'+':''}${pnl}%</b> · Signal <b>${d.position_signal}</b> <button onclick="closePos()" style="margin-left:8px">종료</button>`;
  }else pi.textContent='포지션 없음';
}

async function registerPos(){
  const entry=parseFloat(document.getElementById('entry').value);
  if(!entry){alert('진입가격을 입력하세요.');return}
  const amount=parseInt(document.getElementById('amount').value||'15000000');
  const side=document.getElementById('side').value;
  await fetch('/api/position',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:selected,entry,amount_krw:amount,side})});
  loadSymbol();
}
async function closePos(){await fetch('/api/position',{method:'DELETE'});loadSymbol()}
document.getElementById('registerBtn').onclick=registerPos;

loadMarket(); loadSymbol();
setInterval(()=>{loadMarket();loadSymbol();},5000);
