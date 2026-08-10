"""tests/test_cli_feature_gp.py — C.1 特征工程中台 CLI 测试。

覆盖:
1. fts feature list 子命令（全部/分类/json）
2. fts gp evolve 子命令解析
3. fts feature analyze 子命令解析
"""

from fts.cli import build_parser


def test_feature_list_parser():
    parser = build_parser()
    args = parser.parse_args(["feature", "list"])
    assert args.subcommand == "list"
    assert callable(args.func)
    assert args.category is None


def test_feature_list_with_category_and_json():
    parser = build_parser()
    args = parser.parse_args(["feature", "list", "--category", "price", "--json"])
    assert args.category == "price"
    assert args.json is True


def test_gp_evolve_parser_defaults():
    parser = build_parser()
    args = parser.parse_args(["gp", "evolve"])
    assert args.subcommand == "evolve"
    assert callable(args.func)
    assert args.universe == "futures"
    assert args.population == 200
    assert args.generations == 50
    assert args.forward == 20


def test_gp_evolve_parser_full():
    parser = build_parser()
    args = parser.parse_args(
        [
            "gp",
            "evolve",
            "--universe",
            "csi300",
            "--population",
            "50",
            "--generations",
            "10",
            "--max-stocks",
            "10",
            "--output",
            "tmp/out",
        ]
    )
    assert args.universe == "csi300"
    assert args.population == 50
    assert args.generations == 10
    assert args.max_stocks == 10
    assert args.output == "tmp/out"


def test_feature_analyze_parser():
    parser = build_parser()
    args = parser.parse_args(["feature", "analyze", "--factor-id", "fut_abc123"])
    assert args.subcommand == "analyze"
    assert args.factor_id == "fut_abc123"
    assert callable(args.func)
