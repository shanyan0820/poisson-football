# 泊松足球实验室

手机优先的足球概率分析工具。GitHub Actions 定时读取 football-data.co.uk 的真实比赛结果 CSV，GitHub Pages 提供免费手机页面，浏览器端完成三层攻防加权与泊松计算。当前覆盖五大联赛及荷甲、葡超、比甲、苏超、土超、希腊超。

## 当前版本

- 支持英超、西甲、意甲、德甲、法甲、荷甲、葡超、比甲、苏超、土超、希腊超，共 11 个顶级联赛、194 支本赛季球队；仅使用本赛季已结束联赛比赛，球队显示中文名并保留原始英文队名。
- 联赛与主客队自动选择，无需手工填写进失球。
- 主队历史主场、客队历史客场权重 50%；两队全部联赛比赛 40%；最近 3 场同联赛比赛 10%。
- 每层按 5 场先验强度向相应联赛均值收缩，降低小样本噪声。
- 从联赛真实赛果计算主场、客场进球均值，不设置主观主场加成。
- 输出预期进球、胜平负、大小球、双方进球、零封和最可能比分。
- 页面展示每层样本量、收缩后的进失球、联赛基准和 λ 代入过程。
- GitHub Actions 每 6 小时更新并重新发布；数据源失败时工作流失败，不编造或用空数据替代。
- 14 项解析、中文译名、数据生成与模型单元测试；浏览器 JavaScript 与 Python 模型另做实数据一致性核验。

## 模型

设联赛场均主队进球为 μH、客队进球为 μA。主队合成进攻为 GFH，客队合成失球为 GAA，则：

```text
λ主 = GFH × GAA ÷ μH
λ客 = GFA × GAH ÷ μA
```

每个比分 P(h,a) 使用两个独立泊松概率的乘积。比分矩阵动态扩展，页面显示实际概率覆盖率。

## 静态页面与自动更新

`index.html` 是 GitHub Pages 入口，读取同站 `data/leagues.json`。`generate_data.py` 仅使用 Python 标准库下载、校验并生成真实联赛数据。

```bash
python3 generate_data.py
python3 -m unittest discover -p 'test_*.py' -v
```

`.github/workflows/deploy-pages.yml` 每 6 小时及手动触发时重新抓数、校验并部署 Pages。仓库 Pages 的 Source 必须选 **GitHub Actions**。

## 旧 FastAPI 原型

`app.py`、`scraper.py` 和 `server.html` 保留懂球帝公开页面版本，用于后续维护或数据源对照；它不是当前 GitHub Pages 版本的数据通路。

```bash
pip install -r requirements.txt
playwright install chromium
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 数据来源与已核验限制

- 当前公开版数据源：https://www.football-data.co.uk/ ，赛季 CSV 路径形如 `mmz4281/2627/E0.csv`。全场进球使用 `FTHG` / `FTAG`，未来比赛及空比分不会纳入。
- 2026-11-22：当前云主机与 GitHub Actions 请求懂球帝公开页均返回 HTTP 567，因此公开版改用可读取的真实 CSV，不宣称仍在自动抓取懂球帝。
- 雷速官网在当前环境返回 HTTP 405；球探/新球体育候选页面连接超时，未接入。
- 源站字段、赛程或访问规则改变时需要维护；工作流只保证按公开源生成，不保证第三方服务永久可用。
- 独立泊松未修正低比分相关性，也未纳入阵容、红牌、战意和赛程密度。
- 50/40/10 与 5 场先验均为待历史回测参数，不代表已证明最优。
- 仅用于数据研究，概率不是确定赛果或投注建议。
