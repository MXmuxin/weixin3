import contextlib
import copy
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from zhdate import ZhDate
import main as app


def config():
    return {'app_id': 'test-app', 'app_secret': 'test-secret', 'template_id': 'test-template',
            'user': ['fake-openid-a', 'fake-openid-b'], 'province': '北京', 'city': '北京',
            'love_date': '2000-01-01',
            'birthday1': {'name': '测试甲', 'birthday': '2000-02-29'},
            'birthday2': {'name': '测试乙', 'birthday': 'r2000-01-01'}}


def response(data=None, text='', error=None):
    r = Mock()
    r.json.return_value = data
    r.text = text
    if error:
        r.raise_for_status.side_effect = error
    return r


def session():
    s = Mock()
    s.get.side_effect = [response({'access_token': 'fake-token'}),
                        response(text='var dataSK = {"weatherinfo":{"weather":"晴","temp":"20℃","tempn":"10℃"}};var other={};'),
                        response({'hitokoto': '好心情。'})]
    s.post.return_value = response({'errcode': 0})
    return s


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # 防止测试遗漏 mock 时实际联网；生产代码只通过注入的 Mock Session 测试。
        self.network_guard = patch('requests.sessions.Session.request', side_effect=AssertionError('禁止联网'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def test_config_legacy_safe_literal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.txt'
            p.write_text('# legacy comment\n' + repr(config()), encoding='utf-8')
            self.assertEqual(app.load_config(p, {}), config())

    def test_env_overrides_and_json(self):
        c = app.load_config(environ={'CONFIG_JSON': json.dumps(config()), 'APP_SECRET': 'new-secret',
                                     'WECHAT_USERS': '["fake-other"]'})
        self.assertEqual(c['app_secret'], 'new-secret')
        self.assertEqual(c['user'], ['fake-other'])

    def test_config_missing_or_invalid(self):
        for key, val in [('app_id', ''), ('app_secret', 'YOUR_APP_SECRET'), ('user', 'bad'),
                         ('user', []), ('user', ['a', 'a']), ('user', [1]), ('province', '不存在'),
                         ('city', []), ('love_date', '2023-02-29'), ('birthday1', []),
                         ('birthday2', {'name': '', 'birthday': 'r2000-01-01'})]:
            with self.subTest(key=key, kind=type(val).__name__):
                c = config(); c[key] = val
                with self.assertRaises(app.AppError):
                    app.validate_config(c)
        with self.assertRaises(app.AppError):
            app.load_config('/file/does/not/exist', {})
        for text in ['[]', 'null', '{invalid', "__import__('os').system('echo should-not-run')"]:
            with self.subTest(text=text), self.assertRaises(app.AppError):
                app.load_config(environ={'CONFIG_JSON': text})
        with self.assertRaises(app.AppError):
            app.load_config(environ={'CONFIG_JSON': json.dumps(config()), 'WECHAT_USERS': 'oops'})

    def test_empty_optional_secrets_keep_full_config(self):
        c = app.load_config(environ={'CONFIG_JSON': json.dumps(config()), 'APP_ID': '', 'APP_SECRET': '',
                                     'TEMPLATE_ID': '', 'WECHAT_USERS': ''})
        self.assertEqual(c, config())

    def test_placeholder_cannot_send(self):
        c = config()
        c['app_secret'] = 'YOUR_APP_SECRET'
        with self.assertRaises(app.AppError):
            app.validate_config(c)

    def test_success_all_fields_and_recipients(self):
        s = session()
        with contextlib.redirect_stdout(io.StringIO()):
            app.run(config(), s, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(s.post.call_count, 2)
        payload = s.post.call_args_list[0].kwargs['json']
        self.assertEqual(set(payload['data']), {'date', 'city', 'weather', 'min_temperature',
                         'max_temperature', 'love_day', 'note_en', 'note_ch', 'birthday1', 'birthday2'})
        self.assertEqual(payload['data']['max_temperature']['value'], '20℃')
        self.assertEqual(payload['data']['min_temperature']['value'], '10℃')
        self.assertEqual([c.kwargs['json']['touser'] for c in s.post.call_args_list], config()['user'])
        self.assertEqual(payload['data']['date']['value'], '2026-11-08 星期日')
        for call in s.get.call_args_list + s.post.call_args_list:
            self.assertEqual(call.kwargs['timeout'], app.TIMEOUT)

    def test_timezone_and_year_boundary(self):
        s = session()
        with contextlib.redirect_stdout(io.StringIO()):
            app.run(config(), s, datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc))
        payload = s.post.call_args.kwargs['json']
        self.assertEqual(payload['data']['date']['value'], '2027-01-01 星期五')
        self.assertEqual(payload['data']['love_day']['value'], str((date(2027, 1, 1) - date(2000, 1, 1)).days))

    def test_gregorian_leap_day_and_cross_year(self):
        self.assertEqual(app.get_birthday('2000-02-29', date(2024, 2, 29)), 0)
        self.assertEqual(app.get_birthday('2000-02-29', date(2025, 2, 28)), (date(2028, 2, 29)-date(2025, 2, 28)).days)
        self.assertEqual(app.get_birthday('2000-01-01', date(2026, 12, 31)), 1)
        self.assertEqual(app.get_birthday('2000-11-08', date(2026, 11, 8)), 0)
        self.assertEqual(app.get_birthday('2000-02-29', date(2099, 3, 1)), (date(2104, 2, 29)-date(2099, 3, 1)).days)

    def test_lunar_previous_year_twelfth_month(self):
        # 农历2025年腊月初一在公历2026年，不能错误地跳到农历2026年。
        expected = ZhDate(2025, 12, 1).to_datetime().date()
        today = date(2026, 1, 1)
        self.assertGreater(expected, today)
        self.assertEqual(app.get_birthday('r2000-12-01', today), (expected-today).days)
        self.assertEqual(app.get_birthday('r2000-12-01', expected), 0)
        self.assertEqual(app.get_birthday('r2000-01-01', date(2026, 2, 17)), 0)

    def test_lunar_day_30_search_and_bounds(self):
        today = date(2026, 1, 1)
        candidates = []
        for y in range(2025, 2047):
            try:
                d = ZhDate(y, 12, 30).to_datetime().date()
                if d >= today:
                    candidates.append(d)
            except TypeError:
                pass
        # r2000-12-30 在该年不存在，采用存在的出生年。
        birthyear = next(y for y in range(1900, 2001) if ZhDate.validate(y, 12, 30, False))
        self.assertEqual(app.get_birthday(f'r{birthyear}-12-30', today), (min(candidates)-today).days)
        with self.assertRaises(app.AppError):
            app.get_birthday('r2000-01-01', date(2101, 1, 1))

    def test_invalid_birthday_and_happy_message(self):
        for v in ['2001-02-29', 'r1899-01-01', 'r2000-13-01', 'bad', None, 1]:
            with self.subTest(v=v), self.assertRaises(app.AppError):
                app.birthday_parts(v)
        c = config(); c['birthday1']['birthday'] = '2000-11-08'
        p = app.build_payload(c, 'fake', date(2026, 11, 8), ('晴', '20', '10'), ('中', 'en'))
        self.assertIn('今天测试甲生日', p['data']['birthday1']['value'])

    def test_token_errors_no_sends_and_no_secrets_in_error(self):
        for r in [response({'errcode': 40164, 'errmsg': 'private detail'}), response([]),
                  response(error=requests.HTTPError('private URL')), response({'access_token': ''})]:
            s = Mock(); s.get.return_value = r
            with self.subTest(), self.assertRaises(app.AppError) as caught:
                app.run(config(), s)
            self.assertNotIn('private', str(caught.exception))
            s.post.assert_not_called()
        for exc in [requests.Timeout('secret-url'), requests.ConnectionError('secret-url'), ValueError('bad json')]:
            s = Mock(); s.get.side_effect = exc
            with self.subTest(), self.assertRaises(app.AppError) as caught:
                app.get_access_token(s, config())
            self.assertNotIn('secret-url', str(caught.exception))

    def test_bad_token_json(self):
        r = response(); r.json.side_effect = ValueError('html')
        s = Mock(); s.get.return_value = r
        with self.assertRaises(app.AppError):
            app.get_access_token(s, config())

    def test_weather_fallbacks(self):
        for value in ['<html>bad</html>', 'var a = __import__("os").system("bad");',
                      'var a = {};', 'var a = {"weatherinfo":null};',
                      'var a = {"weatherinfo":{"weather":1,"temp":"2","tempn":"3"}};']:
            s = Mock(); s.get.return_value = response(text=value)
            with self.subTest(), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.get_weather(s, config()), ('暂无数据',)*3)
        for exc in [requests.Timeout(), requests.HTTPError()]:
            s = Mock(); s.get.side_effect = exc
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.get_weather(s, config()), ('暂无数据',)*3)

    def test_ciba_fallback(self):
        for r in [response({}), response({'note': [], 'content': 'hi'}), response(error=requests.HTTPError()), response([])]:
            s = Mock(); s.get.return_value = r
            with self.subTest(), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.get_ciba(s), ('愿你今天拥有好心情。', 'Always here for you.'))
        s = Mock(); s.get.side_effect = requests.Timeout()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.get_ciba(s)[1], 'Always here for you.')

    def test_optional_apis_fail_still_send(self):
        s = session(); s.get.side_effect = [response({'access_token': 'fake'}), requests.Timeout(), response({})]
        with contextlib.redirect_stdout(io.StringIO()):
            app.run(config(), s)
        self.assertEqual(s.post.call_count, 2)
        self.assertEqual(s.post.call_args.kwargs['json']['data']['weather']['value'], '暂无数据')

    def test_send_rejections_no_retry_continue_and_fail(self):
        s = session(); s.post.side_effect = [response({'errcode': 40003, 'errmsg': 'fake-openid-a'}), response({'errcode': 0})]
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log), self.assertRaises(app.AppError):
            app.run(config(), s)
        self.assertEqual(s.post.call_count, 2)
        self.assertNotIn('fake-openid-a', log.getvalue())
        self.assertIn('40003', log.getvalue())

    def test_send_timeout_unknown_no_retry(self):
        s = session(); s.post.side_effect = [requests.Timeout('fake-secret'), response({'errcode': 0})]
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log), self.assertRaises(app.AppError):
            app.run(config(), s)
        self.assertEqual(s.post.call_count, 2)
        self.assertIn('状态未知', log.getvalue())
        self.assertNotIn('fake-secret', log.getvalue())

    def test_malformed_send_reply_fails(self):
        for data in [{}, [], {'errcode': False}, {'errcode': '0'}, {'errcode': 40037}]:
            s = Mock(); s.post.return_value = response(data)
            with self.subTest(data=data), self.assertRaises(app.AppError):
                app.send_message(s, 'fake', {})

    def test_main_failure_exit_codes(self):
        with patch.object(app, 'load_config', side_effect=app.AppError('missing')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main([]), 1)
        with patch.object(app, 'load_config', return_value=config()), patch.object(app, 'run', side_effect=app.AppError('send failure')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main([]), 1)
        with patch.object(app, 'load_config', return_value=config()), patch.object(app, 'run'):
            self.assertEqual(app.main([]), 0)

    def test_safe_check_config_no_network(self):
        with patch.object(app, 'load_config', return_value=config()), patch.object(app, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--check-config']), 0)
            run.assert_not_called()

    def test_batch_failure_produces_nonzero_main_exit(self):
        s = session(); s.post.side_effect = [response({'errcode': 40037}), response({'errcode': 0})]
        original_run = app.run
        with patch.object(app, 'load_config', return_value=config()), patch.object(app, 'run', side_effect=lambda c, **kw: original_run(c, session=s, **kw)), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([]), 1)
        self.assertEqual(s.post.call_count, 2)

    def test_optional_detail_url(self):
        c = config()
        args = ('fake', date(2026, 11, 8), ('晴', '20', '10'), ('中', 'en'))
        original = app.build_payload(c, *args)
        self.assertNotIn('url', original)
        c['detail_url'] = ''
        self.assertNotIn('url', app.build_payload(c, *args))
        c['detail_url'] = 'https://example.com/message'
        app.validate_config(c)
        payload = app.build_payload(c, *args)
        self.assertEqual(payload['url'], c['detail_url'])
        self.assertEqual(payload['data'].keys(), original['data'].keys())
        for k in payload['data']:
            self.assertEqual(payload['data'][k]['value'], original['data'][k]['value'])

    def test_detail_url_validation_and_override(self):
        for url in ['javascript:alert(1)', '/relative', 'https://', 'https://user:pw@example.com', 'https://example.com/a\nb', 3, None]:
            c = config(); c['detail_url'] = url
            with self.subTest(kind=type(url).__name__), self.assertRaises(app.AppError):
                app.validate_config(c)
        c = app.load_config(environ={'CONFIG_JSON': json.dumps(config()), 'DETAIL_URL': 'https://example.com/message'})
        self.assertEqual(c['detail_url'], 'https://example.com/message')
        c = app.load_config(environ={'CONFIG_JSON': json.dumps(config()), 'DETAIL_URL': ''})
        self.assertNotIn('detail_url', c)

    def test_list_fields_is_offline_and_values_hidden(self):
        log = io.StringIO()
        with patch.object(app, 'load_config', return_value=config()), patch.object(app, 'run') as run, contextlib.redirect_stdout(log):
            self.assertEqual(app.main(['--list-fields']), 0)
            run.assert_not_called()
        self.assertEqual(set(log.getvalue().splitlines()), {'date', 'city', 'weather', 'min_temperature', 'max_temperature', 'love_day', 'note_ch', 'note_en', 'birthday1', 'birthday2'})
        self.assertNotIn('测试甲', log.getvalue())

    def test_all_city_table_entries_preserved(self):
        self.assertEqual(len(app.cityinfo.cityInfo), 34)
        self.assertEqual(sum(len(c) for c in app.cityinfo.cityInfo.values()), 375)
        self.assertTrue(all('AREAID' in d for cities in app.cityinfo.cityInfo.values() for d in cities.values()))

    def test_no_mutation_or_naive_datetime(self):
        c = config(); before = copy.deepcopy(c)
        app.validate_config(c)
        self.assertEqual(c, before)
        s = session()
        with self.assertRaises(app.AppError):
            app.run(c, s, datetime(2026, 1, 1))
        s.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
