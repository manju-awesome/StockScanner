"""
The financial profile: what the plan is about before it is a calculation.

Shape
-----
One JSON document, loaded into a `Profile`, holding people, income,
spending, assets, liabilities and goals. Everything downstream reads
from here; nothing downstream invents a number that is missing here.

Spending is split three ways, and the split is load-bearing
-------------------------------------------------------------
ESSENTIAL spending is what the plan must fund in the worst year it ever
sees. LIFESTYLE spending is what it funds when things go normally. ONE-OFF
items are dated lumps that hit once and leave.

The split matters because a plan with 40% of its spending discretionary
is a fundamentally different object from one with 5%: the first can cut
its way through a bad decade, the second cannot. Every guardrail,
scenario and success probability in this engine keys off that flexible
fraction, so collapsing spending into one number would delete the most
important fact about the plan.

Missing data is missing, not zero
----------------------------------
An unstated asset reads as None and is reported as unknown. It never
reads as $0, because a balance sheet with a silent zero in it looks
complete and is wrong in the direction that flatters the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Spending keys the engine understands. Anything else the user writes is
# kept and summed — the taxonomy guides, it does not constrain.
ESSENTIAL_KEYS = ["housing", "food", "utilities", "transportation",
                  "healthcare", "insurance", "education", "family_support",
                  "taxes", "debt_payments"]
LIFESTYLE_KEYS = ["travel", "entertainment", "restaurants", "hobbies",
                  "vehicles", "shopping", "gifts"]


def _num(v) -> float | None:
    """A number, or None. Blank strings are missing, not zero."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _sum(d: dict | None) -> float:
    if not d:
        return 0.0
    return sum(v for v in (_num(x) for x in d.values()) if v is not None)


@dataclass
class Person:
    name: str = ""
    age: float | None = None
    role: str = "self"          # self | spouse | child | dependent

    @classmethod
    def from_dict(cls, d: dict) -> "Person":
        return cls(name=str(d.get("name") or ""),
                   age=_num(d.get("age")),
                   role=str(d.get("role") or "self"))


@dataclass
class OneOff:
    """A dated lump — tuition, a house, a car, a farm, a wedding.

    Held separately from annual spending because it does not recur and
    because it lands on a specific date. Averaging a $200k education bill
    across thirty years hides the fact that it arrives in three of them,
    and sequence risk is entirely about WHEN money leaves.
    """
    label: str = ""
    amount: float | None = None
    year: int | None = None          # calendar year it lands
    currency: str = "USD"
    inflate_with: str = "inflation"  # inflation | education | healthcare | none
    goal: str = "other"              # which goal bucket it belongs to
    funded_from: str = "portfolio"   # portfolio | dedicated | income

    @classmethod
    def from_dict(cls, d: dict) -> "OneOff":
        return cls(label=str(d.get("label") or ""),
                   amount=_num(d.get("amount")),
                   year=int(d["year"]) if _num(d.get("year")) else None,
                   currency=str(d.get("currency") or "USD"),
                   inflate_with=str(d.get("inflate_with") or "inflation"),
                   goal=str(d.get("goal") or "other"),
                   funded_from=str(d.get("funded_from") or "portfolio"))


