# 数据源实测清单

探测时间:2026-10-11(两轮)　环境:bundled chromium + Playwright,headless,无登录态

## 可用数据源

| 数据源 | 访问方式 | 字段完整度 | 实测情况 |
| --- | --- | --- | --- |
| **智联招聘** zhaopin.com | 免登录 | 完整 | 20 条/页，含真实薪资/完整 JD/技能/公司 |
| **前程无忧** we.51job.com | 免登录 | 完整 | 20 条/页，结构化 JSON 属性，薪资不掩码 |
| **中国公共招聘网** job.mohrss.gov.cn | 免登录 | 有限 | 人社部官方，18.3 万条，仅岗位名/单位/薪资档/地区 |
| BOSS直聘 zhipin.com | **需登录** | 完整 | 302 跳登录页 + geetest 风控 |

## 实测不可用(不要承诺)

| 数据源 | 失败原因 |
| --- | --- |
| 猎聘 liepin.com | 搜索页返回 `about:blank`，强风控 |
| 脉脉 maimai.cn | 302 跳 `/platform/login?to=...`，强制扫码登录 |
| 领英 linkedin.com | `ERR_HTTP_RESPONSE_CODE_FAILURE`，国内网络不可达 |
| 拉勾 lagou.com | 「滑动验证页面」captcha 拦截 |
| 北京人才网 bjrc.com | 域名已失效 `ERR_NAME_NOT_RESOLVED` |
| 各城市「人才网」 | 域名高度不稳定，不做逐个适配 |
| 头部公司官网招聘页 | 华为=SPA 空壳(body 0 字)、中建=域名不解析、金螳螂=跳第三方，通用抓取不可行 |

> 公司维度的信息通过**主站职位结果里的公司字段**聚合，不去爬公司官网。

---

## 智联招聘(主通道一)

### 数据来源:优先读页面状态树,而非 DOM

搜索页往 `window.__INITIAL_STATE__` 注入完整岗位数据:

```
state.position.base
  positionId / positionName / salary / education
  positionWorkingExp / workType / salaryReal
state.position.desc
  description        <- 完整 JD 原文
  labels[]           <- 技能标签(混有行业名/福利词,需过滤)
state.position.workLocation.workAddress
```

**关键**:列表页 DOM 上薪资显示为 `**-**元`(掩码),但状态树里是真实值
(`1.5-1.8万` / `8000-16000元` / `2-4万·15薪`)。只解析 DOM 会丢掉全部薪资。

**公司名**在状态树的 `company` 分支常为空,需从 DOM 的 `.job-card__company-name`
补全(用「岗位名→公司名」索引,实测可达 100% 覆盖)。

搜索 URL:`https://www.zhaopin.com/jobs?jl={城市码}&kw={关键词}&p={页码}`

城市码:北京 530 / 上海 538 / 广州 763 / 深圳 765 / 杭州 653 / 成都 801 /
南京 635 / 武汉 736 / 西安 854 / 苏州 639 / 天津 531 / 重庆 551

选择器(改版时按序探测):`.job-card` → `.joblist-box__item` →
`[class*=joblist-box] [class*=item]` → `.joblist-item` → `[class*=job-card]`

### 技能标签过滤

`desc.labels` 混入三类非技能内容,必须剔除:
1. 行业名(「软件/IT服务」「通信/网络设备」「半导体/芯片」) — 含 `/` 的多为此类
2. 福利/工作方式(「早九晚六」「双休」「五险一金」)
3. 描述性短语(「车载项目」「业务系统」)

**但不要只保留英文** —— 产品/运营/设计岗的标签本身就是中文
(「需求分析」「用户研究」「Axure」),过度过滤会让这些岗位无标签可用。

---

## 前程无忧(主通道二)

### 数据来源:卡片上的 `sensorsdata` 属性

搜索 URL:
`https://we.51job.com/pc/search?jobArea={城市码}&keyword={关键词}&pageNum={页码}`

岗位卡片自带完整结构化 JSON:

```html
<div class="joblist-item">
  <div sensorsdata='{"jobId":"173558745","jobTitle":"Java","jobSalary":"1.2-1.4万",
       "jobArea":"北京","jobYear":"3年及以上","jobDegree":"本科",
       "companyId":"10023397","jobTime":"2026-09-07 18:50:49"}'>
```

解析:抓所有 `[sensorsdata]` → 过滤含 `jobTitle` → `json.loads`。

**要点**:
- 列表页薪资**不掩码**,直接给 `1.2-1.4万`
- 缺 `companyName`,需从 `.joblist-item` 文本取(卡片含公司名 + 行业 + 规模)
- 经验写法是「3年及以上」「5-10年」「无需经验」,需归一到统一口径
- 详情页 URL:`https://jobs.51job.com/all/{jobId}.html`
- 薪资含年薪形态(`14-25万/年`),解析时需 ÷12 折算为月薪

城市码(jobArea,6 位):北京 010000 / 上海 020000 / 广州 030200 / 深圳 040000 /
杭州 080200 / 成都 090200 / 南京 070200 / 武汉 180200

**渲染延迟**:首帧常是空壳,必须 `wait_for_selector("[sensorsdata]")` +
滚动 + 最多 3 次重试,否则会误判为「无数据」。

---

## 中国公共招聘网(官方补充源)

列表 URL:
`http://job.mohrss.gov.cn/cjobs/jobinfolist/listJobinfolist?pageNo={页码}`

- 共约 18.3 万条,每行「岗位名称 / 单位名称 / 月薪范围 / 地区」
- **无完整 JD、无学历经验字段**,薪资是粗档(`5000元以上`)
- 价值:官方权威背书、覆盖蓝领与中小企业,适合做「本地岗位总量/供需」参考
- **不适合**做精细薪资分位(与主通道口径差异大,报告中应标注)

---

## BOSS直聘(需登录)

登录是硬门槛。`--login --site zhipin` 扫码后,登录态存 `profile/` 供复用。
但即使登录,DOM 也会漂移,解析走通用兜底,**字段完整度低于智联**。

采集前应向用户说明:「需登录才能取数据,是否登录?」,不要擅自登录。

---

## 维护提示

站点改版是常态。若某源突然返回 0 条:

1. `--headless 0` 有头跑一次,肉眼确认页面结构
2. 把新选择器补到 `SITES[site]["card_candidates"]` **最前面**
3. 状态树字段名变了就改 `extract_zhaopin_state` 的 `page.evaluate` 脚本
4. **不要**为提高成功率加并发或缩短 sleep,会直接触发风控
5. 51job 若为 0 条,先确认是否是渲染延迟(已有重试逻辑,可加大 `--scroll-rounds`)
