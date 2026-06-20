# 分享推广计划设计文档

**状态**: 草稿 V1
**日期**: 2026-06-20
**作者**: 项目方
**目标读者**: 项目团队 + 实施工程师

---

## 1. 背景与目标

### 1.1 现状
- 项目有完整 Pro 付费体系(按场次计费 + 邀请码)
- 流量来源主要靠自然 + 已有用户口碑,缺主动拉新
- 没有"分享"激励机制,用户即使想帮项目传播也无回报

### 1.2 目标
1. **拉新**: 用现有用户的社交关系链,带来首批种子用户之外的增量
2. **转化漏斗**: 分享 → 验证 → 24h Pro 体验 → 付费转化
3. **品牌曝光**: 在目标用户活跃的社媒平台留下足迹(SEO + 口碑)
4. **可度量**: 每个平台的拉新成本(发码数 / 实际新注册)能拆开

### 1.3 非目标(明确不做)
- 不做复杂的推荐人永久分成(运营成本高、容易被薅)
- 不做社交账号 OAuth 登录(隐私问题,用户接受度低)
- 不做自动发文(平台检测后会封号)

---

## 2. 目标用户画像

WC2026 预测项目的核心受众:

| 维度 | 描述 |
|------|------|
| 兴趣 | 足球、世界杯、博彩/竞猜、赔率、数据分析 |
| 年龄 | 22–40 岁(博彩/数据类目典型) |
| 地域 | 6 语言市场: zh-CN / en / es / pt / fr / ar |
| 行为 | 关注赛前赔率、对比不同预测、活跃在体育/博彩社群 |
| 痛点 | 不知道信哪个预测、想看 AI 视角但又怕付费踩雷 |
| 价值锚 | "免费 24h 看 AI 反思 + 价值下注 + 联网情报",刚好命中痛点 |

**关键洞察**: 这群人已经在 Reddit / X / Telegram / 微博 这些地方,只是没理由主动传播。所以激励机制要"轻、准、有回报"。

---

## 3. 平台推荐(按优先级)

### Tier 1 — 必做(高匹配 + 可验证)

