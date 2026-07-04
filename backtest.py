#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
過去データバックテスト。
公式配布の 番組表(Bファイル) と 競走成績(Kファイル) を期間分ダウンロードし、
「手堅い条件」に合致したレースについて、買い方ごとの 的中率・回収率 を集計する。

使い方(GitHub Actions想定):
    python backtest.py --start 20260401 --end 20260630

必要: lhasa (LZH解凍。workflowで apt-get install lhasa 済みであること)
"""
import os
import re
import sys
import time
import argparse
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timedelta
from collections import defaultdict

import requests

BASE = "https://www1.mbrace.or.jp/od2"
UA = {"User-Agent": "Mozilla/5.0 (backtest; personal research; low-rate)"}
SLEEP = 0.6  # ダウンロード間隔(サーバ配慮)

INSIDE_STRONG = {18, 24, 19, 8, 12, 21, 7, 13, 16, 22, 15, 20}  # config.pyと同じ+若松


# ───────────────────────── ダウンロード & 解凍 ─────────────────────────
def fetch_txt(kind, ymd):
    """kind='B'|'K', ymd='YYYYMMDD' → テキスト(cp932) or None"""
    yymm = ymd[2:6]
    url = f"{BASE}/{kind.upper()}/{ymd[:6]}/{kind.lower()}{ymd[2:]}.lzh"
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200 or len(r.content) < 100:
        return None
    with tempfile.TemporaryDirectory() as td:
        lzh = os.path.join(td, "a.lzh")
        with open(lzh, "wb") as f:
            f.write(r.content)
        try:
            subprocess.run(["lhasa", "xq", lzh], cwd=td, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        for name in os.listdir(td):
            if name.lower().endswith(".txt"):
                with open(os.path.join(td, name), "rb") as f:
                    return f.read().decode("cp932", errors="replace")
    return None


def z2h(s):
    """全角→半角正規化(数字・英字・記号・スペース)"""
    return unicodedata.normalize("NFKC", s)


# ───────────────────────── Bファイル(番組表)パース ─────────────────────────
RACER_HEAD_RE = re.compile(r"^([1-6])\s+(\d{4})(.+?)([AB][12])(.*)$")
FLOAT2_RE = re.compile(r"\d{1,3}\.\d{2}")


def _parse_racer_line(line):
    """→ (lane, grade, local2, motor2) or None
    固定幅ファイルは桁が埋まるとスペースが消える(例: 50.00159100.00)ため、
    「小数2桁の数値」だけを出現順に抽出する方式でパースする。
    順序: [0]全国勝率 [1]全国2連率 [2]当地勝率 [3]当地2連率 [4]モーター2連率 [5]ボート2連率
    (モーターNo/ボートNoは整数なので抽出対象外)
    """
    m = RACER_HEAD_RE.match(line.strip())
    if not m:
        return None
    lane = int(m.group(1))
    grade = m.group(4)
    floats = [float(x) for x in FLOAT2_RE.findall(m.group(5))]
    if len(floats) < 6:
        return None
    local2, motor2 = floats[3], floats[4]
    # 健全性チェック(連結の誤吸収などで範囲外になったら不採用)
    if not (0.0 <= motor2 <= 100.0 and 0.0 <= local2 <= 100.0):
        return None
    return lane, grade, local2, motor2


def parse_b(text):
    """→ {(jcd, rno): {'grades':[6], 'motor2_1': float, 'local2_1': float}}"""
    out = {}
    if not text:
        return out
    text = z2h(text)
    jcd = None
    rno = None
    buf = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d{2})BBGN", line)
        if m:
            jcd = int(m.group(1))
            continue
        if re.match(r"\s*\d{2}BEND", line):
            jcd = None
            continue
        if jcd is None:
            continue
        mr = re.match(r"\s*(\d{1,2})R\b", line)
        if mr:
            rno = int(mr.group(1))
            buf = {}
            continue
        if rno is None:
            continue
        parsed = _parse_racer_line(line)
        if parsed:
            lane, grade, local2, motor2 = parsed
            buf[lane] = {"grade": grade, "local2": local2, "motor2": motor2}
            if len(buf) == 6:
                out[(jcd, rno)] = {
                    "grades": [buf[i]["grade"] for i in range(1, 7)],
                    "motor2_1": buf[1]["motor2"],
                    "local2_1": buf[1]["local2"],
                }
                rno = None
    return out


# ───────────────────────── Kファイル(結果)パース ─────────────────────────
def parse_k(text):
    """→ {(jcd, rno): {'order':[1着艇,2着艇,3着艇], 'pay':{key:amount}}}
       pay keys: 'tan'(単勝1号艇のみ), '2t'(2連単), '3t'(3連単), '3f'(3連複) → {組: 金額}
    """
    out = {}
    if not text:
        return out
    text = z2h(text)
    jcd = None
    rno = None
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*(\d{2})KBGN", line)
        if m:
            jcd = int(m.group(1))
            continue
        if re.match(r"\s*\d{2}KEND", line):
            jcd = None
            continue
        if jcd is None:
            continue
        mr = re.match(r"\s*(\d{1,2})R\b", line)
        if mr:
            rno = int(mr.group(1))
            cur = {"order": [], "pay": {}}
            out[(jcd, rno)] = cur
            continue
        if cur is None:
            continue
        # 着順行: " 01  1 4239 ..." (着順2桁 艇番)
        mo = re.match(r"\s*0([1-6])\s+([1-6])\s+\d{4}", line)
        if mo and len(cur["order"]) < 3:
            rank = int(mo.group(1))
            boat = int(mo.group(2))
            if rank == len(cur["order"]) + 1:
                cur["order"].append(boat)
            continue
        # 払戻行
        mp = re.search(r"単勝\s+([1-6])\s+(\d+)", line)
        if mp:
            cur["pay"][f"tan-{mp.group(1)}"] = int(mp.group(2))
        mp = re.search(r"2連単\s+([1-6])-([1-6])\s+(\d+)", line)
        if mp:
            cur["pay"][f"2t-{mp.group(1)}-{mp.group(2)}"] = int(mp.group(3))
        mp = re.search(r"3連単\s+([1-6])-([1-6])-([1-6])\s+(\d+)", line)
        if mp:
            cur["pay"][f"3t-{mp.group(1)}-{mp.group(2)}-{mp.group(3)}"] = int(mp.group(3) if mp.lastindex == 3 else mp.group(4))
        mp = re.search(r"3連複\s+([1-6])-([1-6])-([1-6])\s+(\d+)", line)
        if mp:
            g = mp.groups()
            cur["pay"]["3f-" + "-".join(sorted(g[:3]))] = int(g[3])
    return out


# ───────────────────────── 戦略定義(1点=100円) ─────────────────────────
def strat_results(order, pay):
    """各戦略の (的中?, 投資, 払戻) を返す。orderは[1着,2着,3着]"""
    res = {}
    if len(order) < 3:
        return res
    top2 = set(order[:2])
    o1, o2, o3 = order[0], order[1], order[2]

    def pay3f(a, b, c):
        return pay.get("3f-" + "-".join(sorted([str(a), str(b), str(c)])), 0)

    # S1 単勝1号艇(1点)
    hit = o1 == 1
    res["S1 単勝1号艇"] = (hit, 100, pay.get("tan-1", 0) if hit else 0)
    # S2 2連単1-2(1点)
    hit = (o1, o2) == (1, 2)
    res["S2 2連単1-2"] = (hit, 100, pay.get("2t-1-2", 0) if hit else 0)
    # S3 2連単1→2,3,4(3点)
    hit = o1 == 1 and o2 in (2, 3, 4)
    res["S3 2連単1→234"] = (hit, 300, pay.get(f"2t-1-{o2}", 0) if hit else 0)
    # S4 3連複1=2=3(1点)
    hit = {1, 2, 3} == set(order[:3])
    res["S4 3連複123"] = (hit, 100, pay3f(1, 2, 3) if hit else 0)
    # S5 3連複 1=(2,3,4)から2艇 …{123,124,134}(3点)
    hit = 1 in order[:3] and len({2, 3, 4} & set(order[:3])) >= 2
    amt = pay3f(*sorted(order[:3])) if hit else 0
    res["S5 3連複1=234(3点)"] = (hit, 300, amt)
    # S6 3連単1→2,3→2,3,4(4点: 1-2-3,1-2-4,1-3-2,1-3-4)
    hit = o1 == 1 and o2 in (2, 3) and o3 in (2, 3, 4) and o2 != o3
    res["S6 3連単1→23→234"] = (hit, 400, pay.get(f"3t-1-{o2}-{o3}", 0) if hit else 0)
    return res


# ───────────────────────── 集計 ─────────────────────────
def run(start, end, venues_filter):
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    agg = defaultdict(lambda: [0, 0, 0, 0])  # key→[レース数,的中数,投資,払戻]
    seg_agg = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    days = 0
    matched = 0

    d = d0
    while d <= d1:
        ymd = d.strftime("%Y%m%d")
        d += timedelta(days=1)
        btxt = fetch_txt("B", ymd)
        time.sleep(SLEEP)
        ktxt = fetch_txt("K", ymd)
        time.sleep(SLEEP)
        if not btxt or not ktxt:
            continue
        days += 1
        bd = parse_b(btxt)
        kd = parse_k(ktxt)
        for key, binfo in bd.items():
            jcd, rno = key
            if venues_filter and jcd not in venues_filter:
                continue
            grades = binfo["grades"]
            # ── 手堅い条件(scan.pyと同じ一次フィルタ) ──
            if grades[0] != "A1":
                continue
            if any(grades[i] == "A1" for i in (1, 2)):  # 2,3コースにA1
                continue
            motor = binfo["motor2_1"]
            if motor < 22.0:
                continue
            k = kd.get(key)
            if not k or len(k["order"]) < 3:
                continue
            matched += 1
            seg = "機力35+" if motor >= 35.0 else "機力22-35"
            for name, (hit, cost, ret) in strat_results(k["order"], k["pay"]).items():
                a = agg[name]
                a[0] += 1; a[1] += int(hit); a[2] += cost; a[3] += ret
                s = seg_agg[seg][name]
                s[0] += 1; s[1] += int(hit); s[2] += cost; s[3] += ret

    # ── レポート ──
    print(f"=== バックテスト {start}〜{end} ===")
    print(f"取得できた日数: {days} / 条件合致レース: {matched}")
    if matched == 0:
        print("合致レースなし(取得失敗 or 条件が厳しすぎ)。ログのWARNINGを確認。")
        return

    def report(title, table):
        print(f"\n--- {title} ---")
        print(f"{'戦略':<20} {'N':>5} {'的中率':>7} {'回収率':>7}")
        for name in sorted(table):
            n, h, cost, ret = table[name]
            if n == 0:
                continue
            print(f"{name:<20} {n:>5} {h/n*100:>6.1f}% {ret/cost*100 if cost else 0:>6.1f}%")

    report("全体(1号艇A1・2/3にA1なし・機力22%+)", agg)
    for seg in sorted(seg_agg):
        report(f"セグメント: {seg}", seg_agg[seg])
    print("\n※投資は1点100円換算。回収率100%超=プラス。")
    print("※的中率が高くても回収率が低い戦略は「当たるが儲からない」。両方を見ること。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--all-venues", action="store_true",
                    help="全24場を対象(既定はイン強い場+若松のみ)")
    a = ap.parse_args()
    run(a.start, a.end, None if a.all_venues else INSIDE_STRONG)


if __name__ == "__main__":
    main()
