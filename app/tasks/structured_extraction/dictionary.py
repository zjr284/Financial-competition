"""结构化提取字段字典（契约 docs/接口约定.md 第 4 节「字段字典 v1」）。

字段代码在此冻结，不同模块不得使用不同拼法。三类公告的字段与契约一一对应：
  pledge        shareholder, pledged_shares, pledged_ratio, pledge_start_date,
                pledge_end_date, pledgee
  bid_award     project_name, winning_party, contract_amount, currency, performance_period
  equity_change holder, shares_before, shares_after, ratio_before, ratio_after, change_method
记录级固定字段：document_id, company_code, event_date（提取时补齐）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


ANNOUNCEMENT_TYPES = ("pledge", "bid_award", "equity_change")

TYPE_LABELS = {
    "pledge": "质押公告",
    "bid_award": "中标公告",
    "equity_change": "股权变动",
}

# 公告类型识别关键词
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pledge": ("质押", "质押公告", "解除质押", "补充质押", "股权质押"),
    "bid_award": ("中标", "中标公告", "中标通知书", "预中标", "中标候选人", "项目中标"),
    "equity_change": ("权益变动", "股权变动", "股份变动", "减持", "增持", "变动公告"),
}


@dataclass(frozen=True)
class FieldSpec:
    """一个待提取字段的规则。

    anchors：靠前的强锚点（如「质押股数」），命中即优先；
    weak_anchors：通用锚点（如「股东名称」），仅在强锚点缺失时使用；
    expect：期望值类型，决定归一化方式（amount/share/percent/date/text）。
    """

    field_code: str
    label: str
    expect: str
    anchors: tuple[str, ...] = ()
    weak_anchors: tuple[str, ...] = ()
    required: bool = True
    value_chars: str = r"[^\s，。；;、|]"
    max_value_len: int = 40
    cross_line: bool = True
    notes: str = ""


# 金额/比例/股数的通用数值片段（含量纲与单位）
NUM = r"[+-]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
MAG = r"(?:万亿|千万|百万|十万|亿|万|千)?"
UNIT_MONEY = r"(?:人民币)?(?:元|万元|亿元|万|亿)?"
UNIT_SHARE = r"(?:股|万股|亿股)"


PLEDGE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "shareholder",
        "出质人/股东名称",
        "text",
        anchors=("出质人", "出质股东", "质押股东", "股东名称", "出质方"),
        value_chars=r"[^\s，。；;、|（）()]",
        max_value_len=40,
    ),
    FieldSpec(
        "pledged_shares",
        "质押股数",
        "share",
        anchors=("本次质押股数", "质押股数", "质押股份数量", "本次质押股份数", "质押数量"),
        weak_anchors=("质押股份",),
        value_chars=rf"{NUM}\s*{MAG}\s*{UNIT_SHARE}",
        max_value_len=24,
    ),
    FieldSpec(
        "pledged_ratio",
        "质押比例",
        "percent",
        anchors=("占公司总股本比例", "占总股本比例", "占其所持股份比例", "质押比例"),
        weak_anchors=("占公司总股本", "占其持股比例"),
        value_chars=rf"{NUM}\s*%",
        max_value_len=16,
    ),
    FieldSpec(
        "pledge_start_date",
        "质押起始日",
        "date",
        anchors=("质押起始日", "质押开始日", "起始日期", "质押起始日期"),
        value_chars=r"\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?",
        max_value_len=24,
    ),
    FieldSpec(
        "pledge_end_date",
        "质押到期日",
        "date",
        anchors=("质押到期日", "质押截止日", "到期日期", "购回交易日", "质押期限"),
        value_chars=r"\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?",
        max_value_len=24,
    ),
    FieldSpec(
        "pledgee",
        "质权人",
        "text",
        anchors=("质权人", "质权方", "质权银行"),
        weak_anchors=("受托方",),
        value_chars=r"[^\s，。；;、|]",
        max_value_len=40,
    ),
)


BID_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "project_name",
        "项目名称",
        "text",
        anchors=("项目名称", "中标项目名称", "工程名称", "标的名称"),
        value_chars=r"[^\n。；;|]",
        max_value_len=60,
    ),
    FieldSpec(
        "winning_party",
        "中标方",
        "text",
        anchors=("中标人", "中标方", "中标单位", "中标供应商", "承包人"),
        weak_anchors=("投标人", "联合体"),
        value_chars=r"[^\n，。；;、|]",
        max_value_len=60,
    ),
    FieldSpec(
        "contract_amount",
        "合同金额",
        "amount",
        anchors=("中标金额", "合同金额", "中标价", "合同总金额", "中标总价", "成交金额"),
        weak_anchors=("中标价格", "项目金额"),
        value_chars=rf"{NUM}\s*{MAG}\s*{UNIT_MONEY}",
        max_value_len=24,
    ),
    FieldSpec(
        "currency",
        "币种",
        "text",
        anchors=("币种", "结算币种", "计价货币"),
        weak_anchors=("人民币", "美元", "欧元"),
        value_chars=r"[^\s，。；;、|]",
        max_value_len=12,
    ),
    FieldSpec(
        "performance_period",
        "履约期限",
        "text",
        anchors=("履约期限", "服务期限", "工期", "合同期限", "履约周期", "建设期"),
        value_chars=r"[^\n。；;|]",
        max_value_len=40,
    ),
)


EQUITY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "holder",
        "变动主体",
        "text",
        anchors=("股东名称", "信息披露义务人", "减持主体", "增持主体", "变动主体", "持有人"),
        weak_anchors=("股东",),
        value_chars=r"[^\s，。；;、|（）()]",
        max_value_len=40,
    ),
    FieldSpec(
        "shares_before",
        "变动前股数",
        "share",
        anchors=("本次变动前持有股份", "变动前持股数量", "变动前股数", "减持前持股数量"),
        weak_anchors=("变动前", "减持前"),
        value_chars=rf"{NUM}\s*{MAG}\s*{UNIT_SHARE}",
        max_value_len=24,
    ),
    FieldSpec(
        "shares_after",
        "变动后股数",
        "share",
        anchors=("本次变动后持有股份", "变动后持股数量", "变动后股数", "减持后持股数量"),
        weak_anchors=("变动后", "减持后"),
        value_chars=rf"{NUM}\s*{MAG}\s*{UNIT_SHARE}",
        max_value_len=24,
    ),
    FieldSpec(
        "ratio_before",
        "变动前比例",
        "percent",
        anchors=("变动前持股比例", "本次变动前比例", "减持前持股比例"),
        weak_anchors=("变动前", "减持前"),
        value_chars=rf"{NUM}\s*%",
        max_value_len=16,
    ),
    FieldSpec(
        "ratio_after",
        "变动后比例",
        "percent",
        anchors=("变动后持股比例", "本次变动后比例", "减持后持股比例"),
        weak_anchors=("变动后", "减持后"),
        value_chars=rf"{NUM}\s*%",
        max_value_len=16,
    ),
    FieldSpec(
        "change_method",
        "变动方式",
        "text",
        anchors=("变动方式", "减持方式", "增持方式", "权益变动方式", "交易方式"),
        value_chars=r"[^\n。；;|]",
        max_value_len=40,
    ),
)


FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "pledge": PLEDGE_FIELDS,
    "bid_award": BID_FIELDS,
    "equity_change": EQUITY_FIELDS,
}

# 记录级固定字段：字段字典规定提取记录固定包含
RECORD_FIELDS = ("document_id", "company_code", "event_date")

# 事件日期锚点
EVENT_DATE_ANCHORS = ("公告日期", "披露日期", "发布日期", "签署日期", "中标日期", "报告日期")
DATE_TOKEN = r"\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?"

# 公司代码锚点
COMPANY_ANCHORS = ("证券代码", "股票代码", "公司代码", "证券代码（" )
COMPANY_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")

CHINESE_CURRENCY = {
    "人民币": "CNY",
    "元": "CNY",
    "美元": "USD",
    "欧元": "EUR",
    "港币": "HKD",
    "港元": "HKD",
    "日元": "JPY",
}


def compile_anchor(label: str) -> re.Pattern[str]:
    """标签匹配：允许标签内部存在空格、全角括号等排版噪声。"""
    chars = [re.escape(ch) for ch in label]
    return re.compile(r"\s*".join(chars))


@dataclass
class ExtractConfig:
    """提取行为配置。"""

    announcement_types: tuple[str, ...] = ANNOUNCEMENT_TYPES
    # 跨行取值的字符窗口（一行不足时向下看一行）
    value_window: int = 80
    # 段内最大事件数，防止异常文档产生海量记录
    max_events: int = 40
    segment_min_chars: int = 60
    segment_max_chars: int = 4000
    warnings: list[str] = field(default_factory=list)
