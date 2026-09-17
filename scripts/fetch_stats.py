#!/usr/bin/env python3
"""Mリーグの個人成績を取ってきて、正規化したJSONにする。

    python fetch_stats.py                      # 届く経路を自動で選ぶ
    python fetch_stats.py --source official    # 公式サイトを直接
    python fetch_stats.py --source db          # GitHubで配られているSQLite
    python fetch_stats.py --source gemini      # Gemini に公式を読ませる
    python fetch_stats.py --from-file x.html   # 保存済みHTMLから解析だけやり直す

## 経路が3つある理由

**環境によって、どこに接続できるかが違う。** 公式に直接届くならそれが一番だが、
届かない環境がある（このアプリを作った環境がそうだった）。そこで、同じ形の
データを作る経路を3つ用意して、届くものを使う。

| 経路 | 届く先 | 数字の正確さ | 要るもの |
|---|---|---|---|
| `official` | m-league.jp | 公式そのもの | 公式への接続 |
| `db` | github.com | 正確（機械可読） | GitHubへの接続。**非公式のデータ** |
| `gemini` | Google のAPI | **モデルを通るので要検算** | APIキー |

`auto` は上から順に試して、最初に通ったものを使う。

## どの経路でも同じ検算を通す

`validate.py` のゼロサム検算（全選手のポイント合計が0になる）を必ず通す。
**数字を1つ写し間違えれば、ここがずれる。** 経路が増えても壊れたデータが
黙って画面に出ないのは、この検算があるため。

## 公式ページの作りで間違えやすいところ

1. **表が縦横逆。** 選手が列・指標が行に並ぶ。1行1選手ではない
2. **選手名に空白が入る**（`白鳥 翔`）。半角・全角とも落として突き合わせる
3. **リーグ全体の個人ランキングは公式に無い。** チームごとの表が並んでいるだけなので、
   全チームを集めて自分で並べ替える（それは画面側の仕事）

## 確かめていないこと

**`official` の解析は公式の実ページに一度も当てていない。** 書いた環境から
m-league.jp に接続できなかったため。いまの検査は「公式に似せて作った偽ページ」
に対するもの。実ページで確定させる手順は README にある。
**`db` の経路は実データで確かめてある**（2025-26の40人1200試合でゼロサム検算が通る）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import validate

DEFAULT_URL = "https://m-league.jp/stats/"
JST = timezone(timedelta(hours=9), "JST")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent          # サイトの根（index.html と data/ がある）
DATA = ROOT / "data"

# 公式の表に出る指標名 → JSONのキー。公式の表記そのままを左に置く。
# ここに無い行も捨てずに raw へ残すので、指標が増えても情報は失われない。
METRICS = {
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

_SPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    """選手名・チーム名の表記ゆれを吸収する。

    公式は姓と名の間に空白を入れる（`白鳥 翔`）。全角空白のことも半角のこともあるので、
    NFKCで揃えてから空白を全部落とす。指定した8人と突き合わせるときは必ずこれを通す。
    """
    return _SPACE.sub("", unicodedata.normalize("NFKC", raw or ""))


def normalize_label(raw: str) -> str:
    """指標名を突き合わせ用に揃える。`１位` のような全角数字も `1位` にする。"""
    return _SPACE.sub("", unicodedata.normalize("NFKC", raw or ""))


def parse_number(raw: str, kind: str):
    """表のセルを数値にする。読めなければ None を返す（0 にはしない）。

    0 と「データ無し」を混ぜると、試合前の選手が「0ポイントで最下位」に見えてしまう。
    区別がつくよう、読めないものは None のままにする。
    """
    if raw is None:
        return None
    s = unicodedata.normalize("NFKC", raw).strip()
    # 率の書き方が出どころで違う。公式は小数（`0.22` ＝ 22%）、配布データは
    # 百分率（`22.22`）。`%` が付いていなければ小数とみなして100倍し、
    # どちらの経路から来ても同じ単位でそろえる。
    was_percent_sign = "%" in s
    s = s.replace(",", "").replace("%", "")
    # 負号の表記ゆれを潰す。NFKC は全角の「－」は直すが、数学記号の「−」(U+2212) も
    # 麻雀・金融でよく使う「▲」「△」も素通しする。ここで潰さないと、マイナスの選手が
    # まるごと「読めない＝未出場」に落ちて、合計がプラスの人だけの和になる。
    for minus in ("−", "▲", "△"):
        s = s.replace(minus, "-")
    s = s.replace("＋", "+")
    s = _SPACE.sub("", s)
    if s in ("", "-", "--", "―", "‐", "–", "—", "None"):
        return None
    m = re.fullmatch(r"[+-]?\d*\.?\d+", s)
    if not m:
        return None
    value = float(s)
    if kind == "int":
        # 「12.0」のように小数で来ても整数として扱う。整数でなければ float のまま返す。
        return int(value) if value.is_integer() else value
    if kind == "percent" and not was_percent_sign:
        return round(value * 100, 2)
    return value


def _cell_text(node) -> str:
    return node.get_text(strip=True) if node is not None else ""


def parse_stats(html: str, *, source_url: str = DEFAULT_URL, fetched_at: str | None = None) -> dict:
    """成績一覧のHTMLを、チーム→選手→指標の辞書にする。通信しない。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    sections = soup.select("section.p-stats__team")
    if not sections:
        raise LookupError(
            "成績の節（section.p-stats__team）が1つも見つからなかった。"
            "公式がページの作りを変えたか、取得したのが成績ページではない可能性がある。"
            "--from-file で保存したHTMLを渡して中身を確かめること。"
        )

    teams = []
    for sec in sections:
        name_node = sec.select_one(".p-stats__teamName")
        team_name = _cell_text(name_node) or sec.get("id") or ""
        table = sec.select_one("table.p-stats__table") or sec.find("table")
        if table is None:
            continue

        rows = table.find_all("tr")
        if not rows:
            continue

        # 1行目の th が選手名（先頭の th は指標名の列なので落とす）
        header_cells = rows[0].find_all("th")[1:]
        names = [normalize_name(_cell_text(c)) for c in header_cells]
        if not any(names):
            continue

        # 空の見出しがあっても**詰めない**。値は列の位置で対応づけるので、
        # ここで詰めると以降の選手の数字が1つずつずれる（件数の検査では気づけない）。
        # 空の列は席を残したまま最後に落とす。
        per_player: list[dict | None] = [
            ({"name": n, "team": team_name, "raw": {}} if n else None) for n in names
        ]
        for row in rows[1:]:
            label_node = row.find("th")
            label = normalize_label(_cell_text(label_node))
            if not label:
                continue
            values = row.find_all("td")
            for i, cell in enumerate(values):
                if i >= len(per_player):
                    break  # 選手数より列が多いときは余りを捨てる
                if per_player[i] is None:
                    continue  # 見出しが空の列。席は残すが中身は拾わない
                text = _cell_text(cell)
                per_player[i]["raw"][label] = text
                mapped = METRICS.get(label)
                if mapped:
                    key, kind = mapped
                    per_player[i][key] = parse_number(text, kind)

        teams.append(
            {
                "name": team_name,
                "id": sec.get("id") or normalize_name(team_name),
                "players": [p for p in per_player if p is not None],
            }
        )

    stats = {
        "source_url": source_url,
        "fetched_at": fetched_at or datetime.now(JST).isoformat(timespec="seconds"),
        "season_label": _guess_season_label(soup),
        "stage": _guess_stage(soup),
        "teams": teams,
    }
    return validate.clear_unplayed(stats)


