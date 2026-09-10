import unittest
import test_studio as fixtures
import database
import bot
from access_control import install, allowed


class AccessTests(unittest.IsolatedAsyncioTestCase):
    send = fixtures.ConversationTests.send
    messages = fixtures.ConversationTests.messages

    async def asyncSetUp(self):
        await fixtures.ConversationTests.asyncSetUp(self)
        with database.db:
            database.db.execute('DELETE FROM bot_access')
        install(self.app, 42)
        self.old_owner = bot.ADMIN_USER_ID
        bot.ADMIN_USER_ID = 42
        self.errors = []
        async def error_handler(update,context):
            self.errors.append(context.error)
        self.app.add_error_handler(error_handler)

    async def asyncTearDown(self):
        bot.ADMIN_USER_ID = self.old_owner
        await self.app.shutdown()
        self.assertEqual(self.errors, [])

    async def test_private_default_and_commands_callbacks_blocked(self):
        await self.send('/start', uid=81)
        await self.send('/savepreset', uid=81)
        await self.send(callback='cv:set:brightness:150', uid=81)
        self.assertIn('по приглашению',self.messages())
        self.assertFalse(allowed(81,42))
        self.assertNotIn('studio_section',self.app.user_data.get(81,{}))

    async def test_owner_can_grant_and_revoke_immediately(self):
        await self.send('/start')
        await self.send('👥 Доступ к боту')
        await self.send('➕ Добавить пользователя')
        await self.send('81')
        self.assertTrue(allowed(81,42))
        await self.send('/start',uid=81)
        await self.send('🖼 Обложки',uid=81)
        self.assertEqual(self.app.user_data[81]['studio_section'],'cover')
        await self.send('➖ Удалить пользователя')
        await self.send('81')
        self.assertFalse(allowed(81,42))
        await self.send(callback='cv:set:style:explicit',uid=81)
        self.assertNotEqual(bot.studio.profile(81)['style'],'explicit')

    async def test_invited_user_cannot_grant_or_remove_owner(self):
        with database.db:
            database.db.execute('INSERT INTO bot_access VALUES (82)')
        await self.send('➕ Добавить пользователя',uid=82)
        self.assertIn('только владельцу',self.messages())
        await self.send('➖ Удалить пользователя')
        await self.send('42')
        self.assertTrue(allowed(42,42))

    async def test_setting_change_rebuilds_cached_image(self):
        from unittest.mock import AsyncMock, patch
        await self.send('/start')
        self.app.user_data[42]['cover_source'] = 'cached-image'
        with patch.object(bot.studio, 'photo', new_callable=AsyncMock) as photo:
            await self.send(callback='cv:set:delivery:both')
            photo.assert_awaited_once()
        with patch.object(bot.studio, 'photo', new_callable=AsyncMock) as photo:
            await self.send(callback='cv:field:brightness')
            await self.send('115')
            photo.assert_awaited_once()

    async def test_settings_only_show_active_style_and_save_enum(self):
        await self.send('/start')
        await self.send('🎬 VEVO — оригинал')
        await self.send('⚙️ Настройка стиля')
        await self.send(callback='cv:group:layout')
        markup = self.request.calls[-1][1]['reply_markup']
        self.assertNotIn('cv:field:explicit_blur',str(markup))
        await self.send(callback='cv:set:style:explicit')
        self.assertEqual(bot.studio.profile(42)['style'],'explicit')
        markup = self.request.calls[-1][1]['reply_markup']
        self.assertIn('cv:field:explicit_blur',str(markup))
        await self.send(callback='cv:field:explicit_blur')
        await self.send('0')
        self.assertEqual(bot.studio.profile(42)['explicit_blur'],0)
