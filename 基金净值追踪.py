# -*- coding: utf-8 -*-
"""Cumulative real-NAV tracker and local HTML dashboard for fund_system."""
import argparse
import datetime as dt
import html
import json
import time
import webbrowser
from pathlib import Path

import akshare as ak
from fund_paths import data_path

CONFIG_PATH = data_path("fund_config.json")
HISTORY_PATH = data_path("valuation_history.json")
WATCHLIST_PATH = data_path("fund_watchlist.json")
DASHBOARD_PATH = data_path("基金净值追踪.html")
LOG_DIR = data_path("logs/.keep").parent


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as exc:
        print(f"读取 {path.name} 失败：{exc}")
        return default


def save_json_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def clean_number(value):
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if text.lower() in ("", "--", "nan", "none", "null"):
        return None
    try:
        number = float(text)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def iso_date(value):
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def merge_watchlist(config, history, saved):
    """Keep every previously tracked code, including funds removed from holdings."""
    watchlist = saved if isinstance(saved, dict) else {}
    for code, item in config.items():
        old = watchlist.get(code, {})
        watchlist[code] = {
            "fund_name": item.get("fund_name") or old.get("fund_name") or code,
            "status": old.get("status") or "持有",
            "added_at": old.get("added_at") or dt.datetime.now().strftime("%Y-%m-%d"),
        }
    for code, item in history.items():
        if code not in watchlist:
            watchlist[code] = {
                "fund_name": item.get("fund_name") or code,
                "status": "观察",
                "added_at": "历史记录",
            }
        elif not watchlist[code].get("fund_name"):
            watchlist[code]["fund_name"] = item.get("fund_name") or code
    return watchlist


def fetch_fund_records(code):
    """Fetch the complete published unit-NAV history, so missed days are backfilled."""
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    if df is None or df.empty:
        return []
    fetched_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for _, row in df.iterrows():
        date_text = iso_date(row.get("净值日期"))
        if not date_text:
            continue
        nav = clean_number(row.get("单位净值"))
        pct = clean_number(row.get("日增长率"))
        if nav is None and pct is None:
            continue
        rows.append({
            "date": date_text,
            "actual_pct": round(pct, 4) if pct is not None else None,
            "unit_nav": round(nav, 6) if nav is not None else None,
            "source": "akshare.fund_open_fund_info_em",
            "fetched_at": fetched_at,
        })
    return rows


def upsert_records(history, code, name, new_rows):
    fund = history.setdefault(code, {"fund_name": name, "records": []})
    fund["fund_name"] = name
    by_date = {r.get("date"): r for r in fund.get("records", []) if r.get("date")}
    added = updated = 0
    for row in new_rows:
        old = by_date.get(row["date"])
        if old is None:
            by_date[row["date"]] = row
            added += 1
        else:
            old.update({k: v for k, v in row.items() if v is not None})
            updated += 1
    fund["records"] = sorted(by_date.values(), key=lambda r: r.get("date", ""))
    return added, updated


