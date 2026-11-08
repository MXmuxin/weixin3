import contextlib
import io
import unittest
from unittest.mock import Mock, patch
import requests
import main as app


class HitokotoTest(unittest.TestCase):
    def setUp(self):
        p=patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden'))
        p.start();self.addCleanup(p.stop)

    def test_success_mapping_source_and_single_request(self):
        s=Mock();r=Mock();r.json.return_value={'hitokoto':'春风又绿江南岸。','from':'示例出处','from_who':'示例作者'};s.get.return_value=r
        result=app.get_ciba(s)
        self.assertEqual(result[0],'春风又绿江南岸。')
        self.assertEqual(result[1],app.CARE_EN)
        self.assertEqual(result.source,'hitokoto_online')
        s.get.assert_called_once_with('https://v1.hitokoto.cn/',timeout=app.TIMEOUT,params={'c':'i','encode':'json','min_length':1,'max_length':20})

    def test_timeout_http_bad_json_and_empty_fallback(self):
        for kind in ('timeout','http','json','empty','null','list','wrong_type'):
            s=Mock();r=Mock();s.get.return_value=r
            r.json.return_value={'hitokoto':'正常句'}
            if kind=='timeout':s.get.side_effect=requests.Timeout()
            elif kind=='http':r.raise_for_status.side_effect=requests.HTTPError()
            elif kind=='json':r.json.side_effect=ValueError('bad')
            elif kind=='empty':r.json.return_value={'hitokoto':' \n '}
            elif kind=='null':r.json.return_value={}
            elif kind=='list':r.json.return_value=[]
            elif kind=='wrong_type':r.json.return_value={'hitokoto':1}
            with self.subTest(kind=kind),contextlib.redirect_stdout(io.StringIO()):
                result=app.get_ciba(s)
                self.assertEqual(result,(app.QUOTE_FALLBACK,app.CARE_EN))
                self.assertEqual(result.source,'fallback')
                self.assertEqual(s.get.call_count,1)

    def test_long_excerpt_and_newlines(self):
        s=Mock();r=Mock();s.get.return_value=r;r.json.return_value={'hitokoto':'甲'*25+'\n乙'}
        result=app.get_ciba(s)
        self.assertEqual(result[0],'甲'*19+'…')
        self.assertEqual(len(result[0]),20)
        self.assertEqual(app.quote_excerpt('一\n\t二'),'一 二')
        self.assertLessEqual(len(app.CARE_EN),20)

    def test_source_summary_no_quote_values(self):
        log=io.StringIO()
        with contextlib.redirect_stdout(log):
            app.emit_summary({'data':{'note_ch':{'value':'不应记录的正文'},'note_en':{'value':app.CARE_EN}}},'file','hitokoto_online','before_real_send')
        self.assertIn('QUOTE source=hitokoto_online',log.getvalue())
        self.assertIn('ENGLISH source=local_care_not_translation',log.getvalue())
        self.assertNotIn('不应记录的正文',log.getvalue())
        self.assertNotIn(app.CARE_EN,log.getvalue())


if __name__=='__main__':unittest.main()
