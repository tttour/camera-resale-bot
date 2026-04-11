"""
通知モジュール
利益が出る商品を発見したときにデスクトップ通知またはメールを送る
"""
import logging
import subprocess
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Optional

logger = logging.getLogger(__name__)


def send_email(config: dict, subject: str, body: str):
    """
    SMTPを使用してメールを送信
    """
    import os
    email_conf = config.get("notification", {}).get("email_settings", {})
    
    # 環境変数（GitHub Actions用）を優先し、なければconfigから読み込む
    smtp_server = email_conf.get("smtp_server", "smtp.gmail.com")
    smtp_port = email_conf.get("smtp_port", 587)
    sender_email = os.environ.get("SENDER_EMAIL") or email_conf.get("sender_email")
    sender_password = os.environ.get("SENDER_PASSWORD") or email_conf.get("sender_password")
    receiver_email = os.environ.get("RECEIVER_EMAIL") or email_conf.get("receiver_email")

    if not all([smtp_server, smtp_port, sender_email, sender_password, receiver_email]):
        logger.error("メール設定が不十分です。通知をスキップします。")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # TLS暗号化
            server.login(sender_email, sender_password)
            server.send_message(msg)
            logger.info(f"メール送信完了: {subject}")
    except Exception as e:
        logger.error(f"メール送信失敗: {e}")


def notify_windows_toast(title: str, message: str):
    """
    Windowsトースト通知を送信（PowerShell経由）
    """
    # メッセージ内のクォートをエスケープ
    safe_title = title.replace("'", "''").replace('"', '`"')
    safe_msg = message[:200].replace("'", "''").replace('"', '`"')

    ps_script = f"""
$xml = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{safe_title}</text>
      <text>{safe_msg}</text>
    </binding>
  </visual>
</toast>
"@
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDoc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xmlDoc
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Camera Bot")
$notifier.Show($toast)
"""
    try:
        subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True,
            timeout=10,
        )
        logger.info(f"トースト通知送信: {title}")
    except Exception as e:
        logger.error(f"トースト通知失敗: {e}")


def notify_opportunity(config: dict, result: Any) -> None:
    """
    利益機会を通知する
    result: ProfitResult オブジェクト
    """
    model = result.kitamura_product.model_number or result.kitamura_product.name[:20]
    condition = result.kitamura_product.condition

    title = f"📸 転売チャンス！ {model}"
    message = (
        f"【{condition}】\n"
        f"購入: ¥{result.buy_price:,} → 売却想定: ¥{result.estimated_sell_price:,}\n"
        f"純利益: ¥{result.net_profit:,} ({result.profit_rate*100:.1f}%)\n"
        f"メルカリ n={result.mercari_sample_count}\n"
        f"キタムラURL: {result.kitamura_product.product_url}\n"
        f"メルカリURL: {result.mercari_url}"
    )

    method = config.get("notification", {}).get("method", "windows_toast")
    
    if method == "email":
        logger.info(f"利益通知(メール送信中): {model}")
        send_email(config, title, message)
    else:
        logger.info(f"利益通知(トースト送信中): {model}")
        notify_windows_toast(title, message)


def notify_run_complete(config: dict, found_count: int, checked_count: int):
    """スキャン完了通知"""
    title = "カメラ転売ボット - スキャン完了"
    message = f"チェック: {checked_count}件 | 利益あり: {found_count}件"
    
    method = config.get("notification", {}).get("method", "windows_toast")
    if method == "email":
        if found_count > 0:
            send_email(config, title, message)
    else:
        notify_windows_toast(title, message)


def notify_error(config: dict, error_msg: str):
    """エラー通知"""
    title = "カメラ転売ボット - エラー"
    message = error_msg[:200]
    
    method = config.get("notification", {}).get("method", "windows_toast")
    if method == "email":
        send_email(config, title, message)
    else:
        notify_windows_toast(title, message)
