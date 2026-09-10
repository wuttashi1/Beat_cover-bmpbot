import json
import unittest
from unittest.mock import patch, MagicMock
import test_studio as fixtures
import test_access as access_fixtures
import bot
from cover_settings import FIELDS, builtin
from cover_renderer import render
from PIL import Image, ImageChops


class InlineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = access_fixtures.AccessTests.asyncSetUp
    asyncTearDown = access_fixtures.AccessTests.asyncTearDown
    send = fixtures.ConversationTests.send
    messages = fixtures.ConversationTests.messages

    async def test_retained_message_only_filter_and_real_get_updates(self):
        original = self.request.do_request
        subscription = ['message']
        pending = [{'update_id':999, 'callback_query':{
            'id':'query-999','chat_instance':'test','from':{'id':42,'first_name':'Owner','is_bot':False},
            'message':{'message_id':500,'date':1,'from':{'id':999,'first_name':'Bot','is_bot':True},
                       'chat':{'id':42,'type':'private'},'text':'Settings'},
            'data':'cv:set:export_format:PNG'}}]
        async def request(url, method, request_data=None, **kwargs):
            nonlocal subscription
            if url.endswith('/getUpdates'):
                params=request_data.parameters
                if 'allowed_updates' in params:
                    subscription=params['allowed_updates']
                    if isinstance(subscription,str):
                        subscription=json.loads(subscription)
                return 200,json.dumps({'ok':True,'result':pending if 'callback_query' in subscription else []}).encode()
            return await original(url,method,request_data=request_data,**kwargs)
        with patch.object(self.request,'do_request',side_effect=request), patch.object(self.app.bot._request[0],'do_request',side_effect=request):
            self.assertEqual(await self.app.bot.get_updates(), ())
            updates=await self.app.bot.get_updates(allowed_updates=['message','callback_query'])
            self.assertEqual(len(updates),1)
            await self.app.process_update(updates[0])
        self.assertEqual(bot.studio.profile(42)['export_format'],'PNG')
        self.assertTrue(any(api=='answerCallbackQuery' for api,_ in self.request.calls))

    async def test_every_cover_setting_callback_and_numeric_input(self):
        await self.send('/start')
        for key,(_,spec,_) in FIELDS.items():
            with self.subTest(key=key):
                await self.send(callback=f'cv:field:{key}')
                if isinstance(spec,dict):
                    for value in spec:
                        await self.send(callback=f'cv:set:{key}:{value}')
                        self.assertEqual(bot.studio.profile(42)[key],value)
                else:
                    value = '#123456' if spec=='color' else ('my-cover' if spec=='filename' else spec[0])
                    await self.send(str(value))
                    self.assertEqual(bot.studio.profile(42)[key],value)

    async def test_base_covers_folder_and_back(self):
        from studio_ui import COVER_ROWS
        self.assertNotIn('🎬 VEVO — оригинал',str(COVER_ROWS))
        await self.send('/start')
        await self.send('🖼 Обложки')
        await self.send('🗂 Обложки и пресеты')
        await self.send('📦 Базовые обложки')
        self.assertIn('🎬 VEVO — оригинал',str(self.request.calls[-1][1]['reply_markup']))
        await self.send('🔞 EXPLICIT — оригинал')
        self.assertEqual(bot.studio.profile(42)['style'],'explicit')
        await self.send('⬅️ К обложкам')
        self.assertIn('🗂 Обложки и пресеты',str(self.request.calls[-1][1]['reply_markup']))

    async def test_expired_acknowledgement_still_saves_setting(self):
        original = self.request.do_request
        async def request(url, method, request_data=None, **kwargs):
            if url.endswith('/answerCallbackQuery'):
                return 400,json.dumps({'ok':False,'description':'Bad Request: query is too old and response timeout expired or query ID is invalid'}).encode()
            return await original(url,method,request_data=request_data,**kwargs)
        with patch.object(self.request,'do_request',side_effect=request):
            await self.send(callback='cv:set:watermark_position:top_right')
        self.assertEqual(bot.studio.profile(42)['watermark_position'],'top_right')


class StartupAndPixelsTests(unittest.TestCase):
    def test_production_main_explicitly_subscribes_callbacks(self):
        app=MagicMock()
        with patch.object(bot,'BOT_TOKEN','999:TEST'),patch.object(bot,'ADMIN_USER_ID',42),patch.object(bot,'PUBLISH_CHANNEL','@test'),patch.object(bot,'ApplicationBuilder') as builder:
            builder.return_value.token.return_value.build.return_value=app
            bot.main()
        app.run_polling.assert_called_once_with(allowed_updates=['message','callback_query'])

    def test_contrast_and_watermark_position_change_pixels(self):
        image=Image.new('RGB',(1920,1080),(110,130,150))
        image.paste((40,60,80),(0,0,960,1080))
        settings=builtin('vevo')
        initial=render(image,settings)
        settings['watermark_position']='top_right'
        moved=render(image,settings)
        diff=ImageChops.difference(initial,moved)
        self.assertIsNotNone(diff.crop((1200,0,1920,400)).getbbox())
        self.assertIsNotNone(diff.crop((0,650,650,1080)).getbbox())
        settings['contrast']=150
        contrast=render(image,settings)
        self.assertNotEqual(moved.getpixel((800,500)),contrast.getpixel((800,500)))
