"""对 AI 草稿进行保守的题干查重，保留条件有变化的变式题。"""
from difflib import SequenceMatcher
import re
from unicodedata import normalize


_FORMATTING = re.compile(r'[\s,，、。？?!！；;：:“”"《》]+')
_CONDITIONS = re.compile(
    r'\d+(?:\.\d+)?|[+−\-=≠<>≤≥×÷±*/^√]|不正确|不成立|不属于|不包括|不等于|不是|不能|无需|错误|至少|至多|最多|最少|仅|'
    r'[零一二两三四五六七八九十百千万]+(?=阶|维|次|项|位|步|个)'
)
_SUPER_OR_SUBSCRIPT = re.compile(r'[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]')
_HAN = re.compile(r'[\u3400-\u9fff]')
_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]*')


def normalized_title(title):
    """折叠大小写、全半角、空白及句末标点。"""
    return ' '.join(normalize('NFKC', title).casefold().split()).rstrip('?!。！？. ')


def title_profile(title):
    """保留数字、公式运算符和否定等条件，用字符片段过滤高相似题干。"""
    text = normalized_title(title)
    conditions = tuple(_CONDITIONS.findall(text.replace('−', '-'))) + tuple(_SUPER_OR_SUBSCRIPT.findall(title))
    if _HAN.search(text):
        conditions += tuple(_IDENTIFIER.findall(text))
    compact = _FORMATTING.sub('', text)
    bigrams = {compact[i:i + 2] for i in range(len(compact) - 1)}
    return compact, conditions, bigrams


class QuestionTitleIndex:
    def __init__(self, titles=()):
        """缓存现有题干的比较特征，避免逐题重复分析。"""
        self.profiles = [title_profile(title) for title in titles]

    def add(self, title):
        """让本批后续题目也和刚通过的题干比较。"""
        self.profiles.append(title_profile(title))

    def contains(self, title):
        """匹配同条件下的相同或高度相似题干。"""
        text, conditions, bigrams = title_profile(title)
        for previous, previous_conditions, previous_bigrams in self.profiles:
            if conditions != previous_conditions:
                continue
            if text == previous:
                return True
            shortest, longest = sorted((len(text), len(previous)))
            if shortest < 12 or shortest / longest < 0.72:
                continue
            overlap = 2 * len(bigrams & previous_bigrams) / (len(bigrams) + len(previous_bigrams))
            if overlap >= 0.82 or (overlap >= 0.64 and SequenceMatcher(None, text, previous).ratio() >= 0.85):
                return True
        return False
