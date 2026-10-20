# -*- coding: utf-8 -*-
"""Independent fund watch station. Directly reads Eastmoney public data endpoints."""
import argparse
import datetime as dt
import json
import msvcrt
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from fund_paths import data_path

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
WATCHLIST = data_path("基金观察名单.json")
DB = data_path("基金观察站.db")
PAGE = data_path("基金净值追踪.html")
ALERTS_JSON = data_path("基金阈值触发.json")
SYNC_LOCK_FILE = data_path("fund_watch_sync.lock")
LOGS = data_path("logs/.keep").parent
TZ = dt.timezone(dt.timedelta(hours=8))
SYNC_LOCK = threading.Lock()
SYNC_STATUS_LOCK = threading.Lock()
SYNC_STATUS = {"state": "idle", "started_at": None, "finished_at": None, "message": "尚未手动同步"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
}


def db_conn():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS funds(
      code TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT '持有',
      anchor_target_date TEXT, anchor_date TEXT, anchor_nav REAL, updated_at TEXT,
      display_order INTEGER NOT NULL DEFAULT 999999
    );
    CREATE TABLE IF NOT EXISTS nav(
      fund_code TEXT NOT NULL, date TEXT NOT NULL, unit_nav REAL, accumulated_nav REAL,
      daily_pct REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY(fund_code,date)
    );
    CREATE TABLE IF NOT EXISTS holdings_snapshots(
      fund_code TEXT NOT NULL, quarter TEXT NOT NULL, report_date TEXT,
      fetched_at TEXT NOT NULL, source TEXT NOT NULL,
      PRIMARY KEY(fund_code,quarter)
    );
    CREATE TABLE IF NOT EXISTS holdings(
      fund_code TEXT NOT NULL, quarter TEXT NOT NULL, stock_code TEXT NOT NULL,
      stock_name TEXT, weight_pct REAL, shares REAL, value REAL,
      PRIMARY KEY(fund_code,quarter,stock_code)
    );
    CREATE TABLE IF NOT EXISTS stock_prices(
      stock_code TEXT NOT NULL, date TEXT NOT NULL, open REAL, close REAL,
      high REAL, low REAL, volume REAL, amount REAL, pct_change REAL,
      fetched_at TEXT NOT NULL, PRIMARY KEY(stock_code,date)
    );
    CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS sync_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
      status TEXT, details TEXT
    );
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(funds)")}
    if "display_order" not in columns:
        conn.execute("ALTER TABLE funds ADD COLUMN display_order INTEGER NOT NULL DEFAULT 999999")
    return conn