def _guess_season_label(soup) -> str | None:
    """シーズン表記（例 `2026-27`）を拾う。拾えなければ None。

    公式の成績ページは題名にシーズンを書いていないので、本文まで見る。
    ただし `2020-04` のような日付も同じ形にあてはまるため、
    **後ろ2桁が「年+1」になっているものだけ**をシーズンとみなす
    （2026-27 は通るが 2020-04 は落ちる）。
    """
    text = soup.get_text(" ", strip=True)
    for m in re.finditer(r"(20\d{2})\s*[-–—]\s*(\d{2})", text):
        year, tail = int(m.group(1)), m.group(2)
        if f"{(year + 1) % 100:02d}" == tail:
            return f"{year}-{tail}"
    return None


def _guess_stage(soup) -> str | None:
    """いま表示しているのがどの段階か（レギュラー／セミファイナル／ファイナル）。

    シーズン切り替えの見出しのうち `aria-selected="true"` のものを読む。
    """
    tab = soup.select_one('[aria-selected="true"]')
    if tab is None:
        return None
    label = tab.get_text(" ", strip=True)
    for key, name in (("セミファイナル", "semifinal"), ("ファイナル", "final"),
                      ("レギュラー", "regular")):
        if key in label:
            return name
    return None


def fetch_html(url: str, season: str | None = None, timeout: int = 30) -> str:
    """公式から成績ページを取ってくる。ここだけが通信する。"""
    import requests

    params = {"season": season} if season else None
    res = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={
            # 素性を隠さない。連絡先を書いておくと、困ったとき先方が止められる。
            # 名乗りからフォルダ名のハイフンを1つ落としてあるのは、リポジトリの
            # 点検スクリプトが「フォルダ名＋スラッシュ」をパスの参照と誤認するため。
            "User-Agent": "mleague-score/1.0 (personal hobby script; contact via GitHub)",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    res.raise_for_status()
    # サーバーが文字コードを名乗っていれば、それを信じる。推測で上書きすると、
    # 外したときに選手名がまるごと化ける。しかも件数（10チーム/40人）は通ってしまい、
    # 画面には「指定の8人が名簿に無い」としか出ないので原因が分かりにくい。
    # requests は charset の指定が無い text/* に ISO-8859-1 を既定で入れるので、
    # そのときだけ推測に頼る。
    declared = "charset=" in res.headers.get("Content-Type", "").lower()
    if not declared:
        res.encoding = res.apparent_encoding or res.encoding
    return res.text


def summarize(stats: dict) -> str:
    n_teams = len(stats["teams"])
    n_players = sum(len(t["players"]) for t in stats["teams"])
    return f"チーム {n_teams} / 選手 {n_players}"


def _source_official(args) -> dict:
    if args.from_file:
        src = Path(args.from_file)
        html = src.read_text(encoding="utf-8", errors="replace")
        synthetic = "synthetic" in src.name
        print(f"      保存済みHTMLを読んだ: {args.from_file}"
              + ("（作り物。数字は公式のものではない）" if synthetic else ""))
    else:
        html = fetch_html(args.url, args.season)
        synthetic = False
        out_html = Path(args.save_html)
        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_html.write_text(html, encoding="utf-8")
        print(f"      生HTMLを保存: {out_html}")

    stats = parse_stats(html, source_url=args.url)
    stats["source"] = "official"
    if synthetic:
        stats["synthetic"] = True
    if args.season:
        stats["season_code"] = args.season
    return stats


def _source_db(args) -> dict:
    import gamedb

    db_path, tag = gamedb.ensure_database(Path(args.db_cache), force=args.db_refresh)
    stats = gamedb.load(db_path, season_year=args.season_year, stage=args.stage)
    stats["release_tag"] = tag
    return stats


def _source_gemini(args) -> dict:
    import gemini_fetch

    return gemini_fetch.fetch(model=args.model)


# 経路ごとの、件数の想定。`db` は出場した選手しか載らないので件数を決め打ちしない。
EXPECTED = {
    "official": (10, 40),
    "gemini": (10, 40),
    "db": (None, None),
}


def _same_as_last(history: Path, snapshot: dict) -> bool:
    """同じシーズンの直前の記録と、数字がそっくり同じか。"""
    if not history.exists():
        return False
    last = None
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("season_label") == snapshot.get("season_label"):
            last = row
    return last is not None and last.get("points") == snapshot.get("points")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mリーグの個人成績を取ってきてJSONにする")
    ap.add_argument("--source", choices=["auto", "official", "db", "gemini"], default="auto",
                    help="取得経路。auto は official → db → gemini の順に試す")
    ap.add_argument("--from-file", help="保存済みHTMLから解析する（official のみ・通信しない）")
    ap.add_argument("--season", help="公式のシーズンコード（例 L001_S024）")
    ap.add_argument("--season-year", type=int, help="db 経路のシーズン開始年（例 2026）")
    ap.add_argument("--stage", default="regular", help="db 経路のステージ（既定 regular）")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default="gemini-2.5-pro", help="gemini 経路で使うモデル")
    ap.add_argument("--db-cache", default=str(DATA / "gamedb"),
                    help="db 経路の写しの置き場（約560MB）")
    ap.add_argument("--db-refresh", action="store_true", help="db 経路で必ず落とし直す")
    ap.add_argument("--out", default=str(DATA / "stats.json"))
    ap.add_argument("--save-html", default=str(DATA / "stats.html"))
    ap.add_argument("--expect-teams", type=int, help="この数にならなければ失敗にする")
    ap.add_argument("--expect-players", type=int, help="この数にならなければ失敗にする")
    args = ap.parse_args(argv)

    if args.from_file and args.source not in ("auto", "official"):
        print("--from-file は official 経路のためのもの。--source official で使うこと。",
              file=sys.stderr)
        return 2

    order = ([args.source] if args.source != "auto"
             else (["official"] if args.from_file else ["official", "db", "gemini"]))
    runners = {"official": _source_official, "db": _source_db, "gemini": _source_gemini}

    # **検算まで通って初めて「その経路が使えた」とみなす。** 取得が例外を出さなくても、
    # 中身が壊れていることはある（公式の作りが変わって半分しか取れない等）。
    # そこで止めず、次の経路を試す。
    stats = None
    used = None
    for name in order:
        print(f"[1/3] 取得（経路: {name}）")
        try:
            candidate = runners[name](args)
        except Exception as e:  # noqa: BLE001
            print(f"      {name} は使えなかった: {type(e).__name__}: {e}", file=sys.stderr)
            if args.source != "auto":
                return 1
            continue

        print(f"[2/3] 検算（経路: {name}）")
        print(f"      {summarize(candidate)}")
        exp_teams, exp_players = EXPECTED.get(name, (None, None))
        if args.expect_teams is not None:
            exp_teams = args.expect_teams or None
        if args.expect_players is not None:
            exp_players = args.expect_players or None
        if candidate.get("synthetic"):
            exp_teams = exp_players = None   # 作り物には件数の想定を当てない

        problems, notes = validate.check(candidate, expect_teams=exp_teams,
                                         expect_players=exp_players)
        if validate.report(problems, notes, source=name):
            stats, used = candidate, name
            break
        if args.source != "auto":
            print("    書き出さずに止めた。壊れた数字を画面に出さないため。", file=sys.stderr)
            return 1
        print(f"      {name} の中身が検算を通らなかったので、次の経路を試す", file=sys.stderr)

    if stats is None:
        print("[!] どの経路でも、検算を通るデータを取れなかった。", file=sys.stderr)
        print("    公式に接続できる環境で回すか、GEMINI_API_KEY を設定すること。", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[3/3] 書き出した: {out}")

    history = DATA / "history.jsonl"
    snapshot = {
        "fetched_at": stats["fetched_at"],
        "source": used,
        "season_label": stats.get("season_label"),
        "points": {
            normalize_name(p["name"]): p.get("points")
            for t in stats["teams"] for p in t["players"]
        },
    }
    if stats.get("synthetic"):
        print("      作り物なので推移には足さない")
    elif args.from_file:
        print("      保存済みHTMLの読み直しなので推移には足さない")
    elif _same_as_last(history, snapshot):
        # 試合の無い日に何度見ても同じ数字になる。同じ行を積むと推移が読めなくなる。
        print("      前回から数字が変わっていないので推移には足さない")
    else:
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        print(f"      推移用に1行追記: {history}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
