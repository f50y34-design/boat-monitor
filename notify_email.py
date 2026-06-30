# -*- coding: utf-8 -*-
"""
メール通知（Gmail SMTP / アプリパスワード方式）。
必要な環境変数:
  GMAIL_ADDRESS       … 送信元のGmailアドレス(例: you@gmail.com)
  GMAIL_APP_PASSWORD  … 2段階認証をオンにして発行した「アプリパスワード」16桁
  MAIL_TO             … 受信先アドレス(自分宛でOK。GMAIL_ADDRESSと同じでも可)
"""
import os
import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.utils import formatdate

log = logging.getLogger("mail")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send(body, subject="🚤 手堅いレース通知"):
    sender = os.environ.get("GMAIL_ADDRESS")
    app_pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or sender

    if not sender or not app_pw:
        log.error("GMAIL_ADDRESS / GMAIL_APP_PASSWORD 未設定。送信スキップ。")
        print(body)  # ローカル確認用に標準出力にも出す
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(sender, app_pw)
            server.send_message(msg)
        log.info("メール送信OK → %s", to)
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("認証失敗。アプリパスワード(16桁)を確認。通常のGoogleパスワードでは不可。")
    except Exception as e:
        log.error("メール送信エラー: %s", e)
    return False
