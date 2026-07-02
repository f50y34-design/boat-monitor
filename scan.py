#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手堅いレース監視 本体。

通常実行(GitHub Actionsから):
    python scan.py
ローカル確認(メール送らず標準出力に出すだけ):
    python scan.py --dry-run
特定レースのパース結果だけ見たい(セレクタ検証用):
    python scan.py --debug 24 12        # jcd=大村, rno=12R
日付を指定:
    python scan.py --hd 20260629
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta, timezone

import config
import fetcher
import parsers
import filters
import notify_email

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scan")

JST = timezone(timedelta(hours=config.JST_OFFSET_HOURS))
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")


# ── 状態(重複通知防止) ──
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("notified", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(notified):
    # 当日分だけ残す(肥大化防止)
    today = datetime.now(JST).strftime("%Y%m%d")
    kept = [k for k in notified if k.startswith(today)]
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"notified": sorted(kept)}, f, ensure_ascii=False, indent=2)


def minutes_to(deadline_hhmm, now):
    if not deadline_hhmm:
        return None
    try:
        h, m = map(int, deadline_hhmm.split(":"))
    except ValueError:
        return None
    dl = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return (dl - now).total_seconds() / 60.0


# ── 1レースの評価 ──
def assess_race(jcd, hd, race, phase):
    rno = race["rno"]
    ok, reason, other_a1 = filters.first_filter_from_grades(race["grades"])
    if not ok:
        return None  # 一次で落ちたものは静かに無視

    cand = {
        "jcd": jcd, "hd": hd, "rno": rno, "phase": phase,
        "deadline": race["deadline"], "grades": race["grades"],
        "other_a1_lanes": other_a1,
    }

    # 出走表(モーター/ST)
    html = fetcher.get("racelist", rno=rno, jcd=jcd, hd=hd)
    cand["lane1"] = parsers.parse_racelist_lane1(html) if html else {}

    if phase == "直前":
        bhtml = fetcher.get("beforeinfo", rno=rno, jcd=jcd, hd=hd)
        cand["before"] = parsers.parse_beforeinfo(bhtml) if bhtml else {}
        ohtml = fetcher.get("oddstf", rno=rno, jcd=jcd, hd=hd)
        cand["win_odds"] = parsers.parse_win_odds_lane1(ohtml) if ohtml else None

    return filters.evaluate(cand)


# ── 通知文面 ──
VERDICT_MARK = {"BUY": "🟢買い候補", "CAUTION": "🟡要確認", "SKIP": "⚪見送り"}


def format_message(cands):
    lines = []
    head = "🚤 手堅いレース通知" 
    lines.append(head)
    for c in cands:
        venue = config.INSIDE_STRONG_VENUES.get(c["jcd"], (str(c["jcd"]), "?"))[0]
        l1 = c.get("lane1") or {}
        name = l1.get("name") or "1号艇"
        lines.append("")
        lines.append(f"━━ {venue} {c['rno']}R ({c['phase']}) 締切{c['deadline']}")
        lines.append(f"{VERDICT_MARK.get(c['verdict'], c['verdict'])} ｜ 軸: {name}(1号艇A1)")
        lines.append(f"買い方: {c['buy_style']}")
        for fl in c["flags"]:
            lines.append(f"  {fl}")

        # ── 直前の生データ(そのままAIに貼って買い目を詰められる形) ──
        before = c.get("before") or {}
        if before.get("ready"):
            lines.append("  --- 直前データ ---")
            lines.append(f"  級別(1-6枠): {'/'.join(c.get('grades', []))}")
            mo = l1.get("motor_2rate"); st_avg = l1.get("avg_st"); lo = l1.get("local_2rate")
            lines.append("  軸: モーター{} / 平均ST{} / 当地2連率{}".format(
                f"{mo:.1f}%" if mo is not None else "?",
                f"{st_avg:.2f}" if st_avg is not None else "?",
                f"{lo:.1f}%" if lo is not None else "?"))
            stc = before.get("st_by_course", {})
            if stc:
                lines.append("  スタ展ST(コース順): " + " ".join(
                    f"{k}={stc[k]:.2f}" for k in sorted(stc)))
            etl = before.get("exhibit_time_by_lane", {})
            if etl:
                lines.append("  展示タイム(枠順): " + " ".join(
                    f"{k}={etl[k]:.2f}" for k in sorted(etl)))
            til = before.get("tilt_by_lane", {})
            if til:
                nonzero = {k: v for k, v in til.items() if v != 0.0}
                if nonzero:
                    lines.append("  チルト上げ/下げ: " + " ".join(
                        f"{k}={nonzero[k]:+.1f}" for k in sorted(nonzero)))
            w = before.get("weather", {})
            if w:
                lines.append("  水面: 風{}m / 波{}cm".format(
                    w.get("wind_ms", "?"), w.get("wave_cm", "?")))
            wo = c.get("win_odds")
            if wo is not None:
                lines.append(f"  1号艇単勝オッズ: {wo:.1f}倍")
            lines.append("  (↑この直前データを丸ごとAIに貼れば買い目を詰められます)")
    lines.append("")
    lines.append("※展示・潮・最終オッズは必ず自分の目で最終確認。余裕資金の範囲で。")
    return "\n".join(lines)


