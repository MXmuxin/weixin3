"""微信模板定时推送。导入不执行网络；测试通过注入 Session 完全离线。"""
import argparse
import ast
import copy
import json
import os
from pathlib import Path
import random
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import requests
from zhdate import ZhDate
import cityinfo

CODE_VERSION = 'wechat-private-v4-hitokoto-1'
TIMEOUT = (5, 20)
BASE = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')


class AppError(Exception):
    """只含脱敏、可公开的诊断信息。"""


def parse_config(text):
    try:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, RecursionError):
        raise AppError('配置格式错误：请使用 JSON 或 Python 字典字面量') from None


def load_config(path=None, environ=None):
    env = os.environ if environ is None else environ
    if env.get('CONFIG_JSON'):
        c = parse_config(env['CONFIG_JSON'])
    else:
        try:
            c = parse_config(Path(path or env.get('CONFIG_PATH') or BASE / 'config.txt').read_text(encoding='utf-8'))
        except (OSError, UnicodeError):
            raise AppError('无法读取 config.txt；请提供本地配置或 CONFIG_JSON') from None
    if not isinstance(c, dict):
        raise AppError('配置顶层必须是字典')
    c = copy.deepcopy(c)
    for env_key, key in [('APP_ID', 'app_id'), ('APP_SECRET', 'app_secret'),
                         ('TEMPLATE_ID', 'template_id'), ('DETAIL_URL', 'detail_url')]:
        if env.get(env_key):
            c[key] = env[env_key]
    if env.get('WECHAT_USERS'):
        try:
            c['user'] = json.loads(env['WECHAT_USERS'])
        except (ValueError, TypeError):
            raise AppError('WECHAT_USERS 必须是 JSON 字符串数组') from None
    validate_config(c)
    return c


def birthday_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r'r?\d{4}-\d{1,2}-\d{1,2}', value):
        raise AppError('生日格式错误：公历 YYYY-MM-DD，农历 rYYYY-MM-DD')
    lunar = value.startswith('r')
    y, m, d = map(int, value.lstrip('r').split('-'))
    try:
        if lunar:
            ZhDate(y, m, d)
        else:
            date(y, m, d)
    except (ValueError, TypeError, IndexError):
        raise AppError('生日日期不存在或超出农历支持范围') from None
    return lunar, m, d


def validate_config(c):
    if not all(isinstance(k, str) for k in c):
        raise AppError('配置键必须是字符串')
    for k in ('app_id', 'app_secret', 'template_id', 'province', 'city', 'love_date'):
        v = c.get(k)
        if not isinstance(v, str) or not v.strip() or 'YOUR_' in v:
            raise AppError(f'配置缺失或仍为占位值：{k}')
    detail_url = c.get('detail_url', '')
    if not isinstance(detail_url, str):
        raise AppError('detail_url 必须是字符串，可留空')
    if detail_url:
        try:
            parsed = urlsplit(detail_url)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    any(ch.isspace() or ord(ch) < 32 for ch in detail_url)):
                raise ValueError
            parsed.port
        except ValueError:
            raise AppError('detail_url 必须为无账号密码的完整 HTTP/HTTPS 页面链接') from None
    users = c.get('user')
    if (not isinstance(users, list) or not users or
            any(not isinstance(u, str) or not u.strip() or 'YOUR_' in u for u in users)):
        raise AppError('user 必须为非空收件人字符串数组')
    if len(set(users)) != len(users):
        raise AppError('user 包含重复收件人；为避免重复推送，请先去重')
    if c['province'] not in cityinfo.cityInfo or c['city'] not in cityinfo.cityInfo[c['province']]:
        raise AppError('省份或城市不在 cityinfo.py 城市表中')
    try:
        if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', c['love_date']):
            raise ValueError
        date(*map(int, c['love_date'].split('-')))
    except ValueError:
        raise AppError('love_date 必须是有效公历日期 YYYY-MM-DD') from None
    for k, v in c.items():
        if k.startswith('birth'):
            if not isinstance(v, dict) or not isinstance(v.get('name'), str) or not v['name'].strip():
                raise AppError('生日配置必须包含非空 name 和 birthday')
            birthday_parts(v.get('birthday'))


def get_birthday(birthday, today):
    lunar, month, day = birthday_parts(birthday)
    if lunar and not 1900 <= today.year <= 2100:
        raise AppError('当前日期超出农历库支持范围 1900—2100')
    # 上一农历年的腊月可能仍在本公历年一月。搜索下一次真实存在的生日。
    start = max(1900, today.year - 1) if lunar else today.year
    end = min(2100, today.year + 20) if lunar else min(9999, today.year + 8)
    for y in range(start, end + 1):
        try:
            candidate = ZhDate(y, month, day).to_datetime().date() if lunar else date(y, month, day)
        except (ValueError, TypeError, IndexError):
            continue
        if candidate >= today:
            return (candidate - today).days
    raise AppError('支持范围内找不到下次生日')


