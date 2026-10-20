import time
import datetime
import urllib.request
from fund_utils import load_config, compute_estimated_pct, is_bj_code

# ================== 可配置参数 ==================
ALERT_DROP = -4.0        # 警戒线：放大后估算跌幅超过 4% 时高亮提醒（-4.0 = 跌超4%）
# ================================================

def _index_prefix(code):
    """指数代码转腾讯前缀：399 开头是深证/中证深系，其余(000等)是上证/中证系"""
    return 'sz' if str(code).startswith('399') else 'sh'


def get_global_market_data(holdings_list, index_proxy=None):
    """只抓前十大 + 腾讯接口 + 正确涨跌幅（支持指数代理）

    指数代码与个股代码可能同号。为避免市场前缀冲突和请求顺序不确定导致
    行情覆盖，按带市场前缀的代码保存结果并进行冲突告警：
      ① holdings 中与 index_proxy 同号的条目直接跳过，指数只按指数前缀查一次；
      ② 持仓名称含"指数"的兜底按指数规则请求；
      ③ 字典主 key 带市场前缀，并检测同号冲突后打印告警。
    """
    idx = str(index_proxy).strip() if index_proxy else None
    codes = set()
    for holding in holdings_list:
        c = holding.get('stock_code', '')
        if not c:
            continue
        # 指数代理已在下方按指数规则单独请求，此处跳过，避免同号个股覆盖指数
        if idx and c == idx:
            continue
        # 兜底：名称里带"指数"的条目按指数规则请求
        if '指数' in str(holding.get('stock_name', '')):
            codes.add(f"{_index_prefix(c)}{c}")
            continue
        if len(c) == 5 and c.isdigit():
            codes.add(f"hk{c}")
        elif is_bj_code(c):
            # 北交所：920 新代码 + 430/83x/87x/88x 新三板平移老代码
            codes.add(f"bj{c}")
        elif c.startswith(('6', '5')):
            codes.add(f"sh{c}")
        else:
            codes.add(f"sz{c}")
    if idx:
        codes.add(f"{_index_prefix(idx)}{idx}")

    if not codes:
        return {}

    code_str = ",".join(codes)
    url = f"http://qt.gtimg.cn/q={code_str}"

    for attempt in range(3):
        try:
            # 用标准库 urllib 请求腾讯行情(零依赖,无需 pip 安装 requests)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "http://finance.qq.com",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
            if text.startswith("v_"):
                market = {}
                seen = {}   # 纯代码 -> (带前缀代码, 名称)，用于检测同号冲突
                for line in text.strip().split(";"):
                    if "=" not in line:
                        continue
                    left, right = line.split('=', 1)
                    prefixed = left.split('_')[-1]          # 带市场前缀的证券代码
                    parts = right.strip('"').split('~')
                    if len(parts) > 32:
                        raw_code = parts[2]                 # 纯 6 位代码，兼容旧取值方式
                        name = parts[1]
                        try:
                            change_pct = float(parts[32])
                        except ValueError:
                            change_pct = 0.0
                        market[prefixed] = change_pct
                        if raw_code in seen and seen[raw_code][0] != prefixed:
                            print(f"   ⚠️  代码同号冲突：{raw_code} 同时命中 "
                                  f"{seen[raw_code][1]}({seen[raw_code][0]}) 与 "
                                  f"{name}({prefixed})，已保留先到的一条，请核对基金配置")
                        else:
                            seen[raw_code] = (prefixed, name)
                            market[raw_code] = change_pct
                return market
        except Exception as exc:
            print(f"   ⚠️  第 {attempt + 1} 次请求行情失败: {exc}")
            time.sleep(1)
            continue
    return {}


def run_valuation_engine():
    start_time = time.time()
    beijing_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    
    print("=" * 60)
    print(" 🚀 基金盘中估值引擎 v3.5（本地CMD版）")
    print(f" 📅 执行时间：{beijing_time.strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    print("=" * 60)
    
    config = load_config()
    if not config:
        print("❌ 错误：没找到基金配置！请检查 fund_config.json")
        return
        
    print(f"✅ 成功加载监控列表，共 {len(config)} 只基金。\n")
    print("📡 正在获取实时行情（仅前十大成分股）...\n")
    
    for fund_code, fund_data in config.items():
        fund_name = fund_data.get('fund_name', '未知基金')
        holdings = fund_data.get('holdings', [])
        top10_weight = fund_data.get('top10_total_weight', 0.0)
        
        print(f"🔍 正在估值：【{fund_name}】 ({fund_code})")
        
        global_market = get_global_market_data(holdings, fund_data.get('index_proxy'))
        
        if not global_market:
            print("   ⚠️  获取行情失败，跳过该基金\n")
            continue
        
        normalized, raw = compute_estimated_pct(holdings, top10_weight, global_market)
        
        # 美化输出
        trend_norm = "🔴 大跌" if normalized < 0 else "🟢 上涨" if normalized > 0 else "⚪ 平盘"
        trend_raw = "🔴 大跌" if raw < 0 else "🟢 上涨" if raw > 0 else "⚪ 平盘"
        
        print(f"   前十大真实拉动：{trend_raw} {raw:+.2f}%")
        print(f"   放大后估算涨幅：{trend_norm} {normalized:+.2f}%")
        
        # 如果跌幅较大，在控制台高亮提醒
        if normalized < ALERT_DROP:
            print("   🚨🚨 跌幅超过警戒线！请重点关注！")
        
        print("-" * 60)
    
    elapsed = time.time() - start_time
    print(f"\n✅ 全部估值完成！共耗时 {elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    run_valuation_engine()
    
    # 让CMD窗口暂停，方便你看结果（按任意键关闭）
    input("\n按 Enter 键退出窗口...")
