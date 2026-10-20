# 基金估值与观察工具

本仓库包含基金盘中估值引擎和本地观察站。个人基金配置与运行数据留在本机，不要提交到公开仓库。

## 估值引擎
- Windows：运行 `一键运行估值引擎.bat`。
- 命令行：`python daily_valuation.py`。
- 无交互模式：`python headless_valuation.py`。
- 本机配置文件为数据目录下的 `fund_config.json`。仓库提供 `fund_config.example.json` 空模板；本机配置会被 Git 忽略。

## 本地观察站
运行 `打开基金观察站.bat`，按页面提示添加基金并同步公开净值。程序数据默认保存在应用目录，可通过 `FUND_SYSTEM_DATA_DIR` 或本机 `fund_system_paths.json` 指定其他数据目录。

## GitHub Actions
工作流每天北京时间 14:30 运行无交互估值脚本，也可手动触发。自动估值前，请将私有基金配置 JSON 保存为仓库 Actions Secret `FUND_CONFIG_JSON`；工作流只在运行时写入配置，不把它提交到仓库。
