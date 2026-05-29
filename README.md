# 因子排序回测框架 v1

本项目用于评估日频因子的截面排序能力，不是传统撮合式交易回测框架。框架关注因子在不同股票池内的 RankIC、分组收益、多空收益、覆盖率和异常值诊断。

当前框架版本统一命名为 `v1`。每次运行的 `run_meta.json` 会记录：

```json
{
  "framework_version": "v1"
}
```

## 核心时间语义

因子文件采用宽表格式：

```text
index = trade_date
columns = symbol
values = factor value
```

`factor_df.loc[t]` 表示 `t` 日收盘后生成、下一交易日开盘前可用的因子值。框架默认在下一交易日开盘按因子排序建仓，未来收益使用 open-to-open：

```text
future_return_h[t] = open_price[t+h+1] / open_price[t+1] - 1
```

这里的 `t+1`、`t+h+1` 是交易日序列上的偏移，不是自然日偏移。

默认 horizon：

```python
[1, 5, 10, 20]
```

## 默认路径

```python
project_dir = "/app/workspace/zhangyuan/Factor_Backtest"
data_root = "/data/zhangyuan"
pool_dir = "/data/zhangyuan/pool"
output_root = "/data/zhangyuan/Factor_Backtest_Result"
```

推荐把 `/app/workspace/zhangyuan/Factor_Backtest` 当成代码包目录，把 `/app/workspace/zhangyuan/Factor_Backtest_Result` 当成调用脚本目录，把 `/data/zhangyuan/Factor_Backtest_Result` 当成结果输出目录。代码、脚本和结果不要混在一起。

服务器部署后，建议在代码包目录安装一次：

```bash
cd /app/workspace/zhangyuan/Factor_Backtest
pip install -e .
```

安装后可以在任意目录调用，例如：

```bash
cd /app/workspace/zhangyuan/Factor_Backtest_Result
python factor_dm_20d/run_factor_dm_20d.py
```

项目内提供了一个示例脚本：

```text
examples/run_factor_dm_20d.py
```

可以复制到 `/app/workspace/zhangyuan/Factor_Backtest_Result/factor_dm_20d/run_factor_dm_20d.py` 后按需修改因子名、时间范围和股票池。

如果因子名为 `dm_20d`，默认查找：

```text
/data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
/data/zhangyuan/factor_dm_20d/factor_dm_20d.csv
```

也可以直接传入 `factor_path`。

## 因子格式自动识别

框架支持：

- 标准宽表：`trade_date x symbol`
- MultiIndex 长表：`date/asset` 双索引
- 普通长表：`trade_date/date + symbol/asset + value/factor/factor_value`

内部统一输出为：

```text
index = trade_date
columns = symbol
```

## 股票池

框架配置默认：

```python
selected_pools = ["all"]
```

示例脚本 `examples/run_factor_dm_20d.py` 默认运行：

```python
selected_pools = ["all", "hs300_pool", "zz1000_pool", "zz2000_pool"]
```

`all` 是虚拟池，表示全市场，不读取指数成分文件。

支持的指数池名称：

```text
hs300_pool
zz500_pool
zz1000_pool
zz2000_pool
gz1000_pool
gz2000_pool
gzMidsmallcap_pool
miMicrocap_pool
```

股票池 CSV 格式：

```csv
trade_date,symbol
2020-01-02,000001.XSHE
2020-01-02,000002.XSHE
```

所有计算在每个 pool 内独立完成，只有 `cross_pool_summary` 这类模块会读取各 pool 已完成结果做对比。

## 可交易过滤

默认参数：

```python
tradability_filter = True
min_listed_days = 120
```

开启后，仅过滤建仓日截面，不检查退出日是否可卖。过滤规则：

- 当日最高价触及涨停：`high_price >= limit_up`
- 当日最低价触及跌停：`low_price <= limit_down`
- ST
- 停牌
- 上市交易日数小于 120
- 当日开盘价缺失或小于等于 0

## ClickHouse 行情数据读取

框架已经内置 ClickHouse 读取函数，参考旧项目的数据接口，默认连接参数为：

```python
host = "10.10.0.10"
port = 8123
username = "zhangyuan"
password = "zhang2026"
```

使用示例：

```python
from factor_backtest.clickhouse_adapter import load_market_data_from_clickhouse

market_data = load_market_data_from_clickhouse(
    start_date="2020-01-01",
    end_date="2026-05-02",
)
```

该函数会自动查询：

```text
stock_data.view_stock_qfq_adjusted_ohlcv_v2
cn_stock_fundamentals.shares
cn_stock_fundamentals.is_st_stock
cn_stock_fundamentals.is_suspended
```

