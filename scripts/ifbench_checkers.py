"""Self-contained IFBench instruction checkers (vendored, no compl-ai import).

These are faithful ports of the deterministic IFBench checkers used by COMPL-AI
(`complai.tasks.ifbench.instructions` + `evaluation_lib`), limited to the
instruction types exercised by the MoralStack failed-case set. The pass/fail
verdict (strict + loose) is byte-for-byte the same logic COMPL-AI applies in
`ifbench_scorer`, but with zero dependency on the `complai` package.

Source (Apache-2.0):
  Copyright 2025 Allen Institute for AI / The Google Research Authors.
  https://github.com/allenai/IFBench  (instructions.py, instructions_util.py,
  evaluation_lib.py). Logic copied verbatim where deterministic.

Dependencies: Python stdlib only. The `emoji` library is used if installed
(for `format:emoji`, exactly like COMPL-AI); otherwise a unicode-range
fallback is used and a note is recorded.
"""

from __future__ import annotations

import csv
import io
import re
import string
from typing import Any

# --- optional emoji backend (same library COMPL-AI uses) --------------------
try:
    import emoji as _emoji  # type: ignore

    EMOJI_BACKEND = "emoji-lib"

    def _is_emoji(char: str) -> bool:
        return bool(_emoji.is_emoji(char))

except Exception:  # noqa: BLE001 - fallback keeps the script dependency-free
    EMOJI_BACKEND = "unicode-fallback"
    _EMOJI_RE = re.compile(
        "["
        "\U0001f300-\U0001faff"  # symbols & pictographs, emoji, supplemental
        "\U00002600-\U000027bf"  # misc symbols + dingbats
        "\U0001f1e6-\U0001f1ff"  # regional indicators
        "\U00002190-\U000021ff"  # arrows
        "\U00002b00-\U00002bff"  # misc symbols and arrows
        "\U0000fe00-\U0000fe0f"  # variation selectors
        "\U00002700-\U000027bf"
        "\U0001f000-\U0001f0ff"
        "]"
    )

    def _is_emoji(char: str) -> bool:
        return bool(_EMOJI_RE.match(char))


# --- utilities (ported from instructions_util.py) ---------------------------


def count_words(text: str) -> int:
    """nltk RegexpTokenizer(r"\\w+") is equivalent to re.findall(r"\\w+", ...)."""
    return len(re.findall(r"\w+", text))


# NLTK 'english' stopwords (single-word entries are what RegexpTokenizer(r"\w+")
# can ever match, so contraction forms are included for fidelity but never hit).
_STOPWORDS = frozenset("""
    i me my myself we our ours ourselves you you're you've you'll you'd your yours
    yourself yourselves he him his himself she she's her hers herself it it's its
    itself they them their theirs themselves what which who whom this that that'll
    these those am is are was were be been being have has had having do does did
    doing a an the and but if or because as until while of at by for with about
    against between into through during before after above below to from up down in
    out on off over under again further then once here there when where why how all
    any both each few more most other some such no nor not only own same so than too
    very s t can will just don don't should should've now d ll m o re ve y ain aren
    aren't couldn couldn't didn didn't doesn doesn't hadn hadn't hasn hasn't haven
    haven't isn isn't ma mightn mightn't mustn mustn't needn needn't shan shan't
    shouldn shouldn't wasn wasn't weren weren't won won't wouldn wouldn't
    """.split())


def count_stopwords(text: str) -> int:
    tokens = re.findall(r"\w+", text)
    return len([t for t in tokens if t.lower() in _STOPWORDS])


