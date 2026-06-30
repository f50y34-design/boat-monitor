# -*- coding: utf-8 -*-
"""
boatrace.jp の各ページHTMLをパースする。

★重要★ ここのCSSセレクタ/正規表現は、公開時点で判明している
公式サイトの構造に合わせて書いています。公式サイトのHTMLは時々変わるので、
最初に一度 `python scan.py --debug 24 12`(jcd rno) を実行して、
出力が正しく取れているかだけ確認してください。ズレていたら、このファイルの
該当関数だけ直せば全体が動きます(ロジック本体はfilters.pyにあるので無傷)。
"""
import re
import logging
import warnings
from bs4 import BeautifulSoup

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    pass

log = logging.getLogger("parsers")

GRADE_RE = re.compile(r"\b([AB][12])\b")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
FLOAT_RE = re.compile(r"-?\d+\.\d+")
INT_RE = re.compile(r"\d+")


def _soup(html):
    # ★html.parserを使う★ lxmlだとboatraceのページをXMLと誤判定し、
    # class指定のセレクタ(table.is-w748等)が空振りしてNoneになる事故が起きる。
    # 標準のhtml.parserはHTMLとして安定して扱える(警告も出ない)。
    return BeautifulSoup(html, "html.parser")


# ─────────────────────────────────────────────────────────
# raceindex: その場の全レースの「締切時刻」と「6艇の級別」を取る
#   → これだけで一次フィルタ(1号艇A1・他にA1の位置)が判定できる
# ─────────────────────────────────────────────────────────
def parse_raceindex(html):
    """return: list of dict {rno, deadline(HH:MM), grades:[g1..g6]}"""
    soup = _soup(html)
    races = []
    seen = set()
    # racelist?rno=N へのリンクを持つ行を起点にする(構造変化に強い)
    for a in soup.select('a[href*="racelist?rno="]'):
        m = re.search(r"rno=(\d+)", a.get("href", ""))
        if not m:
            continue
        rno = int(m.group(1))
        if rno in seen:
            continue
        # その行(tr)or 近傍のまとまりを取る
        row = a.find_parent("tr")
        if row is None:
            continue
        text = row.get_text(" ", strip=True)
        grades = GRADE_RE.findall(text)
        if len(grades) < 6:
            # 行に6艇分の級別が無い=ヘッダ等。スキップ
            continue
        tmatch = TIME_RE.search(text)
        deadline = tmatch.group(1) if tmatch else None
        races.append({"rno": rno, "deadline": deadline, "grades": grades[:6]})
        seen.add(rno)
    races.sort(key=lambda r: r["rno"])
    return races


# ─────────────────────────────────────────────────────────
# racelist(出走表): 1号艇のモーター2連率・平均ST等を取る
# ─────────────────────────────────────────────────────────
# 小数2桁の数値(例: 0.11 / 53.85)を抜き出す。体重(52.3=1桁)や整数は拾わない。
DEC2_RE = re.compile(r"\d+\.\d{2}")


def parse_racelist_lane1(html):
    """
    return dict {
        name, grade, motor_2rate, avg_st, local_2rate, national_2rate
    }  失敗時は取れた範囲だけ + None

    ★列構造(出走表で確認済み・1艇分の出現順)★
      … 年齢/体重(52.3=1桁,対象外) →
      平均ST(0.11) →
      全国[勝率, 2連率, 3連率] → 当地[勝率, 2連率, 3連率] →
      モーター[2連率, 3連率] → ボート[2連率, 3連率] → 今節成績(以降)
    小数2桁の値だけを出現順に拾うと、先頭から:
      [0]平均ST [1]全国勝率 [2]全国2連 [3]全国3連
      [4]当地勝率 [5]当地2連 [6]当地3連
      [7]モーター2連 [8]モーター3連 [9]ボート2連 [10]ボート3連
    （モーターNo/ボートNoは整数なので自動的に除外される。今節成績のSTは[11]以降
      なので、先頭11個だけ使えば混ざらない。）
    """
    soup = _soup(html)
    out = {"name": None, "grade": None, "motor_2rate": None,
           "avg_st": None, "local_2rate": None, "national_2rate": None}

    # ★1号艇ブロックの特定★ クラス名に依存せず、選手プロフィールへのリンクを起点にする
    # (raceindexで実証済みの堅い方式)。最初のracersearchリンク = 1号艇。
    links = soup.select('a[href*="racersearch/profile"], a[href*="racersearch"]')
    if not links:
        log.warning("racelist: 選手リンクが見つからない(構造変化)。--debugで確認")
        return out

    first = links[0]
    # リンクを内包する一番近いまとまり(tbody優先、無ければtr、最後はparent)を1艇分とみなす
    body = first.find_parent("tbody") or first.find_parent("tr") or first.parent
    block_text = body.get_text(" ", strip=True)

    out["name"] = first.get_text(strip=True) or None
    g = GRADE_RE.search(block_text)
    if g:
        out["grade"] = g.group(1)

    # 1号艇ブロックの出現順で小数2桁を収集。今節成績STの混入を避けるため先頭11個に限定。
    dec = [float(x) for x in DEC2_RE.findall(block_text)][:11]

    def at(i):
        return dec[i] if i < len(dec) else None

    st = at(0)
    # 健全性チェック: 平均STは0.05〜0.30程度。範囲外なら構造ズレの可能性を警告。
    if st is not None and not (0.0 < st < 0.5):
        log.warning("racelist: 平均STの推定値が異常(%.2f)。--debugで列順を確認", st)
    out["avg_st"] = st
    out["national_2rate"] = at(2)
    out["local_2rate"] = at(5)
    out["motor_2rate"] = at(7)
    return out