@dataclass
class Profile:
    raw: dict = field(default_factory=dict)

    # ── people ──────────────────────────────────────────────────────────
    @property
    def people(self) -> list[Person]:
        return [Person.from_dict(p) for p in self.raw.get("people", [])]

    @property
    def age(self) -> float | None:
        for p in self.people:
            if p.role == "self":
                return p.age
        return _num(self.raw.get("age"))

    @property
    def spouse_age(self) -> float | None:
        for p in self.people:
            if p.role == "spouse":
                return p.age
        return None

    @property
    def children(self) -> list[Person]:
        return [p for p in self.people if p.role == "child"]

    @property
    def retirement_age(self) -> float | None:
        return _num(self.raw.get("retirement_age"))

    @property
    def current_country(self) -> str:
        return str(self.raw.get("current_country") or "US")

    @property
    def retirement_country(self) -> str:
        return str(self.raw.get("retirement_country")
                   or self.current_country)

    @property
    def is_cross_border(self) -> bool:
        return self.retirement_country.upper() not in (
            self.current_country.upper(), "")

    # ── income ──────────────────────────────────────────────────────────
    @property
    def gross_income(self) -> float:
        """Household gross income, all sources, per year."""
        return _sum(self.raw.get("income"))

    @property
    def income_detail(self) -> dict:
        return {k: _num(v) for k, v in (self.raw.get("income") or {}).items()}

    # ── spending ────────────────────────────────────────────────────────
    @property
    def essential_spending(self) -> float:
        return _sum(self.raw.get("spending", {}).get("essential"))

    @property
    def lifestyle_spending(self) -> float:
        return _sum(self.raw.get("spending", {}).get("lifestyle"))

    @property
    def total_spending(self) -> float:
        return self.essential_spending + self.lifestyle_spending

    @property
    def flexible_fraction(self) -> float:
        """Share of spending that could be cut in a bad year.

        The single most important number for surviving a bad sequence,
        and the one most plans never write down.
        """
        total = self.total_spending
        return (self.lifestyle_spending / total) if total else 0.0

    @property
    def one_offs(self) -> list[OneOff]:
        return [OneOff.from_dict(d) for d in self.raw.get("one_offs", [])]

    # ── retirement spending ─────────────────────────────────────────────
    @property
    def retirement_spending(self) -> dict[str, float]:
        """What the plan must fund per year once work stops.

        Defaults to today's spending rather than to a percentage of it.
        The "you'll spend 80% in retirement" rule is an averaging artifact:
        it is true of populations and routinely false of individuals, and
        for someone planning to travel more or support parents it points
        the wrong way. If the user states retirement spending, that wins.
        """
        stated = self.raw.get("retirement_spending") or {}
        ess = _num(stated.get("essential"))
        life = _num(stated.get("lifestyle"))
        if ess is None:
            ess = self.essential_spending
        if life is None:
            life = self.lifestyle_spending
        return {"essential": ess, "lifestyle": life, "total": ess + life,
                "stated": bool(stated)}

    # ── savings ─────────────────────────────────────────────────────────
    @property
    def annual_savings(self) -> float:
        """Contributions actually going in per year, all accounts."""
        return _sum(self.raw.get("savings"))

    @property
    def savings_rate(self) -> float | None:
        """Savings as a share of gross income, in percent."""
        gross = self.gross_income
        if not gross:
            return None
        return 100.0 * self.annual_savings / gross

    @property
    def implied_savings(self) -> float | None:
        """Income minus spending — a cross-check on stated savings.

        When this disagrees badly with `annual_savings`, one of the two
        is wrong, and it is usually spending. The engine reports the gap
        rather than picking a winner.
        """
        gross = self.gross_income
        if not gross:
            return None
        return gross - self.total_spending

    # ── goals ───────────────────────────────────────────────────────────
    @property
    def goals(self) -> dict:
        return self.raw.get("goals") or {}

    @property
    def assumption_overrides(self) -> dict:
        return self.raw.get("assumptions") or {}

    # ── completeness ────────────────────────────────────────────────────
    def missing(self) -> list[str]:
        """The minimum inputs still needed, in priority order.

        Ordered as the spec's section 26 orders them: the engine asks for
        the smallest set that makes the next conclusion honest, rather
        than issuing generic advice into the gap.
        """
        checks = [
            ("age", self.age is not None, "Your current age"),
            ("retirement_age", self.retirement_age is not None,
             "Target retirement age"),
            ("portfolio", bool(self.raw.get("assets")),
             "Current portfolio / asset balances"),
            ("spending", self.total_spending > 0, "Annual spending"),
            ("savings", self.annual_savings > 0
             or self.raw.get("savings") is not None,
             "Annual savings / contributions"),
            ("income", self.gross_income > 0, "Household income"),
            ("debt", self.raw.get("liabilities") is not None,
             "Debt balances (enter [] if none)"),
            ("retirement_country", bool(self.raw.get("retirement_country")),
             "Where you expect to retire"),
            ("one_offs", self.raw.get("one_offs") is not None,
             "Major future one-off expenses (enter [] if none)"),
            ("retirement_spending", self.retirement_spending["stated"],
             "Desired retirement lifestyle spending"),
        ]
        return [label for _k, ok, label in checks if not ok]

    def completeness(self) -> float:
        """How much of the minimum input set is present, 0-100."""
        return round(100.0 * (10 - len(self.missing())) / 10.0, 1)


def load(raw: dict[str, Any] | None) -> Profile:
    return Profile(raw=dict(raw or {}))


BLANK: dict = {
    "people": [{"name": "", "age": None, "role": "self"}],
    "retirement_age": None,
    "current_country": "US",
    "retirement_country": "",
    "income": {},
    "spending": {"essential": {}, "lifestyle": {}},
    "retirement_spending": {},
    "savings": {},
    "assets": [],
    "liabilities": [],
    "one_offs": [],
    "goals": {},
    "assumptions": {},
}
