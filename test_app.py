import json

import pytest

import app as mod

RAW7 = ('test@example.com----pw----https://xiaoheiapi.top/m/demo'
        '----test@example.com----pw----client-id----refresh-token')
RAW5 = 'test@example.com----client-id----refresh-token'


# ---------------------------------------------------------------- input parsing

def test_parse_seven_segments():
    info = mod.parse_input(RAW7)
    assert info['email'] == 'test@example.com'
    assert info['client'] == 'client-id'
    assert info['token'] == 'refresh-token'


def test_parse_five_segments():
    info = mod.parse_input(RAW5)
    assert info['client'] == 'client-id'
    assert info['token'] == 'refresh-token'


def test_parse_four_segments_with_password():
    """邮箱----密码----clientid----refreshtoken (password ignored)."""
    info = mod.parse_input('test@example.com----secret-pw----client-id----refresh-token')
    assert info['email'] == 'test@example.com'
    assert info['client'] == 'client-id'
    assert info['token'] == 'refresh-token'


def test_parse_four_segments_dash_variant():
    info = mod.parse_input('test@example.com—-pw—-client-id—-refresh-token')
    assert info['client'] == 'client-id'
    assert info['token'] == 'refresh-token'


def test_parse_four_segments_rejects_extra_emails():
    with pytest.raises(mod.MailError):
        mod.parse_input('a@b.com----c@d.com----x@y.com----z')


def test_parse_dash_variants():
    info = mod.parse_input('test@example.com—-client-id—-refresh-token')
    assert info['token'] == 'refresh-token'


def test_parse_manual_override_wins():
    info = mod.parse_input('test@example.com', 'typed-client', 'typed-token')
    assert info['client'] == 'typed-client'
    assert info['token'] == 'typed-token'


def test_parse_override_fills_missing():
    info = mod.parse_input(RAW5, 'other-client', '')
    assert info['client'] == 'other-client'
    assert info['token'] == 'refresh-token'


def test_parse_rejects_bad_input():
    with pytest.raises(mod.MailError):
        mod.parse_input('not-an-email')
    with pytest.raises(mod.MailError):
        mod.parse_input(RAW7.replace('----test@example.com', '----other@example.com'))
    with pytest.raises(mod.MailError):
        mod.parse_input('test@example.com')
    with pytest.raises(mod.MailError):
        mod.parse_input('a@b.com----c@d.com----x')


# ------------------------------------------------------------- code extraction

@pytest.mark.parametrize('text,expected', [
    ('请输入以下验证码来为你的新 Meta 账户验证邮箱。 验证码 706097', '706097'),
    ('Your code is 123456. It expires in 10 minutes.', '123456'),
    ('Login code: 889900 Do not share it.', '889900'),
    ('Your verification code is 4321', '4321'),
    ('安全代码 556677，请勿泄露', '556677'),
])
def test_extract_labelled_code(text, expected):
    assert mod.extract_codes(text)[0] == expected


@pytest.mark.parametrize('text', [
    # Meta receipt: card tail, amount and date are not verification codes.
    '为了验证你的年龄，我们暂时冻结了 US$ 1.00 。共 US$ 1.00 Visa · 0020 2026年9月23日',
    'Microsoft 帐户 安全信息已删除 从 someone@outlook.com 中删除',
    'Kostenlos 50000 Punkte sichern ohne Code',
])
def test_extract_ignores_noise(text):
    assert mod.extract_codes(text) == []


def test_extract_dedupes():
    assert mod.extract_codes('验证码 706097 验证码 706097') == ['706097']


def test_extract_caps_results():
    assert len(mod.extract_codes('code 1 2 3 4 5 6 7 8 9')) <= 4


# ------------------------------------------------------------- html -> text

def test_plain_strips_html_head():
    html = ('<html><head><title>验证你的邮箱</title><style>body{color:red}</style></head>'
            '<body><p>验证码 706097</p></body></html>')
    assert mod.plain(html) == '验证码 706097'


