"""Find numbers and dates in text, so the validators can check that every LLM value is literally
stated in its source text (PLAN.md §7). Handles 150,000 / 1,50,000 (lakh grouping) / 150k /
1.5 lakh / 2 crore / Bangla digits / 5% and day-first dates (Bangladesh convention).
"""
from __future__ import annotations

import re
from datetime import date

_BANGLA = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_MULT = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
         "crore": 1e7, "crores": 1e7, "million": 1e6, "mn": 1e6,
         # Banglish and Bangla: "9 hajar", "৯ হাজার", "1 lakh", "১ লাখ"
         "hajar": 1e3, "hazar": 1e3, "হাজার": 1e3, "লাখ": 1e5, "লক্ষ": 1e5, "কোটি": 1e7}
_NUM = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(k|thousand|lakhs?|lacs?|crores?|million|mn|hajar|hazar)\b|\s*(হাজার|লাখ|লক্ষ|কোটি))?", re.IGNORECASE)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def normalise(text: str) -> str:
    """Bangla digits -> ASCII, collapse whitespace, lower-case. Used for 'is this span in the email'."""
    return re.sub(r"\s+", " ", text.translate(_BANGLA)).strip().lower()


def contains(haystack: str, needle: str) -> bool:
    return bool(needle) and normalise(needle) in normalise(haystack)


_WORD_NUMS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_WORD_RE = re.compile(r"\b(" + "|".join(_WORD_NUMS) + r")\b", re.IGNORECASE)


def numbers_in(text: str) -> list[float]:
    out = [float(_WORD_NUMS[m.group(1).lower()]) for m in _WORD_RE.finditer(text)]
    for m in _NUM.finditer(text.translate(_BANGLA)):
        n = float(m.group(1).replace(",", ""))
        mult = m.group(2) or m.group(3)
        if mult:
            n *= _MULT[mult.lower()]
        out.append(n)
    return out


def states_number(text: str, value: float, tol: float = 1e-9) -> bool:
    return any(abs(n - value) <= tol * max(1.0, abs(value)) for n in numbers_in(text))


def dates_in(text: str) -> list[date]:
    t = text.translate(_BANGLA).lower()
    out: list[date] = []

    def add(y, m, d):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass

    for y, m, d in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        add(y, m, d)
    for d, m, y in re.findall(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", t):  # day-first
        add(y, m, d)
    mon = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    for d, m, y in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{mon}\.?,?\s+(\d{{4}})\b", t):
        add(y, _MONTHS[m[:4] if m.startswith("sept") else m[:3]], d)
    for m, d, y in re.findall(rf"\b{mon}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", t):
        add(y, _MONTHS[m[:4] if m.startswith("sept") else m[:3]], d)
    return out


def states_date(text: str, value: date) -> bool:
    return value in dates_in(text)


_DIGITS = re.compile(r"[0-9০-৯]")
# Spelled-out numbers count as numbers too. "one" is left out: it is mostly a pronoun in English.
_NUMBER_WORDS = re.compile(
    r"\b(zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|billion|lakh|lakhs|crore|crores|dozen|percent|per cent|half|"
    r"double|triple|twice|thrice)\b", re.IGNORECASE)


def literal_numbers(text: str) -> list[str]:
    """Digits or number words in text: forbidden in memo prose outside placeholders."""
    return _DIGITS.findall(text) + [m.group(0) for m in _NUMBER_WORDS.finditer(text)]


# Number words in Banglish and Bangla, for messages written in those languages. Words that are also common
# English or Bangla words ("at", "sat", "tin", "char", "noy", "নয়" = "not") are left out on purpose.
_BN_NUMBER_WORDS = re.compile(
    r"\b(ek|dui|panch|choy|dosh|bish|tirish|chollish|ponchash|shoto|sho|hajar|hazar|lakh|lac|koti)\b|"
    r"(এক|দুই|তিন|চার|পাঁচ|ছয়|সাত|আট|দশ|বিশ|শত|শো|হাজার|লাখ|লক্ষ|কোটি|অর্ধেক|দ্বিগুণ)", re.IGNORECASE)


def bn_number_words(text: str) -> list[str]:
    """Banglish and Bangla number words (forbidden in AI-written messages, next to literal_numbers)."""
    return [m.group(0) for m in _BN_NUMBER_WORDS.finditer(text)]
