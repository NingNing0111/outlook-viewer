import hmac
import logging
import os
import re
import secrets
import threading
from html.parser import HTMLParser

import requests
from flask import Flask, jsonify, render_template, request, session

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('mailviewer')

app = Flask(__name__)
COOKIE_SECURE_ENABLED = os.environ.get('COOKIE_SECURE') == '1'
app.secret_key = (os.environ.get('SECRET_KEY') or '').strip() or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=24000, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Strict',
                  SESSION_COOKIE_SECURE=COOKIE_SECURE_ENABLED)
ALLOWED_HOSTS = {'login.microsoftonline.com', 'graph.microsoft.com'}
lock = threading.Lock()
active = threading.BoundedSemaphore(6)

DEFAULT_CLIENT_ID = os.environ.get('DEFAULT_CLIENT_ID', '').strip()
CONTEXT_WORDS = (r'验证码|校验码|动态码|安全代码|认证码|一次性密码|登录码'
                 r'|security code|verification code|verify code|login code|sign.in code'
                 r'|one.time (?:password|code)|authentication code|access code|code')
CODE_RE = re.compile(r'(?<![\dA-Za-z])(\d{4,8})(?![\dA-Za-z])')
ZIP_RE = re.compile(r'(?:CA|NY|WA|TX|FL|IL|MA|AZ|CO|GA|OH|PA|OR|NC|VA|MI|MN|MO|UT|NV)\s+\d{5}(?:-\d{4})?', re.I)