并把查询结果转换成 `MarketDataBundle`，包括：

```text
open_price
close_price
high_price
low_price
volume
amount
market_cap
limit_up_price
limit_down_price
is_st
is_suspended
listed_days
```

## 模块化输出

默认执行全部内置模块：

```python
enabled_sections = "all"
```

内置模块包括：

- `data_quality`
- `ic_overview`
- `cumulative_ic`
- `factor_style_exposure`
- `style_neutralized_ic`
- `style_industry_neutralized_ic`
- `group_exposure_diagnostics`
- `group_return`
- `within_industry_group_return`
- `layered_group_return`
- `long_short`
- `group_turnover`
- `performance_metrics`

每个模块独立计算和渲染。一个模块失败只会写入 `run_log.json`，不会中断其他模块。

## 输出结构

默认会同时生成历史 run 和 latest 镜像：

```text
/data/zhangyuan/Factor_Backtest_Result/
  factor_dm_20d/
    latest/
      run_meta.json
      run_log.json
      report.html
      pools/
    runs/
      20260519_153000_000000/
        run_meta.json
        run_log.json
        report.html
        pools/
```

下游模型、notebook 和人工复盘默认读取 `latest/`。如果需要追溯某次历史运行，再读取 `runs/<run_time>/`。

也可以通过以下配置恢复旧版单时间目录结构：

```python
cfg = BacktestConfig(output_layout="timestamp")
```

单次 run 目录内部结构：

```text
      run_meta.json
      run_log.json
      report.html
      pools/
        all/
          artifacts/
          tables/
          plots/
```

输出会先按因子名分目录。运行时可以通过配置传入：

```python
cfg = BacktestConfig(factor_name="factor_dm_20d")
```

或者在运行函数中传入：

```python
run_factor_backtest(..., factor_name="factor_dm_20d")
```

`artifacts` 是中间结果，优先落盘；`tables` 是统计表；`plots` 是可视化图片；`report.html` 是模块状态汇总入口。

每次运行完成后可以直接打开单次 run 目录下的 `report.html`。报告会按股票池分别汇总：

- 本次回测参数和 warning
- 关键图表：data quality、20D IC 移动平均、累计 IC、10 分组平均收益、按 horizon 拆分的 10 组累计收益线图、分层收益、累计多空收益
- 关键统计表：IC statistics、group return summary、layered group return summary、cumulative long-short tail、performance metrics
- 模块状态，以及到 `plots/`、`tables/`、`artifacts/` 的相对链接

如果服务器安装了 `pyarrow` 或 `fastparquet`，中间结果会保存为 `.parquet`。如果当前 Python 环境缺少 parquet 引擎，框架会明确保存为 `.parquet.pkl`，不会把 pickle 文件伪装成 parquet 后缀。

PNG 图表标题统一使用英文，避免服务器缺少中文字体时出现 `Glyph missing from font(s) DejaVu Sans` warning。HTML 报告和 CSV/JSON 说明仍保留中文。`data_quality` 会拆成 `data_quality_counts.png` 和 `data_quality_ratios.png` 两张图，避免 count 和 ratio 共用同一坐标轴。

1D、5D、10D、20D 的主色调仍分别是蓝、橙、绿、红，但默认使用更柔和的十六进制颜色：`#4C78A8`、`#F58518`、`#54A24B`、`#E45756`。所有 horizon 相关图表都会复用这套颜色。

`long_short_curve.png` 展示的是日度多空收益差的累计和，即 `cumulative_long_short_returns = daily_long_short_returns.cumsum()`。原始日度序列仍保存为 `daily_long_short_returns`，用于后续统计指标计算。
`long_short = G10 - G1` 是固定方向的高因子值组减低因子值组 spread 诊断，不会自动判断因子正负方向。若因子方向未知，阅读分组收益、分组暴露和分组换手时应同时看 G1 与 G10。

最精简版统计输出可以用于批量因子训练：

```python
from factor_backtest import run_factor_backtest_minimal

summary = run_factor_backtest_minimal(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
)
```

只跑数据、不画图：

```python
from factor_backtest import render_factor_backtest_report, run_factor_backtest_data

result = run_factor_backtest_data(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
)

# 如果希望 latest/ 入口也生成图表：
render_factor_backtest_report(result.latest_dir)
```

读取已有结果：

```python
from factor_backtest import load_backtest_result, render_factor_backtest_report

result = load_backtest_result(factor_name="factor_dm_20d", run="latest")
ic_stats = result.read_table("all", "ic_stats")
render_factor_backtest_report(result.run_dir)
```

