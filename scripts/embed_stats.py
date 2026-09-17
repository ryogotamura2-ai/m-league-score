#!/usr/bin/env python3
"""`data/stats.json` を `index.html` に焼き込む。あわせて Artifact 用の写しも作る。

    python embed_stats.py

ページは外部の部品を1つも読み込まない単一ファイルにしてある。だからデータも
外から読むのではなく、`<script type="application/json" id="ml-data">` の中身を
差し替える形で埋める（ファイルを直接ダブルクリックしても動かすため）。

Artifact 用（`artifact.html`）を別に出すのは、Artifact が publish 時に
`<html>/<head>/<body>` を自前で被せる作りで、`index.html` をそのまま出せないため。
`<title>` + `<style>` + `<body>` の中身だけを取り出したものを書く。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent          # サイトの根（index.html と data/ がある）

# JSONを入れる場所の目印。属性の並びまで含めて一致させる。
OPEN_TAG = '<script type="application/json" id="ml-data">'
CLOSE_TAG = "</script>"


def embed(html: str, payload: dict) -> str:
    """`ml-data` の中身を payload で置き換える。"""
    start = html.find(OPEN_TAG)
    if start < 0:
        raise LookupError(
            f"データの置き場所が見つからない: {OPEN_TAG}\n"
            "index.html からこの行を消してしまっていないか確かめること。"
        )
    body_start = start + len(OPEN_TAG)
    end = html.find(CLOSE_TAG, body_start)
    if end < 0:
        raise LookupError("データの置き場所が閉じていない（</script> が無い）")

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # **タグの始まりになりうる文字を丸ごと潰す。**
    #
    # 以前は `</` だけを逃がしていた。ブラウザはそれで十分（HTMLの読み手は
    # `</script` でしかスクリプトを終えない）だが、**ページを正規表現で読む側**は
    # 別だった。`run-selftest.js` は `<script>` という並びを目印にコードを取り出すので、
    # 取ってきた名前に `<script>` が入っていると、そこから後ろがコードとして実行される。
    # 実際に任意のコマンドを実行できることを確かめた（取得元は第三者のデータベース）。
    #
    # `\u003c` などはJSONの正しい逃がし方で、`JSON.parse` が元の文字に戻すので、
    # 画面の見え方は変わらない。
    for ch, esc in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e")):
        text = text.replace(ch, esc)
    return html[:body_start] + "\n" + text + "\n" + html[end:]


def to_artifact(html: str) -> str:
    """Artifact に渡す形にする。`<title>` と `<style>` と本文だけを残す。"""
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    body = re.search(r"<body>(.*?)</body>", html, re.S)
    if not (title and style and body):
        missing = [n for n, m in (("title", title), ("style", style), ("body", body)) if not m]
        raise LookupError("index.html から取り出せない部分がある: " + " / ".join(missing))
    return (
        f"<title>{title.group(1)}</title>\n"
        f"<style>{style.group(1)}</style>\n"
        f"{body.group(1).strip()}\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stats.json を index.html に焼き込む")
    ap.add_argument("--stats", default=str(ROOT / "data" / "stats.json"))
    ap.add_argument("--html", default=str(ROOT / "index.html"))
    ap.add_argument("--artifact", default="",
                    help="別形式の写しの書き出し先。既定では作らない")
    args = ap.parse_args(argv)

    stats_path = Path(args.stats)
    if not stats_path.exists():
        print(f"データが無い: {stats_path}", file=sys.stderr)
        print("先に python fetch_stats.py を回すこと。", file=sys.stderr)
        return 2

    payload = json.loads(stats_path.read_text(encoding="utf-8"))
    n_teams = len(payload.get("teams", []))
    n_players = sum(len(t.get("players", [])) for t in payload.get("teams", []))
    if n_players == 0:
        print("データに選手が1人も入っていない。焼き込みを中止した。", file=sys.stderr)
        return 1

    html_path = Path(args.html)
    html = html_path.read_text(encoding="utf-8")
    out = embed(html, payload)
    html_path.write_text(out, encoding="utf-8")
    print(f"焼き込んだ: {html_path}（チーム {n_teams} / 選手 {n_players}）")

    if args.artifact:
        art = Path(args.artifact)
        art.write_text(to_artifact(out), encoding="utf-8")
        print(f"Artifact 用の写し: {art}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
