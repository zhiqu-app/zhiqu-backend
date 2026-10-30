"""邮箱验证码注册契约；测试捕获邮件，不向真实邮箱发信。"""
from datetime import timedelta
import smtplib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

BASE = '/api/v1'
PAYLOAD = {'email':'student@example.com','password':'  StudyDemo123!  ','name':'小林'}


@pytest.fixture
def outbox(client, settings):
    settings.smtp_host = 'smtp.example.test'
    settings.smtp_port = 465
    settings.smtp_security = 'ssl'
    settings.smtp_username = 'sender@qq.com'
    settings.smtp_password = 'test-smtp-authorization'
    settings.smtp_from = 'sender@qq.com'
    settings.verification_secret = 'test-only-secret-do-not-use-in-production-123456'
    messages = []
    def capture(config, recipient, code):
        messages.append({'email':recipient,'code':code})
    client.app.state.mailer = capture
    return messages


def sent_code(client, outbox, email=PAYLOAD['email']):
    response = client.post(BASE+'/auth/email-code',json={'email':email})
    assert response.status_code == 200, response.text
    assert response.json()['resendAfter'] == 60
    assert response.json()['expiresIn'] == 300
    assert 'code' not in response.json()
    return outbox[-1]['code']


def test_registration_cannot_bypass_email_verification(client):
    response = client.post(BASE+'/auth/register',json=PAYLOAD)
    assert response.status_code == 422


def test_verified_registration_and_login(client, outbox):
    code = sent_code(client,outbox,'Student@Example.com')
    assert outbox[-1]['email'] == 'student@example.com'
    response = client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':code})
    assert response.status_code == 201, response.text
    assert response.json()['user']['emailVerified'] is True
    auth={'Authorization':'Bearer '+response.json()['accessToken']}
    assert client.get(BASE+'/auth/me',headers=auth).json()['emailVerified'] is True
    assert client.get(BASE+'/courses',headers=auth).json()['total'] == 0
    assert client.post(BASE+'/auth/login',json={k:PAYLOAD[k] for k in ['email','password']}).status_code == 200
    assert client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']}).status_code == 409


def test_code_cooldown_resend_and_digest(client,outbox,database):
    from app.models import EmailVerification, utcnow
    first = sent_code(client,outbox)
    retry = client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']})
    assert retry.status_code == 429
    assert 1 <= int(retry.headers['Retry-After']) <= 60
    with database.sessions.begin() as db:
        row=db.get(EmailVerification,PAYLOAD['email'])
        assert row.code_digest != first and len(row.code_digest) == 64
        row.requested_at=utcnow()-timedelta(seconds=61)
    second = sent_code(client,outbox)
    if first != second:
        assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':first}).status_code == 400
    assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':second}).status_code == 201


def test_wrong_code_attempts_are_persisted_and_lock_code(client,outbox,database):
    from app.models import EmailVerification
    code=sent_code(client,outbox)
    wrong='000000' if code!='000000' else '111111'
    for _ in range(5):
        assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':wrong}).status_code == 400
    with database.sessions() as db:
        row=db.get(EmailVerification,PAYLOAD['email'])
        assert row.failed_attempts == 5 and row.active is False
    assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':code}).status_code == 400


def test_expiry_and_email_binding(client,outbox,database):
    from app.models import EmailVerification, utcnow
    code=sent_code(client,outbox)
    assert client.post(BASE+'/auth/register',json={**PAYLOAD,'email':'other@example.com','verificationCode':code}).status_code == 400
    with database.sessions.begin() as db:
        db.get(EmailVerification,PAYLOAD['email']).expires_at=utcnow()-timedelta(seconds=1)
    assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':code}).status_code == 400


def test_hourly_email_quota(client,outbox,database):
    from app.models import EmailVerification, utcnow
    for _ in range(5):
        sent_code(client,outbox)
        with database.sessions.begin() as db:
            db.get(EmailVerification,PAYLOAD['email']).requested_at=utcnow()-timedelta(seconds=61)
    assert client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']}).status_code == 429
    assert len(outbox) == 5


def test_smtp_failure_does_not_claim_sent_or_leave_active_code(client,outbox,database):
    from app.models import EmailVerification
    def failing(config,email,code):
        outbox.append({'email':email,'code':code})
        raise smtplib.SMTPAuthenticationError(535,b'test-secret-must-not-leak')
    client.app.state.mailer=failing
    response=client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']})
    assert response.status_code == 503
    assert 'test-secret-must-not-leak' not in response.text
    with database.sessions() as db:
        assert db.get(EmailVerification,PAYLOAD['email']).active is False
    assert client.post(BASE+'/auth/register',json={**PAYLOAD,'verificationCode':outbox[-1]['code']}).status_code == 400


def test_missing_smtp_config_is_explicit(client,outbox,settings):
    settings.smtp_password=''
    response=client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']})
    assert response.status_code == 503
    assert outbox == []


@pytest.mark.parametrize('email',['victim@example.com,second','a@example..com','a@[bad].com','a(b)@example.com'])
def test_recipient_is_a_single_valid_mailbox(client,outbox,email):
    response=client.post(BASE+'/auth/email-code',json={'email':email})
    assert response.status_code==422
    assert outbox==[]


def test_retry_after_header_is_available_to_frontend(client,outbox):
    sent_code(client,outbox)
    response=client.post(BASE+'/auth/email-code',json={'email':PAYLOAD['email']} ,headers={'Origin':'http://127.0.0.1:5173'})
    assert response.status_code==429
    assert 'retry-after' in response.headers.get('access-control-expose-headers','').lower()


def test_qq_smtp_adapter_uses_ssl_and_authorization(settings,monkeypatch):
    from app.mailer import send_registration_email
    settings.smtp_host='smtp.qq.com'
    settings.smtp_port=465
    settings.smtp_security='ssl'
    settings.smtp_username=settings.smtp_from='sender@qq.com'
    settings.smtp_password='test-authorization-code'
    fake=MagicMock()
    monkeypatch.setattr('app.mailer.smtplib.SMTP_SSL',fake)
    send_registration_email(settings,'recipient@example.com','012345')
    connection=fake.return_value.__enter__.return_value
    connection.login.assert_called_once_with('sender@qq.com','test-authorization-code')
    message=connection.send_message.call_args.args[0]
    assert message['To']=='recipient@example.com'
    assert '012345' in message.get_body(preferencelist=('plain',)).get_content()
    assert fake.call_args.kwargs['context'].check_hostname