## IC 方法配置

默认只计算 Spearman RankIC：

```python
cfg = BacktestConfig(ic_methods=["spearman"])
```

也可以只计算 Pearson IC，或同时计算两种 IC：

```python
cfg = BacktestConfig(ic_methods=["pearson"])
cfg = BacktestConfig(ic_methods=["spearman", "pearson"])
```

Spearman 衡量因子排序和未来收益排序的相关性，适合默认的截面排序评价。Pearson 衡量因子原始数值和未来收益数值的线性相关性，可作为可选诊断。兼容表名 `daily_ic`、`cumulative_ic`、`ic_stats` 在包含 Spearman 时指向 Spearman；如果只配置 Pearson，则指向唯一可用的 Pearson。新输出会同时保留带方法名的结果，例如 `daily_ic_spearman`、`ic_stats_spearman`、`cumulative_ic_spearman.png`。如果启用 Pearson，会额外输出 `daily_ic_pearson`、`ic_stats_pearson`、`cumulative_ic_pearson.png` 和 `ic_overview_pearson.png`。

## 风格暴露和行业数据

如需检查因子和 Barra10 风格暴露的关系，或计算风格/行业中性化 IC，可以把风险暴露和行业 dummy 数据放在：

```text
/data/zhangyuan/risk&industry/CNE5&Industry.csv
```

配置：

```python
from factor_backtest.config import BacktestConfig, DataSourceConfig, PathConfig

cfg = BacktestConfig(
    paths=PathConfig(
        data_root="/data/zhangyuan",
        risk_exposure_path="risk&industry/CNE5&Industry.csv",
    ),
    data_sources=DataSourceConfig(
        risk_exposure_source="csv",
    ),
    min_industry_ic_stocks=10,
)
```

CSV 需要包含 `date`/`trade_date`、`symbol`、Barra10 风格暴露和行业 dummy。默认风格列为：

```text
size, non_linear_size, momentum, liquidity, book_to_price,
leverage, growth, earnings_yield, beta, residual_volatility
```

`comovement` 当前会被忽略。行业归属按每日 `date-symbol` 动态读取；无行业归属的股票会在需要行业信息的计算中剔除并给 warning；多行业 dummy 为 1 时，该股票会同时参与这些行业的行业内分组计算。

默认 `risk_exposure_source="csv"`，所以只要文件存在，`enabled_sections="all"` 会额外输出：

- `factor_style_exposure`：每日因子值与 Barra10 暴露的截面相关性，默认 Spearman，也跟随 `ic_methods` 支持 Pearson。
- `style_neutralized_ic`：用 `factor = intercept + Barra10 + residual` 的 residual 计算 IC。
- `style_industry_neutralized_ic`：用 `factor = intercept + Barra10 + industry dummies + residual` 的 residual 计算 IC。
- `group_exposure_diagnostics`：每天按因子分成 G1-G10 后，输出 pool、G1、G10、G10-G1、G1-pool、G10-pool 的风格暴露和行业暴露。这里会同时关注最低组和最高组，不把 G10 视为唯一重点。
- `within_industry_group_return`：每日在每个行业内部按因子分组，再跨行业合并同组股票收益。
- `group_turnover`：输出 G1-G10 全部分组的日度换手率，并单独汇总 G1、G10 和 edge_avg。`edge_avg` 是 G1 与 G10 换手率的简单平均，用来观察两端组合整体换手压力。

其中 `group_turnover` 不依赖风险暴露数据；即使设置 `risk_exposure_source="none"`，`enabled_sections="all"` 仍会输出分组换手率。其他风格、行业、中性化模块依赖 `risk_exposure`。

行业暴露图的图例会使用 `industry_01` 这类 ASCII 标签，避免服务器缺中文字体时产生 Matplotlib glyph warning；标签和真实行业名的对应关系保存在 `group_industry_exposure_plot_label_map`。

如果当前环境没有风险暴露文件，需要显式关闭：

```python
data_sources=DataSourceConfig(risk_exposure_source="none")
```

关闭后，`enabled_sections="all"` 会跳过这些依赖风险暴露数据的模块，保留基础因子回测模块和不依赖风险暴露的 `group_turnover`。

行业 IC 暂不输出，避免行业数 × horizon × IC 方法导致图表过多。

交互式复盘模板在：

```text
notebooks/analyze_factor_result.ipynb
```

详细参数说明见：

```text
docs/使用手册.md
```

运行时默认会打印轻量进度日志，例如读取因子、读取 ClickHouse 行情、按股票池计算 IC、分组收益、写入 artifacts、执行报告模块和最终输出目录。可以在调用脚本中设置：

