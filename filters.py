# -*- coding: utf-8 -*-
"""
判定ロジック本体。チャットで詰めた戦略をここに集約。
parsers.py が取ってきた素データを受けて「候補か/見送りか/買い方」を決める。
"""
import config


def first_filter_from_grades(grades):
    """
    出走表の級別だけで一次判定(raceindexから取れる)。
    return (ok: bool, reason: str, other_a1_lanes: list)
    条件: 1号艇=A1、かつ2・3コースにA1がいない。
    """
    if len(grades) < 6:
        return False, "級別データ不足", []
    if grades[0] != "A1":
        return False, "1号艇がA1でない", []
    other_a1 = [i + 1 for i in range(1, 6) if grades[i] == "A1"]
    blockers = [ln for ln in other_a1 if ln in config.REJECT_A1_ON_LANES]
    if blockers:
        return False, f"{blockers}コースにA1(内をすぐ差せる強敵)", other_a1
    return True, "一次OK", other_a1


def evaluate(cand):
    """
    cand: dict を受け取り、評価を書き込んで返す。
    必要キー: grades, lane1(name/motor_2rate/avg_st), before(任意), win_odds(任意), other_a1_lanes
    付与キー: verdict(BUY/CAUTION/SKIP), flags(list), buy_style(str), score
    """
    flags = []
    lane1 = cand.get("lane1") or {}
    motor = lane1.get("motor_2rate")
    st = lane1.get("avg_st")

    # ── モーター(機力) ──
    buy_style = "1着固定(2連単/3連単フォーメーション)"
    if motor is None:
        flags.append("⚠モーター2連率を取得できず(要確認)")
    elif motor < config.MOTOR_2RATE_SKIP:
        cand["verdict"] = "SKIP"
        flags.append(f"✕モーター2連率{motor:.1f}%=軸として弱すぎ(郷原型)")
        cand["flags"] = flags
        cand["buy_style"] = "-"
        cand["score"] = 0
        return cand
    elif motor < config.MOTOR_2RATE_GOOD:
        flags.append(f"△モーター2連率{motor:.1f}%=やや弱→3連複モード推奨(原田型)")
        buy_style = "3連複(順番を当てにいかない)"
    else:
        flags.append(f"○モーター2連率{motor:.1f}%")

    # ── 平均ST ──
    if st is None:
        flags.append("⚠平均STを取得できず(要確認)")
    elif st > config.ST_AVG_MAX:
        flags.append(f"△平均ST{st:.2f}=やや遅い")
    else:
        flags.append(f"○平均ST{st:.2f}")

    # ── 他A1(外枠)の注意 ──
    outer_a1 = [ln for ln in cand.get("other_a1_lanes", []) if ln >= 4]
    if outer_a1:
        flags.append(f"△{outer_a1}コースにA1=まくり/差し注意(ヒモ必須)")

    # 基礎点(ここから加減点)
    score = 2

    # ── ① 当地成績(この水面が得意か) ──
    local = lane1.get("local_2rate")
    if local is not None:
        if local >= config.LOCAL_2RATE_GOOD:
            flags.append(f"◎当地2連率{local:.1f}%=この水面が得意")
            score += 1
        elif local < config.LOCAL_2RATE_POOR:
            flags.append(f"△当地2連率{local:.1f}%=この水面は苦手")
            score -= 1

    # ── 直前情報(あれば) ──
    before = cand.get("before") or {}
    if before.get("ready"):
        stc = before.get("st_by_course", {})
        if stc:
            best = min(stc.values())
            st1 = stc.get(1)
            if st1 is not None:
                if st1 <= best + config.EXHIBIT_ST_GAP_MAX:
                    flags.append(f"◎展示で1号艇スタ展{st1:.2f}=内で最速級")
                    score += 2
                else:
                    flags.append(f"△展示で1号艇スタ展{st1:.2f}(最速{best:.2f})=やや甘い")
                    score -= 1
        w = before.get("weather", {})
        wave = w.get("wave_cm")
        wind = w.get("wind_ms")
        if wave is not None and wind is not None:
            if wave <= config.CALM_WAVE_CM_MAX and wind <= config.CALM_WIND_MS_MAX:
                flags.append(f"◎ほぼ凪(波{wave:.0f}cm/風{wind:.0f}m)=イン有利")
                score += 1
            elif wave >= config.ROUGH_WAVE_CM or wind >= config.ROUGH_WIND_MS:
                flags.append(f"△水面荒れ気味(波{wave:.0f}cm/風{wind:.0f}m)=イン割引")
                score -= 1

        # ── ② 展示タイム(1号艇の足が出ているか) ──
        etl = before.get("exhibit_time_by_lane", {})
        if etl and 1 in etl:
            fastest = min(etl.values())
            slowest = max(etl.values())
            e1 = etl[1]
            if e1 <= fastest + config.EXHIBIT_TIME_GAP_MAX:
                flags.append(f"◎展示タイム{e1:.2f}=最速級(足あり)")
                score += 1
            elif e1 >= slowest:
                flags.append(f"△展示タイム{e1:.2f}=出走中最遅(足いまひとつ)")
                score -= 1

        # ── ③ チルト(外枠のまくり気配) ──
        til = before.get("tilt_by_lane", {})
        if til:
            outer_up = [ln for ln, v in til.items() if ln >= 4 and v >= config.TILT_MAKURI]
            if outer_up:
                flags.append(f"△{outer_up}コースがチルト上げ=まくり気配(ヒモ/一発に注意)")

        # ── ④ 風向(取れれば表示。向かい/追いの解釈は場ごとに要調整のため加点は保留) ──
        wdir = w.get("wind_dir")
        if wdir is not None:
            flags.append(f"ℹ風向={wdir}(向かい風=イン有利/追い風=まくり有利。判定は今後調整)")

    # ── value(単勝が安すぎないか) ──
    wo = cand.get("win_odds")
    if wo is not None:
        if wo < 1.3:
            flags.append(f"△1号艇単勝{wo:.1f}倍=人気被り・妙味薄(点数を絞るか見送り)")
            score -= 1
        else:
            flags.append(f"○1号艇単勝{wo:.1f}倍=妙味あり")

    # ── 総合判定 ──
    if score >= 4:
        cand["verdict"] = "BUY"
    elif score >= 2:
        cand["verdict"] = "CAUTION"
    else:
        cand["verdict"] = "SKIP"
    cand["flags"] = flags
    cand["buy_style"] = buy_style
    cand["score"] = score
    return cand