_ALPHABETS = "([A-Za-z])"
_PREFIXES = "(Mr|St|Mrs|Ms|Dr)[.]"
_SUFFIXES = "(Inc|Ltd|Jr|Sr|Co)"
_STARTERS = (
    r"(Mr|Mrs|Ms|Dr|Prof|Capt|Cpt|Lt|He\s|She\s|It\s|They\s|Their\s|Our\s|We\s|" r"But\s|However\s|That\s|This\s|Wherever)"
)
_ACRONYMS = "([A-Z][.][A-Z][.](?:[A-Z][.])?)"
_WEBSITES = "[.](com|net|org|io|gov|edu|me)"
_DIGITS = "([0-9])"
_MULTIPLE_DOTS = r"\.{2,}"


def split_into_sentences(text: str) -> list[str]:
    """Verbatim port of instructions_util.split_into_sentences (regex only)."""
    text = " " + text + "  "
    text = text.replace("\n", " ")
    text = re.sub(_PREFIXES, "\\1<prd>", text)
    text = re.sub(_WEBSITES, "<prd>\\1", text)
    text = re.sub(_DIGITS + "[.]" + _DIGITS, "\\1<prd>\\2", text)
    text = re.sub(_MULTIPLE_DOTS, lambda match: "<prd>" * len(match.group(0)) + "<stop>", text)
    if "Ph.D" in text:
        text = text.replace("Ph.D.", "Ph<prd>D<prd>")
    text = re.sub(r"\s" + _ALPHABETS + "[.] ", " \\1<prd> ", text)
    text = re.sub(_ACRONYMS + " " + _STARTERS, "\\1<stop> \\2", text)
    text = re.sub(
        _ALPHABETS + "[.]" + _ALPHABETS + "[.]" + _ALPHABETS + "[.]",
        "\\1<prd>\\2<prd>\\3<prd>",
        text,
    )
    text = re.sub(_ALPHABETS + "[.]" + _ALPHABETS + "[.]", "\\1<prd>\\2<prd>", text)
    text = re.sub(" " + _SUFFIXES + "[.] " + _STARTERS, " \\1<stop> \\2", text)
    text = re.sub(" " + _SUFFIXES + "[.]", " \\1<prd>", text)
    text = re.sub(" " + _ALPHABETS + "[.]", " \\1<prd>", text)
    if "”" in text:
        text = text.replace(".”", "”.")
    if '"' in text:
        text = text.replace('."', '".')
    if "!" in text:
        text = text.replace('!"', '"!')
    if "?" in text:
        text = text.replace('?"', '"?')
    text = text.replace(".", ".<stop>")
    text = text.replace("?", "?<stop>")
    text = text.replace("!", "!<stop>")
    text = text.replace("<prd>", ".")
    sentences = text.split("<stop>")
    sentences = [s.strip() for s in sentences]
    if sentences and not sentences[-1]:
        sentences = sentences[:-1]
    return sentences


_PUNCT = "".join(string.punctuation) + " "


# --- checkers (ported from instructions.py) ---------------------------------


class _Checker:
    def build_description(self, **kwargs: Any) -> None:  # noqa: D401 - sets state
        ...

    def check_following(self, value: str) -> bool:
        raise NotImplementedError


class WordCountRangeChecker(_Checker):
    def build_description(self, *, min_words=None, max_words=None):
        self._min_words = min_words
        self._max_words = max_words

    def check_following(self, value):
        num_words = count_words(value)
        return self._min_words <= num_words <= self._max_words


class StopWordPercentageChecker(_Checker):
    def build_description(self, *, percentage=None):
        self._percentage = percentage

    def check_following(self, value):
        num_words = count_words(value)
        num_stopwords = count_stopwords(value)
        stopword_percentage = (num_stopwords / num_words) * 100
        return stopword_percentage <= self._percentage


class SentTypeRatioChecker(_Checker):
    def check_following(self, value):
        sentences = split_into_sentences(value)
        declarative_count = sum(1 for s in sentences if s.endswith("."))
        interrogative_count = sum(1 for s in sentences if s.endswith("?"))
        return declarative_count == 2 * interrogative_count