```python
verbose = False
```

关闭日志。

## 本地测试

推荐使用 `pytest` 跑完整测试：

```powershell
& 'C:\Users\fizzz\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests -q
```

如果当前环境没有 `pytest`，可以使用标准库测试入口：

```powershell
& 'C:\Users\fizzz\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests\run_tests.py
```

## 外部收益数据

默认收益仍然是 1D/5D/10D/20D open-to-open：

```text
1D  = open[t+2]  / open[t+1]  - 1
5D  = open[t+6]  / open[t+1]  - 1
10D = open[t+11] / open[t+1]  - 1
20D = open[t+21] / open[t+1]  - 1
```

可以通过 `external_returns` 额外传入外部收益。外部收益会和内置收益同级参与由 `ic_methods` 控制的 IC、累计 IC、IC 统计、分组收益、分层收益、多空收益、performance metrics、artifacts 和 report。默认 `ic_methods=["spearman"]` 时仍输出 Spearman RankIC；启用 Pearson 后会额外输出 Pearson IC 相关表和图。

外部收益支持宽表和长表。宽表要求：

```text
index = trade_date
columns = symbol
value = external return
```

长表支持：

```text
trade_date/date + symbol/asset + return/ret/future_return/value/factor_value/factor
```

关键口径：`external_return.loc[t, symbol]` 必须表示用于检验 `factor_df.loc[t, symbol]` 的未来收益标签。这里的 `trade_date=t` 是因子日期，不是收益开始日，也不是收益结束日。

示例：

```python
result = run_factor_backtest(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
    external_returns={
        "external_alpha": external_return_df,
        "external_5d": {
            "data": external_5d_return_df,
            "horizon_days": 5,
        },
    },
)
```

`horizon_days` 只影响 10 组累计收益线图。未传 `horizon_days` 时，框架仍计算 IC、分组收益、多空收益等统计，但会跳过该外部收益的 `10-Group Cumulative Return` 图，并在 report warning 中说明原因。传入 `horizon_days` 后，累计线使用 `(1 + group_return) ** (1 / horizon_days) - 1` 转成日等价收益后再复利。

如果只想使用外部收益，不计算内置 1D/5D/10D/20D，可以设置 `horizons=[]`：

```python
cfg = BacktestConfig(
    horizons=[],
    ic_methods=["spearman"],
)

result = run_factor_backtest(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
    external_returns={
        "my_return": {
            "data": external_return_df,
            "horizon_days": 5,
        },
    },
)
```

当前接口仍需要传入 `market_data`，因为框架还会用行情日期和股票列做对齐、股票池和可交易性过滤。

## 取数配置集中化

可变取数配置集中在 `factor_backtest.config`。当前行情、ST、停牌等可通过 ClickHouse 获取；pool 暂时仍使用 CSV；因子值长期保留文件和数据库双入口的设计空间；风格暴露和行业 dummy 当前通过 CSV 读取，并预留 ClickHouse 切换入口。

```python
from factor_backtest import BacktestConfig
from factor_backtest.config import ClickHouseConfig, ClickHouseTableConfig, DataSourceConfig, PathConfig

cfg = BacktestConfig(
    paths=PathConfig(
        project_dir="/app/workspace/zhangyuan/Factor_Backtest",
        data_root="/data/zhangyuan",
        pool_dir="/data/zhangyuan/pool",
        risk_exposure_path="risk&industry/CNE5&Industry.csv",
    ),
    data_sources=DataSourceConfig(
        market_data_source="clickhouse",
        pool_source="csv",
        factor_source="file",
        risk_exposure_source="csv",
        clickhouse=ClickHouseConfig(
            host="10.10.0.10",
            port=8123,
            username="zhangyuan",
            password="zhang2026",
        ),
        clickhouse_tables=ClickHouseTableConfig(
            ohlcv="stock_data.view_stock_qfq_adjusted_ohlcv_v2",
            shares="cn_stock_fundamentals.shares",
            st="cn_stock_fundamentals.is_st_stock",
            suspended="cn_stock_fundamentals.is_suspended",
            risk_exposure=None,
        ),
    ),
)
```

现阶段 `pool_source="csv"` 和 `risk_exposure_source="csv"` 是已实现路径；`pool_source="clickhouse"` 和 `risk_exposure_source="clickhouse"` 是未来入库后的统一切换入口，目前会明确报未实现。

读取 ClickHouse 行情时可以直接传入集中配置：

```python
market_data = load_market_data_from_clickhouse(
    start_date="2020-01-01",
    end_date="2026-05-02",
    config=cfg.data_sources,
)
```
