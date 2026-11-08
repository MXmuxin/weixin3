import contextlib
from datetime import datetime, timezone
import io
import json
import unittest
from unittest.mock import patch
import requests
import main as app
from test_main import config, session, response


class DiagnosticTest(unittest.TestCase):
    def test_diagnose_no_network_token_send_or_api(self):
        c = config(); c['detail_url'] = 'https://example.com/private-url-sentinel'
        log = io.StringIO()
        with patch.object(app, 'load_config', return_value=c), patch.dict('os.environ', {}, clear=True), \
                patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')), \
                patch('requests.Session', side_effect=AssertionError('session forbidden')), \
                patch.object(app, 'get_access_token', side_effect=AssertionError('token forbidden')) as token, \
                patch.object(app, 'send_message', side_effect=AssertionError('send forbidden')) as send, \
                patch.object(app, 'get_ciba', side_effect=AssertionError('quote API forbidden')) as quote, \
                patch.object(app, 'get_weather', side_effect=AssertionError('weather forbidden')) as weather, \
                patch.object(app, 'run', side_effect=AssertionError('publisher forbidden')) as run, \
                contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            self.assertEqual(app.main(['--diagnose']), 0)
        for m in (token, send, quote, weather, run):
            m.assert_not_called()
        text = log.getvalue()
        for value in [c['app_id'], c['app_secret'], c['template_id'], c['love_date'],
                      *c['user'], c['birthday1']['name'], c['birthday1']['birthday'],
                      c['birthday2']['name'], c['birthday2']['birthday'], c['detail_url'], c['city']]:
            self.assertNotIn(value, text)
        self.assertIn('version=wechat-private-v4-hitokoto-1', text)
        self.assertIn('CONFIG source=file', text)
        self.assertIn('QUOTE source=diagnostic_placeholder', text)
        self.assertIn('MESSAGES not_sent', text)
        self.assertIn('FIELD key=birthday1 present=True', text)
        self.assertIn('FIELD key=note_en present=True', text)

    def test_source_types_only_no_paths(self):
        self.assertEqual(app.config_source({}), 'file')
        self.assertEqual(app.config_source({'CONFIG_PATH': '/private/sentinel'}), 'file')
        self.assertEqual(app.config_source({'CONFIG_JSON': 'secret contents'}), 'environment')
        self.assertEqual(app.config_source({'APP_SECRET': 'secret'}), 'file_with_environment_overrides')
        self.assertEqual(app.config_source({'CONFIG_JSON': 'secret', 'DETAIL_URL': 'secret'}), 'environment_with_environment_overrides')

    def test_real_send_summary_once_before_send_and_keys_preserved(self):
        s = session(); log = io.StringIO(); snapshots = []
        expected = {'date','city','weather','min_temperature','max_temperature','love_day',
                    'birthday1','birthday2','note_ch','note_en'}
        def post(*args, **kw):
            self.assertIn('mode=before_real_send', log.getvalue())
            self.assertEqual(set(kw['json']['data']), expected)
            snapshots.append(kw['json'])
            return response({'errcode':0})
        s.post.side_effect = post
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(log):
            app.run(config(), s, datetime(2026,11,8,tzinfo=timezone.utc), config_origin='file')
        self.assertEqual(len(snapshots),2)
        self.assertEqual(log.getvalue().count('DIAGNOSTIC version='),1)
        self.assertIn('QUOTE source=hitokoto_online', log.getvalue())
        for value in [config()['app_id'],config()['app_secret'],config()['template_id'],*config()['user'],config()['birthday1']['name']]:
            self.assertNotIn(value, log.getvalue())

    def test_fallback_source_and_data_not_invented_as_online(self):
        s = session(); s.get.side_effect = [response({'access_token':'fake'}),response(text='bad weather'),requests.Timeout()]
        log=io.StringIO()
        with patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')),contextlib.redirect_stdout(log):
            app.run(config(),s)
        self.assertIn('QUOTE source=fallback',log.getvalue())
        self.assertEqual(s.post.call_args.kwargs['json']['data']['note_en']['value'],'Always here for you.')

    def test_unknown_injected_quote_source_not_mislabelled(self):
        s=session();log=io.StringIO()
        with patch.object(app,'get_ciba',return_value=('mock-zh','mock-en')),contextlib.redirect_stdout(log):
            app.run(config(),s)
        self.assertIn('QUOTE source=unknown',log.getvalue())


if __name__=='__main__':
    unittest.main()
