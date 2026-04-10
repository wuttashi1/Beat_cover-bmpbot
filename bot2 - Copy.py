import logging
from PIL import Image, ImageFilter, ImageChops
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "7378399292:AAE8iXgwZE5Tymi7NO9B201K5T6NbUF4oBs"

TARGET_W, TARGET_H = 1920, 1080
WATERMARK_PATH = "watermark.png"
EXPLICIT_WM_PATH = "explicit_wm.png"
WATERMARK_MAX_WIDTH = 450
EXPLICIT_WM_MAX_WIDTH = 300

# храним выбранный стиль пользователя
user_styles = {}  # user_id: "vevo" | "explicit"


# -------------------- UTILS --------------------

def resize_cover(img, target_w, target_h):
    ow, oh = img.size
    orr = ow / oh
    trr = target_w / target_h

    if orr > trr:
        nh = target_h
        nw = int(nh * orr)
    else:
        nw = target_w
        nh = int(nw / orr)

    img = img.resize((nw, nh), Image.LANCZOS)

    left = (nw - target_w) // 2
    top = (nh - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))


def crop_square_center(img):
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))

STYLE_CONFIG = {
    "explicit": {
        "fg_size": 820,
        "radial_strength": 28,
        "radial_center": "center",  # center | bottom | top
        "vignette": True
    }
}


# -------------------- STYLES --------------------

def style_vevo(img):
    img = resize_cover(img, TARGET_W, TARGET_H)

    wm = Image.open(WATERMARK_PATH).convert("RGBA")
    if wm.width > WATERMARK_MAX_WIDTH:
        r = WATERMARK_MAX_WIDTH / wm.width
        wm = wm.resize((int(wm.width * r), int(wm.height * r)), Image.LANCZOS)

    img = img.convert("RGBA")
    img.alpha_composite(wm, (30, TARGET_H - wm.height - 30))
    return img


def style_explicit(img):
    cfg = STYLE_CONFIG["explicit"]

    bg = resize_cover(img, TARGET_W, TARGET_H)

    # ✅ SPIN blur
    bg = radial_spin_blur(
        bg,
        strength=cfg["spin_strength"],
        center=None
    )

    bg = bg.convert("RGBA")

    fg = crop_square_center(img)
    size = cfg["fg_size"]
    fg = fg.resize((size, size), Image.LANCZOS)

    pos = (
        (TARGET_W - size) // 2,
        (TARGET_H - size) // 2
    )
    bg.alpha_composite(fg.convert("RGBA"), pos)

    # SCREEN watermark
    wm = Image.open(EXPLICIT_WM_PATH).convert("RGBA")
    wm = wm.resize((int(wm.width * 0.35), int(wm.height * 0.35)), Image.LANCZOS)

    layer = Image.new("RGBA", bg.size)
    layer.paste(wm, (30, TARGET_H - wm.height - 30))

    bg = ImageChops.screen(bg, layer)

    return bg


QUALITY_MAP = {
    "draft": 6,
    "good": 10,
    "best": 18
}

STYLE_CONFIG = {
    "explicit": {
        "fg_size": 820,
        "spin_strength": QUALITY_MAP["good"]
    }
}

# -------------------- HANDLERS --------------------
def radial_spin_blur(img, strength=10, center=None):
    """
    Эмуляция Photoshop Radial Blur -> SPIN
    strength: 5–25 (10 ≈ PS Amount 10–12)
    """
    img = img.convert("RGBA")
    w, h = img.size

    if center is None:
        cx, cy = w // 2, h // 2
    else:
        cx, cy = center

    accum = Image.new("RGBA", (w, h))
    alpha = 1 / strength

    for i in range(strength):
        angle = (i - strength / 2) * 0.8  # КЛЮЧ: микровращение

        rotated = img.rotate(
            angle,
            resample=Image.BICUBIC,
            center=(cx, cy)
        )

        accum = Image.blend(accum, rotated, alpha)

    return accum


async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    style = user_styles.get(user_id, "vevo")

    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive("input.jpg")

    img = Image.open("input.jpg").convert("RGB")

    if style == "explicit":
        result = style_explicit(img)
    else:
        result = style_vevo(img)

    result.convert("RGB").save("edited.jpg", "JPEG", quality=95)

    await update.message.reply_photo(photo=open("edited.jpg", "rb"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("🎨 Выбор стиля")]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        "Кидай фото. Стиль можно выбрать 👇",
        reply_markup=keyboard
    )


async def choose_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("VEVO MARK")],
            [KeyboardButton("EXPLICIT BLUR")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.message.reply_text("Выбери стиль:", reply_markup=keyboard)


async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if text == "VEVO MARK":
        user_styles[user_id] = "vevo"
    elif text == "EXPLICIT BLUR":
        user_styles[user_id] = "explicit"
    else:
        return

    await update.message.reply_text("✅ Стиль сохранён. Загружай фото.")


# -------------------- MAIN --------------------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.COMMAND, start))
    app.add_handler(MessageHandler(filters.Regex("^🎨 Выбор стиля$"), choose_style))
    app.add_handler(MessageHandler(filters.Regex("^(VEVO MARK|EXPLICIT BLUR)$"), set_style))
    app.add_handler(MessageHandler(filters.PHOTO, process_photo))

    app.run_polling()


if __name__ == "__main__":
    main()
