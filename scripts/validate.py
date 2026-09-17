#!/usr/bin/env python3
"""取ってきたデータが壊れていないかを見る。どの経路で取っても同じ検算を通す。

いちばん効くのが**ゼロサムの検算**。麻雀は1試合の合計が必ず0になる
（各自の点 = (素点 - 30000)/1000 + 順位点、順位点の和 +50+10-10-30 = +20、
素点の和は 100000 なので (100000-120000)/1000 = -20。足して0）。
だからリーグ全員のポイントを足すと0になる。実データで確かめてある:

    2026-27 レギュラー     選手  8人 のべ   8試合  +0.0
    2025-26 レギュラー     選手 40人 のべ1200試合  -0.0
    2025-26 セミファイナル 選手 24人 のべ 120試合  -0.0
    2025-26 ファイナル     選手 16人 のべ  64試合  -0.0

**数字を1つ書き写し間違えれば、ここがずれる。** Gemini経由のように数字が
大規模言語モデルを通る経路では、これが唯一の自動的な安全網になる。
"""

from __future__ import annotations

# ポイントは0.1刻みなので、足し算の誤差ぶんだけ見逃す
ZERO_SUM_TOLERANCE = 0.05


# 試合数0のときに「無い」ことにする項目。着順の内訳（rank1〜4）は残す
# （0のままでよく、着順の合計＝試合数 の検算がそれで通る）。
_UNPLAYED_BLANKS = (
    "points", "avg_rank", "hands", "best_score", "avg_win_points", "avg_deal_in_points",
    "top_rate", "top2_rate", "last_avoid_rate", "call_rate", "riichi_rate",
    "win_rate", "deal_in_rate",
)


def clear_unplayed(stats: dict) -> dict:
    """まだ1試合も出ていない選手の成績を、0ではなく「無い」ことにする。

    **公式ページは未出場の選手も 0 で埋めてくる。** そのまま扱うと
    「0.0ポイントの選手」になり、実際に打って負けている選手（例 -48.1）より
    上に並ぶ。ランキングの意味が壊れるので、ここで落としておく。
    配布データ側は未出場の選手をそもそも載せてこないので、実質この経路のための処置。
    """
    for t in stats.get("teams", []):
        for p in t.get("players", []):
            if p.get("games") == 0:
                for key in _UNPLAYED_BLANKS:
                    if key in p:
                        p[key] = None
    return stats


def collect_players(stats: dict) -> list[dict]:
    return [p for t in stats.get("teams", []) for p in t.get("players", [])]


def check(stats: dict, *, expect_teams: int | None = None,
          expect_players: int | None = None) -> tuple[list[str], list[str]]:
    """(止めるべき問題, 気に留めること) を返す。"""
    problems: list[str] = []
    notes: list[str] = []

    teams = stats.get("teams", [])
    players = collect_players(stats)

    if not players:
        problems.append("選手が1人も取れていない")
        return problems, notes

    if expect_teams and len(teams) != expect_teams:
        problems.append(f"チーム数が {len(teams)}（{expect_teams} のはず）")
    if expect_players and len(players) != expect_players:
        problems.append(f"選手数が {len(players)}（{expect_players} のはず）")

    names = [p.get("name") for p in players]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        problems.append("同じ選手が二度出ている: " + "・".join(dups))

    # 着順の内訳と試合数が合うか。列を取り違えていれば、まずここが合わなくなる。
    bad_rank = []
    for p in players:
        ranks = [p.get("rank1"), p.get("rank2"), p.get("rank3"), p.get("rank4")]
        if None in ranks or p.get("games") is None:
            continue
        if sum(ranks) != p["games"]:
            bad_rank.append(f"{p['name']}（{sum(ranks)}≠{p['games']}）")
    if bad_rank:
        problems.append("1〜4位の合計が試合数と合わない: " + "・".join(bad_rank))

    # ゼロサムの検算
    played = [p for p in players if p.get("points") is not None]
    if not played:
        # 全員の点が空なら合計も0になり、検算を「通って」しまう。
        # 名前だけ拾えて数字を取りこぼした場合がこれなので、通さない。
        problems.append("ポイントが入っている選手が1人もいない（数字を取りこぼした疑い）")
        return problems, notes
    total = round(sum(p["points"] for p in played), 6)
    if abs(total) <= ZERO_SUM_TOLERANCE:
        notes.append(f"ゼロサム検算OK（全{len(played)}人の合計 {total:+.1f}）")
    else:
        problems.append(
            f"全選手のポイント合計が {total:+.1f}（0 になるはず）。"
            "数字の取りこぼしか写し間違いの疑いが強い"
        )

    # 試合数の辻褄。1試合に4人なので、のべ出場数は4の倍数になる。
    total_games = sum(p["games"] for p in players if p.get("games") is not None)
    if total_games % 4 != 0:
        notes.append(f"のべ出場数 {total_games} が4の倍数でない（選手の取りこぼしかもしれない）")

    return problems, notes


def report(problems: list[str], notes: list[str], *, source: str) -> bool:
    """検算の結果を出す。通ったかどうかを返す。"""
    for n in notes:
        print(f"      {n}")
    if problems:
        print(f"[!] 取れたデータがおかしい（経路: {source}）")
        for p in problems:
            print(f"    - {p}")
        return False
    return True
