"""非交互模式估值引擎 - 专供定时任务调用，输出落文件"""
import sys, os, json
from datetime import datetime, timezone, timedelta
from fund_utils import load_config, compute_estimated_pct, get_global_market_data
from fund_paths import data_path

OUTPUT_FILE = str(data_path("valuation_result.json"))

def run_headless():
    beijing = datetime.now(timezone(timedelta(hours=8)))
    today_str = beijing.strftime("%Y-%m-%d")
    time_str = beijing.strftime("%H:%M:%S")

    config = load_config()
    if not config:
        result = {"date": today_str, "time": time_str, "status": "error", "message": "未找到基金配置"}
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        return

    market = get_global_market_data()

    funds_result = []
    for code, fund in config.items():
        name = fund.get("fund_name", "未知")
        holdings = fund.get("holdings", [])
        top10_w = fund.get("top10_total_weight", 0.0)

        if not market:
            funds_result.append({
                "code": code, "name": name,
                "estimated_pct": None, "raw_pct": None,
                "status": "行情获取失败"
            })
            continue

        try:
            norm_pct, raw_pct = compute_estimated_pct(holdings, top10_w, market)
            funds_result.append({
                "code": code, "name": name,
                "estimated_pct": norm_pct,
                "raw_pct": raw_pct,
                "status": "ok"
            })
        except Exception as e:
            funds_result.append({
                "code": code, "name": name,
                "estimated_pct": None, "raw_pct": None,
                "status": f"计算错误: {str(e)}"
            })

    result = {
        "date": today_str,
        "time": time_str,
        "status": "ok",
        "funds": funds_result
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    run_headless()
