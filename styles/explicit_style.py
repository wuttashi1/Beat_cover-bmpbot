from PIL import Image, ImageChops

EXPLICIT_WM_PATH = "explicit_wm.png"
TARGET_W, TARGET_H = 1920, 1080


def resize_cover(img, target_w, target_h):
    """Resize image to cover target dimensions"""
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
    """Crop square from center of image"""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def radial_spin_blur(img, strength=10, center=None):
    """
    Фотошоп-подобный радиальный SPIN blur
    strength: 5-25 (10 оптимально)
    Сохраняет оригинальное качество с использованием BICUBIC
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
        # КЛЮЧЕВОЙ ПАРАМЕТР: микровращение для spin эффекта
        angle = (i - strength / 2) * 0.8

        rotated = img.rotate(
            angle,
            resample=Image.BICUBIC,
            center=(cx, cy),
            fillcolor=(0, 0, 0, 0),
        )

        accum = Image.blend(accum, rotated, alpha)

    return accum


def style_explicit(img, wm_size, blur_strength, fg_size, quality_level):
    """
    Apply EXPLICIT style with radial spin blur
    Сохраняет ОРИГИНАЛЬНОЕ КАЧЕСТВО как в старом коде
    """
    # 1. Масштабируем фон (сохраняем качество)
    bg = resize_cover(img, TARGET_W, TARGET_H)

    # 2. Применяем SPIN blur (оригинальный метод, но параметризируемый)
    # Масштабируем blur_strength к оптимальному диапазону 5-25
    spin_strength = max(5, min(25, blur_strength))
    bg = radial_spin_blur(bg, strength=spin_strength)

    bg = bg.convert("RGBA")

    # 3. Извлекаем и масштабируем центральное фото (максимальное качество)
    fg = crop_square_center(img)
    fg = fg.resize((fg_size, fg_size), Image.LANCZOS)

    # 4. Позиционируем центральное фото
    pos = ((TARGET_W - fg_size) // 2, (TARGET_H - fg_size) // 2)
    bg.alpha_composite(fg.convert("RGBA"), pos)

    # 5. Добавляем водяной знак (SCREEN blend как в оригинале)
    wm = Image.open(EXPLICIT_WM_PATH).convert("RGBA")

    # Масштабируем водяной знак
    if wm.width > wm_size:
        r = wm_size / wm.width
        wm = wm.resize((int(wm.width * r), int(wm.height * r)), Image.LANCZOS)

    # Создаем слой с водяным знаком
    layer = Image.new("RGBA", bg.size)
    layer.paste(wm, (30, TARGET_H - wm.height - 30), wm)

    # SCREEN blend (оригинальный метод)
    bg = ImageChops.screen(bg, layer)

    return bg
