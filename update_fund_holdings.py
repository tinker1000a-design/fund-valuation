import time
import datetime
import akshare as ak
import pandas as pd
import requests
from fund_utils import save_config, load_config, normalize_code

# ---- Monkey-patch: 东方财富 API 要求 Referer，akshare 1.18.64 没带，导致 404 ----
_original_get = requests.get
def _patched_get(url, *args, **kwargs):
    if 'eastmoney.com' in url or 'fundf10' in url:
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers'].setdefault('Referer', 'https://fundf10.eastmoney.com/')
        kwargs['headers'].setdefault('User-Agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    return _original_get(url, *args, **kwargs)
requests.get = _patched_get

# =========================================
# ETF 联接基金专用：不披露股票前十大，实际持仓是对应 ETF 份额
# 用 ETF 的实时涨跌幅作为基金估值代理（联接基金 90%+ 资产投向该 ETF）
# =========================================
ETF_LINKED_FUNDS = {
}

def update_etf_linked_fund(fund_code, fund_name):
    """尝试通过天天基金移动端接口获取联接基金对应的 ETF，写入代理持仓"""
    info = ETF_LINKED_FUNDS.get(fund_code)
    if not info:
        print(f"⚠️ [{fund_name}] 是 ETF 联接基金但未在 ETF_LINKED_FUNDS 登记，跳过。")
        return

    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
        'Referer': 'https://fund.eastmoney.com/',
    }
    url = (f'https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition'
           f'?FCODE={fund_code}&deviceid=Wap&plat=Wap&product=EFund&version=6.2.8')
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()
        datas = data.get('Datas') or {}
        etf_code = datas.get('ETFCODE') or info['etf_code']
        etf_name = datas.get('ETFSHORTNAME') or info['etf_name']
        print(f"🔗 [{fund_name}] 为 ETF 联接基金，绑定标的: {etf_name} ({etf_code})")
    except Exception as e:
        etf_code, etf_name = info['etf_code'], info['etf_name']
        print(f"⚠️ [{fund_name}] 移动端接口获取失败({e})，使用登记标的: {etf_name} ({etf_code})")

    config = load_config()
    config[fund_code] = {
        "fund_name": fund_name,
        "holdings": [{
            "stock_code": normalize_code(etf_code),
            "stock_name": etf_name,
            "weight": 1.0
        }],
        "top10_total_weight": 1.0,
        "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_config(config)
    print(f"✅ [{fund_name}] ETF 代理持仓更新成功！（估值 ≈ {etf_name} 实时涨跌幅）\n")

# =========================================
# 指数代理基金专用：指数基金直接用对应指数实时涨跌估值（比前十大放大更准）
# =========================================
INDEX_LINKED_FUNDS = {
}

def update_index_linked_fund(fund_code, fund_name):
    info = INDEX_LINKED_FUNDS.get(fund_code)
    if not info:
        print(f"⚠️ [{fund_name}] 未在 INDEX_LINKED_FUNDS 登记，跳过。")
        return
    config = load_config()
    config[fund_code] = {
        "fund_name": fund_name,
        "holdings": [{
            "stock_code": normalize_code(info['index_code']),
            "stock_name": info['index_name'],
            "weight": 1.0
        }],
        "top10_total_weight": 1.0,
        "index_proxy": normalize_code(info['index_code']),
        "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_config(config)
    print(f"✅ [{fund_name}] 指数代理持仓更新成功！（估值 ≈ {info['index_name']} 实时涨跌幅）\n")

def update_single_fund(fund_code, fund_name):
    print(f"🚀 开启暴君模式，强制抽查 [{fund_name} - {fund_code}] 最新数据...")
    
    try:
        current_year = datetime.datetime.now().year
        print(f"📡 正在拿鞭子抽打接口，强制索要 {current_year} 与 {current_year - 1} 年的底牌...")
        
        dfs = []
        for year in [str(current_year), str(current_year - 1)]:
            try:
                temp_df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
                if temp_df is not None and not temp_df.empty:
                    dfs.append(temp_df)
                    print(f"   ✅ {year}年 获取到 {len(temp_df)} 条记录")
            except Exception as e:
                print(f"   ⚠️ {year}年 获取失败: {e}")
                continue
                
        if not dfs:
            print(f"❌ [{fund_name}] 彻底没获取到数据，请检查网络或基金代码。")
            return
            
        df = pd.concat(dfs, ignore_index=True)
        print(f"🔍 接口这次老老实实交出了 {len(df)} 条近两年的记录！")
        
        # 数据清洗
        df['占净值比例'] = df['占净值比例'].astype(str).str.replace('%', '').str.replace('--', '0').astype(float)
        
        # 取最新季度
        if '季度' in df.columns:
            df = df.sort_values(by='季度', ascending=False)
            latest_quarter = df['季度'].iloc[0]
            print(f"📅 锁定绝对最新披露期: {latest_quarter.split('股票投资明细')[0]}")
            df = df[df['季度'] == latest_quarter]
        
        # 按权重排序
        df = df.sort_values(by='占净值比例', ascending=False)
        
        print("\n" + "="*60)
        print(f" 🕵️ 最新财报：{fund_name} 前十大持仓明细 ")
        print("="*60)
        
        config = load_config()
        holdings = []
        
        for index, row in df.head(10).iterrows():
            weight = getattr(row, '占净值比例', 0.0)
            stock_code = str(getattr(row, '股票代码', ''))
            stock_name = str(getattr(row, '股票名称', ''))
            quarter = getattr(row, '季度', '未知')
            
            print(f"🔹 {stock_name.ljust(10)} ({stock_code}) | 占比: {weight:6.2f}% | {quarter}")
            
            holdings.append({
                "stock_code": normalize_code(stock_code),
                "stock_name": stock_name,
                "weight": round(weight / 100, 4)
            })
            
        print("="*60 + "\n")
        
        top10_total = sum(x['weight'] for x in holdings)
        
        config[fund_code] = {
            "fund_name": fund_name,
            "holdings": holdings,
            "top10_total_weight": round(top10_total, 4),
            "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        save_config(config)
        print(f"✅ [{fund_name}] 更新成功！")
        print(f"📊 前十大总占比: {round(top10_total * 100, 2)}%\n")
        
    except Exception as e:
        print(f"💥 [{fund_name}] 抓取失败: {e}\n")


def update_all_funds():
    """从共用配置读取基金名单，按普通 / ETF / 指数规则更新持仓。"""
    config = load_config()
    if not config:
        print("❌ 基金配置为空或读取失败，请检查 fund_config.json。")
        return

    # 基金名单只维护 fund_config.json，与估值引擎使用同一份名单。
    # ETF_LINKED_FUNDS / INDEX_LINKED_FUNDS 仅保存特殊处理规则。
    fund_list = [
        {"code": code, "name": data.get("fund_name", "未知基金")}
        for code, data in config.items()
    ]

    print("=" * 70)
    print("      基金持仓透视镜 (暴君索要版) v4.0 - 多基金模式")
    print("=" * 70)
    print(f"共 {len(fund_list)} 只基金即将更新...\n")
    
    for fund in fund_list:
        if fund["code"] in ETF_LINKED_FUNDS:
            update_etf_linked_fund(fund["code"], fund["name"])
        elif fund["code"] in INDEX_LINKED_FUNDS:
            update_index_linked_fund(fund["code"], fund["name"])
        else:
            update_single_fund(fund["code"], fund["name"])
        time.sleep(2)   # 每只基金间隔2秒，避免请求太快被封
    
    print("=" * 70)
    print("🎉 基金处理结束，请以上方每只基金的更新结果为准。")
    print("=" * 70)


if __name__ == "__main__":
    update_all_funds()
    
    input("\n按 Enter 键退出窗口...")