class MailError(Exception):
    pass


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head') and not self.in_title:
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag in ('br', 'p', 'div', 'tr', 'li', 'td', 'table'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head') and not self.in_title and self.hidden > 0:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        elif not self.hidden:
            self.parts.append(data)


def plain(value):
    if isinstance(value, dict):
        value = value.get('content') or value.get('text') or ''
    text = str(value or '')[:200000]
    if '<' in text and '>' in text:
        parser = TextExtractor()
        try:
            parser.feed(text)
            text = ''.join(parser.parts)
        except Exception:
            pass
    text = re.sub(r'[ \t\u00a0\u200b]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def extract_codes(text):
    """Rank numeric candidates: labelled codes first, then bare numbers.

    Noise that is not a verification code (amounts, card tails, years, masked
    addresses, dates) is filtered out before bare numbers are considered.
    """
    labelled = []
    for match in re.finditer(CONTEXT_WORDS, text, re.I):
        window = text[match.end():match.end() + 60]
        found = CODE_RE.search(window)
        if found:
            labelled.append(found.group(1))

    cleaned = text
    # Drop whole sentences that talk about money or quantities, never a login code.
    cleaned = re.sub(r'[^\n。.!?]*(?:US\$|\$|¥|￥|EUR|USD|CNY|退款|冻结|共计|合计|金额|积分|Punkte|points)[^\n。.!?]*', ' ', cleaned, flags=re.I)
    # Masked card tails and masked account names.
    cleaned = re.sub(r'(?:Visa|Mastercard|MasterCard|Amex|银联|信用卡|卡号)[^\n]{0,12}?\d{3,4}', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\*{2,}\d+', ' ', cleaned)
    cleaned = ZIP_RE.sub(' ', cleaned)
    cleaned = re.sub(r'[\w.+-]+@[\w-]+\.[\w.]+', ' ', cleaned)
    cleaned = re.sub(r'https?://\S+', ' ', cleaned)
    cleaned = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', ' ', cleaned)
    # Numbers written with thousand separators are amounts/counters, not codes.
    cleaned = re.sub(r'\d{1,3}(?:[,.\u00a0 ]\d{3})+', ' ', cleaned)
    # Dates: 2026年9月23日 / 2026-09-23 / 09/23/2026 / 23.09.2026
    cleaned = re.sub(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?', ' ', cleaned)
    cleaned = re.sub(r'\b(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\b', ' ', cleaned)
    cleaned = re.sub(r'\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b', ' ', cleaned)
    cleaned = re.sub(r'\b(?:19|20)\d{2}\b', ' ', cleaned)
    # Long runs of bare numbers (lists, IDs) are not single codes.
    for run in re.finditer(r'(?<!\d)(?:\d{1,8}[\s,]+){2,}\d{1,8}(?!\d)', cleaned):
        cleaned = cleaned.replace(run.group(0), ' ')
    bare = CODE_RE.findall(cleaned)

    ranked = list(dict.fromkeys(labelled + bare))
    # Keep a labelled code ahead of anything else; cap the noise we surface.
    return ranked[:4]


def parse_input(raw, override_client='', override_token=''):
    parts = [p.strip() for p in re.split(r'(?:\s*-{3,}\s*|[—–]+-*)', raw.strip())]
    parts = [p for p in parts if p]
    email = account = ''
    client = token = ''
    if len(parts) == 7:
        email, _, _, account, _, client, token = parts
    elif len(parts) == 5:
        email, _, account, client, token = parts
    elif len(parts) == 4:
        # 邮箱----密码----clientid----refreshtoken (password ignored)
        email, _, client, token = parts
        for extra in (client, token):
            if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', extra):
                raise MailError('格式不正确：4 段格式应为 邮箱----密码----clientid----refreshtoken，'
                                '请确认分隔符为 ---- 且字段顺序正确。')
        account = email
    elif len(parts) == 3:
        # Three segments are ambiguous, so decide by looking at the values:
        # email----clientid----token only when exactly one field is an email.
        first, second, third = parts
        emails = sum(1 for p in parts if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', p))
        if emails != 1 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', first):
            raise MailError('格式不正确：3 段简化格式应为 邮箱----clientid----refreshtoken。')
        email = account = first
        client, token = second, third
    elif len(parts) == 1:
        email = account = parts[0]
    else:
        raise MailError('格式不正确：支持 邮箱----密码----clientid----refreshtoken、7 段原始格式、'
                        '5 段简化格式，或仅填写邮箱后单独填入 clientid 与 refresh token。')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise MailError('邮箱地址格式不正确。')
    if email.lower() != account.lower():
        raise MailError('两处邮箱地址不一致，请检查后重试。')
    client = override_client or client
    token = override_token or token
    if not client or not token:
        raise MailError('缺少 clientid 或 refresh token，请在输入框或邮箱信息中提供。')
    return dict(email=email, client=client, token=token)


def get_json(method, url, expect_json=True, **kwargs):
    if urlparse_host(url) not in ALLOWED_HOSTS:
        raise MailError('内部错误：非受信任的请求地址。')
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.request(method, url, timeout=(10, 25), allow_redirects=False, stream=True, **kwargs) as response:
                chunks, size = [], 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 4_000_000:
                        raise MailError('服务端响应过大，已停止读取。')
                    chunks.append(chunk)
                content = b''.join(chunks)
                if response.status_code in (400, 401, 403):
                    raise MailError(auth_error(content, response.status_code))
                if not 200 <= response.status_code < 300:
                    raise MailError(f'微软服务返回 HTTP {response.status_code}，请稍后重试。')
                if not expect_json:
                    return None
                import json
                try:
                    return json.loads(content)
                except (ValueError, UnicodeError):
                    raise MailError('微软服务返回了无法解析的数据。') from None
    except requests.RequestException:
        raise MailError('连接微软服务失败或超时，请稍后重试。') from None


def urlparse_host(url):
    from urllib.parse import urlparse
    return urlparse(url).hostname or ''


def auth_error(content, status):
    detail = ''
    try:
        import json
        payload = json.loads(content)
        detail = str(payload.get('error') or payload.get('error_description') or '')[:200]
    except Exception:
        pass
    if 'invalid_grant' in detail:
        return '刷新令牌已失效或被撤销，请更换新的 refresh token。'
    if status == 401:
        return '微软拒绝了该凭据（401）。令牌可能已过期、密码已更改或账户受限。'
    if 'AADSTS70000' in detail or 'AADSTS7000215' in detail or 'invalid_client' in detail:
        return 'clientid 无效，或该应用不允许此登录方式。'
    if 'AADSTS65001' in detail or 'consent' in detail.lower():
        return '该令牌缺少读取邮件权限，或需要重新授权。'
    return f'微软拒绝了请求（HTTP {status}）。请检查 clientid 与 refresh token 是否匹配、有效。'


def decode_claim(access_token, claim):
    """Read one non-sensitive claim from a JWT payload (no signature needed)."""
    try:
        import base64
        import json
        segment = access_token.split('.')[1]
        segment += '=' * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment))
        return str(payload.get(claim) or '')
    except Exception:
        return ''


def fetch_graph(email, client, token):
    """Refresh the token, confirm the mailbox identity, then read the inbox."""
    token_payload = get_json(
        'POST', 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
        data={
            'client_id': client,
            'refresh_token': token,
            'grant_type': 'refresh_token',
            # Tokens issued to this client only authorize the .default scope set;
            # requesting a narrower scope yields AADSTS70000.
            'scope': 'https://graph.microsoft.com/.default offline_access',
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if not isinstance(token_payload, dict) or not token_payload.get('access_token'):
        raise MailError('微软未返回访问令牌，请检查 clientid 与 refresh token。')
    access_token = token_payload['access_token']
    auth = {'Authorization': 'Bearer ' + access_token}

    # Prefer the JWT identity claim; fall back to /me when the token allows it.
    owner = decode_claim(access_token, 'preferred_username') or decode_claim(access_token, 'upn')
    if not owner:
        try:
            profile = get_json('GET', 'https://graph.microsoft.com/v1.0/me'
                                      '?$select=mail,userPrincipalName', headers=auth)
            if isinstance(profile, dict):
                owner = str(profile.get('mail') or profile.get('userPrincipalName') or '')
        except MailError:
            owner = ''

    data = get_json('GET', 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages',
                    headers=auth,
                    params={'$top': '25', '$orderby': 'receivedDateTime desc',
                            '$select': 'subject,from,toRecipients,receivedDateTime,body,bodyPreview'})

    if owner and owner.lower() != email.lower() and not mailbox_matches(data, email):
        raise MailError('令牌所属账户与输入邮箱不一致；为避免读取错误邮箱，已停止。')
    return data


def mailbox_matches(data, email):
    """Confirm the mailbox by checking delivered-to recipients in recent mail."""
    target = email.lower()
    for item in (data.get('value') or []) if isinstance(data, dict) else []:
        for recipient in (item.get('toRecipients') or []):
            address = (recipient or {}).get('emailAddress') or {}
            if str(address.get('address') or '').lower() == target:
                return True
    return False


def normalize(data):
    if isinstance(data, dict):
        if not any(key in data for key in ('value', 'messages')):
            raise MailError('无法识别微软返回的邮件列表（缺少 value 字段）。')
        data = data.get('value') or data.get('messages') or []
    if not isinstance(data, list):
        raise MailError('无法识别微软返回的邮件列表。')
    result = []
    for item in data[:25]:
        if not isinstance(item, dict):
            continue
        sender = item.get('from') or item.get('sender') or {}
        if isinstance(sender, dict):
            address = sender.get('emailAddress') or sender
            if isinstance(address, dict):
                name = str(address.get('name') or '').strip()
                mail = str(address.get('address') or '').strip()
                sender = f'{name} <{mail}>' if name and mail else (mail or name or '未知发件人')
        preview = plain(item.get('bodyPreview') or '')
        raw_body = item.get('body') or item.get('bodyPreview') or ''
        body = plain(raw_body) or preview
        if len(body) > 20000:
            body = body[:20000] + '\n…（正文过长，已截断）'
        subject = plain(item.get('subject') or '无主题')[:300]
        codes = extract_codes(subject + '\n' + preview + '\n' + body)
        result.append(dict(
            subject=subject,
            sender=str(sender)[:500],
            body=body or preview or '这封邮件没有可显示的正文。',
            date=str(item.get('receivedDateTime') or ''),
            codes=codes,
        ))
    return result


@app.after_request
def headers(response):
    response.headers.update({
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                                   "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                   "base-uri 'none'; form-action 'self'",
    })
    return response


@app.before_request
def guard():
    if request.method == 'POST':
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', secrets.token_hex(32))):
            # Almost always caused by COOKIE_SECURE=1 while browsing over http://:
            # the browser discards the Secure session cookie, so no cookie is sent.
            if COOKIE_SECURE_ENABLED and not request.is_secure:
                log.warning('CSRF check failed on a plain-HTTP request while COOKIE_SECURE=1; '
                            'the session cookie is being dropped by the client. '
                            'Set COOKIE_SECURE=0 for http, or serve the app over HTTPS.')
                return jsonify(error='当前以 HTTP 访问，但 COOKIE_SECURE=1，会话 Cookie 被浏览器丢弃。'
                                     '请改为 HTTPS 访问，或将 COOKIE_SECURE 设为 0 后重启。'), 403
            if not request.cookies:
                log.warning('CSRF check failed and no session cookie was sent; '
                            'the client is likely blocking cookies.')
            return jsonify(error='页面已过期，请刷新后重试。'), 403


@app.get('/')
def home():
    session.setdefault('csrf', secrets.token_hex(24))
    return render_template('index.html', csrf=session['csrf'], default_client=DEFAULT_CLIENT_ID)


@app.post('/api/mail')
def mail():
    if not active.acquire(blocking=False):
        return jsonify(error='当前读取任务较多，请稍后重试。'), 429
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get('raw'), str):
            raise MailError('请填写邮箱信息。')
        raw_client = payload.get('client')
        raw_token = payload.get('token')
        override_client = raw_client.strip() if isinstance(raw_client, str) else ''
        override_token = raw_token.strip() if isinstance(raw_token, str) else ''
        info = parse_input(payload['raw'], override_client, override_token)
        data = fetch_graph(info['email'], info['client'], info['token'])
        return jsonify(email=info['email'], messages=normalize(data))
    except MailError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        log.exception('unhandled error while reading mailbox')
        return jsonify(error='处理失败，已记录日志。请检查输入后重试。'), 500
    finally:
        active.release()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=18765, debug=False)
