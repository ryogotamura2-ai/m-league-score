#!/usr/bin/env python3
"""公式サイトが成績ページの裏で呼んでいるAPIから、成績を取る。

    https://m-league.jp/api/mleague/data?kind=stats

## なぜこれを使うのか（2026-11-10）

以前は `https://m-league.jp/stats/` のHTMLの表をそのまま読んでいた。
ところが公式がページを作り替え、**HTMLの表は枠だけで、数字は後からJavaScriptが
上のAPIから取ってきて差し込む**作りになった。HTMLを取ると、表は全員 `0` のダミーに
なっている。**そのHTMLを読み続けた結果、毎朝の自動更新が10月30日から止まっていた。**

APIは認証が要らず、JSONで返ってくる。形は従来のHTMLの表とほぼ同じ
（チーム → 選手 → 指標名ごとの値の並び）で、指標名も同じ18個。

## 公式のAPIであることについて

これは公式サイト自身が自分のページを描くために使っている口で、公開されている。
ただし**ドキュメントの無い内部用の口**なので、予告なく変わりうる。変わったときに
黙って壊れないよう、中身は `validate.py` の検算（全選手のポイント合計が0）を必ず通す。
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

API_URL = "https://m-league.jp/api/mleague/data"
SOURCE_PAGE = "https://m-league.jp/stats/"
JST = timezone(timedelta(hours=9), "JST")

# APIの指標名 → こちらのキー。`fetch_stats.METRICS` と同じ並び（公式の表記そのまま）。
# 循環importを避けるため、ここでは名前の対応だけを持つ。
METRIC_KEYS = {
    "試合数": ("games", "int"),
    "総局数": ("hands", "int"),
    "ポイント": ("points", "float"),
    "平着": ("avg_rank", "float"),
    "1位": ("rank1", "int"),
    "2位": ("rank2", "int"),
    "3位": ("rank3", "int"),
    "4位": ("rank4", "int"),
    "トップ率": ("top_rate", "percent"),
    "連対率": ("top2_rate", "percent"),
    "ラス回避率": ("last_avoid_rate", "percent"),
    "ベストスコア": ("best_score", "float"),
    "平均打点": ("avg_win_points", "float"),
    "副露率": ("call_rate", "percent"),
    "リーチ率": ("riichi_rate", "percent"),
    "アガリ率": ("win_rate", "percent"),
    "放銃率": ("deal_in_rate", "percent"),
    "放銃平均打点": ("avg_deal_in_points", "float"),
}


def _norm(s) -> str:
    """選手名・チーム名の表記ゆれを吸収する（空白を全部落とす）。"""
    import re

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _number(v, kind: str):
    """APIの値を数にする。無い値は None（0 にはしない）。

    **率は小数（0.4 ＝ 40%）で来る**ので、百分率に直して他の経路と単位をそろえる
    （従来のHTML経路も、配布データ経路も、率は百分率）。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if kind == "int":
        return int(x) if x.is_integer() else x
    if kind == "percent":
        return round(x * 100, 2)
    return x


def fetch_json(kind: str = "stats", timeout: int = 30) -> dict:
    """公式のAPIを呼ぶ。ここだけが通信する。"""
    import requests

    res = requests.get(
        API_URL,
        params={"kind": kind},
        timeout=timeout,
        headers={
            "User-Agent": "mleague-score/1.0 (personal hobby script; contact via GitHub)",
            "Accept": "application/json",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    res.raise_for_status()
    return res.json()


def parse(payload: dict, *, fetched_at: str | None = None) -> dict:
    """APIの返事を、他の経路と同じ形の辞書にする。通信しない。"""
    teams_in = (payload.get("stats") or {}).get("teams")
    if not teams_in:
        raise LookupError(
            "APIの返事に成績（stats.teams）が入っていない。"
            "公式がAPIの形を変えた可能性がある。返事の中身を確かめること。"
        )

    teams = []
    for t in teams_in:
        names = t.get("players") or []
        players = [{"name": _norm(n), "team": t.get("team", ""), "raw": {}} for n in names]

        for metric in t.get("metrics") or []:
            label = unicodedata.normalize("NFKC", metric.get("name", ""))
            mapped = METRIC_KEYS.get(label)
            for i, v in enumerate(metric.get("values") or []):
                if i >= len(players):
                    break  # 選手数より値が多いときは余りを捨てる
                players[i]["raw"][label] = "" if v is None else str(v)
                if mapped:
                    key, kind = mapped
                    players[i][key] = _number(v, kind)

        teams.append({"name": t.get("team", ""), "id": t.get("id") or _norm(t.get("team")),
                      "players": players})

    season = payload.get("season") or {}
    return {
        "source_url": SOURCE_PAGE,
        "fetched_at": fetched_at or datetime.now(JST).isoformat(timespec="seconds"),
        "season_label": _season_label(season),
        "stage": season.get("phase"),
        "season_code": season.get("id"),
        "teams": teams,
    }


def _season_label(season: dict) -> str | None:
    """`2026-27レギュラーシーズン` のような名前から `2026-27` を取り出す。"""
    import re

    m = re.search(r"(20\d{2})\s*[-–—]\s*(\d{2})", season.get("name") or "")
    if m and f"{(int(m.group(1)) + 1) % 100:02d}" == m.group(2):
        return f"{m.group(1)}-{m.group(2)}"
    year = season.get("year")
    if isinstance(year, int):
        return f"{year}-{(year + 1) % 100:02d}"
    return None
