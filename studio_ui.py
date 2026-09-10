"""Cover editor and grouped navigation, shared by all conversation states."""
import asyncio
import hashlib
from io import BytesIO
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Inline, ReplyKeyboardMarkup
from cover_settings import FIELDS, GROUPS, builtin, validate_value
from cover_store import CoverStore
from cover_renderer import render_bytes
from database import DB_PATH, get_user_settings

HOME = "🏠 Главное меню"
COVERS = "🖼 Обложки"
BPM = "🎵 BPM и таймкоды"
MP3 = "🎧 MP3 и публикация"
SETTINGS = "⚙️ Бот"
COVER_ROWS = [["📷 Создать обложку", "⚙️ Параметры обложки"], ["💾 Сохранить обложку как пресет", "🗂 Мои обложки"], ["🎬 VEVO — оригинал", "🔞 EXPLICIT — оригинал"], ["🔄 Пересобрать обложку"], [HOME]]
BPM_ROWS = [["🔢 BPM таймкоды", "🗂 BPM пресеты"], ["💾 Сохранить BPM пресет", "🛒 BPM магазин"], ["🌍 Поделиться BPM пресетом"], [HOME]]
NAV = {HOME,COVERS,BPM,MP3,SETTINGS,"⬅️ Назад","⬅️ Назад в меню"}
NAV.update(x for row in COVER_ROWS+BPM_ROWS for x in row)
NAV.add("🔔 Уведомления о перезапуске")


