const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let busy = false;
let toastTimer;

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $('toast').hidden = true;
  }, 3500);
}

async function post(path, data) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrf
    },
    body: JSON.stringify(data)
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error('服务暂时不可用，请稍后重试。');
  }
  if (!response.ok) {
    throw new Error(result.error || '请求失败');
  }
  return result;
}



function element(tag, className, text) {
  const el = document.createElement(tag);
  el.className = className;
  if (text !== undefined) {
    el.textContent = text;
  }
  return el;
}

function render(messages) {
  $('messages').replaceChildren();
  $('empty').hidden = messages.length > 0;
  if (!messages.length) {
    $('empty').querySelector('h3').textContent = '暂时没有邮件';
    $('empty').querySelector('p').textContent = '邮件可能还在路上，稍后点击刷新试试。';
  }
  for (const mail of messages) {
    const article = element('article', 'mail');
    article.append(element('h3', '', mail.subject));
    const date = new Date(mail.date);
    const displayDate = Number.isNaN(date.getTime()) ? mail.date : date.toLocaleString('zh-CN');
    article.append(element('div', 'mail-info', `${mail.sender} · ${displayDate || '时间未知'}`));

    if (mail.codes.length) {
      const codes = element('div', 'codes');
      for (const code of mail.codes) {
        const button = element('button', 'code', code);
        button.append(element('small', '', '复制候选验证码'));
        button.addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(code);
            toast('验证码已复制');
          } catch {
            toast('复制失败，请手动选择验证码复制');
          }
        });
        codes.append(button);
      }
      article.append(codes);
    }

    const details = element('details', '');
    const summary = element('summary', '', '查看邮件正文');
    details.append(summary, element('pre', '', mail.body || '这封邮件没有可显示的正文。'));
    article.append(details);
    $('messages').append(article);
  }
}

async function loadMail() {
  if (busy) return;
  if (!$('raw').value.trim()) {
    toast('请先粘贴邮箱信息');
    $('raw').focus();
    return;
  }
  busy = true;
  $('fetch').disabled = true;
  $('refresh').disabled = true;
  $('clear').disabled = true;

  $('messages').replaceChildren();
  $('empty').hidden = false;
  $('count').textContent = '读取中';
  $('account').textContent = '正在连接 Microsoft Graph…';
  $('status').hidden = false;
  $('status').className = 'notice';
  $('status').textContent = '正在换取访问令牌并读取收件箱，请稍候…';

  try {
    const data = await post('/api/mail', {
      raw: $('raw').value,
      client: $('client').value.trim(),
      token: $('token').value.trim()
    });
    render(data.messages);
    $('account').textContent = data.email;
    $('count').textContent = `${data.messages.length} 封邮件`;
    $('status').textContent = `读取完成 · ${new Date().toLocaleTimeString('zh-CN')}。数字仅为候选验证码，请以邮件原文为准。`;
  } catch (error) {
    $('status').className = 'notice error';
    $('status').textContent = error.message;
    $('count').textContent = '读取失败';
    $('account').textContent = '请检查输入后重试';
  } finally {
    busy = false;
    $('fetch').disabled = false;
    $('refresh').disabled = false;
    $('clear').disabled = false;
  }
}

$('fetch').addEventListener('click', loadMail);
$('refresh').addEventListener('click', loadMail);

$('clear').addEventListener('click', () => {
  $('raw').value = '';
  $('token').value = '';
  $('messages').replaceChildren();
  $('status').hidden = true;
  $('empty').hidden = false;
  $('empty').querySelector('h3').textContent = '等一封重要的来信';
  $('empty').querySelector('p').textContent = '填写邮箱信息后，即可查看邮件内容。';
  $('account').textContent = '读取结果将在这里显示';
  $('count').textContent = '等待提取';
  $('refresh').disabled = true;
  toast('输入与邮件内容已清空');
});

const groupButtons = document.querySelectorAll('.group');
for (const button of groupButtons) {
  button.addEventListener('click', () => {
    navigator.clipboard.writeText('1071504366').then(
      () => {
        toast('已复制QQ群号：1071504366');
      },
      () => {
        toast('AI账号交流群（QQ）：1071504366');
      }
    );
  });
}
