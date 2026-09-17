#!/usr/bin/env python3
"""Gemini に公式ページを読ませて成績を作る。**公式にも配布物にも届かない環境向け。**

Gemini API の `url_context` を使う。ページを取りに行くのは Google 側なので、
こちらから m-league.jp に接続できなくても中身が手に入る。

    export GEMINI_API_KEY=...        # キーはリポジトリに書かない
    python fetch_stats.py --source gemini

## この経路の弱いところ（必ず読むこと）

**40人×18項目の数字が、大規模言語モデルを通る。** 数字の書き写しはモデルが
いちばん苦手な作業で、1つ違っても画面上は何も壊れて見えない。

だから **`validate.py` のゼロサム検算を必ず通す。** 麻雀は1試合の合計が0なので、
リーグ全員のポイントを足すと0になる。**1つでも写し間違えれば、ここがずれる。**
この経路では検算を落ちたら書き出さない（`--source gemini` は既定で厳しくしてある）。

検算が落ちたときに直す手立ては無い。**公式に直接届く環境か、配布物の経路
（`gamedb.py`）が使えるなら、そちらを先に使うこと。**
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-pro"
STATS_URL = "https://m-league.jp/stats/"
JST = timezone(timedelta(hours=9), "JST")

# 返してほしい形。数える項目だけに絞る（多く求めるほど写し間違いが増える）。
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "season_label": {"type": "string"},
        "teams": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "players": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "games": {"type": "integer", "nullable": True},
                                "points": {"type": "number", "nullable": True},
                                "rank1": {"type": "integer", "nullable": True},
                                "rank2": {"type": "integer", "nullable": True},
                                "rank3": {"type": "integer", "nullable": True},
                                "rank4": {"type": "integer", "nullable": True},
                                "avg_rank": {"type": "number", "nullable": True},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["name", "players"],
            },
        },
    },
    "required": ["teams"],
}

PROMPT = f"""{STATS_URL} を開いて、載っているMリーグの成績表をそのままJSONにしてください。

このページはチームごとに表が分かれており、**表は縦横が逆**です。
選手名が見出しの行に横に並び、各行が「試合数」「ポイント」などの指標です。

厳守してください:
- **数字はページに書いてある値をそのまま写す。計算し直さない。推測で埋めない。**
- セルが「-」や空欄の選手は、その項目を null にする。0 にしない
- 選手名の姓名の間の空白は取り除く（「白鳥 翔」→「白鳥翔」）
- ポイントは符号を含めてそのまま（「-91.3」なら -91.3）
- 表に載っているチームと選手を**全部**入れる。省略しない
- ページが開けない、または成績表が無い場合は teams を空の配列にする
"""


def _post(url: str, payload: dict, timeout: int, api_key: str):
    """キーは**ヘッダで渡す。** URLのクエリに入れると、通信に失敗したときの
    例外文（`Max retries exceeded with url: ...key=AIza...`）にそのまま載って、
    端末やログに残ってしまう。"""
    import requests

    return requests.post(url, json=payload, timeout=timeout,
                         headers={"Content-Type": "application/json",
                                  "x-goog-api-key": api_key})


def list_models(api_key: str, timeout: int = 60) -> list[str]:
    """使えるモデル名を出す。既定のモデルが通らなかったときの当たりをつける用。"""
    import requests

    res = requests.get(f"{API_ROOT}/models", timeout=timeout,
                       headers={"x-goog-api-key": api_key})
    res.raise_for_status()
    out = []
    for m in res.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(m["name"].removeprefix("models/"))
    return out


def _pick_model(available: list[str]) -> str | None:
    """pro を優先し、次に flash。どちらも無ければ先頭。"""
    for want in ("pro", "flash"):
        hits = [m for m in available if want in m and "vision" not in m]
        if hits:
            return sorted(hits)[-1]
    return available[0] if available else None


def _extract_json(text: str) -> dict:
    """本文からJSONを取り出す。コードの囲みが付いていても剥がす。"""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("返事からJSONを取り出せなかった: " + text[:200])
    return json.loads(t[start:end + 1])


def fetch(api_key: str | None = None, *, model: str = DEFAULT_MODEL,
          timeout: int = 180) -> dict:
    """Gemini に公式ページを読ませて、公式経路と同じ形の辞書にする。"""
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY が設定されていない。\n"
            "  export GEMINI_API_KEY=... を先に実行すること（キーはリポジトリに書かない）"
        )

    body = {
        "contents": [{"parts": [{"text": PROMPT}]}],
        "tools": [{"url_context": {}}],
        "generationConfig": {
            "temperature": 0,          # 数字を写す作業なので、ぶれさせない
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }

    used = model
    res = _post(f"{API_ROOT}/models/{used}:generateContent", body, timeout, api_key)

    # 既定のモデルが無い場合は、使えるモデルを見て選び直す
    if res.status_code in (404, 400) and "model" in res.text.lower():
        available = list_models(api_key)
        alt = _pick_model(available)
        if not alt:
            raise RuntimeError("使えるモデルが見つからない。一覧: " + ", ".join(available[:20]))
        print(f"      モデル {used} が使えないので {alt} に切り替える")
        used = alt
        res = _post(f"{API_ROOT}/models/{used}:generateContent", body, timeout, api_key)

    # 「取りに行く道具」と「決まった形での返答」を同時に使えない版のための逃げ道。
    # 形の指定を外し、本文からJSONを拾う。
    if res.status_code == 400 and ("responseSchema" in res.text or "response_schema" in res.text
                                   or "tool" in res.text.lower()):
        print("      形の指定と取得の道具を同時に使えないので、本文からJSONを拾う形に切り替える")
        body["generationConfig"].pop("responseSchema", None)
        body["generationConfig"].pop("responseMimeType", None)
        body["contents"][0]["parts"][0]["text"] = (
            PROMPT + "\n\n返事はJSONだけにしてください。説明文を付けないでください。"
        )
        res = _post(f"{API_ROOT}/models/{used}:generateContent", body, timeout, api_key)

    if not res.ok:
        raise RuntimeError(f"Gemini API がエラーを返した（{res.status_code}）: {res.text[:400]}")

    data = res.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"返事の形が想定と違う: {json.dumps(data)[:400]}") from e

    parsed = _extract_json(text)
    return _to_stats(parsed, model=used)


def _to_stats(parsed: dict, *, model: str) -> dict:
    import unicodedata

    def norm(s):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))

    teams = []
    for t in parsed.get("teams", []):
        players = []
        for p in t.get("players", []):
            player = {"name": norm(p.get("name")), "team": t.get("name", ""), "raw": {}}
            for key in ("games", "points", "rank1", "rank2", "rank3", "rank4", "avg_rank"):
                player[key] = p.get(key)
            player["hands"] = None
            players.append(player)
        teams.append({"name": t.get("name", ""), "id": norm(t.get("name")), "players": players})

    return {
        "source_url": STATS_URL,
        "source": "gemini",
        "via_model": model,
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "season_label": parsed.get("season_label"),
        "teams": teams,
    }
