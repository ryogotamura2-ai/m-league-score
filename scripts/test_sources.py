#!/usr/bin/env python3
"""検算と、新しい2経路（SQLite・Gemini）の検査。通信しない。

    python test_sources.py

実データに当てた確認は README に書いてある。ここで見るのは組み立てのほう。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gamedb
import gemini_fetch
import validate

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))


def player(name, points, games, ranks=None, team="T"):
    r1, r2, r3, r4 = ranks or (games, 0, 0, 0)
    return {"name": name, "team": team, "points": points, "games": games,
            "rank1": r1, "rank2": r2, "rank3": r3, "rank4": r4, "raw": {"x": "1"}}


def wrap(players, team="T"):
    return {"teams": [{"name": team, "id": team, "players": players}]}


# ---------------- 検算 ----------------

def test_validate() -> None:
    ok = wrap([player("a", 10.0, 1, (1, 0, 0, 0)), player("b", -10.0, 1, (0, 1, 0, 0))])
    probs, notes = validate.check(ok)
    check("合計0なら通る", not probs, "; ".join(probs) or "問題なし")
    check("ゼロサムOKの記録が残る", any("ゼロサム" in n for n in notes))

    ng = wrap([player("a", 10.0, 1, (1, 0, 0, 0)), player("b", -9.0, 1, (0, 1, 0, 0))])
    probs, _ = validate.check(ng)
    check("合計が0でなければ止める", any("合計" in p for p in probs), "; ".join(probs))

    # 0.1刻みの足し算で出る誤差は見逃す
    tiny = wrap([player("a", 0.1, 1, (1, 0, 0, 0)), player("b", -0.1, 1, (0, 1, 0, 0))])
    probs, _ = validate.check(tiny)
    check("わずかな誤差では止めない", not probs, "; ".join(probs) or "問題なし")

    bad = wrap([player("a", 10.0, 5, (1, 0, 0, 0)), player("b", -10.0, 1, (0, 1, 0, 0))])
    probs, _ = validate.check(bad)
    check("着順の合計が試合数と合わなければ止める",
          any("1〜4位" in p for p in probs), "; ".join(probs))

    dup = wrap([player("a", 10.0, 1, (1, 0, 0, 0)), player("a", -10.0, 1, (0, 1, 0, 0))])
    probs, _ = validate.check(dup)
    check("同じ選手が二度出ていれば止める", any("二度" in p for p in probs), "; ".join(probs))

    probs, _ = validate.check({"teams": []})
    check("空なら止める", any("1人も" in p for p in probs), "; ".join(probs))

    probs, _ = validate.check(ok, expect_players=40)
    check("件数の想定に合わなければ止める", any("選手数" in p for p in probs), "; ".join(probs))

    # 全員の点が空だと、合計0で「通って」しまう。名前だけ拾えた場合がこれ。
    nameonly = wrap([{"name": "a", "team": "T", "points": None, "games": None, "raw": {}},
                     {"name": "b", "team": "T", "points": None, "games": None, "raw": {}}])
    probs, _ = validate.check(nameonly)
    check("点が1人も入っていなければ止める",
          any("1人もいない" in p for p in probs), "; ".join(probs))

    # 未出場（points が None）は合計に入れない
    mixed = wrap([player("a", 10.0, 1, (1, 0, 0, 0)), player("b", -10.0, 1, (0, 1, 0, 0)),
                  {"name": "c", "team": "T", "points": None, "games": None, "raw": {}}])
    probs, _ = validate.check(mixed)
    check("未出場が混ざっても合計は狂わない", not probs, "; ".join(probs) or "問題なし")


# ---------------- SQLite 経路 ----------------

VIEW_COLS = ["start_season_year", "stage", "player_name", "team_name",
             "total_game_count", "total_points", "rank1_count", "rank2_count",
             "rank3_count", "rank4_count", "top_rate_percent", "top2_rate_percent",
             "avoid_last_rate_percent", "best_score", "avg_win_points",
             "furo_rate_percent", "reach_rate_percent", "win_rate_percent",
             "dealin_rate_percent", "avg_dealin_points"]


def make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE player_season_stage_stats ({','.join(VIEW_COLS)})")
    rows = [
        (2026, "regular", "白鳥 翔", "渋谷ABEMAS", 2, 30.0, 2, 0, 0, 0,
         100.0, 100.0, 100.0, 50.0, 7000.0, 30.0, 20.0, 22.0, 10.0, 5000.0),
        (2026, "regular", "園田 賢", "赤坂ドリブンズ", 2, -30.0, 0, 0, 0, 2,
         0.0, 0.0, 0.0, -5.0, 6000.0, 25.0, 18.0, 20.0, 14.0, 5500.0),
        (2025, "regular", "古い 選手", "むかしチーム", 1, 0.0, 0, 1, 0, 0,
         0.0, 100.0, 100.0, 1.0, 6000.0, 25.0, 18.0, 20.0, 14.0, 5500.0),
    ]
    con.executemany(f"INSERT INTO player_season_stage_stats VALUES ({','.join('?' * len(VIEW_COLS))})", rows)
    con.commit(); con.close()


def test_gamedb() -> None:
    check("平着を着順から出す（1位2回なら1.00）",
          gamedb._avg_rank({"rank1": 2, "rank2": 0, "rank3": 0, "rank4": 0}) == 1.0)
    check("平着（1位と4位が1回ずつなら2.50）",
          gamedb._avg_rank({"rank1": 1, "rank2": 0, "rank3": 0, "rank4": 1}) == 2.5)
    check("着順が欠けていれば平着は出さない",
          gamedb._avg_rank({"rank1": 1, "rank2": None, "rank3": 0, "rank4": 0}) is None)
    check("1試合も無ければ平着は出さない",
          gamedb._avg_rank({"rank1": 0, "rank2": 0, "rank3": 0, "rank4": 0}) is None)
    check("選手名の空白を落とす", gamedb._normalize_name("白鳥　翔") == "白鳥翔")

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.sqlite3"
        make_db(path)

        seasons = gamedb.available_seasons(path)
        check("使えるシーズンを新しい順に出す", seasons[0] == (2026, "regular"), str(seasons))

        stats = gamedb.load(path)
        players = [p for t in stats["teams"] for p in t["players"]]
        check("シーズンを指定しなければ最新を読む", stats["season_label"] == "2026-27",
              stats["season_label"])
        check("最新シーズンの選手だけを読む", len(players) == 2, f"{len(players)}人")
        check("チームごとに分かれる", len(stats["teams"]) == 2)
        check("非公式であることを記録する", stats.get("unofficial") is True)
        check("経路を記録する", stats.get("source") == "gamedb")

        by = {p["name"]: p for p in players}
        check("名前の空白が落ちている", "白鳥翔" in by, str(sorted(by)))
        check("ポイントを写す", by["白鳥翔"]["points"] == 30.0)
        check("列の対応が合っている（放銃率を副露率と取り違えない）",
              by["白鳥翔"]["deal_in_rate"] == 10.0 and by["白鳥翔"]["call_rate"] == 30.0,
              f"放銃率 {by['白鳥翔']['deal_in_rate']} / 副露率 {by['白鳥翔']['call_rate']}")
        check("総局数は元データに無いので None", by["白鳥翔"]["hands"] is None)

        probs, _ = validate.check(stats)
        check("読んだ結果が検算を通る", not probs, "; ".join(probs) or "問題なし")

        old = gamedb.load(path, season_year=2025)
        check("シーズンを指定して読める",
              [p["name"] for t in old["teams"] for p in t["players"]] == ["古い選手"])

        try:
            gamedb.load(path, season_year=1999)
        except LookupError:
            check("無いシーズンは例外で止まる", True)
        else:
            check("無いシーズンは例外で止まる", False, "素通りした")


# ---------------- Gemini 経路 ----------------

def test_gemini_key_not_in_url() -> None:
    """APIキーがURLに乗らないこと。

    乗ると、通信に失敗したときの例外文（`Max retries exceeded with url: ...`）に
    そのまま載り、端末やログに残る。実際に一度その形で書いてしまった。
    """
    src = Path(gemini_fetch.__file__).read_text(encoding="utf-8")
    code = [l for l in src.splitlines() if not l.strip().startswith("#")]
    bad = [l.strip() for l in code if "key={api_key}" in l or 'params={"key"' in l]
    check("キーをURLのクエリに入れていない", not bad, "; ".join(bad) or "無し")
    check("キーをヘッダで渡している", any("x-goog-api-key" in l for l in code))


def test_gemini() -> None:
    check("素のJSONを読む", gemini_fetch._extract_json('{"teams": []}') == {"teams": []})
    check("囲みが付いていても剥がす",
          gemini_fetch._extract_json('```json\n{"teams": []}\n```') == {"teams": []})
    check("前後に説明文が付いていても拾う",
          gemini_fetch._extract_json('はい。\n{"teams": []}\nです。') == {"teams": []})
    try:
        gemini_fetch._extract_json("JSONではありません")
    except ValueError:
        check("JSONが無ければ例外で止まる", True)
    else:
        check("JSONが無ければ例外で止まる", False, "素通りした")

    check("pro を優先して選ぶ",
          gemini_fetch._pick_model(["gemini-2.5-flash", "gemini-2.5-pro"]) == "gemini-2.5-pro")
    check("pro が無ければ flash",
          gemini_fetch._pick_model(["gemini-2.5-flash", "text-embedding"]) == "gemini-2.5-flash")
    check("どちらも無ければ先頭", gemini_fetch._pick_model(["something"]) == "something")
    check("空なら None", gemini_fetch._pick_model([]) is None)

    stats = gemini_fetch._to_stats({
        "season_label": "2026-27",
        "teams": [{"name": "渋谷ABEMAS", "players": [
            {"name": "白鳥 翔", "games": 2, "points": 30.0,
             "rank1": 2, "rank2": 0, "rank3": 0, "rank4": 0},
            {"name": "松本 吉弘", "games": None, "points": None},
        ]}]}, model="gemini-2.5-pro")
    players = stats["teams"][0]["players"]
    check("名前の空白が落ちている", players[0]["name"] == "白鳥翔")
    check("所属が入る", players[0]["team"] == "渋谷ABEMAS")
    check("未出場は None のまま（0 にしない）",
          players[1]["points"] is None and players[1]["games"] is None)
    check("使ったモデルを記録する", stats.get("via_model") == "gemini-2.5-pro")
    check("経路を記録する", stats.get("source") == "gemini")

    empty = gemini_fetch._to_stats({"teams": []}, model="m")
    probs, _ = validate.check(empty)
    check("空の返事は検算で止まる", any("1人も" in p for p in probs), "; ".join(probs))


def test_embed_cannot_inject_code() -> None:
    """**取ってきた名前が、コードとして実行されないこと。**

    以前は焼き込み時に `</` だけを逃がしていた。ブラウザはそれで安全だが、
    ページを正規表現で読む `run-selftest.js` は別で、名前に `<script>` が入っていると
    そこから後ろをコードとして実行していた。**第三者のデータベースから取ってきた名前で
    任意のコマンドを実行できることを実際に確かめた**ので、その筋を検査で塞いでおく。
    """
    import shutil
    import subprocess
    import sys as _sys
    import tempfile

    import embed_stats

    root = Path(__file__).resolve().parent.parent   # index.html はサイトの根にある
    evil = ("<script>process.mainModule.require('child_process')"
            ".execSync('touch PWNED.txt');"
            "globalThis.SelfTest={run:function(){return [];}};//")
    payload = {
        "source": "gamedb", "season_label": "2026-27",
        "fetched_at": "2026-09-16T07:00:00+09:00", "partial_roster": True,
        "teams": [{"name": evil, "id": "x", "players": [
            {"name": "だれか", "team": "x", "points": 0.0, "games": 1,
             "rank1": 0, "rank2": 1, "rank3": 0, "rank4": 0}]}],
    }

    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        shutil.copy(root / "index.html", work / "index.html")
        shutil.copy(root / "run-selftest.js", work / "run-selftest.js")

        written = embed_stats.embed((work / "index.html").read_text(encoding="utf-8"), payload)
        (work / "index.html").write_text(written, encoding="utf-8")

        check("タグの始まりを生のまま焼き込まない", "<script>process" not in written,
              "生の <script> が残っている" if "<script>process" in written else "逃がしてある")
        check("< を逃がしている", "\\u003c" in written)

        if shutil.which("node") is None:
            check("自己点検で仕込みが実行されない（node が無いので飛ばす）", True, "node 無し")
            return
        r = subprocess.run(["node", "run-selftest.js"], cwd=work,
                           capture_output=True, text=True, timeout=120)
        check("仕込んだコマンドが実行されない", not (work / "PWNED.txt").exists(),
              "PWNED.txt ができた" if (work / "PWNED.txt").exists() else "痕跡なし")
        check("自己点検が本物の件数を返す（0件で素通りしない）",
              "0件中 0件" not in r.stdout and r.returncode == 0,
              (r.stdout.strip().splitlines() or ["(出力なし)"])[-1])


def main() -> int:
    test_validate()
    test_gamedb()
    test_gemini_key_not_in_url()
    test_gemini()
    test_embed_cannot_inject_code()
    passed = 0
    for ok, name, detail in _results:
        print(("合格  " if ok else "不合格") + " " + name + (f"  … {detail}" if detail else ""))
        passed += ok
    print(f"\n{len(_results)}件中 {passed}件 合格")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
