import pytest

from pharm.batman import formulas


def test_four_builtin_formulas_composition():
    assert formulas.list_formulas() == ["温经汤", "半夏白术天麻汤", "济川煎", "桃核承气汤"]
    assert formulas.formula_herbs("温经汤") == ["吴茱萸", "当归", "芍药", "川芎", "人参", "桂枝",
                                              "阿胶", "牡丹皮", "生姜", "甘草", "半夏", "麦冬"]
    assert formulas.formula_herbs("半夏白术天麻汤") == ["半夏", "天麻", "茯苓", "橘红",
                                                    "白术", "甘草", "生姜", "大枣"]
    assert formulas.formula_herbs("济川煎") == ["当归", "牛膝", "肉苁蓉", "泽泻", "升麻", "枳壳"]
    assert formulas.formula_herbs("桃核承气汤") == ["桃仁", "大黄", "桂枝", "甘草", "芒硝"]
    assert formulas.SOURCE == "standard_reference_pending_user_confirmation"


def test_resolve_batman_names_candidates():
    assert formulas.resolve_batman_names("芍药") == ["白芍"]
    assert formulas.resolve_batman_names("甘草") == ["甘草", "炙甘草"]
    assert formulas.resolve_batman_names("橘红") == ["橘红", "陈皮"]
    assert formulas.resolve_batman_names("生姜") == ["生姜"]
    # 表外药材候选即其本身，是否命中留给 BATMAN 目录如实回答
    assert formulas.resolve_batman_names("自由药材") == ["自由药材"]


def test_unknown_formula_rejected():
    with pytest.raises(ValueError, match="未收录"):
        formulas.formula_herbs("四物汤")
    with pytest.raises(ValueError, match="未收录"):
        formulas.formula_candidates("四物汤")
