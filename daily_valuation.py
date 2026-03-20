import os
import time
import random
import requests
from fund_utils import load_config, compute_estimated_pct

# ================== 可配置参数 ==================
ENTERPRISE_WECHAT_WEBHOOK = os.environ.get("WEBHOOK")
ALERT_DROP = -0                    # 跌幅超过这个值（比如-2）就推送微信
# ================================================

def get_global_market_data(holdings_list):
    """新加坡专属：只抓前十大 + 腾讯接口 + 正确涨跌幅"""
    codes = set()
    for holding in holdings_list:
        c = holding.get('stock_code', '')
        if not c: continue
        if len(c) == 5 and c.isdigit():
            codes.add(f"hk{c}")
        elif c.startswith(('6', '5')):
            codes.add(f"sh{c}")
        else:
            codes.add(f"sz{c}")

    if not codes:
        return {}

    code_str = ",".join(codes)
    url = f"http://qt.gtimg.cn/q={code_str}"

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and resp.text.startswith("v_"):
                market = {}
                for line in resp.text.strip().split(";"):
                    if "=" not in line: continue
                    parts = line.split('=')[1].strip('"').split('~')
                    if len(parts) > 32:
                        raw_code = parts[2]
                        try:
                            change_pct = float(parts[32])
                            market[raw_code] = change_pct
                        except ValueError:
                            market[raw_code] = 0.0
                return market
        except:
            time.sleep(random.uniform(0.5, 1.5))
            continue
    return {}

def send_wechat_push(fund_name, normalized):
    """企业微信机器人推送（只发一条消息）"""
    try:
        data = {
            "msgtype": "text",
            "text": {
                "content": f"🚨 基金盘中大跌提醒！\n\n【{fund_name}】\n估算跌幅已达 {normalized:.2f}%\n\n建议尽快查看！"
            }
        }
        requests.post(ENTERPRISE_WECHAT_WEBHOOK, json=data, timeout=5)
        print(f"✅ 微信已推送：{fund_name} 跌 {normalized:.2f}%")
    except:
        print("⚠️ 微信推送失败（网络问题）")

def run_valuation_engine():
    print("=" * 45)
    print(" 🚀 盘中估值引擎 v3.5 (企业微信推送版) ")
    print("=" * 45)
    
    config = load_config()
    if not config:
        print("❌ 没找到基金配置！")
        return
        
    print(f"📂 成功加载监控列表，共 {len(config)} 只基金。")
    print("\n📡 正在呼叫行情雷达...（只抓前十大）")
    
    for fund_code, fund_data in config.items():
        fund_name = fund_data.get('fund_name', '未知基金')
        holdings = fund_data.get('holdings', [])
        top10_weight = fund_data.get('top10_total_weight', 0.0)
        
        global_market = get_global_market_data(holdings)
        if not global_market:
            continue
        
        normalized, raw = compute_estimated_pct(holdings, top10_weight, global_market)
        
        trend_norm = "🔴" if normalized > 0 else "🟢" if normalized < 0 else "⚪"
        trend_raw = "🔴" if raw > 0 else "🟢" if raw < 0 else "⚪"
        
        print(f"🎯 【{fund_name}】 ({fund_code})")
        print(f"   ▶ 前十大真实拉动: {trend_raw} {raw:.2f}%")
        print(f"   ▶ 放大后估算涨幅: {trend_norm} {normalized:.2f}%")
        print("-" * 45)
        
        # 🔥 只有跌幅超过设定值时才推送微信（每天只跑一次就发一条）
        if normalized < ALERT_DROP:
            send_wechat_push(fund_name, normalized)

if __name__ == "__main__":
    run_valuation_engine()
