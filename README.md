# 手堅いレース監視 (boatrace-tegatai-monitor)

ボートレース全国24場のうち**イン信頼度が高い場**を巡回し、我々が詰めた
「手堅い × value」の条件に合うレースだけを **メールで通知**する。
阿波踊りホテル監視と同じ **GitHub Actions + Python** 構成。毎日勝手に回る。

## これがやること

1. イン強い場(徳山・大村・下関・常滑・住之江ほか/`config.py`)の本日の番組を取得
2. 一次フィルタ：**1号艇A1 かつ 2・3コースにA1がいない**形だけ抽出
3. 出走表で **1号艇のモーター2連率・平均ST** を確認
4. 締切が近づいたら直前情報で **展示ST・凪/荒れ・単勝オッズ(妙味)** を判定
5. 判定結果(🟢買い候補 / 🟡要確認 / ⚪見送り)を理由つきでメール通知

判定ロジックには我々の教訓が入っている：
- **軸のモーターが平凡(<35%)なら「3連複モード」**を推奨(大村10R原田の反省)
- **モーターが弱すぎ(<22%)なら除外**(大村12R郷原型)
- 凪ならイン加点、荒れたら割引／**単勝が安すぎたら「妙味薄」警告**

## セットアップ

### 1. リポジトリを用意
このフォルダをそのままGitHubに上げる(private推奨)。

### 2. メール通知の準備（Gmailのアプリパスワード）
LINEのような公式アカウント作成は不要。必要なのは「アプリパスワード16桁」1個だけ。
1. Googleアカウントの **2段階認証をオン**にする
   （[セキュリティ設定](https://myaccount.google.com/security) →「2段階認証プロセス」）
2. [アプリパスワード作成ページ](https://myaccount.google.com/apppasswords) を開く
3. 名前に `boatrace` など入力して **「作成」** → 表示される **16桁** をコピー（→ `GMAIL_APP_PASSWORD`）
   - ※この16桁は再表示されないので必ず控える。通常のGoogleパスワードでは送信できない。
4. 送信元の自分のGmailアドレス（→ `GMAIL_ADDRESS`）と、受信先アドレス（→ `MAIL_TO`、自分宛でOK）を決める

### 3. GitHubに secrets を登録
リポジトリ → Settings → Secrets and variables → Actions → New repository secret
- `GMAIL_ADDRESS`（送信元Gmail。例 you@gmail.com）
- `GMAIL_APP_PASSWORD`（手順2で作った16桁）
- `MAIL_TO`（受信先。自分宛でOK。GMAIL_ADDRESSと同じでも可）

### 4. パーサの一度きり検証（重要）
公式サイトのHTML構造は時々変わる。最初に1回だけ、自分の環境で出力を確認する：

```bash
pip install -r requirements.txt
python scan.py --debug 24 12      # jcd=大村, rno=12R（開催中の場/レースで）
```

`[racelist lane1]` の `motor_2rate` や `[beforeinfo]` の `st_by_course` が
正しく取れていればOK。ズレていたら **`parsers.py` の該当関数だけ**直せばよい
（戦略ロジック `filters.py` は無傷）。

### 5. 動作確認 → 自動運転
```bash
python scan.py --dry-run          # メール送らず標準出力に表示
```
問題なければ push。Actionsタブから手動実行(workflow_dispatch)で通知が来るか確認。
あとは `*/30 1-15 * * *`(UTC=JST 10:00〜24:00頃) で自動巡回する。

## 調整

`config.py` の数値を変えるだけ：
- `INSIDE_STRONG_VENUES` … 監視する場
- `MOTOR_2RATE_GOOD / SKIP` … 機力の合否ライン
- `ST_AVG_MAX` … 平均STの許容
- `CALM_*` / `ROUGH_*` … 凪・荒れの判定
- `MIN_FORMATION_ODDS` / `FAV_TRIFECTA_TOO_SHORT` … 妙味(value)の感度
- `CHOKUZEN_WINDOW_MIN` … 直前通知を出す締切前の分レンジ

## 構成

```
scan.py          メイン(巡回→判定→通知→重複防止)
config.py        場・しきい値(ここを触れば挙動が変わる)
fetcher.py       取得(リトライ・間隔・UA)
parsers.py       HTMLパース ★構造変化時はここだけ直す
filters.py       戦略ロジック(手堅い×value)
notify_email.py  メール通知(Gmail SMTP / アプリパスワード)
.github/workflows/scan.yml  スケジュール実行
state.json       重複通知防止(Actionsが自動更新)
```

## 注意

- 公式サイトへの巡回は `REQUEST_DELAY_SEC` で間隔を空けている。**短くしすぎない**こと
  （サイトへの配慮。利用規約・レート制限を尊重）。
- GitHub Actionsのスケジュールは混雑時に数分ずれる(仕様)。分刻みの精度は出ない。
- これは「手堅い候補を見つける」補助であって、**勝ちを保証しない**。
  最終判断（展示・潮・最終オッズ）は必ず自分の目で。舟券は余裕資金の範囲で。
- 還元率75%(控除25%)は不変。回数を絞り、勝った分は引き出すのが長期の鉄則。
