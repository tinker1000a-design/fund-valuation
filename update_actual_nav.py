import json
import time
import datetime
from pathlib import Path
from fund_paths import data_path

import akshare as ak


CONFIG_PATH = data_path("fund_config.json")
HISTORY_PATH = data_path("valuation_history.json")


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as exc:
        print(f"读取失败：{path.name}，原因：{exc}")
        return default


def save_json(path, data):
    with path.open("w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_pct(value):
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if text in ("", "--", "nan", "None"):
        return None
    try:
        return round(float(text), 4)
    except ValueError:
        return None


def fetch_latest_actual_pct(fund_code):
    """Fetch the latest published real NAV daily change from Eastmoney/Tiantian via AKShare."""
    df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    if df is None or df.empty:
        return None

    latest = df.iloc[-1]
    date_value = latest.get("净值日期")
    actual_pct = normalize_pct(latest.get("日增长率"))
    unit_nav = latest.get("单位净值")

    if date_value is None or actual_pct is None:
        return None

    date_text = str(date_value)[:10]
    try:
        unit_nav = round(float(unit_nav), 4)
    except Exception:
        unit_nav = None

    return {
        "date": date_text,
        "actual_pct": actual_pct,
        "unit_nav": unit_nav,
        "source": "akshare.fund_open_fund_info_em",
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def upsert_history(history, fund_code, fund_name, record):
    fund_history = history.setdefault(fund_code, {
        "fund_name": fund_name,
        "records": []
    })
    fund_history["fund_name"] = fund_name

    records = fund_history.setdefault("records", [])
    for old in records:
        if old.get("date") == record["date"]:
            old.update(record)
            return "updated"

    records.append(record)
    records.sort(key=lambda x: x.get("date", ""))
    return "added"


def update_all_actual_nav():
    config = load_json(CONFIG_PATH, {})
    if not config:
        print("没有找到基金配置，请确认 fund_config.json 存在且内容正常。")
        return

    history = load_json(HISTORY_PATH, {})
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 70)
    print("          基金真实涨跌幅抓取器")
    print(f"          执行时间：{now}")
    print("=" * 70)
    print(f"准备抓取 {len(config)} 只基金的最新真实日增长率。\n")

    success = 0
    skipped = 0

    for fund_code, fund_data in config.items():
        fund_name = fund_data.get("fund_name", "未知基金")
        print(f"正在获取：{fund_name} ({fund_code})")

        try:
            record = fetch_latest_actual_pct(fund_code)
            if not record:
                skipped += 1
                print("   未获取到有效净值数据，跳过。\n")
                continue

            action = upsert_history(history, fund_code, fund_name, record)
            success += 1
            # 每只抓完立即落盘：原先只在循环结束后保存一次，中途 Ctrl+C
            # 或接口卡死会导致前面已抓到的净值全部丢失
            save_json(HISTORY_PATH, history)

            action_text = "更新" if action == "updated" else "新增"
            nav_text = f"，单位净值 {record['unit_nav']}" if record.get("unit_nav") is not None else ""
            print(f"   {action_text} {record['date']}：真实日增长率 {record['actual_pct']:+.2f}%{nav_text}\n")
        except Exception as exc:
            skipped += 1
            print(f"   获取失败：{exc}\n")

        time.sleep(1)

    save_json(HISTORY_PATH, history)

    print("=" * 70)
    print(f"完成：成功 {success} 只，跳过/失败 {skipped} 只。")
    print(f"历史文件：{HISTORY_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    update_all_actual_nav()
    try:
        input("\n按 Enter 键退出窗口...")
    except EOFError:
        pass