def format_daily_report(cands, hd):
    """朝イチで送る「本日の候補一覧」。締切時刻つきで1日の予定が分かる。"""
    lines = [f"🚤 本日の手堅い候補一覧 ({hd[:4]}/{hd[4:6]}/{hd[6:]})"]
    if not cands:
        lines.append("")
        lines.append("本日は条件(1号艇A1・2/3コースにA1なし・機力OK)を満たすレースがありません。")
        lines.append("無理に買わないのが正解の日です。")
    else:
        lines.append(f"候補 {len(cands)}件。締切の前に直前情報(展示・オッズ)の確認を。")
        for c in cands:
            venue = config.INSIDE_STRONG_VENUES.get(c["jcd"], (str(c["jcd"]), "?"))[0]
            l1 = c.get("lane1") or {}
            name = l1.get("name") or "1号艇"
            motor = l1.get("motor_2rate")
            mtxt = f"モーター{motor:.1f}%" if motor is not None else "モーター取得不可"
            lines.append("")
            lines.append(f"◆ {venue} {c['rno']}R 締切{c['deadline']} ｜ 軸:{name} ({mtxt})")
            lines.append(f"   {VERDICT_MARK.get(c['verdict'], c['verdict'])} / {c['buy_style']}")
    lines.append("")
    lines.append("※直前(展示ST・凪・オッズ)で最終判断。締切20分前頃に再通知します。")
    return "\n".join(lines)


def run(hd, dry_run):
    now = datetime.now(JST)
    notified = load_state()
    to_notify = []          # 直前(締切窓)の個別通知
    report_items = []       # 本日の候補一覧(1日1回)
    report_key = f"{hd}-dailyreport"
    do_report = report_key not in notified

    for jcd in config.INSIDE_STRONG_VENUES:
        html = fetcher.get("raceindex", jcd=jcd, hd=hd)
        if not html:
            continue
        races = parsers.parse_raceindex(html)
        if not races:
            continue  # その場は本日非開催
        for race in races:
            mins = minutes_to(race["deadline"], now)
            if mins is None or mins < config.CHOKUZEN_WINDOW_MIN[0]:
                continue  # 締切過ぎ or 不明

            in_window = mins <= config.CHOKUZEN_WINDOW_MIN[1]
            phase = "直前" if in_window else "事前"

            # 直前窓の個別通知は重複防止キーで管理
            key = f"{hd}-{jcd}-{race['rno']}-直前"
            need_chokuzen = in_window and key not in notified
            if not need_chokuzen and not do_report:
                continue  # このレースについて今やることが無い

            cand = assess_race(jcd, hd, race, phase)
            if cand is None:
                continue  # 一次フィルタ落ち

            # ── 本日の候補一覧(事前情報のみで判定した見込み) ──
            if do_report and cand["verdict"] in ("BUY", "CAUTION"):
                report_items.append(cand)

            # ── 締切窓に入ったレースの個別通知(直前情報込みの最終判定) ──
            if need_chokuzen:
                if cand["verdict"] in ("BUY", "CAUTION", "SKIP"):
                    # SKIPも「見送り推奨」として一報(直前で崩れた場合に分かるように)
                    to_notify.append(cand)
                    notified.add(key)

    # ── 送信 ──
    if do_report:
        report_items.sort(key=lambda c: (c["deadline"] or "99:99"))
        rep = format_daily_report(report_items, hd)
        if dry_run:
            print(rep)
        else:
            notify_email.send(rep, subject="🚤 本日の手堅い候補一覧")
            notified.add(report_key)
        log.info("本日の候補一覧: %d件", len(report_items))

    if to_notify:
        order = {"BUY": 0, "CAUTION": 1, "SKIP": 2}
        to_notify.sort(key=lambda c: order.get(c["verdict"], 9))
        msg = format_message(to_notify)
        if dry_run:
            print(msg)
        else:
            notify_email.send(msg)
        log.info("%d件 直前通知", len(to_notify))
    else:
        log.info("直前通知なし")

    if not dry_run:
        save_state(notified)


