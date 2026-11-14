# Lottery Lab · 双色球 / 大乐透统计研究

一个小型、可复现的 **Python 标准库** 彩票历史开奖研究工具。**只支持双色球（SSQ）和超级大乐透（DLT）**。

> 研究目标：验证是否存在样本外可重复的统计优势，而不是承诺预测或中奖。公平且独立的开奖中，历史号码无法提升下一期某张票的中奖概率。请理性购彩。

## 快速开始

需要 Python 3.10+，无需第三方 Python 依赖。

```bash
python -m lottery_lab sync --game all
python -m lottery_lab analyze --game ssq --simulations 500
python -m lottery_lab analyze --game dlt --simulations 500
python -m lottery_lab backtest --game ssq --min-train 100 --window 50
python -m lottery_lab backtest --game dlt --min-train 100 --window 50
python -m lottery_lab pick --game ssq --strategy random --count 5 --seed 42
python -m unittest discover -s tests -v
```

所有命令均输出 JSON；可通过 `> report.json` 保存结果，方便后续接入前端或自动化分析。`sync` 将历史数据保存到 `data/{ssq,dlt}.json`，开奖数据未变化时不会重写缓存。不会自动下注。GitHub Actions 的 `update-data.yml` 在北京时间每天23:05自动同步两个彩种，仅当号码数据变化时提交。

## 数据源

目前采用两份公开历史开奖镜像，**不是彩票发行机构的官方API**：

1. 主源：[`gudaoxuri/lottery_history`](https://github.com/gudaoxuri/lottery_history)，其项目说明称从官方渠道采集，JSON 位于 `data/ssq.json`、`data/dlt.json`。
2. 备源：[`Justdoitfor/my-lottery-data`](https://github.com/Justdoitfor/my-lottery-data)，项目说明称来自 `datachart.500.com`，JSON同名。

同步时依次请求两份数据：两源均可访问时，对有交集的开奖日期进行**逐期号码交叉校验**，验证通过后合并独有期号以保留最长历史；只要出现冲突就拒绝覆盖本地数据。单源不可用时会降级并在元数据中标记“无法交叉检验”。每份数据记录源URL、读取时间、SHA-256与来源诊断。

公开镜像可能延迟、遗漏或存在上游错误；参与任何真实决策前请对照[中国福彩网](https://www.cwl.gov.cn/)及[中国体彩网](https://www.lottery.gov.cn/)官方开奖公告。可在 `lottery_lab/core.py` 的 `SOURCES` 更换或扩展镜像。

## 统计与回测方法

**统计分析 `analyze`**：前/后区逐号出现频率、当前遗漏期数、理论期望频次、Pearson卡方统计量，以及使用符合“每期不放回抽球”的蒙特卡洛随机化检验。该检验的 `p` 值不是“下期中某个号的概率”；小 `p` 值也不自动意味着可利用的偏差（需要检查多重检验及样本外复现）。

**严格滚动样本外回测 `backtest`**：每期仅用该期开奖之前的数据生成一注号码，按前区/后区命中数计分，对比三种策略：

- `random`：均匀随机，作为基线。
- `hot`：用最近 N 期的出现次数+1作加权无放回抽样。
- `cold`：用最近 N 期的频次倒数作加权无放回抽样。

三种策略在每一期各生成一张单式票，分别计2元实验成本。输出平均命中数、前区≥4个命中次数、头奖完全匹配次数、命中分布，以及热/冷与随机策略前区命中差的**探索性**95%置信区间。置信区间未经多重比较修正，也不能证明获得现实投注收益。实验中的策略均具有随机性，可通过 `--seed` 复现。

**选号演示 `pick`**：展示随机/热号/冷号抽样所生成的合法号码，不提供中奖保证，所有合法单式组合的头奖概率一样。

## 数学基线

| 彩种 | 单式投注 | 头奖（完全匹配）概率 |
| --- | --- | --- |
| 双色球 | 33选6 + 16选1 | 1 / 17,721,088 |
| 大乐透 | 35选5 + 12选2 | 1 / 21,425,712 |

两个概率均按号码完全等概率、不同期相互独立计算。实际中奖奖金、加奖、追加投注和奖项规则可能变化；**初版只评价命中数，不模拟现金收益**。

## 设计边界与限制

- 初版**没有**官方数据接口直连、GUI、彩票购买、投注或对赌功能。
- 数据源是GitHub镜像，在线同步需要联网且可能受到网络访问限制。
- 回测本身不等于有效的预测能力证据：参数寻优、策略挑选都可能过拟合。后续应加入预先注册的训练/验证/锁定测试集，控制多重比较与策略搜索偏差。
- 没有找到可利用的预测优势是完全合理且预期的研究结果。

## 项目结构

```text
lottery_lab/core.py       数据校验、镜像同步、统计检验、回测
lottery_lab/__main__.py   CLI
.github/workflows/       GitHub Actions 单元测试
tests/                   标准库 unittest
```

MIT License。历史号码事实来自已注明出处的公开数据镜像；开启 GitHub Actions 后会将经校验的历史号码缓存提交到仓库，代码采用 MIT License。