def keyboard(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def digest(name):
    return hashlib.sha256(name.encode()).hexdigest()[:16]


class StudioUI:
    def __init__(self, host):
        self.host = host
        self.store = CoverStore(DB_PATH)

    def profile(self, uid):
        return self.store.get(uid,get_user_settings(uid))

    async def screen(self, update, text, rows=None):
        markup = Inline(rows) if rows else None
        await update.effective_message.reply_text(text,reply_markup=markup)

    async def section(self, update, context, section):
        context.user_data["studio_section"] = section
        if section == "cover":
            s = self.profile(update.effective_user.id)
            await update.effective_message.reply_text(
                f"🖼 Обложки · {s['style'].upper()} · {s['resolution']} · {s['export_format']}\n"
                "Отправь картинку как фото или файл. Настройки сохраняются автоматически.\n"
                "Готовые VEVO и EXPLICIT доступны ниже.",reply_markup=keyboard(COVER_ROWS))
        elif section == "bpm":
            await update.effective_message.reply_text("🎵 BPM и таймкоды\nРасчёт по BPM и структуре в тактах (4/4). Выбери действие:",reply_markup=keyboard(BPM_ROWS))
        elif section == "mp3":
            await update.effective_message.reply_text("🎧 MP3 и публикация\nОтправь MP3: разбор названия, BPM, тональность, теги и обложка. Публикация доступна публикаторам канала.",reply_markup=keyboard([[HOME]]))
        elif section == "settings":
            await update.effective_message.reply_text("⚙️ Настройки бота",reply_markup=keyboard([["🔔 Уведомления о перезапуске"],[HOME]]))
        else:
            await self.host.show_main_menu(update)
        return 0

    async def cancel_pending(self, context):
        for key in ("studio_input","awaiting_preset_name","awaiting_shared_preset_name","awaiting_custom_structure",
                    "awaiting_publisher_add","awaiting_publisher_remove","input_type","return_state",
                    "bpm_selected_structure","bpm_tempo_pending","access_action"):
            context.user_data.pop(key,None)
        # Leaving a section cancels the MP3 draft so an old cover prompt cannot eat a new thumbnail.
        await self.host._mp3_cleanup_draft(context)

    async def route(self, update, context):
        text = update.effective_message.text.strip()
        uid = update.effective_user.id
        pending = context.user_data.get("studio_input")
        if text in (HOME,COVERS,BPM,MP3,SETTINGS,"⬅️ Назад в меню"):
            await self.cancel_pending(context)
            return await self.section(update,context,{COVERS:"cover",BPM:"bpm",MP3:"mp3",SETTINGS:"settings"}.get(text,"home"))
        from access_control import manage
        if await manage(update, context, self.host.ADMIN_USER_ID):
            return 0
        if text == "⬅️ Назад" and pending:
            context.user_data.pop("studio_input",None)
            return await self.section(update,context,"cover")
        if text in NAV and text != "⬅️ Назад":
            for key in ("studio_input","awaiting_preset_name","awaiting_shared_preset_name","awaiting_custom_structure"):
                context.user_data.pop(key,None)
            pending = None
        if text == "⬅️ Назад" and context.user_data.get("studio_section") in ("cover","settings"):
            return await self.section(update,context,"home")
        if text == "🔢 BPM таймкоды":
            context.user_data["studio_section"]="bpm"
            context.user_data.pop("bpm_selected_structure",None)
            await update.effective_message.reply_text("Введи BPM (1–300):", reply_markup=keyboard([["⬅️ Назад",HOME]]))
            return self.host.BPM_INPUT
        if text == "🔔 Уведомления о перезапуске":
            return await self.host.main_menu_choice(update,context)
        if text in ("🎬 VEVO", "🔞 EXPLICIT"):
            s = self.profile(uid)
            s["style"] = "vevo" if text.startswith("🎬") else "explicit"
            self.store.put(uid, s)
            await self.section(update, context, "cover")
            return 0
        if text == "📷 Создать обложку":
            await update.effective_message.reply_text("Пришли картинку. Для лучшего качества отправь её файлом. Для повторной обработки последней картинки нажми «Пересобрать обложку».")
            return 0
        if text in ("⚙️ Параметры обложки", "⚙️ Настройка стиля"):
            await self.section(update, context, "cover")
            await self.settings(update,uid)
            return 0
        if text == "💾 Сохранить обложку как пресет":
            context.user_data["studio_input"] = ("save",None)
            await update.effective_message.reply_text("Название нового пресета (1–40 символов):",reply_markup=keyboard([["⬅️ Назад",HOME]]))
            return 0
        if text == "🗂 Мои обложки":
            await self.presets(update,uid)
            return 0
        if text in ("🎬 VEVO — оригинал","🔞 EXPLICIT — оригинал"):
            style = "vevo" if text.startswith("🎬") else "explicit"
            self.store.put(uid,builtin(style))
            await self.section(update,context,"cover")
            return 0
        if text == "🔄 Пересобрать обложку":
            await self.photo(update,context,repeat=True)
            return 0
        if text in ("🗂 BPM пресеты","🛒 BPM магазин"):
            await self.bpm_presets(update,uid,shared=text.startswith("🛒"))
            return 0
        if text == "💾 Сохранить BPM пресет":
            await self.host.savepreset_command(update,context)
            return 0
        if text == "🌍 Поделиться BPM пресетом":
            await self.host.save_to_shared_command(update,context)
            return 0
        if pending:
            action,key = pending
            try:
                if action == "field":
                    s = self.profile(uid)
                    s[key] = validate_value(key,text)
                    self.store.put(uid,s)
                elif action == "save":
                    self.store.save(uid,text,self.profile(uid))
                elif action == "rename":
                    self.store.rename(uid,key,text)
                context.user_data.pop("studio_input",None)
                await update.effective_message.reply_text("✅ Сохранено")
                await self.section(update,context,"cover")
                if action == "field":
                    await self.settings(update,uid,FIELDS[key][2])
                    if context.user_data.get("cover_source"):
                        await self.photo(update,context,repeat=True)
            except ValueError as error:
                await update.effective_message.reply_text(str(error))
            return 0
        if context.user_data.get("awaiting_preset_name"):
            if text == "⬅️ Назад":
                context.user_data.pop("awaiting_preset_name",None)
                return await self.section(update,context,"bpm")
            return await self.host.handle_preset_name_input(update,context)
        if context.user_data.get("awaiting_shared_preset_name"):
            if text == "⬅️ Назад":
                context.user_data.pop("awaiting_shared_preset_name",None)
                return await self.section(update,context,"bpm")
            return await self.host.handle_shared_preset_name_input(update,context)
        if await self.host.handle_mp3_edit_text(update,context):
            return 0
        return None

    async def settings(self,update,uid,group=None):
        s = self.profile(uid)
        if group not in GROUPS:
            await self.screen(update,"⚙️ Параметры обложки\nВсе изменения сохраняются автоматически в твоём профиле. Размеры в px указаны для 1920×1080 и масштабируются при экспорте.",
                              [[Button(label,callback_data=f"cv:group:{key}")] for key,label in GROUPS.items()])
            return
        rows = []
        for key,(label,spec,category) in FIELDS.items():
            explicit_only = {"background", "explicit_blur", "background_brightness", "explicit_fg_size", "foreground_x", "foreground_y", "explicit_wm_size"}
            vevo_only = {"fit", "vevo_wm_size"}
            if s["style"] == "vevo" and key in explicit_only:
                continue
            if s["style"] == "explicit" and key in vevo_only:
                continue
            if category == group:
                value = spec.get(s[key],s[key]) if isinstance(spec,dict) else s[key]
                rows.append([Button(f"{label}: {value}",callback_data=f"cv:field:{key}")])
        rows.append([Button("⬅️ Все параметры",callback_data="cv:group:all")])
        await self.screen(update,f"{GROUPS[group]} · {s['style'].upper()}",rows)

    async def presets(self,update,uid,page=0):
        items = self.store.list(uid)
        page = min(max(0,page),max(0,(len(items)-1)//8))
        rows = [[Button(name,callback_data=f"cv:preset:{pid}")] for pid,name in items[page*8:page*8+8]]
        nav = []
        if page:
            nav.append(Button("←",callback_data=f"cv:list:{page-1}"))
        if len(items) > (page+1)*8:
            nav.append(Button("→",callback_data=f"cv:list:{page+1}"))
        if nav:
            rows.append(nav)
        await self.screen(update,"🗂 Мои обложки" if items else "Пока нет именованных пресетов. Текущие параметры уже сохранены автоматически. Нажми «Сохранить обложку как пресет».",rows)

    async def bpm_presets(self,update,uid,shared=False,page=0):
        presets = self.host.load_shared_presets() if shared else self.host.load_presets(uid)
        names = sorted(n for n,p in presets.items() if not shared or not p.get("hidden"))
        page = min(max(0,page),max(0,(len(names)-1)//8))
        mode = "shared" if shared else "own"
        rows = [[Button(n[:55],callback_data=f"bp:pick:{mode}:{digest(n)}")] for n in names[page*8:page*8+8]]
        nav=[]
        if page:
            nav.append(Button("←",callback_data=f"bp:list:{mode}:{page-1}"))
        if len(names)>(page+1)*8:
            nav.append(Button("→",callback_data=f"bp:list:{mode}:{page+1}"))
        if nav:
            rows.append(nav)
        await self.screen(update,("🛒 Общие BPM пресеты" if shared else "🗂 BPM пресеты") if names else "Пресетов пока нет. Сначала рассчитай таймкоды и сохрани результат.",rows)

    async def callback(self,update,context):
        query=update.callback_query
        await query.answer()
        uid=update.effective_user.id
        parts=query.data.split(":")
        try:
            await self.cancel_pending(context)
            context.user_data["studio_section"] = "bpm" if parts[0]=="bp" else "cover"
            if parts[0]=="bp":
                await self.bpm_callback(update,context,parts)
                return 0
            _,action,key=parts[:3]
            context.user_data.pop("studio_input",None)
            if action=="group":
                await self.settings(update,uid,key)
            elif action=="field":
                label,spec,_=FIELDS[key]
                if isinstance(spec,dict):
                    await self.screen(update,label,[[Button(v,callback_data=f"cv:set:{key}:{k}")] for k,v in spec.items()])
                else:
                    context.user_data["studio_input"]=("field",key)
                    hint="#101010" if spec=="color" else ("имя без расширения, 1–60 символов" if spec=="filename" else f"{spec[0]}–{spec[1]}")
                    await query.message.reply_text(f"{label}\nВведи значение ({hint}). Сейчас: {self.profile(uid)[key]}",reply_markup=keyboard([["⬅️ Назад",HOME]]))
            elif action=="set":
                s=self.profile(uid)
                s[key]=validate_value(key,parts[3])
                self.store.put(uid,s)
                await query.message.reply_text("✅ Настройка сохранена", reply_markup=keyboard(COVER_ROWS))
                await self.settings(update,uid,FIELDS[key][2])
                if context.user_data.get("cover_source"):
                    await self.photo(update,context,repeat=True)
            elif action=="list":
                await self.presets(update,uid,int(key))
            elif action in ("preset","load","rename","delete","confirm"):
                name,s=self.store.load(uid,int(key))
                if action=="preset":
                    await self.screen(update,f"{name} · {s['style'].upper()} · {s['resolution']} · {s['export_format']}",[
                        [Button("✅ Применить",callback_data=f"cv:load:{key}")],
                        [Button("✏️ Переименовать",callback_data=f"cv:rename:{key}"),Button("🗑 Удалить",callback_data=f"cv:delete:{key}")]])
                elif action=="load":
                    self.store.put(uid,s)
                    await self.section(update,context,"cover")
                    await query.message.reply_text(f"✅ Применён пресет «{name}»")
                elif action=="rename":
                    context.user_data["studio_input"]=("rename",int(key))
                    await query.message.reply_text("Новое название:",reply_markup=keyboard([["⬅️ Назад",HOME]]))
                elif action=="delete":
                    await self.screen(update,f"Удалить «{name}»?",[[Button("Удалить",callback_data=f"cv:confirm:{key}"),Button("Отмена",callback_data="cv:list:0")]])
                else:
                    self.store.delete(uid,int(key))
                    await self.presets(update,uid)
        except (ValueError,KeyError,IndexError,TypeError) as error:
            await query.message.reply_text(f"Не удалось применить действие: {error}. Открой меню заново.")
        return 0

    async def bpm_callback(self,update,context,parts):
        _,action,mode,key=parts
        uid=update.effective_user.id
        shared=mode=="shared"
        if action=="list":
            await self.bpm_presets(update,uid,shared,int(key))
            return
        presets=self.host.load_shared_presets() if shared else self.host.load_presets(uid)
        name=next((n for n,p in presets.items() if digest(n)==key and (not shared or not p.get("hidden"))),None)
        if name is None:
            raise ValueError("Пресет не найден")
        if action=="pick":
            rows=[[Button("▶️ Загрузить таймкоды",callback_data=f"bp:load:{mode}:{key}"),Button("🔢 Другой BPM",callback_data=f"bp:tempo:{mode}:{key}")]]
            if not shared:
                rows.append([Button("🗑 Удалить",callback_data=f"bp:delete:{mode}:{key}")])
            await self.screen(update,name,rows)
        elif action in ("load","tempo"):
            structure=self.host.deserialize_structure(presets[name]["structure"])
            bpm=int(presets[name]["bpm"])
            if not 1<=bpm<=300:
                raise ValueError("BPM вне диапазона")
            if action=="tempo":
                context.user_data["bpm_selected_structure"]=structure
                await update.effective_message.reply_text("Введи новый BPM (1–300) для выбранной структуры:",reply_markup=keyboard([["⬅️ Назад",HOME]]))
                # callback caller returns this state via a flag below
                context.user_data["bpm_tempo_pending"]=True
            else:
                self.host.remember_bpm_result(context,bpm,structure)
                await update.effective_message.reply_text(f"✅ {name}\nBPM: {bpm}\n\n{self.host.build_timestamps(structure,bpm)}",reply_markup=keyboard(BPM_ROWS))
        elif action=="delete" and not shared:
            await self.screen(update,f"Удалить «{name}»?",[[Button("Удалить",callback_data=f"bp:confirm:own:{key}"),Button("Отмена",callback_data="bp:list:own:0")]])
        elif action=="confirm" and not shared:
            del presets[name]
            self.host.save_presets(uid,presets)
            await self.bpm_presets(update,uid)

    async def photo(self,update,context,repeat=False):
        uid=update.effective_user.id
        if repeat:
            file_id=context.user_data.get("cover_source")
            if not file_id:
                await update.effective_message.reply_text("Сначала пришли картинку. После перезапуска её нужно отправить заново; настройки сохраняются.")
                return
            source=await context.bot.get_file(file_id)
        else:
            media=update.effective_message.document or update.effective_message.photo[-1]
            if media.file_size and media.file_size>19*1024*1024:
                await update.effective_message.reply_text("Пришли картинку до 19 МБ.")
                return
            source=await media.get_file()
        status=await update.effective_message.reply_text("⏳ Собираю обложку…")
        try:
            raw=await source.download_as_bytearray()
            settings=self.profile(uid)
            result,output=await asyncio.to_thread(render_bytes,raw,settings)
            context.user_data["cover_source"]=source.file_id
            caption=f"{settings['style'].upper()} · {settings['resolution']} · {settings['export_format']} · {len(output.getbuffer())/1024:.0f} КБ"
            delivery=settings["delivery"]
            if delivery in ("photo","both"):
                preview=BytesIO()
                result.save(preview,"JPEG",quality=90)
                preview.seek(0)
                await update.effective_message.reply_photo(preview,caption="Предпросмотр · Telegram сжимает фото")
            if delivery in ("file","both"):
                await update.effective_message.reply_document(output,filename=output.name,caption=caption)
            await status.edit_text("✅ Готово. Измени параметры и нажми «Пересобрать обложку», чтобы применить их к той же картинке.")
        except Exception as error:
            self.host.logger.exception("Cover processing failed")
            self.host.record_error("cover_processing_failed",str(error))
            await status.edit_text("❌ Не удалось обработать картинку. Пришли корректный JPEG, PNG или WebP до 19 МБ и 30 мегапикселей.")
