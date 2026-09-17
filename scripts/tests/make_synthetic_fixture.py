#!/usr/bin/env python3
"""解析の検査に使う「公式ページに似せたHTML」を作る。

    python tests/make_synthetic_fixture.py

**中の数字はすべて作り物。** 公式から取ったものではない。
実ページが手に入るまでの当て木で、手に入ったらそちらを正とする（README参照）。

似せてあるのは作りだけ:
  - `section.p-stats__team` > `h2.p-stats__teamName` + `table.p-stats__table`
  - 表が縦横逆（選手が列・指標が行）
  - 選手名に空白が入る（`白鳥 翔`）
  - 試合に出ていない選手のセルが `-`
  - **1試合の合計が0になる**（麻雀がゼロサムなので、本物もそうなる）
  - 率に `%`、打点に桁区切りの `,` が付く
"""
from __future__ import annotations

import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures" / "stats-synthetic.html"

# チーム名は公式の表記。所属は**仮**で、`ダミーNN` は実在しない。
TEAMS = [
    ("abemas", "渋谷ABEMAS", ["白鳥 翔", "松本 吉弘", "ダミー 01", "ダミー 02"]),
    ("pirates", "U-NEXT Pirates", ["鈴木 優", "仲林 圭", "ダミー 03", "ダミー 04"]),
    ("phoenix", "セガサミーフェニックス", ["竹内 元太", "ダミー 05", "ダミー 06", "ダミー 07"]),
    ("jets", "EARTH JETS", ["逢川 恵夢", "ダミー 08", "ダミー 09", "ダミー 10"]),
    ("drivens", "赤坂ドリブンズ", ["園田 賢", "ダミー 11", "ダミー 12", "ダミー 13"]),
    ("konami", "KONAMI麻雀格闘倶楽部", ["伊達 朱里紗", "ダミー 14", "ダミー 15", "ダミー 16"]),
    ("raiden", "TEAM RAIDEN / 雷電", ["ダミー 17", "ダミー 18", "ダミー 19", "ダミー 20"]),
    ("beast", "BEAST X", ["ダミー 21", "ダミー 22", "ダミー 23", "ダミー 24"]),
    ("fuurinkazan", "EX風林火山", ["ダミー 25", "ダミー 26", "ダミー 27", "ダミー 28"]),
    ("sakura", "KADOKAWAサクラナイツ", ["ダミー 29", "ダミー 30", "ダミー 31", "ダミー 32"]),
]

ROWS = ["試合数", "総局数", "ポイント", "平着", "1位", "2位", "3位", "4位",
        "トップ率", "連対率", "ラス回避率", "ベストスコア", "平均打点",
        "副露率", "リーチ率", "アガリ率", "放銃率", "放銃平均打点"]


def simulate(rng: random.Random, players: list[str], n_games: int) -> dict[str, dict]:
    """試合を回して、選手ごとの積み上げを作る。

    **1試合の合計は必ず0**（麻雀がゼロサムだから）。作り物であっても、そこを
    守っていないと `validate.py` のゼロサム検算に落ちる。落ちるべきなのは
    「壊れたデータ」だけなので、見本のほうを本物に近づける。
    """
    acc = {p: {"games": 0, "points": 0.0, "hands": 0,
               "ranks": [0, 0, 0, 0], "best": None} for p in players}
    for _ in range(n_games):
        four = rng.sample(players, 4)
        # 合計0になる4つの点差を作る
        xs = [round(rng.gauss(0, 45), 1) for _ in range(3)]
        xs.append(round(-sum(xs), 1))
        xs.sort(reverse=True)                      # 上から1位〜4位
        for rank, (name, pt) in enumerate(zip(four, xs)):
            a = acc[name]
            a["games"] += 1
            a["points"] = round(a["points"] + pt, 1)
            a["hands"] += rng.randint(8, 12)
            a["ranks"][rank] += 1
            a["best"] = pt if a["best"] is None else max(a["best"], pt)
    return acc


def cells(rng: random.Random, a: dict) -> dict[str, str]:
    """1人分のセルを作る。1試合も出ていなければ全部 `-`。"""
    g = a["games"]
    if not g:
        return {label: "-" for label in ROWS}
    r1, r2, r3, r4 = a["ranks"]
    return {
        "試合数": str(g),
        "総局数": str(a["hands"]),
        "ポイント": f"{a['points']:+.1f}",
        "平着": f"{(r1 + 2 * r2 + 3 * r3 + 4 * r4) / g:.2f}",
        "1位": str(r1), "2位": str(r2), "3位": str(r3), "4位": str(r4),
        "トップ率": f"{r1 / g * 100:.1f}%",
        "連対率": f"{(r1 + r2) / g * 100:.1f}%",
        "ラス回避率": f"{(g - r4) / g * 100:.1f}%",
        "ベストスコア": f"{a['best']:+.1f}",
        "平均打点": f"{rng.randint(5000, 8200):,}",
        "副露率": f"{rng.uniform(20, 42):.1f}%",
        "リーチ率": f"{rng.uniform(14, 26):.1f}%",
        "アガリ率": f"{rng.uniform(17, 26):.1f}%",
        "放銃率": f"{rng.uniform(9, 16):.1f}%",
        "放銃平均打点": f"{rng.randint(4800, 6600):,}",
    }


def build() -> str:
    rng = random.Random(20260915)  # 固定。回すたびに検査の期待値が変わると困る
    everyone = [p for _, _, ps in TEAMS for p in ps]
    # 1人だけ出場前の選手を作る（`-` の扱いを検査するため）
    benched = "ダミー 32"
    acc = simulate(rng, [p for p in everyone if p != benched], 90)
    acc[benched] = {"games": 0, "points": 0.0, "hands": 0, "ranks": [0, 0, 0, 0], "best": None}
    parts = [
        "<!doctype html>",
        "<html lang=\"ja\"><head><meta charset=\"utf-8\">",
        "<title>Stats 2026-27 チーム成績表 | M.LEAGUE（Mリーグ）</title>",
        "</head><body>",
        "<!-- 解析の検査用に作った偽ページ。数字はすべて作り物で、公式のものではない。 -->",
        "<h1>2026-27 チーム成績表</h1>",
    ]
    for team_id, team_name, players in TEAMS:
        table = {p: cells(rng, acc[p]) for p in players}
        parts.append(f'<section class="p-stats__team" id="{team_id}">')
        parts.append(f'<h2 class="p-stats__teamName">{team_name}</h2>')
        parts.append('<table class="p-stats__table">')
        head = "".join(f"<th>{p}</th>" for p in players)
        parts.append(f"<tr><th></th>{head}</tr>")
        for label in ROWS:
            tds = "".join(f"<td>{table[p][label]}</td>" for p in players)
            parts.append(f"<tr><th>{label}</th>{tds}</tr>")
        parts.append("</table></section>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"書き出した: {OUT}")
