#!/usr/bin/env python3
"""解析部（`fetch_stats.parse_stats`）の検査。通信しない。

    python test_parse.py

`tests/fixtures/` のHTMLに対して回す。実ページ（`stats-*.html` のうち
`synthetic` でないもの）があればそちらを優先し、無ければ合成HTMLを使う。
**実ページが入ったら、期待値をそのページの数字に合わせて書き直すこと**（README参照）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from fetch_stats import METRICS, normalize_name, parse_number, parse_stats

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "tests" / "fixtures"

# 指定された8人。解析がこの8人を取りこぼしたら検査を落とす。
TARGETS = ["白鳥翔", "鈴木優", "竹内元太", "逢川恵夢",
           "伊達朱里紗", "園田賢", "仲林圭", "松本吉弘"]

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))


def pick_fixture() -> Path:
    real = sorted(p for p in FIXTURES.glob("stats-*.html") if "synthetic" not in p.name)
    if real:
        return real[-1]
    return FIXTURES / "stats-synthetic.html"


def test_normalize_name() -> None:
    check("選手名の空白を落とす（半角）", normalize_name("白鳥 翔") == "白鳥翔")
    check("選手名の空白を落とす（全角）", normalize_name("白鳥　翔") == "白鳥翔")
    check("前後の空白も落とす", normalize_name("  園田 賢 \n") == "園田賢")
    check("空でも落ちない", normalize_name("") == "" and normalize_name(None) == "")


def test_parse_number() -> None:
    check("符号つき小数", parse_number("+123.4", "float") == 123.4)
    check("負の小数", parse_number("-45.6", "float") == -45.6)
    check("全角プラス", parse_number("＋12.5", "float") == 12.5)
    check("桁区切り", parse_number("6,200", "float") == 6200.0)
    check("百分率は数値だけにする", parse_number("21.5%", "percent") == 21.5)
    check("整数", parse_number("12", "int") == 12 and isinstance(parse_number("12", "int"), int))
    check("ハイフンは None（0 ではない）", parse_number("-", "float") is None)
    check("空欄は None", parse_number("", "float") is None and parse_number("   ", "float") is None)
    check("読めない文字列は None", parse_number("未計測", "float") is None)
    # 負号の表記ゆれ。ここを取りこぼすとマイナスの選手が全員「未出場」に落ち、
    # 2組の合計がプラスの選手だけの和になる（画面上は何も壊れて見えない）。
    check("数学記号の負号（−）", parse_number("\u221245.6", "float") == -45.6)
    check("全角の負号（－）", parse_number("－45.6", "float") == -45.6)
    check("黒三角の負号（▲）", parse_number("▲45.6", "float") == -45.6)
    check("白三角の負号（△）", parse_number("△45.6", "float") == -45.6)
    check("0 は None にならない", parse_number("0", "float") == 0.0)
    # 率の書き方が出どころで違う（公式は小数、配布データは百分率）
    check("百分率はそのまま", parse_number("22.2%", "percent") == 22.2)
    check("小数の率は100倍して単位をそろえる", parse_number("0.22", "percent") == 22.0)
    check("率の 1 は 100%", parse_number("1", "percent") == 100.0)
    check("率の 0 は 0%", parse_number("0", "percent") == 0.0)
    check("0.0 は None にならない", parse_number("0.0", "float") == 0.0)


def test_parse_stats(path: Path) -> None:
    html = path.read_text(encoding="utf-8", errors="replace")
    stats = parse_stats(html, source_url="https://m-league.jp/stats/")
    teams = stats["teams"]
    players = [p for t in teams for p in t["players"]]
    by_name = {normalize_name(p["name"]): p for p in players}

    check("チームが10個取れる", len(teams) == 10, f"取れた数: {len(teams)}")
    check("選手が40人取れる", len(players) == 40, f"取れた数: {len(players)}")
    check("選手名が重複しない", len(by_name) == len(players),
          f"ユニーク {len(by_name)} / 全体 {len(players)}")

    missing = [t for t in TARGETS if t not in by_name]
    check("指定の8人が全員見つかる", not missing, f"見つからない: {missing or 'なし'}")

    check("チーム名が空でない", all(t["name"] for t in teams))
    check("選手に所属チームが入る", all(p.get("team") for p in players))

    # 縦横を取り違えていないかを見る。指標名が選手名の位置に来ていたら落ちる。
    labels = set(METRICS)
    check("選手名が指標名になっていない（縦横の取り違え）",
          not (set(by_name) & labels), f"かぶり: {sorted(set(by_name) & labels) or 'なし'}")

    # `if p.get("games")` にすると試合数0の選手が漏れる。列ずれの検知は
    # この検査が主な安全網なので、0 と「値が無い」を必ず区別する。
    # 実ページは未出場の選手も 0 で埋めてくるので、「出場した」は試合数>0 で見る
    played = [p for p in players if (p.get("games") or 0) > 0]
    check("出場している選手にポイントが入る",
          all(isinstance(p.get("points"), float) for p in played),
          f"出場者 {len(played)}人")
    check("18指標すべてがどこかの選手で取れている",
          all(any(p.get(key) is not None for p in players) for key, _ in METRICS.values()),
          f"指標数: {len(METRICS)}")

    # 着順の内訳が試合数と合うか。列がずれていれば、ここがまず合わなくなる。
    bad = [p["name"] for p in played
           if None not in (p.get("rank1"), p.get("rank2"), p.get("rank3"), p.get("rank4"))
           and p["rank1"] + p["rank2"] + p["rank3"] + p["rank4"] != p["games"]]
    check("1〜4位の合計が試合数と一致する（列ずれの検知）", not bad,
          f"合わない選手: {bad or 'なし'}")

    未出場 = [p for p in players if not p.get("games")]
    check("出場前の選手はポイントが None（0 ではない）",
          all(p.get("points") is None for p in 未出場),
          f"出場前: {len(未出場)}人")
    # 0.0 として残ると、実際に打って負けた選手より上に並んでしまう
    check("未出場が 0.0 ポイントとして残っていない",
          not any(p.get("points") == 0.0 and not p.get("games") for p in players))

    check("生の値を捨てずに残している",
          all(p.get("raw") for p in players))
    check("取得元と取得日時が入る",
          bool(stats.get("source_url")) and bool(stats.get("fetched_at")))


def test_season_label() -> None:
    """シーズンの拾い方。日付を誤って拾わないこと。"""
    from fetch_stats import _guess_season_label
    from bs4 import BeautifulSoup

    def guess(html):
        return _guess_season_label(BeautifulSoup(html, "html.parser"))

    check("本文のシーズン表記を拾う", guess("<p>2026-27 レギュラー</p>") == "2026-27")
    check("日付（2020-04）はシーズンと見なさない", guess("<p>2020-04-01 更新</p>") is None)
    check("無ければ None", guess("<p>なにもない</p>") is None)
    check("日付が先にあってもシーズンを見つける",
          guess("<p>2020-04 更新</p><p>2026-27</p>") == "2026-27")


def test_empty_header_column() -> None:
    """見出しが空の列があっても、値と選手の対応がずれないこと。

    空の見出しを詰めてしまうと、以降の選手の数字が1つずつずれる。
    件数の検査（10チーム/40人）では気づけないので、ここで直に見る。
    """
    html = """
    <section class="p-stats__team" id="x">
      <h2 class="p-stats__teamName">検査チーム</h2>
      <table class="p-stats__table">
        <tr><th></th><th>甲 一郎</th><th></th><th>乙 二郎</th></tr>
        <tr><th>試合数</th><td>10</td><td>-</td><td>20</td></tr>
        <tr><th>ポイント</th><td>+11.1</td><td>-</td><td>+22.2</td></tr>
      </table>
    </section>
    """
    stats = parse_stats(html)
    players = stats["teams"][0]["players"]
    check("空の見出し列は選手として数えない", len(players) == 2, f"取れた数: {len(players)}")
    got = {p["name"]: (p.get("games"), p.get("points")) for p in players}
    check("空列の後ろの選手の値がずれない",
          got.get("乙二郎") == (20, 22.2), f"乙二郎: {got.get('乙二郎')}")
    check("空列の前の選手の値も正しい",
          got.get("甲一郎") == (10, 11.1), f"甲一郎: {got.get('甲一郎')}")


def test_missing_section_raises() -> None:
    try:
        parse_stats("<html><body><p>成績はありません</p></body></html>")
    except LookupError:
        check("成績の節が無ければ例外で止まる", True)
    except Exception as e:  # noqa: BLE001
        check("成績の節が無ければ例外で止まる", False, f"別の例外: {type(e).__name__}")
    else:
        check("成績の節が無ければ例外で止まる", False, "例外が出ずに通ってしまった")


def main() -> int:
    fixture = pick_fixture()
    if not fixture.exists():
        print(f"検査用HTMLが無い: {fixture}", file=sys.stderr)
        print("先に python tests/make_synthetic_fixture.py を回すこと。", file=sys.stderr)
        return 2

    kind = "合成（当て木）" if "synthetic" in fixture.name else "実ページ"
    print(f"検査対象: {fixture.name}（{kind}）\n")

    test_normalize_name()
    test_parse_number()
    test_parse_stats(fixture)
    test_season_label()
    test_empty_header_column()
    test_missing_section_raises()

    passed = 0
    for ok, name, detail in _results:
        print(("合格  " if ok else "不合格") + " " + name + (f"  … {detail}" if detail else ""))
        passed += ok
    print(f"\n{len(_results)}件中 {passed}件 合格")
    if kind == "合成（当て木）":
        print("※ いまは作り物のHTMLに対する検査。実ページを入れて回し直すこと。")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
