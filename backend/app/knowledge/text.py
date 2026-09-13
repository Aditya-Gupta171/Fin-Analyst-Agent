"""Tokenisation for lexical retrieval, tuned for financial text.

Deliberately small: lower-casing, stop-word removal, a light suffix stemmer, and expansion of abbreviations
analysts use constantly (DSO, CFO, OFS, GCP...), applied to both documents and queries so either form matches
the other.
"""

import re

_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")

_STOP_WORD_TEXT = """
    a about above after again against all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each either few for from
    further had has have having here how i if in into is it its itself just may might more most much
    must my no nor not of off on once only or other our out over own same should so some such than
    that the their them then there these they this those through to too under until up upon very was
    we were what when where whether which while who whom why will with within would you your
"""
STOP_WORDS = frozenset(_STOP_WORD_TEXT.split())

ABBREVIATIONS: dict[str, str] = {
    "dso": "days sales outstanding receivables",
    "dio": "days inventory outstanding",
    "dpo": "days payables outstanding",
    "ccc": "cash conversion cycle",
    "cfo": "cash flow operating activities",
    "fcf": "free cash flow",
    "pat": "profit after tax",
    "pbt": "profit before tax",
    "ebitda": "earnings before interest tax depreciation amortisation operating profit",
    "ebit": "operating profit",
    "eps": "earnings per share",
    "roce": "return capital employed",
    "roe": "return equity",
    "roa": "return assets",
    "etr": "effective tax rate",
    "capex": "capital expenditure",
    "cwip": "capital work progress",
    "rpt": "related party transactions",
    "rpts": "related party transactions",
    "ofs": "offer sale",
    "gcp": "general corporate purposes",
    "ipo": "initial public offer",
    "drhp": "draft red herring prospectus offer document",
    "rhp": "red herring prospectus offer document",
    "icdr": "issue capital disclosure requirements",
    "lodr": "listing obligations disclosure requirements",
    "kam": "key audit matters",
    "kams": "key audit matters",
    "caro": "companies auditor report order",
    "npa": "non performing assets",
    "npas": "non performing assets",
    "gnpa": "gross non performing assets",
    "nnpa": "net non performing assets",
    "crar": "capital risk weighted assets ratio",
    "nim": "net interest margin",
    "ecl": "expected credit loss",
    "nbfc": "non banking financial company",
    "msme": "micro small medium enterprises",
    "epc": "engineering procurement construction",
    "sez": "special economic zone",
    "waca": "weighted average cost acquisition",
    "kpi": "key performance indicators",
    "kpis": "key performance indicators",
    "qib": "qualified institutional buyers",
    "qibs": "qualified institutional buyers",
    "esop": "employee stock options share based payments",
    "esops": "employee stock options share based payments",
    "yoy": "year on year",
    "qoq": "quarter on quarter",
}


def stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ingly", "edly", "ing", "ed", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        expansion = ABBREVIATIONS.get(word)
        words = [word, *expansion.split()] if expansion else [word]
        tokens.extend(stem(w) for w in words if w not in STOP_WORDS)
    return tokens
