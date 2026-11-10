#!/usr/bin/env python3
"""有志がGitHubで配っているSQLiteから成績を作る。**通信の制限が強い環境向け。**

公式サイトに届かない環境でも、GitHub には届くことが多い。そこを使う。

    https://github.com/konoui/m-league-game-db

**公式ではない。** 有志の作成物で、更新も作者次第。出どころは画面に必ず出す。
そのかわり数字は正確（大規模言語モデルを通さない）で、APIキーも要らない。

## 大きさと更新の見分け

配布物は約195MB（展開して約560MB）。毎回落とすのは重いので、**落とす前に
リリースのタグだけを見る**。タグは `20260914-2123-<コミット>` の形で、
ここに更新時刻が入っている。前に落としたタグと同じなら、手元の写しを使う。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

LATEST_URL = "https://github.com/konoui/m-league-game-db/releases/latest/download/database.zip"
SOURCE_PAGE = "https://github.com/konoui/m-league-game-db"
JST = timezone(timedelta(hours=9), "JST")

# こちらのキー → ビューの列名の候補（新しい名前が先、旧い名前があとに続く）。
#
# **配布データは列名がまるごと付け替わることがある**（2026-11 に起きた:
# `start_season_year` → `season_start_year`、`win_rate_percent` → `agari_per_kyoku_percent`
# など）。そのとき黙って壊れて毎朝の更新が止まった。そこで、候補を並べて
# 「ビューに実際にある列」を選ぶ。どちらの版でも読め、どちらにも無ければ
# **どの項目が見つからないかを名指しして**止まる。
#
# 新旧の対応は、公式APIの値と3人×16項目を突き合わせて、全部一致することを確かめてある。
COLUMNS = {
    "games":              ("total_game_count",),
    "points":             ("league_points_total", "total_points"),
    "rank1":              ("rank1_count",),
    "rank2":              ("rank2_count",),
    "rank3":              ("rank3_count",),
    "rank4":              ("rank4_count",),
    "top_rate":           ("top_per_game_percent", "top_rate_percent"),
    "top2_rate":          ("top2_per_game_percent", "top2_rate_percent"),
    "last_avoid_rate":    ("avoid_last_per_game_percent", "avoid_last_rate_percent"),
    "best_score":         ("best_score",),
    "avg_win_points":     ("agari_points_per_agari", "avg_win_points"),
    "call_rate":          ("furo_per_kyoku_percent", "furo_rate_percent"),
    "riichi_rate":        ("reach_per_kyoku_percent", "reach_rate_percent"),
    "win_rate":           ("agari_per_kyoku_percent", "win_rate_percent"),
    "deal_in_rate":       ("dealin_per_kyoku_percent", "dealin_rate_percent"),
    "avg_deal_in_points": ("dealin_points_per_dealin", "avg_dealin_points"),
}

# シーズン開始年の列名も付け替わった
YEAR_COLUMNS = ("season_start_year", "start_season_year")

# これが無ければ成績として成り立たない項目。見つからなければ止める。
REQUIRED = ("games", "points", "rank1", "rank2", "rank3", "rank4")


def _pick(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    """候補のうち、ビューに実在する最初の列名。"""
    return next((c for c in candidates if c in columns), None)


def _view_columns(con) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(player_season_stage_stats)")}


def _year_column(columns: set[str]) -> str:
    col = _pick(columns, YEAR_COLUMNS)
    if col is None:
        raise LookupError(
            "配布データのビューに、シーズン開始年の列が見つからない"
            f"（探した名前: {', '.join(YEAR_COLUMNS)}）。配布側の作りが変わった可能性がある。"
            f" ビューにある列: {', '.join(sorted(columns)[:12])} …"
        )
    return col


def latest_tag(timeout: int = 40) -> str | None:
    """落とさずにリリースのタグだけを見る。分からなければ None。"""
    import requests

    try:
        res = requests.head(LATEST_URL, allow_redirects=False, timeout=timeout)
        loc = res.headers.get("Location", "")
        m = re.search(r"/releases/download/([^/]+)/", loc)
        return m.group(1) if m else None
    except Exception:
        return None


def ensure_database(cache_dir: Path, *, force: bool = False, timeout: int = 600) -> tuple[Path, str]:
    """データベースを手元に用意して、(場所, タグ) を返す。同じタグなら落とし直さない。"""
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = latest_tag()

    # 版を確かめられなかった（通信が一時的にこけた等）ときに落とし直すと、195MBを
    # 無駄に引いたうえ、後片づけで正しい写しを消してしまう。手元にあるならそれを使う。
    if tag is None:
        have = sorted((d for d in cache_dir.iterdir() if (d / "database.sqlite3").exists()),
                      key=lambda d: d.stat().st_mtime)
        if have and not force:
            found = have[-1]
            print(f"      最新版を確かめられなかったので手元の写しを使う（{found.name}）")
            return found / "database.sqlite3", found.name
        tag = "unknown"

    target = cache_dir / tag / "database.sqlite3"
    if target.exists() and not force:
        size = target.stat().st_size / 1048576
        print(f"      手元の写しを使う（{tag} / {size:.0f}MB）")
        return target, tag

    target.parent.mkdir(parents=True, exist_ok=True)
    zip_path = target.parent / "database.zip"
    print(f"      取得中（{tag}）… 約195MB あるので少しかかる")
    with requests.get(LATEST_URL, stream=True, timeout=timeout) as res:
        res.raise_for_status()
        with zip_path.open("wb") as fh:
            for chunk in res.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        inner = next((n for n in z.namelist() if n.endswith(".sqlite3")), None)
        if inner is None:
            raise LookupError("配布物の中に .sqlite3 が見つからない")
        # 途中で切れた半端なファイルを掴まないよう、別名で展開してから置き換える。
        # 次に回したとき `target.exists()` だけ見て「手元の写しを使う」と言うため。
        tmp = target.with_name("database.sqlite3.part")
        with z.open(inner) as src, tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    tmp.replace(target)
    zip_path.unlink(missing_ok=True)   # 展開できたら圧縮物は要らない（容量が倍になる）

    # 古いタグの写しは消す。放っておくと1回ごとに560MB積まれる。
    # 版が分からないときは消さない（正しい写しを巻き添えにするため）。
    if tag != "unknown":
        for other in cache_dir.iterdir():
            if other.is_dir() and other.name != tag:
                shutil.rmtree(other, ignore_errors=True)
                print(f"      古い写しを片づけた（{other.name}）")
    return target, tag


def available_seasons(db_path: Path) -> list[tuple[int, str]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        year = _year_column(_view_columns(con))
        rows = con.execute(
            f"SELECT DISTINCT {year}, stage FROM player_season_stage_stats"
            f" ORDER BY {year} DESC"
        ).fetchall()
    finally:
        con.close()
    return [(int(y), s) for y, s in rows]


def resolve_columns(columns: set[str]) -> dict[str, str | None]:
    """こちらのキー → ビューに実在する列名。見つからない項目は None。

    **成績として必須の項目（試合数・ポイント・着順）が見つからなければ、
    名前を挙げて止める。** 黙って None にすると、列名が付け替わったときに
    「ポイントが1人も入っていない」としか出ず、原因が分からない。
    """
    chosen = {key: _pick(columns, cands) for key, cands in COLUMNS.items()}
    missing = [k for k in REQUIRED if chosen[k] is None]
    if missing:
        wanted = "; ".join(f"{k}（探した名前: {', '.join(COLUMNS[k])}）" for k in missing)
        raise LookupError(
            f"配布データのビューに、必須の項目が見つからない: {wanted}。"
            "配布側が列名を付け替えた可能性がある。gamedb.py の COLUMNS に新しい名前を足すこと。"
        )
    return chosen


def load(db_path: Path, *, season_year: int | None = None, stage: str = "regular") -> dict:
    """ビューを読んで、公式経路と同じ形の辞書にする。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        columns = _view_columns(con)
        year = _year_column(columns)
        chosen = resolve_columns(columns)
        if season_year is None:
            row = con.execute(
                f"SELECT MAX({year}) y FROM player_season_stage_stats WHERE stage=?",
                (stage,),
            ).fetchone()
            season_year = row["y"]
            if season_year is None:
                raise LookupError(f"ステージ '{stage}' のデータが無い")
        rows = con.execute(
            f"SELECT * FROM player_season_stage_stats WHERE {year}=? AND stage=?",
            (int(season_year), stage),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        raise LookupError(f"{season_year}-{stage} のデータが無い")

    by_team: dict[str, list[dict]] = {}
    for r in rows:
        team = r["team_name"] or ""
        player = {
            "name": _normalize_name(r["player_name"]),
            "team": team,
            "raw": {},
        }
        for key, col in chosen.items():
            player[key] = r[col] if col is not None else None
        # 平着はこのビューに無いので着順の内訳から出す。総局数は元データに無い。
        player["avg_rank"] = _avg_rank(player)
        player["hands"] = None
        by_team.setdefault(team, []).append(player)

    return {
        "source_url": SOURCE_PAGE,
        "source": "gamedb",
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "season_label": f"{season_year}-{str(season_year + 1)[2:]}",
        "stage": stage,
        "unofficial": True,
        # このビューには**出場した選手しか載らない**。開幕直後は大半が居ない。
        # 画面側はこれを見て、名簿に無い選手を「行方不明」ではなく「未出場」と出す。
        "partial_roster": True,
        "teams": [
            {"name": name, "id": name, "players": players}
            for name, players in sorted(by_team.items())
        ],
    }


def _avg_rank(p: dict) -> float | None:
    ranks = [p.get("rank1"), p.get("rank2"), p.get("rank3"), p.get("rank4")]
    if None in ranks:
        return None
    n = sum(ranks)
    if not n:
        return None
    return round((ranks[0] + 2 * ranks[1] + 3 * ranks[2] + 4 * ranks[3]) / n, 2)


def _normalize_name(raw: str) -> str:
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw or ""))