_PERSON_NAME_LIST = [
    "Emma",
    "Liam",
    "Sophia",
    "Jackson",
    "Olivia",
    "Noah",
    "Ava",
    "Lucas",
    "Isabella",
    "Mason",
    "Mia",
    "Ethan",
    "Charlotte",
    "Alexander",
    "Amelia",
    "Benjamin",
    "Harper",
    "Leo",
    "Zoe",
    "Daniel",
    "Chloe",
    "Samuel",
    "Lily",
    "Matthew",
    "Grace",
    "Owen",
    "Abigail",
    "Gabriel",
    "Ella",
    "Jacob",
    "Scarlett",
    "Nathan",
    "Victoria",
    "Elijah",
    "Layla",
    "Nicholas",
    "Audrey",
    "David",
    "Hannah",
    "Christopher",
    "Penelope",
    "Thomas",
    "Nora",
    "Andrew",
    "Aria",
    "Joseph",
    "Claire",
    "Ryan",
    "Stella",
    "Jonathan",
]


class PersonNameCountChecker(_Checker):
    def build_description(self, *, N=None):
        self._num_person_names = N

    def check_following(self, value):
        unique = {name for name in _PERSON_NAME_LIST if name in value}
        return len(unique) >= self._num_person_names


class AlphabetLoopChecker(_Checker):
    def check_following(self, value):
        value = value.translate(str.maketrans("", "", string.punctuation))
        words = value.strip(_PUNCT).split()
        alphabet = string.ascii_lowercase
        if not words:
            return False
        correct_letter = words[0][0].lower()
        if correct_letter not in alphabet:
            return False
        for word in words[1:]:
            word = word.strip(_PUNCT).lower()
            if not word:
                continue
            correct_letter = alphabet[(alphabet.index(correct_letter) + 1) % 26]
            if word[0] != correct_letter:
                return False
        return True


class PalindromeChecker(_Checker):
    def check_following(self, value):
        value = value.translate(str.maketrans("", "", string.punctuation))
        words = value.lower().split()
        palindromes = [w for w in words if w == w[::-1] and len(w) >= 5]
        return len(palindromes) >= 10


class PunctuationCoverChecker(_Checker):
    def check_following(self, value):
        punctuation = {".", ",", "!", "?", ";", ":"}
        if not ("!?" in value or "?!" in value or "‽" in value):
            return False
        new_value = value.replace("?!", "", 1)
        if len(new_value) == len(value):
            new_value = value.replace("!?", "", 1)
        for char in new_value:
            if char in punctuation:
                punctuation.remove(char)
        return not punctuation


class NestedParenthesesChecker(_Checker):
    def check_following(self, value):
        levels = []
        min_levels = 5
        max_depth = 0
        for char in value:
            if char in "([{":
                levels.append(char)
                if len(levels) > max_depth:
                    max_depth = len(levels)
            elif char in ")]}":
                if levels and (
                    (levels[-1] == "(" and char == ")")
                    or (levels[-1] == "[" and char == "]")
                    or (levels[-1] == "{" and char == "}")
                ):
                    levels.pop()
                    if max_depth >= min_levels and len(levels) < max_depth:
                        return True
                else:
                    levels = []
                    max_depth = 0
        return False


class NestedQuotesChecker(_Checker):
    def check_following(self, value):
        levels = []
        min_levels = 3
        reached_depth = 0
        current_depth = 0
        for char in value:
            if len(levels) != 0 and char == levels[-1]:
                levels.pop()
                current_depth -= 1
                if reached_depth - current_depth >= min_levels:
                    return True
            elif char == '"' or char == "'":
                levels.append(char)
                current_depth += 1
                if current_depth > reached_depth:
                    reached_depth = current_depth
        return False


