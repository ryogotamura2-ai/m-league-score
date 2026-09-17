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

# ビューの列 → こちらのキー。公式ページの18項目に対応するものだけを拾う。
COLUMNS = {
    "total_game_count": "games",
    "total_points": "points",
    "rank1_count": "rank1",
    "rank2_count": "rank2",
    "rank3_count": "rank3",
    "rank4_count": "rank4",
    "top_rate_percent": "top_rate",
    "top2_rate_percent": "top2_rate",
    "avoid_last_rate_percent": "last_avoid_rate",
    "best_score": "best_score",
    "avg_win_points": "avg_win_points",
    "furo_rate_percent": "call_rate",
    "reach_rate_percent": "riichi_rate",
    "win_rate_percent": "win_rate",
    "dealin_rate_percent": "deal_in_rate",
    "avg_dealin_points": "avg_deal_in_points",
}


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
        rows = con.execute(
            "SELECT DISTINCT start_season_year, stage FROM player_season_stage_stats"
            " ORDER BY start_season_year DESC"
        ).fetchall()
    finally:
        con.close()
    return [(int(y), s) for y, s in rows]


def load(db_path: Path, *, season_year: int | None = None, stage: str = "regular") -> dict:
    """ビューを読んで、公式経路と同じ形の辞書にする。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if season_year is None:
            row = con.execute(
                "SELECT MAX(start_season_year) y FROM player_season_stage_stats WHERE stage=?",
                (stage,),
            ).fetchone()
            season_year = row["y"]
            if season_year is None:
                raise LookupError(f"ステージ '{stage}' のデータが無い")
        rows = con.execute(
            "SELECT * FROM player_season_stage_stats WHERE start_season_year=? AND stage=?",
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
        for col, key in COLUMNS.items():
            player[key] = r[col] if col in r.keys() else None
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
