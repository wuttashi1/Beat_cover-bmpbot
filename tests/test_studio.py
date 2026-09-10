import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from PIL import Image, ImageChops

# Never touch the repository's existing user database or preset files in tests.
DATA = tempfile.TemporaryDirectory()
os.environ['BOT_DB_PATH'] = str(Path(DATA.name)/'users.db')
os.environ['BPM_PRESETS_DIR'] = str(Path(DATA.name)/'presets')
import bot
from cover_settings import builtin, validate_value
from cover_store import CoverStore
from cover_renderer import render, encode
from styles import style_vevo, style_explicit
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler
from telegram.request import BaseRequest


class SettingsTests(unittest.TestCase):
    def test_reject_invalid_numbers(self):
        for value in ('nan','inf','1.5',-1,101):
            with self.assertRaises(ValueError):
                validate_value('watermark_opacity',value)

    def test_reject_paths_and_colors(self):
        for value in ('../file','x.png','/tmp/test',''):
            with self.assertRaises(ValueError):
                validate_value('filename',value)
        with self.assertRaises(ValueError):
            validate_value('background_color','red')
        self.assertEqual(validate_value('background_color','#aabbcc'),'#AABBCC')

    def test_profile_restart_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite'
            store=CoverStore(path)
            settings=store.get(1,{'style':'explicit','explicit_fg_size':950,'explicit_quality':'best'})
            self.assertEqual(settings['export_format'],'PNG')
            settings['brightness']=130
            store.put(1,settings)
            self.assertEqual(CoverStore(path).get(1)['brightness'],130)
            self.assertEqual(store.get(2)['brightness'],100)
            self.assertEqual(store.get(1)['explicit_fg_size'],950)

    def test_snapshot_crud_and_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CoverStore(Path(directory)/'db')
            store.save(1,'Мой стиль',builtin('explicit'))
            pid=store.list(1)[0][0]
            with self.assertRaises(ValueError):
                store.load(2,pid)
            store.delete(2,pid)
            store.rename(1,pid,'Новый')
            self.assertEqual(store.load(1,pid)[0],'Новый')
            with self.assertRaises(ValueError):
                store.save(1,'Новый',builtin('vevo'))
            store.put(1,builtin('vevo'))
            self.assertEqual(store.load(1,pid)[1]['style'],'explicit')
            store.delete(1,pid)
            self.assertEqual(store.list(1),[])

    def test_old_database_migration_by_column_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.db'
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE users(user_id INTEGER PRIMARY KEY, style TEXT, notifications_enabled INTEGER, vevo_wm_size INTEGER)")
                db.execute("INSERT INTO users VALUES(12,'explicit',1,500)")
            env=dict(os.environ,BOT_DB_PATH=str(path))
            code="import database; s=database.get_user_settings(12); assert s['vevo_wm_size']==500; assert s['notifications_enabled']; assert s['explicit_blur']==10"
            subprocess.run([sys.executable,'-c',code],env=env,check=True,capture_output=True)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image=Image.new('RGB',(500,700),'#285080')
        cls.image.paste('#db6942',(0,0,250,350))

    def test_original_presets_pixel_compatible(self):
        for style in ('vevo','explicit'):
            s=builtin(style)
            expected=style_vevo(self.image,450) if style=='vevo' else style_explicit(self.image,300,10,820,'good')
            actual=render(self.image,s)
            self.assertIsNone(ImageChops.difference(expected.convert('RGB'),actual).getbbox())

    def test_export_dimensions_formats_and_quality(self):
        s=builtin('vevo');s.update(watermark_enabled='off',resolution='1280x720',filename='WUTSHY')
        image=render(self.image,s)
        self.assertEqual(image.size,(1280,720))
        for fmt in ('JPEG','PNG','WEBP'):
            s['export_format']=fmt
            output=encode(image,s)
            with Image.open(output) as decoded:
                self.assertEqual(decoded.format,fmt)
                self.assertEqual(decoded.size,(1280,720))
            self.assertTrue(output.name.startswith('WUTSHY.'))
        s['export_format']='JPEG';s['jpeg_quality']=40
        low=encode(image,s).getvalue();s['jpeg_quality']=100
        self.assertNotEqual(low,encode(image,s).getvalue())

    def test_contain_and_solid_background(self):
        s=builtin('vevo');s.update(fit='contain',background_color='#12AB34',watermark_enabled='off')
        image=render(self.image,s)
        self.assertEqual(image.getpixel((0,0)),(18,171,52))
        s.update(style='explicit',background='solid',explicit_fg_size=500,foreground_x=100,foreground_y=100)
        image=render(self.image,s)
        self.assertEqual(image.getpixel((0,0)),(18,171,52))
        self.assertNotEqual(image.getpixel((1800,1000)),(18,171,52))

    def test_exif_orientation(self):
        image=Image.new('RGB',(20,10),'red')
        image.getexif()[274]=6
        s=builtin('vevo');s.update(fit='contain',watermark_enabled='off')
        result=render(image,s)
        self.assertEqual(result.getpixel((500,540)),(16,16,16))
        self.assertEqual(result.getpixel((960,100)),(255,0,0))


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.calls=[]
    async def initialize(self): pass
    async def shutdown(self): pass
    async def do_request(self,url,method,request_data=None,**kwargs):
        api=url.rsplit('/',1)[-1]
        params=request_data.parameters if request_data else {}
        self.calls.append((api,params))
        if api=='getMe':
            result={'id':999,'is_bot':True,'first_name':'Studio','username':'studio_test_bot'}
        elif api=='answerCallbackQuery':
            result=True
        else:
            result={'message_id':len(self.calls),'date':1,'chat':{'id':params.get('chat_id',42),'type':'private'},'text':params.get('text','')}
        return 200,json.dumps({'ok':True,'result':result}).encode()


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.request=FakeTelegram()
        self.app=ApplicationBuilder().token('999:TEST').request(self.request).get_updates_request(FakeTelegram()).build()
        self.app.add_handler(bot.build_conversation())
        self.app.add_handler(CommandHandler('savepreset',bot.savepreset_command))
        await self.app.initialize()
        self.seq=0

    async def asyncTearDown(self):
        await self.app.shutdown()

    async def send(self,text=None,callback=None,uid=42):
        self.seq+=1
        user={'id':uid,'first_name':'Tester','is_bot':False}
        message={'message_id':self.seq,'date':1,'chat':{'id':uid,'type':'private'},'from':user,'text':text or 'Menu'}
        data={'update_id':self.seq}
        if callback:
            data['callback_query']={'id':str(self.seq),'from':user,'chat_instance':'test','message':message,'data':callback}
        else:
            if text.startswith('/'):
                message['entities']=[{'type':'bot_command','offset':0,'length':len(text.split()[0])}]
            data['message']=message
        await self.app.process_update(Update.de_json(data,self.app.bot))

    def messages(self):
        return '\n'.join(str(p.get('text','')) for _,p in self.request.calls)

    async def test_cover_edit_save_and_load_from_actual_conversation(self):
        await self.send('/start')
        await self.send('🖼 Обложки')
        await self.send(callback='cv:field:brightness')
        await self.send('not a number')
        self.assertIn('Введи целое число',self.messages())
        await self.send('135')
        self.assertEqual(bot.studio.profile(42)['brightness'],135)
        await self.send('💾 Сохранить обложку как пресет')
        await self.send('Integration preset')
        pid=next(pid for pid,name in bot.studio.store.list(42) if name=='Integration preset')
        await self.send('🎬 VEVO — оригинал')
        self.assertEqual(bot.studio.profile(42)['brightness'],100)
        await self.send(callback=f'cv:load:{pid}')
        self.assertEqual(bot.studio.profile(42)['brightness'],135)

    async def test_bpm_save_input_not_swallowed_and_retempo(self):
        await self.send('/start')
        await self.send('🎵 BPM и таймкоды')
        await self.send('🔢 BPM таймкоды')
        await self.send('120')
        await self.send('🧩 Структура по умолчанию')
        await self.send('/savepreset')
        await self.send('Test BPM')
        self.assertEqual(bot.load_presets(42)['Test BPM']['bpm'],120)
        from studio_ui import digest
        await self.send(callback='bp:tempo:own:'+digest('Test BPM'))
        await self.send('60')
        self.assertEqual(self.app.user_data[42]['last_bpm'],60)
        self.assertIn('0:32 - Chorus',self.messages())

    async def test_home_cancels_pending_name_and_bpm_structure(self):
        await self.send('/start')
        await self.send('💾 Сохранить обложку как пресет')
        await self.send('🏠 Главное меню')
        self.assertNotIn('studio_input',self.app.user_data[42])
        await self.send('🔢 BPM таймкоды')
        await self.send('100')
        await self.send('✍️ Своя структура')
        await self.send('🖼 Обложки')
        self.assertNotIn('awaiting_custom_structure',self.app.user_data[42])

    async def test_callback_after_restart_and_other_user_access(self):
        bot.studio.store.save(77,'Private',builtin('explicit'))
        pid=bot.studio.store.list(77)[0][0]
        await self.send(callback=f'cv:load:{pid}',uid=78)
        self.assertIn('Пресет не найден',self.messages())
        self.assertEqual(bot.studio.profile(78)['style'],'vevo')

    async def test_buttons_and_image_documents_are_routable(self):
        conv=bot.build_conversation()
        message={'message_id':1,'date':1,'chat':{'id':42,'type':'private'},'from':{'id':42,'first_name':'T','is_bot':False},
                 'document':{'file_id':'test','file_unique_id':'unique','mime_type':'image/png','file_name':'test.png'}}
        update=Update.de_json({'update_id':1,'message':message},self.app.bot)
        self.assertTrue(bot.IMAGE_FILTER.check_update(update))
        for handlers in conv.states.values():
            self.assertTrue(any(h.check_update(update) for h in handlers))


if __name__=='__main__':
    unittest.main()