def test_plain_drops_scripts():
    assert mod.plain('<script>evil()</script><p>safe</p>') == 'safe'


def test_plain_handles_dict_and_text():
    assert mod.plain({'content': '<div>Hello</div>'}) == 'Hello'
    assert mod.plain('plain text') == 'plain text'
    assert mod.plain(None) == ''


# ------------------------------------------------------------------ normalize

def test_normalize_graph_shape():
    data = {'value': [{
        'subject': '验证你的邮箱',
        'from': {'emailAddress': {'name': 'Meta', 'address': 'security@account.meta.com'}},
        'receivedDateTime': '2026-09-23T12:43:14Z',
        'bodyPreview': '验证码 706097',
        'body': {'contentType': 'html', 'content': '<p>验证码 <b>706097</b></p>'},
    }]}
    result = mod.normalize(data)
    assert len(result) == 1
    assert result[0]['subject'] == '验证你的邮箱'
    assert result[0]['sender'] == 'Meta <security@account.meta.com>'
    assert result[0]['date'] == '2026-09-23T12:43:14Z'
    assert result[0]['codes'] == ['706097']
    assert result[0]['body'] == '验证码 706097'


def test_normalize_empty_inbox():
    assert mod.normalize({'value': []}) == []


def test_normalize_rejects_unexpected_shape():
    with pytest.raises(mod.MailError):
        mod.normalize({'unexpected': 'shape'})


def test_normalize_truncates_long_body():
    data = {'value': [{'subject': 's', 'body': {'content': 'x' * 30000}}]}
    assert '已截断' in mod.normalize(data)[0]['body']


# ------------------------------------------------------------ network / scope

def test_host_allowlist():
    assert mod.urlparse_host('https://graph.microsoft.com/v1.0/me') == 'graph.microsoft.com'
    with pytest.raises(mod.MailError):
        mod.get_json('GET', 'https://evil.example/v1.0/me')


def test_token_error_messages():
    body = json.dumps({'error': 'invalid_grant', 'error_description': 'AADSTS70000'}).encode()
    assert '失效' in mod.auth_error(body, 400)
    assert '401' in mod.auth_error(b'', 401)
    assert 'clientid' in mod.auth_error(json.dumps({'error_description': 'invalid_client'}).encode(), 400)


def test_decode_claim():
    import base64
    payload = base64.urlsafe_b64encode(json.dumps({'preferred_username': 'a@b.com'}).encode()).decode().rstrip('=')
    token = f'header.{payload}.signature'
    assert mod.decode_claim(token, 'preferred_username') == 'a@b.com'
    assert mod.decode_claim('garbage', 'preferred_username') == ''


