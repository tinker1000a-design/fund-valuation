import os
import json
import logging
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from fund_paths import data_path

# =========================================
# 基础配置
# =========================================
data_path("logs/.keep").parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
CONFIG_PATH = data_path('fund_config.json')
EXAMPLE_CONFIG_PATH = Path(__file__).resolve().with_name('fund_config.example.json')

def normalize_code(code: str) -> str:
    raw = str(code).strip().upper()
    cleaned = raw.replace('HK', '').replace('.HK', '').replace(' ', '')
    return cleaned.zfill(5) if len(cleaned) <= 5 else cleaned.zfill(6)

def load_config():
    for path in (CONFIG_PATH, EXAMPLE_CONFIG_PATH):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return {}

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# =========================================
# 🎯 核心逻辑：给腾讯接口翻译代码
# =========================================
def is_bj_code(code):
    """北交所代码判定

    北交所股票代码不止 920 一种：
      920xxx —— 2024 年起启用的新证券代码段
      430xxx / 83xxxx / 87xxxx / 88xxxx —— 由新三板精选层平移而来的老代码
    只认 920 会导致老代码被误判成沪深个股，行情取到别的标的。
    """
    c = str(code)
    if c.startswith(('920', '430')):
        return True
    return c.startswith(('83', '87', '88'))


def get_tencent_code(code):
    c = normalize_code(code)
    if len(c) == 5: return f"hk{c}"       # 港股 5 位
    if is_bj_code(c): return f"bj{c}"     # 北交所（含 430/83x/87x/88x 老代码）
    if c.startswith(('60', '68', '51', '56', '58')): return f"sh{c}"  # 沪市股票 + 沪市ETF
    if c.startswith(('00', '30', '15', '16')): return f"sz{c}"  # 深市股票 + 深市ETF/LOF
    return f"sh{c}"

def get_index_tencent_code(code):
    """指数代码转腾讯前缀：399 开头是深证/中证深系指数，其余(000等)是上证/中证指数"""
    c = normalize_code(code)
    if c.startswith('399'):
        return f"sz{c}"
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
    index_codes = set()   # 指数代理基金的目标（用指数前缀请求）
    for fund in config.values():
        for item in fund.get('holdings', []):
            target_codes.add(normalize_code(item['stock_code']))
        # 指数代理：fund_config.json 中的 index_proxy 字段
        if fund.get('index_proxy'):
            index_codes.add(normalize_code(fund['index_proxy']))

    # 指数代理代码必须进查询集合：若它只存在于 index_proxy 而未写进 holdings，
    # 不合并进来就会导致该基金取不到行情、估值恒为 0
    target_codes |= index_codes

    if not target_codes:
        return {}

    # 2. 组装狙击指令（指数与股票用不同交易所前缀）
    query_parts = [get_index_tencent_code(c) if c in index_codes else get_tencent_code(c)
                   for c in target_codes]
    query_str = ",".join(query_parts)
    url = f"http://qt.gtimg.cn/q={query_str}"
    
    market_dict = {}
    try:
        print(f"📡 正在向腾讯总部发送密电，精准请求 {len(target_codes)} 个标的...")
        # 伪装成浏览器正常访问
        req = urllib.request.Request(url, headers={'Referer': 'http://finance.qq.com'})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            res = response.read().decode('gbk')
            
        # 3. 解析腾讯传回的密码本
        seen = {}   # 纯代码 -> (带前缀代码, 名称)，用于检测同号冲突
        for line in res.split(';'):
            if '=' in line:
                left, right = line.split('=')
                raw_code = left.split('_')[-1]      # 带市场前缀的证券代码
                code_only = ''.join(filter(str.isdigit, raw_code))
                code = normalize_code(code_only)
                data = right.replace('"', '').split('~')
                
                # 腾讯接口的第 32 位就是涨跌幅百分比！
                if len(data) > 32:
                    try:
                        pct = float(data[32])
                    except Exception:
                        pct = 0.0

                    # 同一串 6 位代码在不同市场可能是两个完全不同的标的
                    # （同一代码在不同市场可能对应不同标的）。
                    # 旧逻辑用纯代码做 key，后返回者会静默覆盖先返回者，且
                    # set 拼接顺序受 hash 随机化影响，结果时对时错。
                    if code in seen and seen[code][0] != raw_code:
                        print(f"⚠️ 代码同号冲突：{code} 同时命中 {seen[code][1]}"
                              f"({seen[code][0]}) 与 {data[1]}({raw_code})，"
                              f"已保留先到的一条，请核对 fund_config.json")
                    else:
                        seen.setdefault(code, (raw_code, data[1]))
                        market_dict[code] = pct
                    market_dict[raw_code] = pct   # 带市场前缀的 key，供精确匹配
                        
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
