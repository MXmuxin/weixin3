# 微信定时关怀推送：完整项目

## 先看这几条

- **必须新建Private私有仓库！本包含真实账号密钥、人物、生日和接收人配置。不要公开上传或转发。** 私有仓库也应只授权可信人员；密钥若曾泄露请在微信后台重置。
- config.txt已保留全部个人设置及3位原收件人，并填好你新建的完整标签模板ID，无需重填。旧模板不用删除。
- 没有替你创建、上传或启用远程仓库，也没有获取微信token或发送消息。
- **先停用旧仓库正式推送workflow，再启用新仓库，避免两个定时任务重复发送。**

## 目录（上传后仓库Code根目录应如此）

```
.github/
  workflows/
    weixin.yml          # 正式推送：定时+手动，会真实发给3人
    diagnostics.yml     # 仅手动：离线诊断，不联网、不发送
.gitignore
main.py
config.txt
cityinfo.py
requirements.txt
TEMPLATE_BODY.txt
README.md
DEPLOY.txt
TEST_RESULTS.md
tests/
  test_main.py
  test_diagnostics.py
  test_hitokoto.py
```

## 新仓库部署：5步

1. 在GitHub新建仓库，选择**Private**。在旧仓库Actions里停用旧的正式推送workflow；不要删除你的原配置备份。
2. 把完整项目ZIP解压。**上传解压后的全部内容，不是ZIP文件本身，也不要再套一层文件夹。** Windows资源管理器如看不到`.github`/`.gitignore`，开启“查看→显示→隐藏的项目”，并开启“文件扩展名”。两份workflow必须叫`.yml`，不能叫`.py`或`.yml.txt`。若上传界面仍不显示隐藏项，可直接把解压目录中的`.github`文件夹和`.gitignore`一起拖入上传区，并在提交后核对。
3. Code根目录必须看到`.github`文件夹，里面恰好有`workflows/weixin.yml`和`workflows/diagnostics.yml`；与main.py、config.txt同属根项目。若漏了，可解压单独工作流ZIP，合并其中`.github`到项目根目录，再上传。不能把`.github`里的两个YAML平放根目录。
4. Actions选择**微信离线诊断（不发消息）**→Run workflow。它仅手动触发，检查配置、日期和字段长度，不访问任何数据接口，不获取token、不发送。新仓库如提示启用Actions，需你自行确认；文件需在默认分支中可用。
5. 确认后自行选择：运行**微信正式推送（会发给三人）**一次，或等待原定时。正式任务每次都按原名单向3位发送，不是仅发本人。不要紧接着手动又等同一轮定时，避免重复；请到手机核对完整内容。

## 时间与任务行为

正式cron保持`0 23 * * *`：每日UTC23:00，即次日北京时间07:00、**东京时间08:00 JST**。程序所有业务日期按北京时间。GitHub定时可能延迟，也可能因仓库/平台策略暂停，不是准点服务。

只有这两个工作流；没有旧单人四字段测试任务。正式工作流包含安装依赖、离线单元测试和真实推送；诊断工作流只安装依赖、执行`python main.py --diagnose`。诊断中天气/短句是明确的离线占位，不等于在线接口可用，也不能证明旧消息的实际payload。正式推送前会有脱敏字段摘要，供后续排查，不打印实际个人值。

## 配置如何读取

新Private仓库**不设置Secrets也能用本地config.txt**，不需要重新填写。

- 非空CONFIG_JSON优先于文件，必须是完整JSON或安全Python字典文本。
- 非空APP_ID、APP_SECRET、TEMPLATE_ID、WECHAT_USERS、DETAIL_URL继续覆盖相应项。WECHAT_USERS须为JSON字符串数组。
- 如果你后来设置了CONFIG_JSON/TEMPLATE_ID，须确认其中模板ID与本包新完整模板一致，否则会覆盖已填好的配置。若设置WECHAT_USERS则也会改变真实接收名单。空Secret不覆盖本地配置。
- 本地CONFIG_PATH可指定其他配置文件；不设置就读取main.py同目录config.txt。

这些变量已与两份workflow一致映射。不要把Secret值或完整配置贴进报错截图。若计划迁移Secrets保存配置，请先核对优先级；本项目不会替你清除Git历史里的旧密钥。

## 保留功能和实际限制

保留原城市表、天气/最高最低温、日期星期、在一起天数、两个生日及生日当天祝福、农历/公历跨年计算、全部原data字段、随机颜色、批量处理与失败退出。公历2月29日按下次真实闰日；农历小月三十按下次实际存在日期，最多搜索20年；农历库1900—2100，原格式不能指定闰月。在一起当天为0天。

中文短句改用公益免key HTTPS一言诗词接口，字段hitokoto；文档：https://developer.hitokoto.cn/sentence/ 。每次最多请求一次，故障使用本地备用文案；不循环找句、不自动重试。它是随机短句，不保证每日唯一。note_en是固定本地原创英文关怀句，**不是在线英文金句，也不是中文句子的翻译**。短句超过20字符取前19加省略号，为展示摘要而非完整引文。天气仍用原数据源，失败显示“暂无数据”。没有替换成收费服务。

完整后台模板正文备份见TEMPLATE_BODY.txt，标题保留原来的温柔标题。当前config已填新完整模板ID，但**完整模板手机显示尚未最终在线验证**；四字段测试成功不能保证全部10字段都显示。末尾固定心愿可能被微信当尾部删除，显示还受平台限制。

默认不带详情URL，项目没有网页/详情托管。若自行配置detail_url或DETAIL_URL，只会链接到你已有的网页，不会自动创建详情页。

微信账号权限、IP白名单、关注关系、模板有效性、终端送达须真实环境核对。errcode=0只代表接口受理。某个收件人失败会继续处理其他人，最后任务失败；不对发送盲目重试。超时可能已送达，部分成功后重跑会重复，先核对。工作流串行化不是持久化去重。

## 本地安全检查

Python3.11，安装`python -m pip install -r requirements.txt`后：
```
python -m unittest discover -s tests -v
python main.py --check-config
python main.py --diagnose
```
以上程序检查和单元测试不访问真实接口或发送消息（安装依赖需下载包）。**不带参数的`python main.py`会真实发送。** 诊断保留代码标记`wechat-private-v4-hitokoto-1`以便识别相同生产代码，不代表存在多套项目。