def debug(jcd, rno, hd):
    print(f"=== DEBUG jcd={jcd} rno={rno} hd={hd} ===")
    idx = fetcher.get("raceindex", jcd=jcd, hd=hd)
    print("[raceindex]", parsers.parse_raceindex(idx) if idx else "取得失敗")
    rl = fetcher.get("racelist", rno=rno, jcd=jcd, hd=hd)
    if rl:
        print("[racelist lane1]", parsers.parse_racelist_lane1(rl))
        # 解析失敗時の手掛かり: 選手リンク数と1号艇ブロックのテキスト断片を出す
        from bs4 import BeautifulSoup
        s = BeautifulSoup(rl, "html.parser")
        links = [a for a in s.select('a[href*="racersearch/profile"]')
                 if 'toban=' in a.get("href", "")]
        print("  [diag] 選手リンク(profile?toban)数 =", len(links), "(6が正常)")
        if links:
            blk = links[0].find_parent("tbody") or links[0].find_parent("tr") or links[0].parent
            snippet = blk.get_text(" ", strip=True)[:240]
            print("  [diag] 1号艇ブロック先頭240字 =", snippet)
    else:
        print("[racelist lane1] 取得失敗")
    bi = fetcher.get("beforeinfo", rno=rno, jcd=jcd, hd=hd)
    if bi:
        print("[beforeinfo]", parsers.parse_beforeinfo(bi))
        # 展示ST(st_by_course)が空の時の手掛かり: スタート展示まわりの構造を出す
        from bs4 import BeautifulSoup
        s = BeautifulSoup(bi, "html.parser")
        full = s.get_text(" ", strip=True)
        idx = full.find("スタート展示")
        print("  [diag] 'スタート展示'の位置 =", idx)
        if idx >= 0:
            print("  [diag] 周辺テキスト =", full[idx:idx + 200])
            print("  [diag] 展示タイム/チルト帯(先頭300字) =", full[max(0, idx - 320):idx])
        # F付きST/小数STのトークンを拾って並びを確認
        import re as _re
        toks = _re.findall(r"F?\.?\d{2}(?!\d)", full)
        print("  [diag] ST候補トークン(先頭20) =", toks[:20])
    else:
        print("[beforeinfo] 取得失敗")
    of = fetcher.get("oddstf", rno=rno, jcd=jcd, hd=hd)
    print("[win_odds lane1]", parsers.parse_win_odds_lane1(of) if of else "取得失敗")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hd", default=datetime.now(JST).strftime("%Y%m%d"),
                    help="対象日 YYYYMMDD(既定: 今日JST)")
    ap.add_argument("--dry-run", action="store_true", help="メール送信せず標準出力のみ")
    ap.add_argument("--debug", nargs=2, metavar=("JCD", "RNO"), type=int,
                    help="指定レースのパース結果を表示")
    args = ap.parse_args()

    if args.debug:
        debug(args.debug[0], args.debug[1], args.hd)
    else:
        run(args.hd, args.dry_run)


if __name__ == "__main__":
    main()