# ─────────────────────────────────────────────────────────
# beforeinfo(直前情報): スタ展ST(コース順)・展示タイム・気象
# ─────────────────────────────────────────────────────────
def parse_beforeinfo(html):
    """
    return dict {
        ready(bool),                # 直前情報が出ているか
        st_by_course: {1..6: float},# スタート展示ST(コース番号→ST)
        weather: {wind_ms, wave_cm, weather_text}
    }
    """
    soup = _soup(html)
    out = {"ready": False, "st_by_course": {}, "weather": {}}
    text = soup.get_text(" ", strip=True)

    # 気象
    w = {}
    mwind = re.search(r"風速\s*(\d+(?:\.\d+)?)\s*m", text)
    if mwind:
        w["wind_ms"] = float(mwind.group(1))
    mwave = re.search(r"波高\s*(\d+(?:\.\d+)?)\s*cm", text)
    if mwave:
        w["wave_cm"] = float(mwave.group(1))
    out["weather"] = w

    # スタート展示ST: 「スタート展示」セクション配下の .table1 等。
    # 各行に コース番号 と ST(.07 / F.02 等)が並ぶ。
    st_table = None
    for h in soup.find_all(string=re.compile("スタート展示")):
        cont = h.find_parent()
        if cont:
            st_table = cont.find_next("table")
            if st_table:
                break
    if st_table:
        rows = st_table.select("tbody tr") or st_table.select("tr")
        course = 0
        for tr in rows:
            cells = [c.get_text(" ", strip=True) for c in tr.select("td")]
            joined = " ".join(cells)
            stm = re.search(r"(F?)\.?(\d\.\d{2}|\d{2})", joined)
            # ST表記は ".07" や "F.02" 形式。0.07 に正規化。
            stm2 = re.search(r"F?\s*\.?(\d{1,2})(?!\d)", joined)
            val = _parse_st(joined)
            if val is not None:
                course += 1
                if 1 <= course <= 6:
                    out["st_by_course"][course] = val
    out["ready"] = bool(out["st_by_course"]) or bool(w)
    return out


def _parse_st(s):
    # ".07" / "0.07" / "F.02"(フライング) → 0.07 / 0.02。Fも数値は拾う。
    m = re.search(r"F?\s*0?\.(\d{2})", s)
    if m:
        return round(int(m.group(1)) / 100.0, 2)
    return None


# ─────────────────────────────────────────────────────────
# oddstf(単勝・複勝): 1号艇の単勝オッズ(value判定用)
# ─────────────────────────────────────────────────────────
def parse_win_odds_lane1(html):
    """
    return float(1号艇単勝オッズ) or None

    ★暫定★ オッズ表の構造を実HTMLで未確認のため、確実に1号艇の単勝と
    特定できない限り None を返す(誤った値で評価を狂わせない方が安全)。
    value判定はオッズが取れた時だけ加味され、Noneならスキップされる。
    実HTMLを --debug で確認後、ここを確実な抽出に差し替える。
    """
    soup = _soup(html)
    # 「単勝」見出しの直近テーブルの先頭行を1号艇とみなして試行
    for label in soup.find_all(string=re.compile("単勝")):
        tbl = label.find_parent()
        tbl = tbl.find_next("table") if tbl else None
        if not tbl:
            continue
        row = tbl.select_one("tbody tr") or tbl.select_one("tr")
        if not row:
            continue
        for td in row.select("td"):
            m = FLOAT_RE.search(td.get_text(" ", strip=True))
            if m:
                v = float(m.group())
                # 単勝の妥当域。1.0ちょうど等の不審値は採用しない。
                if 1.05 <= v <= 100.0:
                    return v
    return None