def test_fetch_graph_uses_default_scope(monkeypatch):
    """Tokens for this client only authorize .default; a narrow scope fails."""
    calls = []

    def fake_get_json(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if 'login.microsoftonline.com' in url:
            return {'access_token': 'header.eyJwcmVmZXJyZWRfdXNlcm5hbWUiOiJ0ZXN0QGV4YW1wbGUuY29tIn0.sig'}
        return {'value': []}

    monkeypatch.setattr(mod, 'get_json', fake_get_json)
    mod.fetch_graph('test@example.com', 'cid', 'rtok')

    token_calls = [c for c in calls if 'login.microsoftonline.com' in c[1]]
    assert len(token_calls) == 1
    assert token_calls[0][2]['data']['scope'] == 'https://graph.microsoft.com/.default offline_access'
    assert all('xiaoheiapi' not in c[1] for c in calls)
    assert any('/me/mailFolders/inbox/messages' in c[1] for c in calls)


def make_csrf(client):
    """The CSRF token is issued on GET / and stored in the session."""
    client.get('/')
    with client.session_transaction() as session:
        return session['csrf']


def test_fetch_graph_blocks_foreign_mailbox(monkeypatch):
    import base64
    claim = base64.urlsafe_b64encode(json.dumps({'preferred_username': 'other@example.com'}).encode()).decode().rstrip('=')
    token = f'h.{claim}.s'

    def fake_get_json(method, url, **kwargs):
        if 'login.microsoftonline.com' in url:
            return {'access_token': token}
        return {'value': [{'toRecipients': [{'emailAddress': {'address': 'other@example.com'}}]}]}

    monkeypatch.setattr(mod, 'get_json', fake_get_json)
    with pytest.raises(mod.MailError):
        mod.fetch_graph('test@example.com', 'cid', 'rtok')


def test_mailbox_matches_recipients():
    data = {'value': [{'toRecipients': [{'emailAddress': {'address': 'test@example.com'}}]}]}
    assert mod.mailbox_matches(data, 'TEST@example.com') is True
    assert mod.mailbox_matches({'value': []}, 'test@example.com') is False


# -------------------------------------------------------------------- routes

def test_index_has_no_login_gate():
    client = mod.app.test_client()
    page = client.get('/')
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'login-panel' not in body
    assert '访问口令' not in body
    assert '1071504366' in body
    assert page.headers['Cache-Control'] == 'no-store'


def test_mail_requires_csrf():
    client = mod.app.test_client()
    assert client.post('/api/mail', json={'raw': RAW7}).status_code == 403


def test_csrf_error_explains_secure_cookie_mismatch(monkeypatch):
    """COOKIE_SECURE=1 over plain HTTP breaks CSRF; the error must say so."""
    monkeypatch.setattr(mod, 'COOKIE_SECURE_ENABLED', True)
    client = mod.app.test_client()
    res = client.post('/api/mail', json={'raw': RAW7})
    assert res.status_code == 403
    assert 'COOKIE_SECURE' in res.get_json()['error']


def test_csrf_error_is_generic_on_https(monkeypatch):
    monkeypatch.setattr(mod, 'COOKIE_SECURE_ENABLED', True)
    client = mod.app.test_client()
    res = client.post('/api/mail', json={'raw': RAW7}, base_url='https://localhost')
    assert res.status_code == 403
    assert res.get_json()['error'] == '页面已过期，请刷新后重试。'


def graph_stub(messages):
    """Return a get_json replacement: token request, then the inbox list."""
    def fake(method, url, **kwargs):
        if 'login.microsoftonline.com' in url:
            return {'access_token': 'h.eyJwcmVmZXJyZWRfdXNlcm5hbWUiOiJ0ZXN0QGV4YW1wbGUuY29tIn0.s'}
        return {'value': messages}
    return fake


def test_mail_reads_graph(monkeypatch):
    client = mod.app.test_client()
    csrf = make_csrf(client)
    monkeypatch.setattr(mod, 'get_json', graph_stub([
        {'subject': '验证码 654321', 'bodyPreview': '验证码 654321',
         'from': {'emailAddress': {'address': 'a@b.com'}},
         'receivedDateTime': '2026-09-23T00:00:00Z'}]))
    res = client.post('/api/mail', json={'raw': RAW7}, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    data = res.get_json()
    assert data['email'] == 'test@example.com'
    assert data['messages'][0]['codes'] == ['654321']
    assert 'refresh-token' not in res.get_data(as_text=True)


def test_mail_accepts_separate_credentials(monkeypatch):
    client = mod.app.test_client()
    csrf = make_csrf(client)
    monkeypatch.setattr(mod, 'get_json', graph_stub([]))
    res = client.post('/api/mail', json={'raw': 'test@example.com', 'client': 'cid', 'token': 'rtok'},
                      headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200


def test_mail_error_does_not_leak_token(monkeypatch):
    client = mod.app.test_client()
    csrf = make_csrf(client)

    def boom(*a, **kw):
        raise mod.MailError('刷新令牌已失效或被撤销，请更换新的 refresh token。')

    monkeypatch.setattr(mod, 'get_json', boom)
    res = client.post('/api/mail', json={'raw': RAW7}, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    assert 'refresh-token' not in res.get_data(as_text=True)
