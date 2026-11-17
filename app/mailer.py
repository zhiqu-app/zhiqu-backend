"""SMTP 邮件适配：默认 QQ 邮箱 SSL，不把授权码或验证码写入日志。"""
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate
import smtplib
import ssl


def send_registration_email(settings, recipient, code):
    """向 recipient 发送注册验证码；code 为 6 位字符串，失败时交由调用方统一处理。"""
    _send_verification_email(settings, recipient, code, '邮箱注册', '注册知序学习空间',
                             '输入下面的验证码，开始建立你的学习空间。')


def send_password_reset_email(settings, recipient, code):
    """发送只用于重置密码的邮箱验证码。"""
    _send_verification_email(settings, recipient, code, '密码重置', '重置知序学习空间密码',
                             '在找回密码页面输入下面的验证码，设置新密码。')


def _send_verification_email(settings, recipient, code, subject, action, instructions):
    """使用相同 SMTP 配置发送不同用途的验证码。"""
    message = EmailMessage()
    message['Subject'] = f'知序 · {subject}验证码'
    message['From'] = Address(display_name='知序学习空间', addr_spec=settings.smtp_from)
    message['To'] = Address(addr_spec=recipient)
    message['Date'] = formatdate(localtime=True)
    message.set_content(f'你正在{action}。\n\n验证码：{code}\n\n5 分钟内有效，请勿转发给他人。'
                        '\n如果这不是你的操作，请忽略此邮件。')
    message.add_alternative(f'''<html lang="zh-CN"><body style="margin:0;padding:32px;background:#eef4f8;font-family:Arial,sans-serif;color:#142b40">
      <main style="max-width:480px;margin:auto;background:#fff;padding:32px;border-radius:12px">
      <p style="color:#087caa">知序 · 学习空间</p><h2>{subject}</h2>
      <p>{instructions}</p>
      <p style="font-size:34px;letter-spacing:8px;font-weight:bold;color:#1265bb">{code}</p>
      <p>5 分钟内有效，请勿转发给他人。</p><p style="font-size:12px;color:#60798c">如果这不是你的操作，请忽略此邮件。</p>
      </main></body></html>''', subtype='html')
    context = ssl.create_default_context()
    if settings.smtp_security == 'ssl':
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                              timeout=settings.smtp_timeout, context=context) as server:
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