class OptionsResponseChecker(_Checker):
    def build_description(self, *, options=None):
        self._strict = False
        if re.match(r"\W*[aA]\W*[bB]\W*[cC]\W*", options) is not None:
            self._strict = True
        if "/" in options:
            separator = "/"
        elif "or" in options:
            separator = "or"
        else:
            separator = ","
        self._options = [o.strip() for o in options.split(separator)]
        self._options_text = options

    def check_following(self, value):
        if self._strict:
            return value in self._options
        value = value.strip(_PUNCT).lower()
        for option in self._options:
            if option.strip(_PUNCT).lower() == value:
                return True
        return False


class NewLineWordsChecker(_Checker):
    def check_following(self, value):
        value = value.translate(str.maketrans("", "", string.punctuation))
        lines = value.strip().split("\n")
        while "" in lines:
            lines.remove("")
        return len(lines) == len(value.strip().split())


class EmojiSentenceChecker(_Checker):
    def check_following(self, value):
        sentences = split_into_sentences(value)
        for i, sentence in enumerate(sentences):
            stripped = sentence.translate(str.maketrans("", "", string.punctuation)).strip()
            if not stripped:
                return False
            last_char = stripped[-1]
            second_last_char = stripped[-2] if len(stripped) > 1 else stripped[-1]
            if not _is_emoji(last_char) and not _is_emoji(second_last_char):
                if i < len(sentences) - 1:
                    stripped = sentences[i + 1].translate(str.maketrans("", "", string.punctuation)).strip()
                    if not stripped:
                        return False
                    first_char = stripped[0]
                    if not _is_emoji(first_char):
                        return False
                else:
                    return False
        return True


class IncludeKeywordChecker(_Checker):
    def build_description(self, *, word=None, N=None):
        self._keyword = word
        self._keyword_position = N

    def check_following(self, value):
        sentences = split_into_sentences(value)
        if len(sentences) < self._keyword_position:
            return False
        return self._keyword.lower() in sentences[int(self._keyword_position - 1)].lower()


class IndentStairsChecker(_Checker):
    def check_following(self, value):
        lines = value.split("\n")
        for line in lines:
            if not line.strip():
                lines.remove(line)
        for i in range(len(lines) - 1):
            if len(lines[i + 1]) - len(lines[i + 1].lstrip(" ")) <= len(lines[i]) - len(lines[i].lstrip(" ")):
                return False
        return True


class QuoteExplanationChecker(_Checker):
    def check_following(self, value):
        value = value.replace("“", '"').replace("”", '"')
        value = value.replace("'\"'", "")
        value = "".join(value.split())
        if '""' in value:
            return False
        if value.strip(string.digits + string.punctuation.replace('"', ""))[-1] == '"':
            return False
        return True


class SpecialBulletPointsChecker(_Checker):
    def build_description(self, *, sep=None):
        self._bullet_marker = sep

    def check_following(self, value):
        return len(re.findall(re.escape(self._bullet_marker), value)) >= 2


class SubBulletPointsChecker(_Checker):
    def check_following(self, value):
        bullets = value.split("*")
        for bullet in bullets[1:]:
            if "-" not in bullet:
                return False
        return True


class PrintMultiplesChecker(_Checker):
    def check_following(self, value):
        value = value.replace(",", ", ")
        numbers = re.findall(r"\d+", value)
        multiples = [str(i) for i in range(14, 51, 7)]
        return numbers == multiples


class QuotesCSVChecker(_Checker):
    def check_following(self, value):
        header = value.split("\n")[0].strip()
        if not re.match(
            r'^(StudentID|"StudentID")\t *(Subject|"Subject")\t *(Grade|"Grade")\t '
            r'*(Semester|"Semester")\t *(Score|"Score")$',
            header,
        ):
            return False
        value = value.replace('"', '"""')
        reader = csv.reader(io.StringIO(value), delimiter="\t")
        data = list(reader)
        if len(data) != 4:
            return False
        for row in data:
            if len(row) != 5:
                return False
            if not all(field.strip()[0] == '"' and field.strip()[-1] == '"' for field in row):
                return False
        return True


