import os
import json
import logging
import urllib.request
from datetime import datetime, timedelta

# =========================================
# 基础配置
# =========================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
CONFIG_PATH = 'fund_config.json'

def normalize_code(code: str) -> str:
    raw = str(code).strip().upper()
    cleaned = raw.replace('HK', '').replace('.HK', '').replace(' ', '')
    return cleaned.zfill(5) if len(cleaned) <= 5 else cleaned.zfill(6)

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# =========================================
# 🎯 核心逻辑：给腾讯接口翻译代码
# =========================================
def get_tencent_code(code):
    c = normalize_code(code)
    if len(c) == 5: return f"hk{c}"
    if c.startswith(('60', '68')): return f"sh{c}"
    if c.startswith(('00', '30')): return f"sz{c}"
    return f"sh{c}"

# =========================================
# 🚀 动力核心：精准狙击引擎 (告别全量下载)
# =========================================
def get_global_market_data():
    print("\n🎯 启动【精准狙击】模式 (专治海外网络不服)...")
    config = load_config()
    if not config:
        print("❌ 监控列表为空，狙击枪没有目标。")
        return {}

    # 1. 智能提取你的持仓目标，不多抓一只无关股票！
    target_codes = set()
    for fund in config.values():
        for item in fund.get('holdings', []):
            target_codes.add(normalize_code(item['stock_code']))

    if not target_codes:
        return {}

    # 2. 组装狙击指令
    query_str = ",".join([get_tencent_code(c) for c in target_codes])
    url = f"http://qt.gtimg.cn/q={query_str}"
    
    market_dict = {}
    try:
        print(f"📡 正在向腾讯总部发送密电，精准请求 {len(target_codes)} 个标的...")
        # 伪装成浏览器正常访问
        req = urllib.request.Request(url, headers={'Referer': 'http://finance.qq.com'})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            res = response.read().decode('gbk')
            
        # 3. 解析腾讯传回的密码本
        for line in res.split(';'):
            if '=' in line:
                left, right = line.split('=')
                raw_code = left.split('_')[-1] # 提取如 v_sz300750 里的 sz300750
                code = normalize_code(raw_code[2:])
                data = right.replace('"', '').split('~')
                
                # 腾讯接口的第 32 位就是涨跌幅百分比！
                if len(data) > 32:
                    try:
                        market_dict[code] = float(data[32])
                    except:
                        market_dict[code] = 0.0
                        
        print("✅ 腾讯总部回传数据成功！防火墙算个屁！")
    except Exception as e:
        print(f"❌ 狙击失败，被发现了: {e}")

    return market_dict

def compute_estimated_pct(holdings, total_top10_weight, global_dict):
    sum_contrib = 0.0
    for item in holdings:
        code = normalize_code(item['stock_code'])
        pct = global_dict.get(code, 0.0)
        sum_contrib += item['weight'] * pct
    normalized = round(sum_contrib / total_top10_weight, 2) if total_top10_weight > 0 else 0.0
    raw = round(sum_contrib, 2)
    return normalized, raw