def request_json(session, method, url, label, **kwargs):
    try:
        r = getattr(session, method)(url, timeout=TIMEOUT, **kwargs)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (requests.RequestException, ValueError, TypeError):
        # requests 异常可能包含带 token/secret 的 URL，不可直接打印。
        raise AppError(f'{label}失败：网络、HTTP 或 JSON 异常；发送请求不会自动重试') from None


def get_access_token(session, c):
    data = request_json(session, 'get', 'https://api.weixin.qq.com/cgi-bin/token', '获取微信令牌',
                        params={'grant_type': 'client_credential', 'appid': c['app_id'], 'secret': c['app_secret']})
    token = data.get('access_token')
    if not isinstance(token, str) or not token:
        code = data.get('errcode')
        code = code if type(code) is int else '未知'
        raise AppError(f'获取微信令牌失败（errcode={code}），请检查账号、密钥、IP 白名单与接口权限')
    return token


def get_weather(session, c):
    area = cityinfo.cityInfo[c['province']][c['city']]['AREAID']
    try:
        r = session.get(f'https://d1.weather.com.cn/dingzhi/{area}.html',
                        headers={'Referer': f'https://www.weather.com.cn/weather1d/{area}.shtml',
                                 'User-Agent': 'Mozilla/5.0'}, timeout=TIMEOUT)
        r.raise_for_status()
        r.encoding = 'utf-8'
        text = r.text.strip()
        # 仅解析首个 JS 赋值右侧 JSON，不执行外部代码。
        match = re.match(r'^(?:var\s+)?[A-Za-z_$][\w$]*\s*=\s*', text)
        if not match:
            raise ValueError
        obj, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
        info = obj['weatherinfo']
        values = tuple(info[k] for k in ('weather', 'temp', 'tempn'))
        if any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError
        return values
    except (requests.RequestException, ValueError, TypeError, KeyError):
        print('警告：天气接口不可用，天气及气温字段改为“暂无数据”')
        return ('暂无数据',) * 3


class Note(tuple):
    def __new__(cls, values, source):
        obj = super().__new__(cls, values)
        obj.source = source
        return obj


CARE_EN = 'Always here for you.'  # 本地原创关怀短句，不是中文一言的翻译。
QUOTE_FALLBACK = '愿你今天拥有好心情。'


def quote_excerpt(text):
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= 20 else text[:19] + '…'


def get_ciba(session):
    """兼容旧函数名；实际改用一言中文诗词，英文为明确的本地关怀短句。"""
    try:
        data = request_json(session, 'get', 'https://v1.hitokoto.cn/', '获取一言短句',
                            params={'c': 'i', 'encode': 'json', 'min_length': 1, 'max_length': 20})
        sentence = data.get('hitokoto')
        if not isinstance(sentence, str) or not sentence.strip():
            raise AppError('一言短句字段缺失')
        return Note((quote_excerpt(sentence), CARE_EN), 'hitokoto_online')
    except AppError:
        print('警告：一言接口不可用，使用本地备用文案')
        return Note((QUOTE_FALLBACK, CARE_EN), 'fallback')


def get_color():
    return f'#{random.randint(0, 0xFFFFFF):06x}'


def build_payload(c, user, today, weather, note):
    love_date = date(*map(int, c['love_date'].split('-')))
    values = {
        'date': f'{today} 星期' + '一二三四五六日'[today.weekday()],
        'city': c['city'], 'weather': weather[0],
        'min_temperature': weather[2], 'max_temperature': weather[1],
        'love_day': str((today - love_date).days), 'note_en': quote_excerpt(note[1]), 'note_ch': quote_excerpt(note[0]),
    }
    for k, v in c.items():
        if k.startswith('birth'):
            days = get_birthday(v['birthday'], today)
            name = v['name']
            values[k] = (f'今天{name}生日哦，祝{name}生日快乐！' if days == 0 else
                         f'距离{name}的生日还有{days}天')
    payload = {'touser': user, 'template_id': c['template_id'],
               'topcolor': '#FF0000',
               'data': {k: {'value': v, 'color': get_color()} for k, v in values.items()}}
    if c.get('detail_url'):
        payload['url'] = c['detail_url']
    return payload


def config_source(environ=None):
    env = os.environ if environ is None else environ
    base = 'environment' if env.get('CONFIG_JSON') else 'file'
    overrides = any(env.get(k) for k in ('APP_ID', 'APP_SECRET', 'TEMPLATE_ID', 'WECHAT_USERS', 'DETAIL_URL'))
    return base + ('_with_environment_overrides' if overrides else '')