#### 3.1 X (Twitter) — **核心** ⭐
- **为何选**: 体育博彩类内容 X 是头号阵地,实时讨论密集
- **地域**: 全球,英语/西/葡/阿/法语用户都重
- **可分享形式**: 推文 + hashtag (#WorldCup2026 / #AI预测 / 价值下注)
- **验证可行性**: ✅ 通过 `publish.twitter.com/oembed?url=...` 取推文 html,搜索关键词
- **风险**: X 反爬严,需带 User-Agent,速率限制 180 req/15min/IP
- **推荐落地**: 默认开启,作为旗舰平台

#### 3.2 Reddit
- **为何选**: r/soccer / r/sportsbook / r/worldcup 讨论深度最高,长文分享自然
- **地域**: 英语 + 西语(各 r/LigaMX / r/futbol 也有空间)
- **可分享形式**: 发个帖子介绍项目,带链接 + 截图
- **验证可行性**: ✅ Reddit 公开 JSON: `reddit.com/r/{sub}/comments/{id}.json` 直接拿正文
- **风险**: Reddit 反垃圾,新号发外链会被 shadowban,需提示老号
- **推荐落地**: 默认开启

#### 3.3 Telegram
- **为何选**: CIS / MENA / 巴西 / 东南亚体育博彩群密度极高
- **可分享形式**: 公开频道发消息,或公开群组聊天记录链接
- **验证可行性**: ✅ `t.me/s/{channel}/{msg_id}` 公开页面可抓
- **风险**: 私密群组/频道抓不到,需明确提示"需公开"
- **推荐落地**: 默认开启(尤其 ar / pt / ru 市场)

### Tier 2 — 强烈推荐(用户量大,验证稍麻烦)

#### 3.4 微博
- **为何选**: 中文市场必入,微博是中文体育/博彩讨论主战场
- **可分享形式**: 公开微博正文 + 链接
- **验证可行性**: ✅ `m.weibo.cn/status/{id}` 移动版公开可抓(html)
- **风险**: 需登录的页面抓不到,要明确"公开微博"
- **推荐落地**: 默认开启(zh 必入)

#### 3.5 YouTube
- **为何选**: 长视频讲解"AI 怎么预测世界杯"是天然拉新内容
- **可分享形式**: 视频描述或置顶评论含项目链接
- **验证可行性**: ✅ YouTube oembed + 视频页面(html)
- **风险**: 视频制作门槛高,分享激励主要是奖励内容创作者
- **推荐落地**: 开启,但预期量不大

#### 3.6 Discord
- **为何选**: 体育博彩 Discord 社群活跃
- **可分享形式**: 公开频道消息(但多数是私有)
- **验证可行性**: ⚠️ 公开消息可抓,但 Discord 反爬严 + 大量私有
- **风险**: 验证成功率低,建议作为"补充"
- **推荐落地**: 开启但限流(每个 Discord 链接 24h 内最多 1 人用)

### Tier 3 — 备选(流量大但验证难)

| 平台 | 流量 | 验证方式 | 推荐 |
|------|------|----------|------|
| 抖音 / TikTok | 巨大 | 个人主页 bio 链接可抓 | 开启,验证宽松 |
| 小红书 | 中文大 | 笔记需登录,只能看首屏 | 暂缓,等 API |
| Instagram | 全球 | 公开 Reel URL 可抓 | 开启,验证宽松 |
| WhatsApp Status | 拉美/印度 | 私有,无法验证 | 关闭 |
| 微信公众号 | 中文 | 文章需登录,验证困难 | 关闭 |
| Substack / Medium | 长文 | 公开文章 URL 可抓 | 开启,小众 |
| Facebook 群 | 广泛 | 公开群可抓 | 开启,验证宽松 |

### 3.7 推荐组合(首期)
- **自动验证**(URL 抓得到): X / Reddit / Telegram / 微博 / YouTube
- **宽松验证**(限流兜底): Discord / TikTok / Instagram / Substack / Facebook
- **关闭**: WhatsApp Status / 微信公众号 / 小红书(等 API 出来)

---

## 4. 验证机制

### 4.1 整体流程

```
用户打开弹窗 → 选"分享换 1 天 Pro"
    ↓
选择平台(显示 logo + 说明)
    ↓
后端返回"分享文案模板" + "项目 URL" + "验证校验码"(8 字符随机串)
    ↓
用户复制内容,到所选平台发布
    ↓
用户回到弹窗,粘贴"分享链接 URL" + 选"验证"
    ↓
后端 /api/verify-share:
  1. 拉取 URL
  2. 解析 HTML/JSON 提取正文
  3. 校验:
     - 域名在白名单
     - 正文长度 ≥ 30 字符
     - 必须包含: ①项目域名 OR 校验码 ② 至少一个产品关键词
  4. 抓取时间戳(发布 ≤ 24h)
  5. 速率限制
    ↓
通过 → 生成 24h 邀请码,展示给用户
失败 → 显示具体原因(链接不可达/未含项目信息/已用过/超限)
```

### 4.2 各平台验证具体实现

#### X (Twitter) — 关键路径
```
GET https://publish.twitter.com/oembed?url={tweet_url}
→ 200 OK: {"author_name":..., "html":"<blockquote>...tweet text...</blockquote>", ...}
→ 404: 推文不存在/被删/私密
→ 429: 速率限制
```
- **校验要点**:
  - author_name 非空(证明是真人,不是 bot)
  - html 里 grep 项目域名 或 校验码
  - 通过 `X.com/{user}/status/{id}` 二次抓页面,看 og:description 拿完整推文
- **失败兜底**: 如果 oembed 404,引导用户检查是否设为公开

#### Reddit
```
GET https://www.reddit.com/r/{sub}/comments/{id}.json
→ 200: [{data:{children:[{data:{selftext:..., author:..., created_utc:...}}]}}]
```
- **校验要点**:
  - selftext 长度 ≥ 50 字符
  - 含项目域名或校验码
  - created_utc 在 24h 内
- **注意**: Reddit UA 必须唯一(`wc-predict-verify/1.0`)

#### Telegram (公开频道/群)
```
GET https://t.me/s/{channel}/{msg_id}
→ 200: HTML,内含 .tgme_widget_message_text 节点
```
- **校验要点**:
  - DOM 选择器 `.tgme_widget_message_text` 提取文本
  - 含项目域名或校验码
  - datetime 属性在 24h 内

#### 微博
```
GET https://m.weibo.cn/detail/{status_id}
→ 200: HTML(部分内容由 JS 加载,可能不全)
→ 或 GET https://m.weibo.cn/statuses/show?id={id} (公开接口)
```
- **校验要点**: 状态码 200 + 文本含项目域名
- **降级**: 如果抓不到正文,检查页面 title 含项目关键词即可

#### YouTube
```
GET https://www.youtube.com/watch?v={video_id} → HTML
或 GET https://www.youtube.com/oembed?url=... → JSON
```
- **校验要点**: 视频描述(description)含项目域名或校验码
- **限制**: 视频需公开(非 unlisted/private)

#### Discord / TikTok / Instagram / Substack / Facebook — 宽松模式
- 域名白名单 + URL 可访问(2xx) + 含校验码 = 通过
- **限流更严**: 每平台每 24h 最多 5 个新码(防滥用)
- 不强制要求含项目域名(因为这些平台链接文本经常被截断)

### 4.3 抓取实现要点

```python
# webapp/share_verifier.py 草稿
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

UA = "wc-predict-verify/1.0 (+https://wc-predict.example.com)"
TIMEOUT = 8  # 秒

PLATFORM_DOMAINS = {
    "x": ("twitter.com", "x.com"),
    "reddit": ("reddit.com",),
    "telegram": ("t.me",),
    "weibo": ("weibo.com", "m.weibo.cn"),
    "youtube": ("youtube.com", "youtu.be"),
    "discord": ("discord.com", "discord.gg"),
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "substack": ("substack.com",),
}

def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for plat, domains in PLATFORM_DOMAINS.items():
        if any(host.endswith(d) for d in domains):
            return plat
    return ""

def fetch_url(url: str) -> tuple[int, str]:
    """返回 (status, html)"""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                     allow_redirects=True)
    return r.status_code, r.text

def extract_x_text(html: str) -> str:
    """从 X oembed 返回的 HTML 抽文本"""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)

# 各平台具体提取函数...
```

### 4.4 关键词 / 域名校验

```python
PROJECT_DOMAINS = ["wc-predict.example.com"]  # 项目自己的域名
PRODUCT_KEYWORDS = [
    "WC2026 预测", "World Cup 2026 prediction",
    "AI 反思", "AI reflection", "value bet", "价值下注",
    "Elo + Dixon-Coles",  # 技术名词,博彩老手认
    "Monte Carlo",  # 概率名词
]
# 校验码(per-share 唯一 8 字符,生成时插入分享文案)
```

---

## 5. 邀请码生命周期

### 5.1 生成

```json
{
  "SHARE-X-ABC12345": {
    "code": "SHARE-X-ABC12345",
    "used": false,
    "expires_at": 1718899200,        // unix ts, now + 86400
    "source": "share:x",
    "source_meta": {
      "platform": "x",
      "tweet_url": "https://x.com/user/status/1234567890",
      "author_handle": "@user",
      "verified_at": 1718812800,
    },
    "created_at": 1718812800,
    "redeemed_by": null,
    "redeemed_at": null
  }
}
```

### 5.2 命名规则

格式: `SHARE-{平台简称}-{8 字符 base32}`

例:
- `SHARE-X-A3K9M2P7`
- `SHARE-RD-7H4F2L9Q`
- `SHARE-TG-M5N8J3K1`
- `SHARE-WB-X2L7D4R9`

- 8 字符 = 32^8 ≈ 1.1 万亿,碰撞可忽略
- 用 `secrets.token_urlsafe(6)[:8].upper()` 生成

### 5.3 状态机

```
[生成] → valid (used=false, expires_at > now)
         ↓ (24h 内 redeem)
        redeemed (used=true, redeemed_by=email, redeemed_at=ts)
         ↓
       [终态]

         ↓ (24h 内未 redeem,expires_at < now)
        expired (used=false, expires_at < now, redeem 时拒绝)
         ↓
       [终态,但 invite_codes.json 保留作为审计]
```

### 5.4 存储

**复用现有 `invite_codes.json`**,扩展字段:
- 老的硬编码码(legacy): 保留 `used` / `used_by` / `used_at`
- 新分享码: 加 `source` / `source_meta` / `expires_at` / `created_at`

```python
# payments.py 新增函数
PLATFORM_PREFIX = {"x": "X", "reddit": "RD", "telegram": "TG", "weibo": "WB"}

def _existing_recent_code(platform: str, email: str, codes: dict, now: int) -> dict | None:
    """同 (platform, email) 24h 内是否已有未 redeem 或已 redeem 的码。
    有则返回该码记录,无则 None。"""
    cutoff = now - 86400
    for c in codes.values():
        meta = c.get("source_meta") or {}
        if meta.get("platform") != platform:
            continue
        if meta.get("email", "").lower() != email.lower():
            continue
        # 未用且未过期
        if not c.get("used") and c.get("expires_at", 0) > now:
            return c
        # 已用且 redeem 在 24h 内(Pro 窗口内)
        if c.get("used") and c.get("redeemed_at", 0) >= cutoff:
            return c
    return None

def generate_share_code(platform: str, email: str, source_meta: dict) -> dict:
    """生成邀请码。同 (platform, email) 24h 内只允许 1 个有效码。"""
    now = int(time.time())
    with _invite_lock:
        codes = _load_codes()
        existing = _existing_recent_code(platform, email, codes, now)
        if existing is not None:
            return {
                "code": existing["code"],
                "expires_at": existing.get("expires_at"),
                "reused": True,
                "reason": "duplicate_platform_24h",
            }
        code = f"SHARE-{PLATFORM_PREFIX.get(platform, platform.upper()[:2])}-{secrets.token_urlsafe(6)[:8].upper()}"
        expires = now + 86400
        meta = {**source_meta, "platform": platform, "email": email}
        codes[code] = {
            "code": code,
            "used": False,
            "expires_at": expires,
            "source": f"share:{platform}",
            "source_meta": meta,
            "created_at": now,
        }
        _save_codes(codes)
    return {"code": code, "expires_at": expires, "reused": False}

def redeem_share_code(email: str, code: str) -> dict:
    # 与现有 redeem_invite_code 类似,但额外检查 expires_at
    ...
```

### 5.5 redeem 时的检查顺序

```
1. email 格式
2. code 格式(SHARE-XX-XXXXXXXX)
3. code 存在
4. !used
5. now < expires_at (新增)
6. 写 used=true + redeemed_by/at
```

错误码扩展:
- `"expired"` — 超过 24h
- 复用现有 `"not_found"` / `"already_used"` / `"invalid_email"`

---

## 6. 24 小时数据窗口(免费/邀请码用户)

### 6.1 现状

现在 Pro 权限是 `_is_paid(email)` 返回 True → 看所有比赛。

免费用户看到所有比赛(只是部分 Pro 功能被锁)。

### 6.2 新规则

**只对"分享码"用户生效**(因为是激励机制的一部分,不是产品定位改变):

> 用 SHARE-* 码激活的 Pro 权限,只能看到 **激活时刻起未来 24 小时内开赛** 的比赛数据。**已结束的比赛** 和 **24h 之外的比赛** 全部隐藏/置灰。

**实现**:

```python
# webapp/access.py(新文件)
def is_share_pro(email: str) -> bool:
    """是否为 SHARE-* 码激活的 Pro,带 24h 数据窗口"""
    users = _load_users()
    rec = users.get(email.strip().lower(), {})
    return rec.get("plan") == "share" and rec.get("expires_at", 0) > time.time()

def get_visible_match_filter(email: str) -> callable:
    """返回过滤函数:对 schedule 中每场判断是否对当前用户可见"""
    if not is_share_pro(email):
        return lambda m: True  # 全付费用户看所有
    activated_at = _get_activated_at(email)
    window_end = activated_at + 86400
    now = time.time()
    def _filter(m):
        # 已结束的永远不可见(对分享码用户)
        if m.get("status") == "finished":
            return False
        # 在窗口外(>24h 后开赛)不可见
        ko = parse_kickoff_ts(m.get("kickoff_utc", ""))
        if ko and ko > window_end:
            return False
        return True
    return _filter
```

### 6.3 UI 表现

- 看不到的比赛: 卡片置灰 + 🔒 标识 + hover 提示"24h Pro 仅显示 24 小时内开赛"
- 不在 schedule API 返回(后端过滤),前端不感知
- Tabs 上加小角标:"今日 + 未来 24h: 12 场(全部 100 场)"
- 抽屉详情页:显示"分享 24h Pro"标签,与全程 Pro 视觉区分

### 6.4 后端接入点

需要修改的端点(给非全程 Pro 用户过滤):

- `GET /api/schedule` — 过滤未来比赛
- `GET /api/champions` — 仍可访问(只算未来 24h 夺冠概率)
- `GET /api/groups` — 过滤
- `GET /api/match?date=&home=&away=` — 已结束的返回 404
- `GET /api/backtest` — 不限制(历史回测本身就是过去数据,但意义不大,可考虑禁)
- `GET /api/market` / `GET /api/value-bets` — 过滤
- `GET /api/subscription-status` — 返回 `plan: "share"` + `expires_at`

---

## 7. 反滥用措施

### 7.1 速率限制

| 维度 | 限制 | 原因 |
|------|------|------|
| 每 email | 1 码 / 7 天 | 防同一人反复薅 |
| 每 IP | 5 次验证 / 小时 | 防脚本刷 |
| 每 share_url | 3 人 / 链接 | 一条 X 帖子最多 3 人用,鼓励发新内容 |
| 每平台每天(全站) | 100 码 | 兜底,防某平台爆量 |
| 已用 URL 二次提交 | 拒绝 | 防止码转卖 |

### 7.2 链接黑名单

- 短链(bit.ly / t.co / goo.gl): 不直接拒绝,但要求最终 URL 也要可访问 + 含关键词
- 已知诈骗/赌博诱导站: 黑名单
- 自我引用(项目自己域名): 拒绝

### 7.3 校验码机制

每个 share 申请生成一个 8 字符校验码,要求用户**必须**把它写进分享正文(我们返回的模板文案里会带)。

这样:
- 自动筛掉那些发完链接就跑的人
- 校验时 grep 这个码,几乎不可能误判(8 字符 base32 唯一)

### 7.4 异常检测

- 同 email 多次失败 → 临时封 1h
- 同一 IP 多次失败 → 临时封 24h
- 短时间内大量同平台码 → 平台级限流告警
- 链接内容里出现"换行 + 大量 emoji + 项目域名塞在角落"等典型刷量模式 → 人工 review

### 7.5 审计

`invite_codes.json` 永久保留所有历史码 + 验证日志(`share_verify_log.jsonl`),方便事后追溯。

---

## 8. 用户体验流程

### 8.1 弹窗新 tab:"分享换 1 天 Pro"

放在现有升级弹窗里,作为第三个按钮(在"立即支付"和"邀请码"之间):

```
[ 💰 立即支付 ¥136.00 ]   ← 已有
[ 🎁 使用邀请码解锁 ]     ← 已有
[ 🌍 分享换 1 天 Pro ]    ← 新增
```

点击后展开:
```
1. 选择平台 (9 个 logo 网格)
2. 显示分享文案模板(可编辑):
   "我用 WC2026 预测看了下 XX 比赛,AI 反思 Elo 命中率还不错
    → https://wc-predict.example.com/?ref=SHARE-X-A3K9M2P7
    校验码:A3K9M2P7"
3. [📋 一键复制] 按钮
4. 用户去平台发布
5. 回来粘贴链接 → [✓ 验证并领码]
6. 24h 邀请码显示 → 自动填入下方"邀请码"输入框
```

### 8.2 i18n

新增 6 语言 key(每个平台 × 3 句模板 + UI 文案),约 30 key × 6 语言 = 180 条。

---

## 9. 实施阶段(建议)

### Phase 1: MVP(2-3 天)
- 单一平台验证(选 X)
- 简单关键词匹配
- 24h 邀请码 + 24h 数据窗口
- 速率限制基础版
- 弹窗新 tab
- 邀请码生命周期扩展

### Phase 2: 多平台(1 周)
- Reddit / Telegram / 微博 三个高匹配平台接入
- 平台 logo + 分享模板
- 异常检测 + 黑名单

### Phase 3: 体验优化(持续)
- YouTube / Discord / TikTok / Instagram
- 累进奖励(分享 N 次 → 7 天 Pro)
- 推荐转化追踪(UTM + 渠道归因)
- A/B 测试不同分享文案

---

## 10. 风险与决策记录

### 10.1 决策(已锁定)

| 决策点 | 选择 | 影响 |
|--------|------|------|
| 奖励粒度 | **1 分享 = 1 个 24h 码**(不累进) | 简单,鼓励持续分享 |
| 24h 窗口起点 | **激活瞬间起**(redeem 成功为 t=0) | 不依赖分享发布时间,服务端时钟权威 |
| 数据窗口可见性 | **免费层基础上 + 24h Pro 功能**(详见 §6.5) | 不是"限制可见比赛",而是"限时解锁 Pro 功能" |
| 分享文案 | **模板固定 + 可改** | 防 spam 同时保留个性化空间 |
| 跨语言 | **分享文案统一用英文** | X 是国际平台,英文触达最广;UI 与邮件仍按 email locale |
| X 验证深度 | **只验"链接真实可访问"**(oembed 拿到 author_name + html 即视为真) | 不验"原创/转推/评论"动作,降低用户摩擦 |
| 同平台去重 | **同平台 24h 内只发 1 个有效码** | 防止反复分享刷 Pro 时长;一旦 redeem 成功,该平台在 Pro 窗口结束前不再发新码 |

### 10.2 技术风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| X oembed API 限流 | 中 | 高 | 缓存 + 备用 syndication API |
| Reddit 改版导致 JSON 路径失效 | 低 | 中 | 多重 fallback 解析 |
| 微博/抖音反爬升级 | 高 | 中 | 宽松验证模式 + 人工 review |
| 抓取超时影响用户感知 | 中 | 中 | 8s 超时 + 友好错误提示 |
| 校验码泄漏(用户提前知道 8 字符) | 高 | 中 | 校验码只为辅助 grep,主校验是项目域名 |

### 10.3 业务风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 用户发完就删链接 | X/微博可能发完就删,我们已记录 source_url 但 link 失效 | 邀请码已生成,redeem 不再二次校验 |
| 24h 体验转化率低 | 用户拿了 1 天但没转付费 | 24h 内 PUSH 关键功能,体验结束前 2h 弹升级提醒 |
| 平台风控把项目当广告 | 大量发链接被平台降权 | 限流 + 鼓励自然文案(模板不能太"广告") |
| 邀请码被转卖 | 闲鱼/淘宝上卖 | 限流 + 邮箱绑定 + 异常检测 |
| 邮箱被识别为 spam | 自动发送码到用户邮箱可能被屏蔽 | Phase 1 先 in-app 显示码,不发邮件;Phase 2 邮件 + SPF/DKIM |

---

## 11. 验收标准

Phase 1 上线标准:
- [ ] 用户能选 X 平台 → 看到分享模板 → 复制 → 发 X
- [ ] 回来粘贴推文链接 → 后端能在 8s 内验证
- [ ] 拿到 24h 邀请码 → 填入 redeem → 激活成功
- [ ] 激活后只能看未来 24h 比赛,其他置灰
- [ ] 24h 后自动失效,前端立刻反映
- [ ] 速率限制工作:同 IP 第 6 次返回 429
- [ ] 邀请码 `invite_codes.json` 记录完整 source_meta,可追溯
- [ ] 6 语言 i18n 全部就位
- [ ] **同平台去重**:同 email 24h 内第二次分享 X → 返回原码(`reused:true`),不生成新码
- [ ] **redeem 后去重**:已 redeem 一个 X 码的 email,24h 内再发 X 链接 → 拒绝生成新码

---

## 12. 相关文件(实施时)

新建:
- `webapp/share_verifier.py` — 平台检测 + 抓取 + 校验
- `webapp/access.py` — Pro 权限分级(全程 / 分享 24h / 免费)
- `docs/share-verify-log-spec.md` — 验证日志格式
- `webapp/static/share-verify.css` — 弹窗新 tab 样式

修改:
- `webapp/payments.py` — `generate_share_code` / `redeem_share_code` / 邀请码 schema 扩展
- `webapp/app.py` — `/api/verify-share` / `/api/share-template` / `schedule` 等端点加过滤
- `webapp/static/app.js` — 弹窗新 tab + 验证流程
- `webapp/static/index.html` — 弹窗新 tab DOM
- `webapp/static/i18n/*.json` — 30+ 新 key × 6 语言
- `.gitignore` — `share_verify_log.jsonl` 视情况(可能要保留审计,就不 ignore)

---

**下一步**: 确认上述 5 个开放问题,然后按 Phase 1 实施。