def now_text():
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def load_watchlist():
    data = json.loads(WATCHLIST.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("基金观察名单.json 应为对象数组")
    normalized = []
    for item in data:
        code = str(item.get("code", "")).strip().zfill(6)
        name = str(item.get("name", code)).strip()
        if re.fullmatch(r"\d{6}", code):
            normalized.append({"code": code, "name": name, "status": item.get("status", "持有")})
    return normalized


def import_watchlist(conn):
    for order, item in enumerate(load_watchlist()):
        conn.execute("""INSERT INTO funds(code,name,status,updated_at,display_order) VALUES(?,?,?,?,?)
          ON CONFLICT(code) DO UPDATE SET name=excluded.name,display_order=excluded.display_order""",
          (item["code"], item["name"], item["status"], now_text(), order))
    conn.commit()


def save_watchlist_file(conn):
    items = [dict(r) for r in conn.execute("SELECT code,name,status FROM funds ORDER BY display_order,code")]
    WATCHLIST.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def fetch_fund_name(code):
    response = requests.get(f"https://fund.eastmoney.com/pingzhongdata/{code}.js", headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = "utf-8"
    match = re.search(r'var\s+fS_name\s*=\s*("(?:\\.|[^"\\])*")', response.text)
    return json.loads(match.group(1)) if match else code


def js_array(text, variable):
    match = re.search(r"var\s+" + re.escape(variable) + r"\s*=\s*(\[.*?\]);", text, re.S)
    if not match:
        return []
    return json.loads(match.group(1))


def fetch_nav(code):
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    text = response.text
    trend = js_array(text, "Data_netWorthTrend")
    accumulated = js_array(text, "Data_ACWorthTrend")
    acc_by_date = {}
    for row in accumulated:
        if len(row) >= 2:
            date = dt.datetime.fromtimestamp(float(row[0]) / 1000, TZ).strftime("%Y-%m-%d")
            acc_by_date[date] = float(row[1]) if row[1] not in (None, "") else None
    result = []
    fetched = now_text()
    for row in trend:
        date = dt.datetime.fromtimestamp(float(row["x"]) / 1000, TZ).strftime("%Y-%m-%d")
        result.append((code, date, float(row["y"]) if row.get("y") not in (None, "") else None,
                       acc_by_date.get(date), float(row["equityReturn"]) if row.get("equityReturn") not in (None, "") else None,
                       "Eastmoney pingzhongdata", fetched))
    return result


def save_nav(conn, code, rows):
    conn.executemany("""INSERT INTO nav(fund_code,date,unit_nav,accumulated_nav,daily_pct,source,fetched_at)
       VALUES(?,?,?,?,?,?,?) ON CONFLICT(fund_code,date) DO UPDATE SET
       unit_nav=excluded.unit_nav, accumulated_nav=COALESCE(excluded.accumulated_nav,nav.accumulated_nav),
       daily_pct=excluded.daily_pct, source=excluded.source, fetched_at=excluded.fetched_at""", rows)
    conn.commit()
    fill_pending_anchor(conn, code)


def fill_pending_anchor(conn, code):
    fund = conn.execute("SELECT status,anchor_target_date,anchor_nav FROM funds WHERE code=?", (code,)).fetchone()
    if not fund or fund["status"] != "观察" or not fund["anchor_target_date"] or fund["anchor_nav"] is not None:
        return
    row = conn.execute("SELECT date,COALESCE(accumulated_nav,unit_nav) AS nav FROM nav WHERE fund_code=? AND date>=? AND unit_nav IS NOT NULL ORDER BY date LIMIT 1",
                       (code, fund["anchor_target_date"])).fetchone()
    if row:
        conn.execute("UPDATE funds SET anchor_date=?,anchor_nav=?,updated_at=? WHERE code=?",
                     (row["date"], row["nav"], now_text(), code))
        conn.commit()


def quarter_of_label(text):
    m = re.search(r"(20\d{2})\s*年\s*([1-4])\s*季", text)
    return f"{m.group(1)}Q{m.group(2)}" if m else None


def archive_response(code, year):
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {"type": "jjcc", "code": code, "rt": str(time.time()), "topline": "10000", "year": str(year), "month": ""}
    headers = {**HEADERS, "Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html", "X-Requested-With": "XMLHttpRequest"}
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    match = re.search(r"content:(\"(?:\\.|[^\"\\])*\")", response.text, re.S)
    if not match:
        raise ValueError("持仓接口未返回 content 字段")
    content = json.loads(match.group(1))
    soup = BeautifulSoup(content, "html.parser")
    headings = soup.select("h4.t")
    tables = pd.read_html(StringIO(content)) if "<table" in content else []
    snapshots = []
    for i, heading in enumerate(headings):
        quarter = quarter_of_label(heading.get_text(" ", strip=True))
        if not quarter or i >= len(tables):
            continue
        table = tables[i]
        cols = {str(c).replace(" ", "").replace("（", "(").replace("）", ")"): c for c in table.columns}
        def col(*names):
            for name in names:
                key = name.replace(" ", "").replace("（", "(").replace("）", ")")
                if key in cols: return cols[key]
            return None
        c_code, c_name = col("股票代码"), col("股票名称")
        c_weight, c_shares, c_value = col("占净值比例", "占净值比率"), col("持股数(万股)", "持股数"), col("持仓市值(万元)", "持仓市值")
        rows = []
        for _, row in table.iterrows():
            code_value = str(row.get(c_code, "")).strip().split(".")[0]
            if not re.fullmatch(r"\d{6}", code_value):
                continue
            def number(column, percent=False):
                if not column: return None
                value = str(row.get(column, "")).replace("%", "").replace(",", "").strip()
                try: return float(value)
                except (ValueError, TypeError): return None
            rows.append({"code": code_value, "name": str(row.get(c_name, "")),
                         "weight": number(c_weight), "shares": number(c_shares), "value": number(c_value)})
        report_date_match = re.search(r"截止日期\s*([0-9-]+)", heading.get_text(" ", strip=True))
        report_date = report_date_match.group(1) if report_date_match else None
        snapshots.append((quarter, report_date, rows))
    return snapshots


def save_holdings(conn, fund_code, snapshots):
    fetched = now_text()
    for quarter, report_date, rows in snapshots:
        conn.execute("INSERT OR REPLACE INTO holdings_snapshots VALUES(?,?,?,?,?)",
                     (fund_code, quarter, report_date, fetched, "Eastmoney FundArchivesDatas"))
        conn.execute("DELETE FROM holdings WHERE fund_code=? AND quarter=?", (fund_code, quarter))
        conn.executemany("INSERT INTO holdings VALUES(?,?,?,?,?,?,?)",
                         [(fund_code, quarter, r["code"], r["name"], r["weight"], r["shares"], r["value"]) for r in rows])
    conn.commit()


def fetch_stock_prices(stock_code, since):
    market = "1" if stock_code.startswith(("5", "6", "9")) else "0"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {"secid": f"{market}.{stock_code}", "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61", "klt": "101", "fqt": "1",
              "beg": since.replace("-", ""), "end": dt.date.today().strftime("%Y%m%d"), "lmt": "10000"}
    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = (response.json().get("data") or {}).get("klines") or []
    fetched = now_text()
    result = []
    for line in data:
        parts = line.split(",")
        if len(parts) < 9: continue
        def val(i):
            try: return float(parts[i]) if parts[i] else None
            except (ValueError, IndexError): return None
        result.append((stock_code, parts[0], val(1), val(2), val(3), val(4), val(5), val(6), val(8), fetched))
    return result


def next_quarter_target(today):
    quarters = []
    for year in range(today.year - 2, today.year + 1):
        for q, month, day in ((1,3,31),(2,6,30),(3,9,30),(4,12,31)):
            end = dt.date(year, month, day)
            if end + dt.timedelta(days=14) <= today:
                quarters.append((end, f"{year}Q{q}", year))
    return max(quarters)[1:] if quarters else None


def should_attempt_quarter(conn, quarter):
    key = f"quarter_attempt:{quarter}"
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    if row:
        try:
            last = dt.datetime.fromisoformat(row["value"])
            if (dt.datetime.now(TZ) - last).days < 7:
                return False
        except ValueError: pass
    return True


def fetch_quarterly_holdings(conn, force=False):
    target = next_quarter_target(dt.date.today())
    if not target: return 0
    target_quarter, year = target
    if not force and not should_attempt_quarter(conn, target_quarter): return 0
    count = 0
    codes = [r["code"] for r in conn.execute("SELECT code FROM funds").fetchall()]
    for code in codes:
        have_target = conn.execute("SELECT 1 FROM holdings_snapshots WHERE fund_code=? AND quarter=?", (code, target_quarter)).fetchone()
        # Initial load backfills the current and prior reporting year from the same public API.
        years = [year, year - 1] if not conn.execute("SELECT 1 FROM holdings_snapshots WHERE fund_code=? LIMIT 1", (code,)).fetchone() else [year]
        if have_target and not force: continue
        for query_year in years:
            try:
                snapshots = archive_response(code, query_year)
                save_holdings(conn, code, snapshots)
                count += len(snapshots)
                time.sleep(0.25)
            except Exception as exc:
                log_line(f"持仓抓取失败 {code} {query_year}：{exc}")
    conn.execute("INSERT INTO sync_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (f"quarter_attempt:{target_quarter}", dt.datetime.now(TZ).isoformat(timespec="seconds")))
    conn.commit()
    return count


def sync_stock_quotes(conn):
    # Refresh histories for stocks in the newest published holdings; saves direct daily prices for reference.
    newest = conn.execute("SELECT MAX(quarter) AS q FROM holdings_snapshots").fetchone()["q"]
    if not newest: return 0
    stocks = conn.execute("SELECT DISTINCT stock_code FROM holdings WHERE quarter=?", (newest,)).fetchall()
    since = (dt.date.today() - dt.timedelta(days=210)).strftime("%Y-%m-%d")
    count = 0
    for row in stocks:
        code = row["stock_code"]
        try:
            data = fetch_stock_prices(code, since)
            conn.executemany("""INSERT INTO stock_prices VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_code,date) DO UPDATE SET
              open=excluded.open,close=excluded.close,high=excluded.high,low=excluded.low,volume=excluded.volume,
              amount=excluded.amount,pct_change=excluded.pct_change,fetched_at=excluded.fetched_at""", data)
            conn.commit(); count += len(data)
        except Exception as exc:
            log_line(f"个股行情抓取失败 {code}：{exc}")
        time.sleep(0.15)
    return count


def log_line(message):
    LOGS.mkdir(exist_ok=True)
    path = LOGS / ("fund_watch_station_" + dt.date.today().isoformat() + ".log")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{now_text()}] {message}\n")
    print(message, flush=True)


def _sync_all_impl(force_quarter=False):
    conn = db_conn(); import_watchlist(conn)
    start = now_text(); failures = []; total_records = 0
    funds = conn.execute("SELECT code,name FROM funds ORDER BY code").fetchall()
    log_line(f"独立基金观察站开始同步 {len(funds)} 只基金的历史净值")
    for i, fund in enumerate(funds, 1):
        try:
            rows = fetch_nav(fund["code"])
            save_nav(conn, fund["code"], rows)
            total_records += len(rows)
            log_line(f"[{i}/{len(funds)}] {fund['name']}：已同步 {len(rows)} 条净值，最新 {rows[-1][1] if rows else '无'}")
        except Exception as exc:
            failures.append(fund["code"])
            log_line(f"[{i}/{len(funds)}] {fund['name']}：净值接口失败：{exc}")
        time.sleep(0.25)
    holdings_count = fetch_quarterly_holdings(conn, force=force_quarter)
    # A manual refresh rechecks the quarterly endpoint immediately, but does not
    # fan out into hundreds of auxiliary stock-history requests.
    stock_count = sync_stock_quotes(conn) if holdings_count and not force_quarter else 0
    finish = now_text(); status = "部分失败" if failures else "成功"
    details = f"基金 {len(funds)} 只；净值点 {total_records}；季度持仓快照 {holdings_count}；股票日线 {stock_count}；失败代码 {','.join(failures) or '无'}"
    conn.execute("INSERT INTO sync_log(started_at,finished_at,status,details) VALUES(?,?,?,?)", (start, finish, status, details))
    conn.execute("INSERT INTO sync_state(key,value) VALUES('last_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (finish,))
    conn.commit()
    write_alert_report(conn)
    conn.close()
    render_page()
    log_line("同步完成：" + details)
    return 0 if not failures else 1


def _acquire_process_sync_lock():
    handle = SYNC_LOCK_FILE.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _release_process_sync_lock(handle):
    if handle is None:
        return
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def _run_locked_sync(process_lock, force_quarter=False):
    started = now_text()
    with SYNC_STATUS_LOCK:
        SYNC_STATUS.update(state="running", started_at=started, finished_at=None, message="正在同步最新可用净值和到期季度持仓")
    try:
        code = _sync_all_impl(force_quarter=force_quarter)
        with SYNC_STATUS_LOCK:
            SYNC_STATUS.update(state="completed" if code == 0 else "partial_failure",
                               finished_at=now_text(), message="同步完成" if code == 0 else "同步完成，但有基金净值抓取失败")
        return code
    except Exception as exc:
        with SYNC_STATUS_LOCK:
            SYNC_STATUS.update(state="failed", finished_at=now_text(), message=f"同步失败：{exc}")
        raise
    finally:
        _release_process_sync_lock(process_lock)
        SYNC_LOCK.release()


def sync_all(force_quarter=False):
    if not SYNC_LOCK.acquire(blocking=False):
        log_line("同步请求未执行：已有同步任务正在运行")
        return 2
    process_lock = _acquire_process_sync_lock()
    if process_lock is None:
        with SYNC_STATUS_LOCK:
            SYNC_STATUS.update(state="busy", message="另一同步进程正在运行，请稍后再试")
        SYNC_LOCK.release()
        return 2
    return _run_locked_sync(process_lock, force_quarter=force_quarter)


def start_manual_sync():
    if not SYNC_LOCK.acquire(blocking=False):
        return False
    process_lock = _acquire_process_sync_lock()
    if process_lock is None:
        with SYNC_STATUS_LOCK:
            SYNC_STATUS.update(state="busy", message="另一同步进程正在运行，请稍后再试")
        SYNC_LOCK.release()
        return False
    thread = threading.Thread(target=_run_locked_sync, args=(process_lock, True), daemon=True, name="fund-watch-manual-sync")
    try:
        thread.start()
    except Exception:
        _release_process_sync_lock(process_lock)
        SYNC_LOCK.release()
        raise
    return True


def api_payload(conn):
    funds = []
    for fund in conn.execute("SELECT * FROM funds ORDER BY CASE status WHEN '持有' THEN 0 ELSE 1 END, display_order, code"):
        rows = [dict(r) for r in conn.execute("SELECT date,unit_nav,accumulated_nav,daily_pct FROM nav WHERE fund_code=? ORDER BY date", (fund["code"],))]
        funds.append({"code": fund["code"], "name": fund["name"], "status": fund["status"],
                      "anchorTarget": fund["anchor_target_date"], "anchorDate": fund["anchor_date"], "anchorNav": fund["anchor_nav"], "records": rows})
    holdings = []
    for fund in funds:
        snap = conn.execute("SELECT quarter,report_date,fetched_at FROM holdings_snapshots WHERE fund_code=? ORDER BY quarter DESC LIMIT 1", (fund["code"],)).fetchone()
        if not snap:
            continue
        rows = [dict(r) for r in conn.execute("SELECT stock_code,stock_name,weight_pct,shares,value FROM holdings WHERE fund_code=? AND quarter=? ORDER BY weight_pct DESC LIMIT 10", (fund["code"], snap["quarter"]))]
        holdings.append({"code": fund["code"], "name": fund["name"], "quarter": snap["quarter"], "reportDate": snap["report_date"], "fetchedAt": snap["fetched_at"], "stocks": rows})
    return {"funds": funds, "holdings": holdings, "lastSync": (conn.execute("SELECT value FROM sync_state WHERE key='last_sync'").fetchone() or {"value": None})["value"]}



def _js_month_cutoff(date_value, months):
    """Match the dashboard Date.setMonth cutoff, including month-end rollover."""
    month_index = date_value.month - 1 - months
    year = date_value.year + month_index // 12
    month = month_index % 12 + 1
    return dt.date(year, month, 1) + dt.timedelta(days=date_value.day - 1)


def build_alert_report(conn, months=3):
    """Build a neutral, machine-readable report using the dashboard's own signal rules."""
    funds = []
    for fund in conn.execute("SELECT code,name,status FROM funds ORDER BY CASE status WHEN '持有' THEN 0 ELSE 1 END,display_order,code"):
        source = [dict(r) for r in conn.execute("SELECT date,unit_nav,accumulated_nav,daily_pct FROM nav WHERE fund_code=? ORDER BY date", (fund["code"],))]
        rows = []
        for row in source:
            value = row["accumulated_nav"] if row["accumulated_nav"] is not None else row["unit_nav"]
            if value is not None:
                rows.append((dt.date.fromisoformat(row["date"]), float(value), row))
        base = {"code": fund["code"], "name": fund["name"], "status": fund["status"]}
        if not rows:
            funds.append({**base, "sample_sufficient": False, "signal": "无净值数据", "triggered": False})
            continue
        last_date, last_nav, last_row = rows[-1]
        floor = _js_month_cutoff(last_date, months)
        span = [x for x in rows if floor <= x[0] <= last_date]
        peak_date, peak_nav, _ = max(span, key=lambda x: x[1])
        current = (last_nav / peak_nav - 1) * 100 if peak_nav else 0.0
        item = {**base, "as_of": last_date.isoformat(), "current_nav": last_nav,
                "latest_daily_pct": last_row["daily_pct"], "window_months": months,
                "window_peak_date": peak_date.isoformat(), "window_peak_nav": peak_nav,
                "current_drawdown_pct": round(current, 4)}
        if len(rows) < 180:
            funds.append({**item, "sample_sufficient": False, "sample_months": 0,
                          "signal": "样本不足", "triggered": False})
            continue
        month_ends = [rows[i] for i in range(len(rows))
                      if i == len(rows) - 1 or rows[i][0].strftime("%Y-%m") != rows[i + 1][0].strftime("%Y-%m")]
        draws = []
        for end_date, end_nav, _ in month_ends:
            lower = _js_month_cutoff(end_date, months)
            sample = [v for d, v, _ in rows if lower <= d <= end_date]
            high = max(sample) if sample else None
            if high and end_nav / high - 1 < 0:
                draws.append((end_nav / high - 1) * 100)
        if len(draws) < 12:
            funds.append({**item, "sample_sufficient": False, "sample_months": len(draws),
                          "signal": "样本不足", "triggered": False})
            continue
        draws.sort()
        quantile = lambda ratio: draws[int((len(draws) - 1) * ratio)]
        watch, deep, extreme = quantile(.25), quantile(.10), quantile(.05)
        signal = "极深回撤" if current <= extreme else "深回撤关注" if current <= deep else "回撤关注" if current <= watch else "继续观察"
        funds.append({**item, "sample_sufficient": True, "sample_months": len(draws), "signal": signal,
                      "triggered": signal != "继续观察",
                      "thresholds_pct": {"watch": round(watch, 4), "deep": round(deep, 4), "extreme": round(extreme, 4)}})
    latest = max((f.get("as_of", "") for f in funds), default="") or None
    triggered = [f for f in funds if f.get("triggered")]
    return {"schema_version": 1, "generated_at": now_text(), "data_as_of": latest,
            "window_months": months,
            "strategy": "累计净值优先；逐月计算月末相对近N个月高点的回撤；历史回撤样本的25%、10%、5%分位数分别为关注、深回撤、极深阈值。",
            "fund_count": len(funds), "triggered_count": len(triggered),
            "triggered_funds": triggered, "funds": funds}


def write_alert_report(conn):
    report = build_alert_report(conn)
    temp = ALERTS_JSON.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(ALERTS_JSON)
    return report

def render_page():
    conn = db_conn(); data = json.dumps(api_payload(conn), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"); conn.close()
    page = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>基金观察站</title><style>
:root{--ink:#182822;--muted:#75857d;--line:#e6eee9;--paper:#f2f6f3;--green:#176e50;--red:#c4524d;--navy:#132b23;--shadow:0 16px 40px #1937280b}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 90% 0,#deeee2 0,transparent 32%),var(--paper);color:var(--ink);font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif}.shell{max-width:1380px;margin:auto;padding:30px 26px 50px}.hero{background:linear-gradient(120deg,#132b23,#1e6046 72%,#2e805d);border-radius:24px;padding:30px 34px;color:#fff;position:relative;overflow:hidden;box-shadow:var(--shadow)}.hero:after{content:"";position:absolute;width:400px;height:400px;right:3%;top:-250px;border:1px solid #ffffff24;border-radius:50%;box-shadow:0 0 0 40px #ffffff0a,0 0 0 85px #ffffff08}.eyebrow{font-size:11px;letter-spacing:.18em;color:#b9d9c8}.hero h1{font-size:29px;margin:7px 0 4px}.hero p{margin:0;color:#deebe3}.hero-bottom{margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;position:relative;z-index:1}.chip{border:1px solid #ffffff38;border-radius:999px;padding:6px 11px;font-size:12px;background:#ffffff0d}.hero-bottom .action{background:#fff;color:#176e50;border-radius:999px;padding:7px 13px}.hero-bottom .action:disabled{opacity:.72;cursor:wait}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:16px 0}.metric,.panel{background:#fff;border:1px solid var(--line);border-radius:17px;box-shadow:var(--shadow)}.metric{padding:17px 20px}.metric small{color:var(--muted)}.metric strong{display:block;font-size:24px;margin-top:7px;letter-spacing:-.03em}.panel{overflow:hidden}.toolbar{padding:16px 19px;display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line);flex-wrap:wrap}.toolbar h2{font-size:16px;margin:0 auto 0 0}.search,select{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#fbfcfb;color:var(--ink);font:inherit}.tabs{display:flex;background:#f1f5f1;border-radius:10px;padding:3px}.tabs button,.action{border:0;border-radius:8px;padding:7px 11px;background:transparent;color:#65756c;font:inherit;cursor:pointer}.tabs button.active{background:white;color:var(--green);box-shadow:0 1px 4px #1b392412}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;min-width:1080px}th{padding:12px 14px;background:#fbfcfb;color:#819087;text-align:left;font-size:11px;letter-spacing:.04em;white-space:nowrap;border-bottom:1px solid var(--line)}td{padding:13px 14px;border-bottom:1px solid #eef2ef;vertical-align:middle}tbody tr:hover{background:#fbfdfb}.fund{font-weight:650}.code{font-size:11px;color:#92a097;margin-top:2px}.status{display:inline-block;background:#eaf4ed;color:#3a7957;padding:3px 9px;border-radius:99px;font-size:11px}.status.watch{background:#f6f0e4;color:#9b783a}.num{font-variant-numeric:tabular-nums;font-weight:700}.neg{color:var(--red)}.pos{color:var(--green)}.muted{color:#9ba7a0}.peak{font-size:11px;color:#819087;margin-top:2px;white-space:nowrap}.action{background:#edf5ef;color:#28744f;font-size:12px}.action:hover{background:#dceee1}.action.watch{background:#f7f0e5;color:#8f6e34}.date-input{width:135px;border:1px solid var(--line);border-radius:8px;padding:6px;font:12px inherit}.spark{width:90px;height:28px}.note{padding:13px 18px;color:#87938d;font-size:11px;border-top:1px solid var(--line)}.bar{height:5px;border-radius:6px;background:#eaf0ec;overflow:hidden;min-width:72px}.bar i{display:block;height:100%;background:linear-gradient(90deg,#df786c,#bd454a)}.empty{text-align:center;padding:45px;color:var(--muted)}.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:#173b2c;color:#fff;padding:10px 16px;border-radius:10px;box-shadow:var(--shadow);display:none}.legend{font-size:11px;color:var(--muted)}.list-panel{margin-top:16px}.list-heading{display:flex;align-items:center;justify-content:space-between;padding:17px 20px;border-bottom:1px solid var(--line)}.list-heading h2{margin:0;font-size:17px}.list-heading span{font-size:12px;color:var(--muted)}.count-badge{border-radius:999px;background:#e9f3ed;color:var(--green);padding:6px 12px;font-size:12px;white-space:nowrap}.watch-badge{background:#f6f0e4;color:#947238}.fund-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:12px;padding:15px}.fund-card{border:1px solid var(--line);border-radius:14px;padding:15px;background:linear-gradient(145deg,#fff,#fbfdfb);min-width:0}.card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.card-name{font-weight:700;line-height:1.35}.card-code{font-size:11px;color:var(--muted);margin-top:3px}.card-date{text-align:right;font-size:11px;color:var(--muted);white-space:nowrap}.card-day{display:block;font-size:13px;font-weight:700;margin-top:3px}.card-main{display:grid;grid-template-columns:1fr 1.15fr;gap:12px;margin:15px 0}.card-stat{background:#f5f8f5;border-radius:11px;padding:10px 12px}.card-label{font-size:10px;color:var(--muted);display:block}.card-dd{font-size:22px;font-weight:750;line-height:1.25;margin-top:3px}.card-peak{font-size:11px;color:var(--muted);margin-top:3px}.card-signal{display:flex;justify-content:space-between;align-items:center;gap:8px}.card-signal b{font-size:13px}.card-threshold{font-size:10px;color:var(--muted);margin-top:3px}.card-bar{height:5px;border-radius:8px;background:#e9efeb;overflow:hidden;margin-top:7px}.card-bar i{display:block;height:100%;background:linear-gradient(90deg,#e2a15f,#c4524d)}.card-foot{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:10px}.anchor-box{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:6px;flex-wrap:wrap}.anchor-box .date-input{width:125px}.card-foot .action{white-space:nowrap}
@media(max-width:760px){.shell{padding:15px 10px}.hero{padding:24px 20px}.hero h1{font-size:24px}.metrics{grid-template-columns:repeat(2,1fr);gap:9px}.metric{padding:13px}.metric strong{font-size:20px}.toolbar h2{width:100%}.search{width:100%;order:4}}
</style></head><body><main class="shell"><section class="hero"><div class="eyebrow">PERSONAL FUND WATCH</div><h1>基金观察站</h1><p>直接读取基金公开历史净值；卖出后转入观察池，从指定日期开始跟踪回撤。</p><div class="hero-bottom"><span class="chip" id="synctime">最近同步：载入中</span><span class="chip">近高点回撤：默认近 3 个月</span><span class="chip">观察锚点：卖出日期对应的实际净值</span><button class="action" id="syncButton" onclick="manualSync()">手动更新净值/季报</button><span class="chip" id="syncStatus">只更新接口已发布的数据</span></div></section>
<section class="metrics"><div class="metric"><small>跟踪基金</small><strong id="total">—</strong></div><div class="metric"><small>未持有观察数量</small><strong id="observationTotal">—</strong></div><div class="metric"><small>观察池中最大跌幅</small><strong id="worst">—</strong></div><div class="metric"><small>净值数据更新至</small><strong id="lastdate">—</strong></div></section>
<section class="panel"><div class="toolbar"><h2>列表筛选</h2><select id="window"><option value="3">近 3 个月高点</option><option value="1">近 1 个月高点</option><option value="6">近 6 个月高点</option></select><input id="search" class="search" placeholder="搜索名称或代码" autocomplete="off"><input id="newcode" class="search" style="min-width:125px" placeholder="添加基金代码"><button class="action" onclick="addFund()">＋ 添加到观察</button></div><div class="note">已分成两个独立区域：上方是当前持有，下方是未持有观察。回撤信号按每只基金自己的历史计算，仅供参考，不会自动下单。</div></section><section class="panel list-panel"><div class="list-heading"><div><h2>当前持有</h2><span>只显示持有基金，顺序与估值清单一致</span></div><b class="count-badge" id="heldcount">— 只</b></div><div class="fund-grid" id="heldRows"></div><div class="empty" id="heldEmpty" hidden>没有匹配的持有基金</div></section><section class="panel list-panel"><div class="list-heading"><div><h2>未持有观察池</h2><span>只显示已卖出或待观察基金，不混入持有清单</span></div><b class="count-badge watch-badge" id="watchcount">— 只</b></div><div class="fund-grid" id="watchRows"></div><div class="empty" id="watchEmpty" hidden>观察池暂无匹配基金</div><div class="note">观察锚点取指定日期后首个已公布累计净值。要开始观察，从卡片内选择日期并更新锚点。</div></section><section class="panel" style="margin-top:16px"><div class="toolbar"><h2>最近一期基金持仓</h2><select id="holdingFund" aria-label="选择基金"></select><span class="legend" id="holdingMeta">季度持仓同步后显示</span></div><div class="scroll"><table style="min-width:620px"><thead><tr><th>股票代码</th><th>股票名称</th><th>披露占净值比</th><th>持股数量（原披露单位）</th><th>持仓市值</th></tr></thead><tbody id="holdingRows"></tbody></table><div class="empty" id="holdingEmpty" hidden>暂时没有可显示的季度持仓</div></div><div class="note">这里显示基金公开季报中的前十大持仓；报告发布前，季度数据不会提前出现。</div></section><div class="toast" id="toast"></div></main>
<script>const DATA=__DATA__;let filter='all';const fmt=(x)=>x==null?'—':`${x>0?'+':''}${x.toFixed(2)}%`;const cls=x=>x==null?'muted':x<0?'neg':x>0?'pos':'';const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function cutoff(months,last){const [year,month,day]=last.split('-').map(Number);const d=new Date(year,month-1,day);d.setMonth(d.getMonth()-months);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}function metricNav(r){return r.accumulated_nav??r.unit_nav}function calc(f,months){const valid=f.records.filter(r=>metricNav(r)!=null);if(!valid.length)return{};const last=valid.at(-1),start=cutoff(months,last.date),span=valid.filter(r=>r.date>=start&&r.date<=last.date);if(!span.length)return{};const peak=span.reduce((a,b)=>metricNav(b)>metricNav(a)?b:a,span[0]);const dd=(metricNav(last)/metricNav(peak)-1)*100;let anchorChange=null;if(f.anchorNav!=null)anchorChange=(metricNav(last)/f.anchorNav-1)*100;return{last,peak,dd,anchorChange,anchorDate:f.anchorDate||f.anchorTarget}}function buySignal(f,months,current){const rows=f.records.filter(r=>metricNav(r)!=null);if(rows.length<180)return{label:'样本不足',n:0};const monthEnds=[];for(let i=0;i<rows.length;i++){const key=rows[i].date.slice(0,7),next=rows[i+1]?.date.slice(0,7);if(key!==next)monthEnds.push(rows[i])}const draws=[];for(const r of monthEnds){const floor=cutoff(months,r.date);let high=0,lo=0,hi=rows.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(rows[mid].date<floor)lo=mid+1;else hi=mid}for(let j=lo;j<rows.length&&rows[j].date<=r.date;j++)high=Math.max(high,metricNav(rows[j]));if(high>0){const dd=(metricNav(r)/high-1)*100;if(dd<0)draws.push(dd)}}if(draws.length<12)return{label:'样本不足',n:draws.length};draws.sort((a,b)=>a-b);const quantile=p=>draws[Math.floor((draws.length-1)*p)];const deep=quantile(.10),watch=quantile(.25),extreme=quantile(.05);let label='继续观察';if(current<=extreme)label='极深回撤';else if(current<=deep)label='深回撤关注';else if(current<=watch)label='回撤关注';return{label,watch,deep,extreme,n:draws.length}}function toast(t){const x=document.getElementById('toast');x.textContent=t;x.style.display='block';setTimeout(()=>x.style.display='none',2400)}async function manualSync(){const button=document.getElementById('syncButton'),status=document.getElementById('syncStatus');button.disabled=true;button.textContent='正在同步…';status.textContent='正在读取最新可用净值和到期季报';try{const start=await fetch('/api/sync',{method:'POST'});if(!start.ok&&start.status!==409)throw new Error('sync start failed');const poll=async()=>{try{const r=await fetch('/api/sync-status',{cache:'no-store'});if(!r.ok)throw new Error('status failed');const state=await r.json();status.textContent=state.message||'正在同步';if(state.state==='running'||state.state==='idle'){setTimeout(poll,1500);return}button.disabled=false;button.textContent='手动更新净值/季报';if(state.state==='completed'){status.textContent='同步完成 · '+(state.finished_at||'');setTimeout(()=>location.reload(),500)}else{status.textContent=state.message||'同步未完整完成';toast(state.message||'同步未完整完成')}}catch(e){button.disabled=false;button.textContent='手动更新净值/季报';status.textContent='无法读取同步状态';toast('同步状态读取失败，请刷新看板')}};await poll()}catch(e){button.disabled=false;button.textContent='手动更新净值/季报';status.textContent='启动同步失败';toast('启动同步失败，请检查本机服务')}}
function localToday(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}async function setStatus(code){const f=DATA.funds.find(x=>x.code===code);if(!f){toast('基金信息已变化，请刷新看板');return}const status=f.status==='观察'?'持有':'观察';const target=status==='观察'?localToday():null;try{const r=await fetch('/api/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:f.code,status,anchor_target_date:target})});if(!r.ok){toast('保存失败，请检查观察站是否运行后重试');return}location.reload()}catch(e){toast('连接不到观察站，请点一键启动入口后重试')}}
async function addFund(){const code=document.getElementById('newcode').value.trim();if(!/^\d{6}$/.test(code)){toast('请输入 6 位基金代码');return}try{const r=await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});if(!r.ok){const detail=await r.text();toast(detail.includes('already tracked')?'这只基金已经在清单中':'添加失败，请检查代码或网络');return}location.reload()}catch(e){toast('连接不到观察站，请点一键启动入口后重试')}}
async function setAnchor(code){const f=DATA.funds.find(x=>x.code===code);if(!f){toast('基金信息已变化，请刷新看板');return}const target=document.getElementById('anchor-'+f.code).value;if(!target){toast('请先选择锚点日期');return}try{const r=await fetch('/api/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:f.code,status:'观察',anchor_target_date:target})});if(r.ok)location.reload();else toast('锚点日期无效或保存失败，请检查后重试')}catch(e){toast('连接不到观察站，请点一键启动入口后重试')}}
function card(f,months){const c=calc(f,months),last=c.last,s=buySignal(f,months,c.dd??0),watch=f.status==='观察',signalClass=s.label==='继续观察'||s.label==='样本不足'?'muted':s.label==='极深回撤'?'neg':'pos';const anchor=watch?`<div class="anchor-box"><span>观察锚点</span><input class="date-input" id="anchor-${f.code}" type="date" value="${f.anchorTarget||f.anchorDate||localToday()}"><span class="num ${cls(c.anchorChange)}">${fmt(c.anchorChange)}</span></div>`:`<div class="anchor-box">${f.anchorDate?`锚点 ${f.anchorDate} · ${fmt(c.anchorChange)}`:'持有中'}</div>`;return `<article class="fund-card"><div class="card-head"><div><div class="card-name">${escapeHtml(f.name)}</div><div class="card-code">${f.code}</div></div><div class="card-date">净值日 ${last?.date||'暂无'}<span class="card-day ${cls(last?.daily_pct)}">当日 ${fmt(last?.daily_pct)}</span></div></div><div class="card-main"><div class="card-stat"><span class="card-label">距近 ${months} 个月高点</span><div class="card-dd ${cls(c.dd)}">${fmt(c.dd)}</div><div class="card-peak">高点 ${c.peak?metricNav(c.peak).toFixed(4)+' · '+c.peak.date:'暂无'}</div><div class="card-bar"><i style="width:${Math.min(100,Math.max(0,-(c.dd||0)*4))}%"></i></div></div><div class="card-stat"><span class="card-label">个性化回撤参考</span><div class="card-signal"><b class="${signalClass}">${s.label}</b></div><div class="card-threshold">关注 ${s.watch==null?'—':fmt(s.watch)} · 深回撤 ${s.deep==null?'—':fmt(s.deep)} · 极深 ${s.extreme==null?'—':fmt(s.extreme)}</div><div class="card-threshold">按 ${s.n||0} 个月历史样本计算</div></div></div><div class="card-foot">${anchor}<button class="action ${watch?'watch':''}" onclick="${watch?`setAnchor('${f.code}')`:`setStatus('${f.code}')`}">${watch?'更新观察锚点':'卖出后转入观察'}</button>${watch?`<button class="action" onclick="setStatus('${f.code}')">转回持有</button>`:''}</div></article>`}
function render(){const months=Number(document.getElementById('window').value),q=document.getElementById('search').value.toLowerCase();const match=f=>`${f.name}${f.code}`.toLowerCase().includes(q);const held=DATA.funds.filter(f=>f.status==='持有'),watches=DATA.funds.filter(f=>f.status==='观察');const hf=held.filter(match),wf=watches.filter(match);document.getElementById('heldRows').innerHTML=hf.map(f=>card(f,months)).join('');document.getElementById('watchRows').innerHTML=wf.map(f=>card(f,months)).join('');document.getElementById('heldEmpty').hidden=hf.length>0;document.getElementById('watchEmpty').hidden=wf.length>0;const shownCount=(shown,total)=>q?`${shown} / ${total} 只`:`${total} 只`;document.getElementById('heldcount').textContent=shownCount(hf.length,held.length);document.getElementById('watchcount').textContent=shownCount(wf.length,watches.length)}
const navDates=DATA.funds.flatMap(f=>f.records.map(r=>r.date)).sort();const watches=DATA.funds.filter(f=>f.status==='观察');document.getElementById('total').textContent=DATA.funds.length;document.getElementById('observationTotal').textContent=watches.length;document.getElementById('lastdate').textContent=navDates.at(-1)||'暂无';document.getElementById('synctime').textContent='最近同步：'+(DATA.lastSync||'尚未同步');const worst=watches.map(f=>calc(f,3).anchorChange).filter(x=>x!=null).sort((a,b)=>a-b)[0];document.getElementById('worst').textContent=worst==null?'—':fmt(worst);document.getElementById('worst').className='';document.getElementById('search').addEventListener('input',render);document.getElementById('window').addEventListener('change',render);render();
const hselect=document.getElementById('holdingFund');for(const [label,status] of [['当前持有','持有'],['未持有观察','观察']]){const group=document.createElement('optgroup');group.label=label;for(const f of DATA.funds.filter(x=>x.status===status)){const o=document.createElement('option');o.value=f.code;o.textContent=`${f.name}（${f.code}）`;group.appendChild(o)}hselect.appendChild(group)}
function renderHoldings(){const item=DATA.holdings.find(x=>x.code===hselect.value);const body=document.getElementById('holdingRows');if(!item||!item.stocks.length){body.innerHTML='';document.getElementById('holdingEmpty').hidden=false;document.getElementById('holdingMeta').textContent='暂无已归档季度';return}document.getElementById('holdingEmpty').hidden=true;document.getElementById('holdingMeta').textContent=`${item.quarter} · 抓取于 ${item.fetchedAt||'—'}`;body.innerHTML=item.stocks.map(x=>`<tr><td>${escapeHtml(x.stock_code)}</td><td>${escapeHtml(x.stock_name||'—')}</td><td>${x.weight_pct==null?'—':Number(x.weight_pct).toFixed(2)+'%'}</td><td>${x.shares==null?'—':Number(x.shares).toLocaleString('zh-CN')}</td><td>${x.value==null?'—':Number(x.value).toLocaleString('zh-CN')}</td></tr>`).join('')}
hselect.addEventListener('change',renderHoldings);renderHoldings();</script></body></html>'''.replace("__DATA__", data)
    PAGE.write_text(page, encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/.well-known/fund-watch.json":
            manifest = {"name": "Local Fund Drawdown Watch", "version": 1,
                        "description": "Read-only local interface for current fund drawdown thresholds and evidence.",
                        "endpoints": {"alerts": "/api/alerts", "all_signals": "/api/alerts?all=1",
                                      "latest_file": str(ALERTS_JSON), "dashboard": "/"},
                        "update_command": f'python "{Path(__file__).resolve()}" --update'}
            payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if path == "/api/alerts":
            conn = db_conn()
            report = build_alert_report(conn)
            conn.close()
            if "all=1" not in urlparse(self.path).query:
                report = {key: value for key, value in report.items() if key != "funds"}
            payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if path == "/api/sync-status":
            with SYNC_STATUS_LOCK:
                status = dict(SYNC_STATUS)
            payload = json.dumps(status, ensure_ascii=False).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if path not in ("/", "/index.html"):
            self.send_error(404); return
        render_page()
        payload = PAGE.read_bytes()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/sync":
            if start_manual_sync():
                self.send_response(202); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(b'{"ok":true,"state":"running"}')
            else:
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(b'{"ok":false,"state":"running"}')
            return
        if path not in ("/api/status", "/api/add"): self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length", "0")); body = json.loads(self.rfile.read(length))
            if path == "/api/add":
                code = str(body.get("code", ""))
                if not re.fullmatch(r"\d{6}", code): raise ValueError("invalid fund code")
                conn = db_conn()
                exists = conn.execute("SELECT 1 FROM funds WHERE code=?", (code,)).fetchone()
                conn.close()
                if exists: raise ValueError("already tracked")
                name = fetch_fund_name(code)
                rows = fetch_nav(code)
                conn = db_conn()
                # Check again in case another request added it while the public API was loading.
                if conn.execute("SELECT 1 FROM funds WHERE code=?", (code,)).fetchone():
                    conn.close()
                    raise ValueError("already tracked")
                conn.execute("INSERT INTO funds(code,name,status,anchor_target_date,updated_at,display_order) VALUES(?,?,'观察',?,?,?)",
                             (code, name, dt.date.today().isoformat(), now_text(),
                              (conn.execute("SELECT COALESCE(MAX(display_order),-1)+1 FROM funds").fetchone()[0])))
                conn.commit(); save_nav(conn, code, rows); save_watchlist_file(conn); write_alert_report(conn); conn.close()
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.end_headers(); self.wfile.write(b'{"ok":true}'); return
            code = str(body.get("code", "")); status = body.get("status"); target = body.get("anchor_target_date")
            if status not in ("持有", "观察"): raise ValueError("invalid status")
            target_date = None
            if status == "观察":
                if not target or not re.fullmatch(r"20\d\d-\d\d-\d\d", target): raise ValueError("invalid anchor date")
                try: target_date = dt.date.fromisoformat(target)
                except ValueError: raise ValueError("invalid anchor date")
                if target_date.isoformat() != target or target_date > dt.date.today():
                    raise ValueError("anchor date must be a real date no later than today")
            conn = db_conn(); fund = conn.execute("SELECT code FROM funds WHERE code=?", (code,)).fetchone()
            if not fund: conn.close(); raise ValueError("unknown fund")
            if status == "观察":
                conn.execute("UPDATE funds SET status=?,anchor_target_date=?,anchor_date=NULL,anchor_nav=NULL,updated_at=? WHERE code=?", (status,target,now_text(),code))
                fill_pending_anchor(conn, code)
            else:
                conn.execute("UPDATE funds SET status='持有',anchor_target_date=NULL,anchor_date=NULL,anchor_nav=NULL,updated_at=? WHERE code=?",(now_text(),code))
            conn.commit(); save_watchlist_file(conn); write_alert_report(conn); conn.close(); self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.end_headers(); self.wfile.write(b'{"ok":true}')
        except Exception as exc:
            self.send_error(400, str(exc))
    def log_message(self, fmt, *args):
        pass


def serve(open_browser=True):
    conn = db_conn(); import_watchlist(conn); conn.close(); render_page()
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:8765/")).start()
    print("基金观察站已在本机启动：http://127.0.0.1:8765/")
    server.serve_forever()


def install_schedule():
    python = Path(sys.executable)
    script = Path(__file__).resolve()
    quote = lambda value: str(value).replace("'", "''")
    ps = (
        f"$a=New-ScheduledTaskAction -Execute '{quote(python)}' -Argument '\"{quote(script)}\" --update' -WorkingDirectory '{quote(BASE)}';"
        "$t=New-ScheduledTaskTrigger -Daily -At '9:35 PM';"
        "$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew;"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive -RunLevel Limited;"
        "Register-ScheduledTask -TaskName '基金观察站每日同步' -Action $a -Trigger $t -Settings $s -Principal $p "
        "-Description '每天晚上同步基金真实净值和季度持仓；错过后下次可用时补跑。' -Force | Out-Null"
    )
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    print("已注册本机每日 21:35 自动同步任务（需要电脑开机；离线数据下次运行补抓）。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--serve-no-browser", action="store_true")
    parser.add_argument("--install-schedule", action="store_true")
    parser.add_argument("--force-quarter", action="store_true")
    parser.add_argument("--export-alerts", action="store_true")
    args = parser.parse_args()
    if args.install_schedule: install_schedule()
    if args.export_alerts:
        conn = db_conn(); import_watchlist(conn); report = write_alert_report(conn); conn.close()
        print(f"已生成 {ALERTS_JSON}；触发 {report['triggered_count']} 只。")
        return 0
    if args.update:
        return sync_all(force_quarter=args.force_quarter)
    if args.serve or args.serve_no_browser:
        serve(open_browser=not args.serve_no_browser)
        return 0
    parser.print_help(); return 0

if __name__ == "__main__":
    raise SystemExit(main())
