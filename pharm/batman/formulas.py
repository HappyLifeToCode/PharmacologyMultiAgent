"""内置经典方剂组成表。

组成按标准教材通用口径（《金匮要略》《医学心悟》等），source 统一标注
standard_reference_pending_user_confirmation，待用户确认。canonical 名到
BATMAN 检索候选名的映射（如 芍药→白芍、甘草→甘草/炙甘草）尚未经真实
BATMAN 目录核验，查询时按候选顺序取第一个命中项，全部未命中记入
unmatched，不报错也不编造匹配。
"""
from __future__ import annotations

SOURCE = "standard_reference_pending_user_confirmation"

# (canonical 名, BATMAN 检索候选名列表)；无把握时候选即 canonical 本身
FORMULAS = {
    "温经汤": [
        ("吴茱萸", ["吴茱萸"]), ("当归", ["当归"]), ("芍药", ["白芍"]),
        ("川芎", ["川芎"]), ("人参", ["人参"]), ("桂枝", ["桂枝"]),
        ("阿胶", ["阿胶"]), ("牡丹皮", ["牡丹皮"]), ("生姜", ["生姜"]),
        ("甘草", ["甘草", "炙甘草"]), ("半夏", ["半夏"]), ("麦冬", ["麦冬"]),
    ],
    "半夏白术天麻汤": [
        ("半夏", ["半夏"]), ("天麻", ["天麻"]), ("茯苓", ["茯苓"]),
        ("橘红", ["橘红", "陈皮"]), ("白术", ["白术"]),
        ("甘草", ["甘草", "炙甘草"]), ("生姜", ["生姜"]), ("大枣", ["大枣"]),
    ],
    "济川煎": [
        ("当归", ["当归"]), ("牛膝", ["牛膝"]), ("肉苁蓉", ["肉苁蓉"]),
        ("泽泻", ["泽泻"]), ("升麻", ["升麻"]), ("枳壳", ["枳壳"]),
    ],
    "桃核承气汤": [
        ("桃仁", ["桃仁"]), ("大黄", ["大黄"]), ("桂枝", ["桂枝"]),
        ("甘草", ["甘草", "炙甘草"]), ("芒硝", ["芒硝"]),
    ],
}


def list_formulas():
    """内置方剂名称列表（按表内顺序）。"""
    return list(FORMULAS)


def formula_herbs(name):
    """方剂的 canonical 药材清单；未知方剂抛 ValueError。"""
    if name not in FORMULAS:
        raise ValueError("未收录的内置方剂：" + str(name))
    return [canonical for canonical, _ in FORMULAS[name]]


def formula_candidates(name):
    """方剂的 (canonical, 候选名列表) 对；未知方剂抛 ValueError。"""
    if name not in FORMULAS:
        raise ValueError("未收录的内置方剂：" + str(name))
    return [(canonical, list(candidates)) for canonical, candidates in FORMULAS[name]]


def resolve_batman_names(herb):
    """canonical 药材名 -> BATMAN 检索候选名列表；表外药材候选即其本身。"""
    for entries in FORMULAS.values():
        for canonical, candidates in entries:
            if canonical == herb:
                return list(candidates)
    return [herb]