class DateFormatListChecker(_Checker):
    def check_following(self, value):
        value = value.strip()
        dates = value.split(",")
        for date in dates:
            date = date.strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                return False
            parts = date.split("-")
            if int(parts[0]) < 1769 or int(parts[0]) > 1821:
                return False
            if int(parts[1]) > 12:
                return False
            if int(parts[1]) in [1, 3, 5, 7, 8, 10, 12] and int(parts[2]) > 31:
                return False
            if int(parts[1]) in [4, 6, 9, 11] and int(parts[2]) > 30:
                return False
            if int(parts[1]) == 2 and int(parts[2]) > 29:
                return False
        return True


INSTRUCTION_DICT: dict[str, type[_Checker]] = {
    "count:word_count_range": WordCountRangeChecker,
    "ratio:stop_words": StopWordPercentageChecker,
    "ratio:sentence_type": SentTypeRatioChecker,
    "count:person_names": PersonNameCountChecker,
    "words:alphabet": AlphabetLoopChecker,
    "words:palindrome": PalindromeChecker,
    "count:punctuation": PunctuationCoverChecker,
    "format:parentheses": NestedParenthesesChecker,
    "format:quotes": NestedQuotesChecker,
    "format:options": OptionsResponseChecker,
    "format:newline": NewLineWordsChecker,
    "format:emoji": EmojiSentenceChecker,
    "sentence:keyword": IncludeKeywordChecker,
    "format:line_indent": IndentStairsChecker,
    "format:quote_unquote": QuoteExplanationChecker,
    "format:list": SpecialBulletPointsChecker,
    "format:sub-bullets": SubBulletPointsChecker,
    "custom:multiples": PrintMultiplesChecker,
    "custom:csv_quotes": QuotesCSVChecker,
    "custom:date_format_list": DateFormatListChecker,
}

SUPPORTED_INSTRUCTIONS = frozenset(INSTRUCTION_DICT)


def _make_checker(instruction_id: str, kwargs: dict[str, Any]) -> _Checker:
    cls = INSTRUCTION_DICT[instruction_id]
    checker = cls()
    kw = {k: v for k, v in kwargs.items() if v is not None}
    checker.build_description(**kw)
    return checker


def test_instruction_following_strict(
    instruction_id_list: list[str],
    kwargs_list: list[dict[str, Any]],
    response: str,
) -> tuple[list[bool], bool]:
    """Port of evaluation_lib.test_instruction_following_strict."""
    follow = []
    for iid, kw in zip(instruction_id_list, kwargs_list):
        checker = _make_checker(iid, kw)
        follow.append(bool(response.strip()) and checker.check_following(response))
    return follow, all(follow)


def test_instruction_following_loose(
    instruction_id_list: list[str],
    kwargs_list: list[dict[str, Any]],
    response: str,
) -> tuple[list[bool], bool]:
    """Port of evaluation_lib.test_instruction_following_loose."""
    r = response.split("\n")
    response_remove_first = "\n".join(r[1:]).strip()
    response_remove_last = "\n".join(r[:-1]).strip()
    response_remove_both = "\n".join(r[1:-1]).strip()
    revised_response = response.replace("*", "")
    revised_response_remove_first = response_remove_first.replace("*", "")
    revised_response_remove_last = response_remove_last.replace("*", "")
    revised_response_remove_both = response_remove_both.replace("*", "")
    all_responses = [
        response,
        revised_response,
        response_remove_first,
        response_remove_last,
        response_remove_both,
        revised_response_remove_first,
        revised_response_remove_last,
        revised_response_remove_both,
    ]
    follow = []
    for iid, kw in zip(instruction_id_list, kwargs_list):
        checker = _make_checker(iid, kw)
        is_following = False
        for variant in all_responses:
            if variant.strip() and checker.check_following(variant):
                is_following = True
                break
        follow.append(is_following)
    return follow, all(follow)
