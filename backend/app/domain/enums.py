"""Controlled vocabularies used across the domain."""

from enum import StrEnum


class DocType(StrEnum):
    QUARTERLY_RESULT = "quarterly_result"  # SEBI LODR Regulation 33
    ANNUAL_REPORT = "annual_report"
    DRHP = "drhp"  # Draft Red Herring Prospectus
    RHP = "rhp"  # Red Herring Prospectus


class Basis(StrEnum):
    STANDALONE = "standalone"
    CONSOLIDATED = "consolidated"


class Sector(StrEnum):
    BANKING = "banking"
    NBFC = "nbfc"
    INSURANCE = "insurance"
    IT_SERVICES = "it_services"
    MANUFACTURING = "manufacturing"
    INFRASTRUCTURE = "infrastructure"
    PHARMA_HEALTHCARE = "pharma_healthcare"
    FMCG_CONSUMER = "fmcg_consumer"
    AUTO = "auto"
    CHEMICALS = "chemicals"
    METALS_MINING = "metals_mining"
    ENERGY_UTILITIES = "energy_utilities"
    REAL_ESTATE = "real_estate"
    TELECOM_MEDIA = "telecom_media"
    CONSUMER_INTERNET = "consumer_internet"
    OTHER = "other"


# Named groups that rule packs can reference instead of listing sectors one by one.
SECTOR_GROUPS: dict[str, frozenset[Sector]] = {
    "financials": frozenset({Sector.BANKING, Sector.NBFC, Sector.INSURANCE}),
    "lenders": frozenset({Sector.BANKING, Sector.NBFC}),
}


def expand_sectors(names: list[str]) -> frozenset[Sector]:
    """Resolve a mix of sector names and sector-group names into concrete sectors."""
    sectors: set[Sector] = set()
    for name in names:
        if name in SECTOR_GROUPS:
            sectors |= SECTOR_GROUPS[name]
        else:
            sectors.add(Sector(name))
    return frozenset(sectors)


class SizeBucket(StrEnum):
    """Size band by annualised revenue from operations (₹ crore)."""

    SMALL = "small"  # below 500
    MID = "mid"  # 500 to 5,000
    LARGE = "large"  # above 5,000


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)


class Unit(StrEnum):
    INR_CRORE = "inr_crore"
    INR = "inr"  # per-share amounts
    RATIO = "ratio"  # stored as a fraction, displayed as a percentage
    TIMES = "times"
    DAYS = "days"
    COUNT = "count"
    TEXT = "text"


class Statement(StrEnum):
    PROFIT_AND_LOSS = "profit_and_loss"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    DISCLOSURE = "disclosure"
    ISSUE = "issue"  # offer-document specific (DRHP / RHP)


class Nature(StrEnum):
    FLOW = "flow"  # accumulates over a period (revenue, cash flow)
    STOCK = "stock"  # point-in-time balance (receivables, borrowings)


class Scope(StrEnum):
    PERIOD = "period"  # one value per reporting period
    DOCUMENT = "document"  # one value for the whole document (issue size, promoter pledge)