def emit_summary(payload, origin, note_source, mode):
    # Only allowlisted metadata, never payload values, IDs, URLs, names, dates or hashes.
    safe_origins = {'file', 'environment', 'file_with_environment_overrides',
                    'environment_with_environment_overrides', 'provided'}
    print('DIAGNOSTIC version=' + CODE_VERSION + ' mode=' + mode)
    print('CONFIG source=' + (origin if origin in safe_origins else 'provided'))
    allowed_sources = {'hitokoto_online', 'fallback', 'diagnostic_placeholder', 'unknown'}
    print('QUOTE source=' + (note_source if note_source in allowed_sources else 'unknown'))
    if note_source in ('hitokoto_online', 'fallback'):
        print('ENGLISH source=local_care_not_translation')
    data = payload['data']
    keys = list(dict.fromkeys(['date', 'city', 'weather', 'min_temperature', 'max_temperature',
                              'love_day', 'birthday1', 'birthday2', 'note_en', 'note_ch'] + list(data)))
    for key in keys:
        field = data.get(key)
        v = field.get('value') if isinstance(field, dict) else None
        safe_key = key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key) else 'custom_field'
        print(f'FIELD key={safe_key} present={field is not None} '
              f'chars={len(v) if isinstance(v, str) else 0} '
              f'nonempty={isinstance(v, str) and bool(v.strip())}')
    birthdays = [k for k in data if k.startswith('birth')]
    birthday_ok = all(isinstance(data[k].get('value'), str) and bool(data[k]['value'].strip()) for k in birthdays)
    quote_ok = all(isinstance(data.get(k, {}).get('value'), str) and bool(data[k]['value'].strip()) for k in ('note_ch', 'note_en'))
    print('BIRTHDAY status=' + ('generated' if birthdays and birthday_ok else 'none_or_empty'))
    print('QUOTE status=' + ('generated' if quote_ok else 'empty'))


def diagnose(c, origin='provided'):
    validate_config(c)
    note = Note(('离线诊断占位', 'Offline diagnostic placeholder'), 'diagnostic_placeholder')
    payload = build_payload(c, '', datetime.now(TZ).date(), ('离线占位',) * 3, note)
    emit_summary(payload, origin, note.source, 'offline_no_messages')
    print('NETWORK not_used; WEATHER diagnostic_placeholder; TOKEN not_requested; MESSAGES not_sent')


def send_message(session, token, payload):
    data = request_json(session, 'post', 'https://api.weixin.qq.com/cgi-bin/message/template/send',
                        '发送微信模板消息（网络异常时送达状态未知，核实前不要重跑）',
                        params={'access_token': token}, json=payload)
    code = data.get('errcode')
    if type(code) is not int or code != 0:
        safe_code = code if type(code) is int else '未知'
        # 不输出微信回包 errmsg，避免回显收件人或请求内容。
        raise AppError(f'微信发送失败（errcode={safe_code}）；请核对模板、接收人及账号权限')


def run(c, session=None, now=None, config_origin='provided'):
    validate_config(c)
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise AppError('测试/调用方传入的时间必须含时区')
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    # 所有日期/模板在第一次发送之前预检，避免半途遇到日期错误。
    for user in c['user']:
        build_payload(c, user, today, ('暂无数据',) * 3, ('', ''))
    client = session if session is not None else requests.Session()
    try:
        token = get_access_token(client, c)
        weather = get_weather(client, c)
        note = get_ciba(client)
        failed = 0
        for index, user in enumerate(c['user'], 1):
            try:
                payload = build_payload(c, user, today, weather, note)
                if index == 1:
                    emit_summary(payload, config_origin, getattr(note, 'source', 'unknown'), 'before_real_send')
                send_message(client, token, payload)
                print(f'收件人 #{index}：微信接口已受理（不等于终端确认送达）')
            except AppError as exc:
                failed += 1
                print(f'收件人 #{index}：{exc}', file=sys.stderr)
        if failed:
            raise AppError(f'本次有 {failed} 位收件人失败或状态未知；已成功的不会回滚，勿直接重跑全部')
    finally:
        if session is None:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='微信模板消息推送；默认会真实发送')
    parser.add_argument('--check-config', action='store_true', help='只离线校验配置与日期，不访问网络、不发送')
    parser.add_argument('--list-fields', action='store_true', help='只列出模板字段名，不输出值、不联网、不发送')
    parser.add_argument('--diagnose', action='store_true', help='离线脱敏诊断，不联网、不获取令牌、不发送')
    args = parser.parse_args(argv)
    try:
        c = load_config()
        if args.diagnose:
            diagnose(c, config_source())
            return 0
        if args.list_fields:
            payload = build_payload(c, '', datetime.now(TZ).date(), ('',) * 3, ('', ''))
            print('\n'.join(payload['data']))
            return 0
        if args.check_config:
            today = datetime.now(TZ).date()
            for user in c['user']:
                build_payload(c, user, today, ('暂无数据',) * 3, ('', ''))
            print('离线配置与日期校验通过；未访问网络，未发送消息')
            return 0
        run(c, config_origin=config_source())
        return 0
    except AppError as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1
    except Exception:
        # 不让 traceback 将配置/令牌/完整 URL 带入 Actions 日志。
        print('错误：未预期的运行异常；请先运行离线测试，不要反复触发真实发送', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