def build_dashboard(watchlist, history, refreshed_at):
    funds = []
    for code, meta in watchlist.items():
        hist = history.get(code, {})
        records = sorted(hist.get("records", []), key=lambda r: r.get("date", ""))
        funds.append({
            "code": code,
            "name": meta.get("fund_name") or hist.get("fund_name") or code,
            "status": meta.get("status") or "观察",
            "records": records,
        })
    funds.sort(key=lambda f: f["name"])
    payload = json.dumps({"refreshedAt": refreshed_at, "funds": funds}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    page = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>基金净值追踪</title><style>
:root{--ink:#15231f;--muted:#73817b;--line:#e8eee9;--paper:#f4f7f4;--green:#187a5a;--lime:#c8efc7;--red:#c4524d;--amber:#bc8735;--navy:#142b25;--white:#fff;--shadow:0 12px 32px rgba(26,57,43,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif}.shell{max-width:1320px;margin:auto;padding:36px 28px 52px}.hero{position:relative;overflow:hidden;background:linear-gradient(118deg,#102b24 0%,#1b5842 63%,#287954 100%);border-radius:24px;color:white;padding:34px 38px;box-shadow:var(--shadow)}.hero:after{content:"";position:absolute;width:350px;height:350px;border:1px solid #ffffff22;border-radius:50%;right:8%;top:-210px;box-shadow:0 0 0 36px #ffffff0a,0 0 0 76px #ffffff08}.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#b6d6c5}.hero h1{font-size:30px;letter-spacing:.02em;margin:8px 0 4px}.hero p{margin:0;color:#d5e5dc}.hero-meta{position:relative;z-index:1;margin-top:25px;display:flex;gap:10px;flex-wrap:wrap}.pill{border:1px solid #ffffff35;border-radius:999px;padding:6px 11px;color:#e6f1eb;font-size:12px;background:#ffffff0d}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.card{background:white;border:1px solid var(--line);border-radius:17px;padding:18px 20px;box-shadow:0 3px 14px rgba(26,57,43,.025)}.card small{display:block;color:var(--muted);font-size:12px}.card strong{display:block;font-size:25px;line-height:1.2;margin-top:9px;letter-spacing:-.03em}.card em{font-style:normal;font-size:12px;font-weight:600;margin-left:5px}.panel{background:white;border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);overflow:hidden}.toolbar{padding:18px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--line);flex-wrap:wrap}.toolbar h2{font-size:16px;margin:0 auto 0 0}.search{min-width:220px;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#fbfcfb;font:inherit;outline:none}.search:focus{border-color:#7eae97;box-shadow:0 0 0 3px #d7eee1}.tabs{display:flex;background:#f1f5f1;border-radius:10px;padding:3px}.tabs button{border:0;border-radius:8px;padding:7px 11px;background:transparent;color:#66746d;font:inherit;cursor:pointer}.tabs button.active{background:white;color:var(--green);box-shadow:0 1px 4px #1b392412}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;min-width:920px}th{padding:12px 16px;text-align:left;color:#829088;font-size:11px;font-weight:600;letter-spacing:.04em;background:#fbfcfb;border-bottom:1px solid var(--line);white-space:nowrap}td{padding:13px 16px;border-bottom:1px solid #eef2ef;vertical-align:middle}tr:last-child td{border-bottom:0}tbody tr:hover{background:#fbfdfb}.fund{font-weight:650;font-size:13px}.code{font-size:11px;color:#8a9890;margin-top:2px}.status{display:inline-flex;border-radius:999px;padding:3px 9px;background:#edf5ef;color:#3d7659;font-size:11px}.status.watch{background:#f6f1e7;color:#9a7435}.ret{font-variant-numeric:tabular-nums;font-weight:700}.pos{color:var(--green)}.neg{color:var(--red)}.muted{color:#9aa69f}.spark{width:94px;height:30px;display:block}.data-date{white-space:nowrap;font-variant-numeric:tabular-nums;color:#67756e;font-size:12px}.footer{padding:14px 20px;color:#87938d;font-size:11px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.empty{text-align:center;padding:48px;color:var(--muted)}.stamp{display:flex;gap:7px;align-items:center}.dot{width:7px;height:7px;border-radius:50%;background:#5bc28a;box-shadow:0 0 0 3px #5bc28a22}
@media(max-width:780px){.shell{padding:18px 12px 32px}.hero{padding:26px 22px;border-radius:18px}.hero h1{font-size:25px}.cards{grid-template-columns:repeat(2,1fr);gap:9px}.card{padding:15px}.card strong{font-size:21px}.toolbar{padding:14px}.search{min-width:100%;order:3}.tabs{overflow:auto}.toolbar h2{width:100%}}
</style></head><body><main class="shell">
<section class="hero"><div class="eyebrow">PERSONAL FUND MONITOR</div><h1>基金净值追踪</h1><p>以已公布单位净值累计表现，持仓变化不会清除观察记录。</p><div class="hero-meta"><span class="pill" id="last-refresh">更新中</span><span class="pill">区间表现按交易日净值计算</span><span class="pill">估算涨跌与真实净值分开展示</span></div></section>
<section class="cards"><div class="card"><small>长期跟踪基金</small><strong id="total">—</strong></div><div class="card"><small>最近已公布净值日期</small><strong id="nav-date">—</strong></div><div class="card"><small>近十个交易日下跌超过 5%</small><strong id="deep-loss">—</strong></div><div class="card"><small>本次抓取状态</small><strong id="sync-state">—</strong></div></section>
<section class="panel"><div class="toolbar"><h2>真实净值表现</h2><div class="tabs" id="tabs"><button class="active" data-filter="all">全部</button><button data-filter="持有">持有中</button><button data-filter="观察">观察 / 已卖出</button></div><input id="search" class="search" placeholder="搜索基金名称或代码" autocomplete="off"></div><div class="scroll"><table><thead><tr><th>基金</th><th>状态</th><th>净值日期</th><th>近 1 日</th><th>近 5 日</th><th>近 10 日</th><th>近 20 日</th><th>近 60 日</th><th>近 30 日走势</th></tr></thead><tbody id="rows"></tbody></table><div class="empty" id="empty" hidden>没有符合条件的基金</div></div><div class="footer"><span>区间涨跌 = 最新单位净值 ÷ 区间起点单位净值 − 1；近 N 日按 N 个交易日间隔计算。</span><span>公开净值可能延迟发布；无足够历史时显示“数据积累中”。</span></div></section></main>
<script>
const DATA=__DATA__;
let filter='all';
const pct=(v)=>v==null?'<span class="muted">数据积累中</span>':`<span class="ret ${v<0?'neg':v>0?'pos':''}">${v>0?'+':''}${v.toFixed(2)}%</span>`;
function period(records,n){const rs=records.filter(r=>Number.isFinite(Number(r.unit_nav))&&Number(r.unit_nav)>0);if(rs.length<=n)return null;return (Number(rs.at(-1).unit_nav)/Number(rs.at(-1-n).unit_nav)-1)*100}
function spark(records){const rs=records.filter(r=>Number.isFinite(Number(r.unit_nav))).slice(-30);if(rs.length<2)return '';const vals=rs.map(r=>Number(r.unit_nav));const lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||1;const pts=vals.map((v,i)=>`${(i/(vals.length-1)*92+1).toFixed(1)},${(26-(v-lo)/span*22).toFixed(1)}`).join(' ');const color=vals.at(-1)>=vals[0]?'#27825e':'#ca6259';return `<svg class="spark" viewBox="0 0 94 30" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`}
function render(){const q=document.getElementById('search').value.trim().toLowerCase();let fs=DATA.funds.filter(f=>(filter==='all'||(filter==='观察'?f.status!=='持有':f.status===filter))&&(`${f.name}${f.code}`.toLowerCase().includes(q)));fs.sort((a,b)=>(period(a.records,10)??999)-(period(b.records,10)??999));const body=document.getElementById('rows');body.innerHTML=fs.map(f=>{const r=f.records.at(-1);const status=f.status||'观察';return `<tr><td><div class="fund">${esc(f.name)}</div><div class="code">${esc(f.code)}</div></td><td><span class="status ${status==='持有'?'':'watch'}">${esc(status)}</span></td><td class="data-date">${r?.date||'暂无数据'}</td><td>${pct(r?.actual_pct==null?null:Number(r.actual_pct))}</td><td>${pct(period(f.records,5))}</td><td>${pct(period(f.records,10))}</td><td>${pct(period(f.records,20))}</td><td>${pct(period(f.records,60))}</td><td>${spark(f.records)}</td></tr>`}).join('');document.getElementById('empty').hidden=fs.length>0}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
document.getElementById('total').textContent=DATA.funds.length;
const dates=DATA.funds.flatMap(f=>f.records.map(r=>r.date)).filter(Boolean).sort();document.getElementById('nav-date').textContent=dates.at(-1)||'暂无';
document.getElementById('deep-loss').textContent=DATA.funds.filter(f=>(period(f.records,10)??0)<-5).length;
document.getElementById('last-refresh').textContent='最近抓取：'+(DATA.refreshedAt||'暂无');
document.getElementById('sync-state').textContent=dates.length?'已累计':'暂无数据';
document.getElementById('search').addEventListener('input',render);document.querySelectorAll('#tabs button').forEach(b=>b.addEventListener('click',()=>{document.querySelector('#tabs .active').classList.remove('active');b.classList.add('active');filter=b.dataset.filter;render()}));render();
</script></body></html>'''.replace("__DATA__", payload)
    DASHBOARD_PATH.write_text(page, encoding="utf-8")


def run(open_page=False):
    config = load_json(CONFIG_PATH, {})
    history = load_json(HISTORY_PATH, {})
    saved = load_json(WATCHLIST_PATH, {})
    watchlist = merge_watchlist(config, history, saved)
    if not watchlist:
        print("追踪名单为空；请先维护 fund_config.json 或 fund_watchlist.json。")
        return 1
    save_json_atomic(WATCHLIST_PATH, watchlist)

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / ("fund_tracker_" + dt.date.today().strftime("%Y-%m-%d") + ".log")
    print(f"开始更新 {len(watchlist)} 只基金的真实净值（包含历史跟踪基金）……")
    total_added = total_updated = succeeded = failed = 0
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now}] 开始更新 {len(watchlist)} 只基金\n")
        for index, (code, meta) in enumerate(watchlist.items(), start=1):
            name = meta.get("fund_name") or code
            try:
                rows = fetch_fund_records(code)
                if not rows:
                    failed += 1
                    message = f"[{index}/{len(watchlist)}] {name} {code}：接口未返回净值"
                else:
                    added, updated = upsert_records(history, code, name, rows)
                    total_added += added
                    total_updated += updated
                    succeeded += 1
                    latest = rows[-1]
                    message = f"[{index}/{len(watchlist)}] {name}：最新净值日 {latest['date']}，补入 {added} 条、校正 {updated} 条"
                    save_json_atomic(HISTORY_PATH, history)
            except Exception as exc:
                failed += 1
                message = f"[{index}/{len(watchlist)}] {name} {code}：抓取失败：{exc}"
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()
            if index < len(watchlist):
                time.sleep(0.8)

    refreshed_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json_atomic(HISTORY_PATH, history)
    build_dashboard(watchlist, history, refreshed_at)
    summary = f"完成：成功 {succeeded} 只，失败 {failed} 只；新增 {total_added} 条历史、校正 {total_updated} 条。\n看板：{DASHBOARD_PATH}"
    print(summary)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(summary + "\n")
    if open_page:
        webbrowser.open(DASHBOARD_PATH.as_uri())
    return 0 if succeeded else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="更新累积真实净值并生成 HTML 看板")
    parser.add_argument("--open", action="store_true", help="更新完成后打开 HTML 看板")
    args = parser.parse_args()
    raise SystemExit(run(open_page=args.open))
